#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path

import requests


APP_DIR = Path(__file__).resolve().parent
SONGS_DIR = APP_DIR / "songs"
DEFAULT_OUTPUT_DIR = APP_DIR / "meta-enriched"
MUSICBRAINZ_CACHE = APP_DIR / ".musicbrainz_cache.json"

AUDIO_EXTENSIONS = {".opus", ".m4a", ".mp3", ".flac", ".ogg"}
MUSICBRAINZ_RECORDING_URL = "https://musicbrainz.org/ws/2/recording"
USER_AGENT = "CodexMusicMetadataUpdater/2.0 (local metadata updater)"
MIN_MATCH_SCORE = 0.82
PROGRESS_WIDTH = 28
PLACEHOLDER_ARTISTS = {"NA", "N/A", "UNKNOWN", "NONE", "NULL"}
ARTIST_TITLE_SEPARATORS = (" - ", " – ", " — ", "：", ":")
CACHE_VERSION = "v3-date-only"
YOUTUBE_TITLE_PATTERNS = (
    r"\s+(?:ft\.?|feat\.?|featuring)\s+.+$",
    r"\s*\+\s*lyrics?\b.*$",
    r"\s*[-|:]*\s*with\s+lyrics?\s*$",
    r"\s*[-|:]*\s*lyrics?\s*$",
    r"\s*[-|:]*\s*official\s+hd\s+(?:music\s+)?video\s*$",
    r"\s*[-|:]*\s*official\s+(?:music\s+)?video\s*$",
    r"\s*[-|:]*\s*official\s+audio\s*$",
    r"\s*[-|:]*\s*official\s+visuali[sz]er\s*$",
    r"\s*[-|:]*\s*official\s+lyric\s+video\s*$",
    r"\s*[-|:]*\s*lyric\s+video\s*$",
    r"\s*[-|:]*\s*music\s+video\s*$",
    r"\s*[-|:]*\s*directions\s+(?:music\s+)?video\s*$",
    r"\s*[-|:]*\s*audio\s+only\s*$",
    r"\s*[-|:]*\s*album\s+version\s+video\s*$",
    r"\s*[-|:]*\s*album\s+version\s*$",
    r"\s*[-|:]*\s*single\s+version\s*$",
    r"\s*[-|:]*\s*video\s*$",
    r"\s*[-|:]*\s*visuali[sz]er\s*$",
    r"\s*[-|:]*\s*full\s+album\s*$",
    r"\s*[-|:]*\s*official\s*$",
    r"\s*[-|:]*\s*remastered\s+hd\s*$",
    r"\s*[-|:]*\s*remaster(?:ed)?\s*$",
    r"\s*[-|:]*\s*hd\s*$",
    r"\s*[-|:]*\s*4k\s*$",
)


@dataclass(frozen=True)
class TrackMetadata:
    path: Path
    title: str
    artist: str
    track: str = ""
    album: str = ""
    date: str = ""
    recording_id: str = ""
    release_id: str = ""


@dataclass(frozen=True)
class MusicBrainzResult:
    title: str
    artist: str
    album: str = ""
    date: str = ""
    track: str = ""
    recording_id: str = ""
    release_id: str = ""


class MusicBrainzUnavailable(RuntimeError):
    pass


class MusicBrainzNoMatch(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update metadata for audio files in musicmeta/songs.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Move successfully tagged files here. Defaults to musicmeta/meta-enriched.",
    )
    return parser.parse_args()


def audio_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS)


def clean_download_name(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[＿]", "_", value)
    value = re.sub(r"[：]", ":", value)
    value = re.sub(r"\s+", " ", value)
    value = re.split(r"\s*[\[(（【]", value, maxsplit=1)[0]
    for pattern in YOUTUBE_TITLE_PATTERNS:
        value = re.sub(pattern, "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*[|/]\s*$", "", value)
    value = re.sub(r"\s*[-–—_:]+\s*$", "", value)
    value = re.sub(r"\s+[-–—_:]+\s+", " - ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -–—_:：")


def split_artist_title(value: str) -> tuple[str, str] | None:
    for separator in ARTIST_TITLE_SEPARATORS:
        if separator in value:
            artist, title = (part.strip() for part in value.split(separator, 1))
            if artist and title:
                return artist, title
    return None


def parse_filename(path: Path) -> TrackMetadata:
    if "_" not in path.stem:
        raise ValueError("expected filename format title_artist.ext")

    title, artist = (clean_download_name(part) for part in path.stem.rsplit("_", 1))
    if not title or not artist:
        raise ValueError("expected non-empty title and artist in title_artist.ext")
    if artist.upper() in PLACEHOLDER_ARTISTS:
        inferred = split_artist_title(title)
        if not inferred:
            raise ValueError("artist is a placeholder and no artist/title separator was found")
        artist, title = inferred
        artist = clean_download_name(artist)
        title = clean_download_name(title)
        if not title or not artist:
            raise ValueError("expected non-empty title and artist after filename cleanup")

    return TrackMetadata(path=path, title=title, artist=artist)


def normalize(value: str) -> str:
    value = value.lower().replace("&", "and")
    value = re.sub(r"[\W_]+", " ", value)
    return " ".join(value.split())


def match_score(expected_title: str, expected_artist: str, found_title: str, found_artist: str) -> float:
    title_score = SequenceMatcher(None, normalize(expected_title), normalize(found_title)).ratio()
    artist_score = SequenceMatcher(None, normalize(expected_artist), normalize(found_artist)).ratio()
    return (title_score * 0.65) + (artist_score * 0.35)


def artist_score(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize(left), normalize(right)).ratio()


def load_cache() -> dict[str, dict[str, str]]:
    if not MUSICBRAINZ_CACHE.exists():
        return {}
    try:
        return json.loads(MUSICBRAINZ_CACHE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_cache(cache: dict[str, dict[str, str]]) -> None:
    MUSICBRAINZ_CACHE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def cache_key(title: str, artist: str) -> str:
    return f"{CACHE_VERSION}::{normalize(title)}::{normalize(artist)}"


def artist_credit(recording: dict) -> str:
    names: list[str] = []
    for credit in recording.get("artist-credit") or []:
        if not isinstance(credit, dict):
            continue
        name = credit.get("name") or (credit.get("artist") or {}).get("name")
        if name:
            names.append(name)
    return ", ".join(names)


def release_artist_credit(release: dict) -> str:
    names: list[str] = []
    for credit in release.get("artist-credit") or []:
        if not isinstance(credit, dict):
            continue
        name = credit.get("name") or (credit.get("artist") or {}).get("name")
        if name:
            names.append(name)
    return ", ".join(names)


def release_priority(release: dict, expected_artist: str) -> tuple[int, int, int, int, str]:
    status_priority = {"official": 0, "promotion": 1, "bootleg": 2, "pseudo-release": 3}
    type_priority = {"album": 0, "ep": 1, "single": 2, "other": 3}
    release_group = release.get("release-group") or {}
    release_artist = release_artist_credit(release)
    secondary_types = {value.lower() for value in release_group.get("secondary-types") or []}

    return (
        0 if release_artist and artist_score(expected_artist, release_artist) >= 0.80 else 1,
        1 if "compilation" in secondary_types or normalize(release_artist) == "various artists" else 0,
        status_priority.get((release.get("status") or "").lower(), 9),
        type_priority.get((release_group.get("primary-type") or "").lower(), 4),
        release.get("date") or "9999",
    )


def track_number(release: dict, recording_id: str) -> str:
    for medium in release.get("media") or []:
        for track in medium.get("tracks") or []:
            if (track.get("recording") or {}).get("id") == recording_id:
                return str(track.get("number") or track.get("position") or "")
    return ""


def result_from_recording(recording: dict, expected_artist: str) -> MusicBrainzResult:
    recording_id = recording.get("id") or ""
    releases = sorted(recording.get("releases") or [], key=lambda release: release_priority(release, expected_artist))
    release = releases[0] if releases else {}
    release_group = release.get("release-group") or {}
    release_date = release.get("date") or ""

    return MusicBrainzResult(
        title=recording.get("title") or "",
        artist=artist_credit(recording),
        album=release_group.get("title") or release.get("title") or "",
        date=release_date,
        track=track_number(release, recording_id),
        recording_id=recording_id,
        release_id=release.get("id") or "",
    )


def search_musicbrainz(
    title: str,
    artist: str,
    cache: dict[str, dict[str, str]],
) -> MusicBrainzResult | None:
    key = cache_key(title, artist)
    if key in cache:
        return MusicBrainzResult(**cache[key]) if cache[key] else None

    try:
        response = requests.get(
            MUSICBRAINZ_RECORDING_URL,
            params={
                "query": f'recording:"{title}" AND artist:"{artist}"',
                "fmt": "json",
                "limit": 10,
                "inc": "artist-credits+releases+media",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=12,
        )
        response.raise_for_status()
        time.sleep(1.1)
    except requests.RequestException as exc:
        raise MusicBrainzUnavailable(f"MusicBrainz cannot be reached: {exc}") from exc

    best: MusicBrainzResult | None = None
    best_score = 0.0
    for recording in response.json().get("recordings") or []:
        candidate = result_from_recording(recording, artist)
        score = match_score(title, artist, candidate.title, candidate.artist)
        if score >= MIN_MATCH_SCORE and score > best_score:
            best = candidate
            best_score = score

    cache[key] = best.__dict__ if best else {}
    save_cache(cache)
    return best


def search_musicbrainz_with_retry(
    title: str,
    artist: str,
    cache: dict[str, dict[str, str]],
) -> MusicBrainzResult | None:
    match = search_musicbrainz(title, artist, cache)
    if match:
        return match
    return search_musicbrainz(artist, title, cache)


def enrich(track: TrackMetadata, cache: dict[str, dict[str, str]]) -> TrackMetadata:
    match = search_musicbrainz_with_retry(track.title, track.artist, cache)
    if not match:
        raise MusicBrainzNoMatch("no confident MusicBrainz match found after title/artist and artist/title attempts")

    return replace(
        track,
        title=match.title or track.title,
        artist=match.artist or track.artist,
        album=track.album or match.album,
        date=track.date or match.date,
        track=track.track or match.track,
        recording_id=match.recording_id,
        release_id=match.release_id,
    )


def ffmpeg_metadata_args(track: TrackMetadata) -> list[str]:
    tags = {
        "title": track.title,
        "artist": track.artist,
        "track": track.track,
        "album": track.album,
        "date": track.date,
        "musicbrainz_trackid": track.recording_id,
        "musicbrainz_albumid": track.release_id,
    }

    args: list[str] = []
    for key, value in tags.items():
        if value:
            args.extend(["-metadata", f"{key}={value}"])
    return args


def write_metadata(track: TrackMetadata) -> None:
    temp_path = track.path.with_name(f"{track.path.stem}.tagging{track.path.suffix}")
    if temp_path.exists():
        temp_path.unlink()

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(track.path),
        "-map",
        "0",
        "-c",
        "copy",
        "-map_metadata",
        "-1",
        *ffmpeg_metadata_args(track),
        str(temp_path),
    ]

    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode:
        if temp_path.exists():
            temp_path.unlink()
        raise RuntimeError(f"ffmpeg failed for {track.path}: {result.stderr.strip()}")

    os.replace(temp_path, track.path)


def unique_destination(path: Path, output_dir: Path) -> Path:
    destination = output_dir / path.name
    if not destination.exists():
        return destination

    counter = 2
    while True:
        destination = output_dir / f"{path.stem} [{counter}]{path.suffix}"
        if not destination.exists():
            return destination
        counter += 1


def process_file(path: Path, output_dir: Path, cache: dict[str, dict[str, str]]) -> None:
    track = enrich(parse_filename(path), cache)
    write_metadata(track)
    shutil.move(str(path), str(unique_destination(path, output_dir)))


def progress_line(done: int, total: int, moved: int, failed: int) -> str:
    if total == 0:
        percent = 100
        filled = PROGRESS_WIDTH
    else:
        percent = round((done / total) * 100)
        filled = round((done / total) * PROGRESS_WIDTH)

    bar = "#" * filled + "-" * (PROGRESS_WIDTH - filled)
    return f"[{bar}] {done}/{total} {percent:3d}%  moved:{moved} failed:{failed}"


def render_progress(done: int, total: int, moved: int, failed: int) -> None:
    if not sys.stdout.isatty():
        return
    print("\r" + progress_line(done, total, moved, failed), end="", flush=True)


def finish_progress(done: int, total: int, moved: int, failed: int) -> None:
    if sys.stdout.isatty():
        print("\r" + progress_line(done, total, moved, failed))


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(APP_DIR))
    except ValueError:
        return str(Path(path.parent.name) / path.name)


def print_failures(failures: list[tuple[Path, str]]) -> None:
    if not failures:
        return

    by_reason: dict[str, list[Path]] = defaultdict(list)
    for path, reason in failures:
        by_reason[reason].append(path)

    print()
    for reason, paths in by_reason.items():
        print(f"Following files failed because {reason}:")
        for path in paths:
            print(f"  - {display_path(path)}")


def main() -> None:
    args = parse_args()

    if not SONGS_DIR.exists():
        print(f"{display_path(SONGS_DIR)} does not exist; nothing to do.")
        return

    output_dir = args.output.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = audio_files(SONGS_DIR)
    cache = load_cache()
    moved = 0
    failures: list[tuple[Path, str]] = []
    render_progress(0, len(paths), moved, len(failures))
    for index, path in enumerate(paths, start=1):
        try:
            process_file(path, output_dir, cache)
            moved += 1
        except Exception as exc:
            failures.append((path, str(exc)))
        render_progress(index, len(paths), moved, len(failures))

    finish_progress(len(paths), len(paths), moved, len(failures))
    print(f"Updated and moved {moved} audio files to {display_path(output_dir)}.")
    if failures:
        print(f"Left {len(failures)} unsuccessful audio files in {display_path(SONGS_DIR)}.")
        print_failures(failures)


if __name__ == "__main__":
    main()
