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
from dataclasses import dataclass, fields, replace
from rapidfuzz import fuzz
from pathlib import Path

from mutagen.mp4 import MP4, MP4Cover
import requests


APP_DIR = Path(__file__).resolve().parent
SONGS_DIR = APP_DIR / "songs"
DEFAULT_OUTPUT_DIR = APP_DIR / "meta-enriched"
MUSICBRAINZ_CACHE = APP_DIR / ".musicbrainz_cache.json"

COVER_ART_URL = "https://coverartarchive.org/release/{}/front-500"
COVER_DIR = APP_DIR / "covers"
LEGACY_COVER_CACHE = APP_DIR / ".cover_cache"

AUDIO_EXTENSIONS = {".opus", ".m4a", ".mp3", ".flac", ".ogg"}
MP4_EXTENSIONS = {".m4a", ".mp4", ".m4b", ".mov"}
MUSICBRAINZ_RECORDING_URL = "https://musicbrainz.org/ws/2/recording"
USER_AGENT = "CodexMusicMetadataUpdater/2.0 (local metadata updater)"
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": USER_AGENT,
})
MUSICBRAINZ_MIN_REQUEST_INTERVAL = 1.1
LAST_MUSICBRAINZ_REQUEST = 0.0
MIN_MATCH_SCORE = 0.82
TITLE_ONLY_MIN_MATCH_SCORE = 0.96
PROGRESS_WIDTH = 28
PLACEHOLDER_ARTISTS = {"NA", "N/A", "UNKNOWN", "NONE", "NULL"}
ARTIST_TITLE_SEPARATORS = (" - ", " – ", " — ", "：", ":")
CACHE_VERSION = "v4-normalized-match"
LEGACY_POSITIVE_CACHE_VERSIONS = ("v3-date-only",)
ARTIST_SPLIT_PATTERN = re.compile(
    r"\s*(?:,|;|\+|\b(?:feat|ft)\.?\s+|\bfeaturing\s+|\bwith\s+)\s*",
    flags=re.IGNORECASE,
)
MANUAL_TRACK_OVERRIDES = {
    "America (Radio Mix)_Motionless In White.m4a": ("America (Radio Mix)", "Motionless In White"),
    "Bang Bang.m4a": ("Bang Bang", "K'NAAN, Adam Levine"),
    "Bless the Lord, My Soul_The London Fox Taize Choir, Jacques Berthier.m4a": (
        "Bless the Lord, My Soul",
        "The London Fox Taize Choir, Jacques Berthier",
    ),
    "Bubblegum Bitch (Slowed)_ANGUISH, AmbVsh, ily.m4a": ("Bubblegum Bitch (Slowed)", "ANGUISH, AmbVsh, ily"),
    "DOLBIT!_LXKERSON.m4a": ("DOLBIT!", "LXKERSON"),
    "Dance, Dance.m4a": ("Dance, Dance", "Fall Out Boy"),
    "Die Your Daughter_Susannah Joffe, Cella Raiteri, Susannah Joffe.m4a": (
        "Die Your Daughter",
        "Susannah Joffe, Cella Raiteri",
    ),
    "Dig A Giant Hole_Jugboy, Elijah Wright, Elijah Wright.m4a": ("Dig A Giant Hole", "Jugboy, Elijah Wright"),
    "Don't Say Goodbye_overrated, Gustav, Tove Lo, Ilkay Sencan, Alok Achkar Peres Petrillo, Hannah Wilson, Sander Van Der Waal, Robert Uhlmann, Tove Lo, Ilkay Sencan, Alok Achkar Peres Petrillo, Hannah Wilson, Sander Van Der Waal, Robert Uhlmann.m4a": (
        "Don't Say Goodbye",
        "overrated, Gustav, Tove Lo, Ilkay Sencan, Alok Achkar Peres Petrillo, Hannah Wilson, Sander Van Der Waal, Robert Uhlmann",
    ),
    "Electro Fight (Live)_OutworlD2.m4a": ("Electro Fight (Live)", "OutworlD2"),
    "Empire (Let Them Sing)_Bring Me The Horizon.m4a": ("Empire (Let Them Sing)", "Bring Me The Horizon"),
    "Evil Morty Theme (For The Damaged Coda) (Epic Version)_Samuel Kim.m4a": (
        "Evil Morty Theme (For the Damaged Coda) (Epic Version)",
        "Samuel Kim",
    ),
    "Fighting For_Nick Double， EWAVE, Nick Double, EWAVE.m4a": ("Fighting For", "Nick Double, EWAVE"),
    "First Love - Late Spring_Mitski.mp3": ("First Love / Late Spring", "Mitski"),
    "First Love⧸Late Spring_Mitski.m4a": ("First Love / Late Spring", "Mitski"),
    "GOZALO_DOCTOR TREE.m4a": ("GOZALO", "DOCTOR TREE"),
    "Get Jinxed_League of Legends, Djerv.m4a": ("Get Jinxed", "League of Legends, Djerv"),
    "Gold.m4a": ("Gold", "Imagine Dragons"),
    "Holy Spirit, Come to Us (Tui amoris ignem)_Taizé.m4a": ("Holy Spirit, Come to Us", "Taize"),
    "Holy Spirit_Jesus Is The Way.m4a": ("Holy Spirit", "Jesus Is The Way"),
    "Internet baby (interlude)_PinkPantheress.m4a": ("Internet baby (interlude)", "PinkPantheress"),
    "Let Me Drive My Van Into Your Heart (feat. Tom Scharpling)_Steven Universe, Tom Scharpling, Tom Scharpling.m4a": (
        "Let Me Drive My Van Into Your Heart",
        "Steven Universe, Tom Scharpling",
    ),
    "Let’s Go.m4a": ("Let's Go", "Stuck In the Sound"),
    "Love Like You (feat. Rebecca Sugar) [End Credits]_Steven Universe, Rebecca Sugar, Rebecca Sugar.m4a": (
        "Love Like You (End Credits)",
        "Steven Universe, Rebecca Sugar",
    ),
    "Machine.m4a": ("Machine", "Imagine Dragons"),
    "Migraine_Moonstar 88.m4a": ("Migraine", "Moonstar88"),
    "Na Na Na (Na Na Na Na Na Na Na Na Na)_My Chemical Romance.m4a": (
        "Na Na Na (Na Na Na Na Na Na Na Na Na)",
        "My Chemical Romance",
    ),
    "Nocturne (Interlude)_Laufey.m4a": ("Nocturne (Interlude)", "Laufey"),
    "Paro House (Luciid VIP)_Luciid.m4a": ("Paro House (Luciid VIP)", "Luciid"),
    "Pigstep (Stereo Mix)_Lena Raine, Minecraft.m4a": ("Pigstep (Stereo Mix)", "Lena Raine"),
    "Puppets (The First Snow)_Motionless In White.m4a": ("Puppets (The First Snow)", "Motionless In White"),
    "Silver Springs_Mae Shell.m4a": ("Silver Springs", "Mae Shell"),
    "Swan Lake_Natalya Swan, Natalya Swan, Natalya Swan.m4a": ("Swan Lake", "Natalya Swan"),
    "The Curse of the Sad Mummy_League of Legends.m4a": ("The Curse of the Sad Mummy", "League of Legends"),
    "Thunder.m4a": ("Thunder", "Imagine Dragons"),
    "Twin Skeleton's (Hotel In NYC).m4a": ("Twin Skeleton's (Hotel in NYC)", "Fall Out Boy"),
    "Twin Skeleton's (Hotel In NYC)_Fall Out Boy.m4a": ("Twin Skeleton's (Hotel in NYC)", "Fall Out Boy"),
    "Violin V_Helion.m4a": ("Violin V", "Helion"),
    "WNDRLND (feat. Roxi)_Robby Burke, Roxi.m4a": ("WNDRLND", "Robby Burke, Roxi"),
    "Who？.m4a": ("Who?", "Azari"),
    "it's been so long_HARDX.m4a": ("it's been so long", "HARDX"),
    "なまえとつばさをください (feat. Hatsune Miku).m4a": ("なまえとつばさをください", "Babuchan, Hatsune Miku"),
    "なまえとつばさをください (feat. Hatsune Miku)_Babuchan.m4a": (
        "なまえとつばさをください",
        "Babuchan, Hatsune Miku",
    ),
    "愛して　愛して　愛して (feat. Hatsune Miku).m4a": ("愛して 愛して 愛して", "Kikuo, Hatsune Miku"),
}
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
    total_tracks: str = ""

    disc: str = ""

    album: str = ""
    album_artist: str = ""

    date: str = ""

    genre: str = ""

    isrc: str = ""

    recording_id: str = ""
    release_id: str = ""


@dataclass(frozen=True)
class MusicBrainzResult:
    title: str
    artist: str

    album: str = ""
    album_artist: str = ""

    date: str = ""

    track: str = ""
    total_tracks: str = ""

    disc: str = ""

    genre: str = ""

    isrc: str = ""

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
    parser.add_argument(
        "--no-cover",
        action="store_true",
        help="Skip cover art downloads/embedding for a faster metadata-only run.",
    )
    return parser.parse_args()


def audio_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS)


def normalize_download_text(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[＿]", "_", value)
    value = re.sub(r"[，]", ",", value)
    value = re.sub(r"[：]", ":", value)
    value = re.sub(r"[⧸]", "/", value)
    value = re.sub(r"\s+", " ", value)
    return value


def clean_lookup_value(value: str) -> str:
    value = normalize_download_text(value)
    value = re.sub(r"\s*[|/]\s*$", "", value)
    value = re.sub(r"\s*[-–—_:]+\s*$", "", value)
    return value.strip(" -–—_:：")


def clean_download_name(value: str) -> str:
    value = normalize_download_text(value)
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


def manual_track_override(path: Path) -> TrackMetadata | None:
    override = MANUAL_TRACK_OVERRIDES.get(path.name)
    if not override:
        return None

    title, artist = override
    return TrackMetadata(
        path=path,
        title=clean_lookup_value(title),
        artist=compact_artist_list(artist),
    )


def compact_artist_list(value: str) -> str:
    artists = artist_names(value)
    return ", ".join(dict.fromkeys(artists))


def parse_filename(path: Path) -> TrackMetadata:
    manual = manual_track_override(path)
    if manual:
        return manual

    if "_" not in path.stem:
        inferred = split_artist_title(clean_download_name(path.stem))
        if inferred:
            artist, title = inferred
            return TrackMetadata(
                path=path,
                title=clean_download_name(title),
                artist=compact_artist_list(artist),
            )

        title = clean_download_name(path.stem)
        if title:
            return TrackMetadata(path=path, title=title, artist="")
        raise ValueError("expected filename format title_artist.ext")

    title, artist = (clean_download_name(part) for part in path.stem.rsplit("_", 1))
    artist = compact_artist_list(artist)
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


def artist_names(value: str) -> list[str]:
    names: list[str] = []
    for name in ARTIST_SPLIT_PATTERN.split(value):
        cleaned = clean_download_name(name)
        if cleaned:
            names.append(cleaned)
    if len(names) >= 2 and names[1].lower() == "the creator":
        names = [f"{names[0]}, {names[1]}", *names[2:]]
    return names


def primary_artist(value: str) -> str:
    artists = artist_names(value)
    return artists[0] if artists else clean_download_name(value)


def title_similarity(left: str, right: str) -> float:
    return fuzz.token_sort_ratio(normalize(left), normalize(right)) / 100


def artist_similarity(left: str, right: str) -> float:
    left_names = artist_names(left) or [left]
    right_names = artist_names(right) or [right]
    scores = [
        fuzz.token_sort_ratio(normalize(left_name), normalize(right_name)) / 100
        for left_name in left_names
        for right_name in right_names
        if left_name and right_name
    ]
    if not scores:
        return 0.0
    return max(scores)


def match_score(expected_title, expected_artist, found_title, found_artist):
    title = title_similarity(expected_title, found_title)
    if not expected_artist:
        return title

    artist = artist_similarity(expected_artist, found_artist)

    return title * 0.7 + artist * 0.3


def artist_score(left, right):
    return artist_similarity(left, right)


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


def musicbrainz_result_from_cache(raw: dict[str, str]) -> MusicBrainzResult:
    field_names = {field.name for field in fields(MusicBrainzResult)}
    return MusicBrainzResult(**{
        key: value
        for key, value in raw.items()
        if key in field_names
    })


def versioned_cache_key(version: str, title: str, artist: str) -> str:
    return f"{version}::{normalize(title)}::{normalize(artist)}"


def cached_musicbrainz_result(
    title: str,
    artist: str,
    cache: dict[str, dict[str, str]],
) -> MusicBrainzResult | None:
    key = cache_key(title, artist)
    if key in cache:
        return musicbrainz_result_from_cache(cache[key]) if cache[key] else None

    for version in LEGACY_POSITIVE_CACHE_VERSIONS:
        legacy_key = versioned_cache_key(version, title, artist)
        legacy_value = cache.get(legacy_key)
        if legacy_value:
            cache[key] = legacy_value
            return musicbrainz_result_from_cache(legacy_value)

    return None


def cache_search_aliases(
    title: str,
    artist: str,
    result: MusicBrainzResult,
    cache: dict[str, dict[str, str]],
) -> None:
    payload = result.__dict__
    for alias_title, alias_artist in search_pairs(title, artist):
        key = cache_key(alias_title, alias_artist)
        if key not in cache:
            cache[key] = payload


def unique_search_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for title, artist in pairs:
        title = clean_lookup_value(title)
        artist = compact_artist_list(artist)
        if not title:
            continue
        key = (normalize(title), normalize(artist))
        if key in seen:
            continue
        seen.add(key)
        unique.append((title, artist))
    return unique


def search_pairs(title: str, artist: str) -> list[tuple[str, str]]:
    if not artist:
        return unique_search_pairs([(title, "")])

    primary = primary_artist(artist)
    pairs = [
        (title, primary),
        (title, artist),
    ]

    if title and artist:
        pairs.append((artist, title))

    return unique_search_pairs(pairs)


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


def track_info(release: dict, recording_id: str) -> tuple[str, str, str]:
    for medium in release.get("media") or []:
        tracks = medium.get("tracks") or []

        total_tracks = str(len(tracks))
        disc = str(medium.get("position") or "1")

        for track in tracks:
            if (track.get("recording") or {}).get("id") == recording_id:
                track_number = str(
                    track.get("number") or
                    track.get("position") or
                    ""
                )

                return track_number, disc, total_tracks

    return "", "", ""


def result_from_recording(recording: dict, expected_artist: str) -> MusicBrainzResult:
    recording_id = recording.get("id") or ""
    releases = sorted(recording.get("releases") or [], key=lambda release: release_priority(release, expected_artist))
    release = releases[0] if releases else {}
    release_group = release.get("release-group") or {}
    release_date = release.get("date") or ""

    genre = ""
    genres = recording.get("genres") or []

    if genres:
        genre = genres[0].get("name", "")

    isrc = ""
    isrcs = recording.get("isrcs") or []

    if isrcs:
        isrc = isrcs[0]

    track, disc, total_tracks = track_info(release, recording_id)

    return MusicBrainzResult(
        title=recording.get("title") or "",
        artist=artist_credit(recording),
        genre=genre,
        isrc=isrc,
        album=release_group.get("title") or release.get("title") or "",
        album_artist=release_artist_credit(release),

        date=release_date,

        track=track,
        disc=disc,
        total_tracks=total_tracks,

        recording_id=recording_id,
        release_id=release.get("id") or "",
    )


def migrate_legacy_cover_cache() -> None:
    if not LEGACY_COVER_CACHE.exists():
        return

    COVER_DIR.mkdir(exist_ok=True)
    for cached_cover in LEGACY_COVER_CACHE.glob("*.jpg"):
        destination = COVER_DIR / cached_cover.name
        if not destination.exists():
            try:
                shutil.copy2(cached_cover, destination)
            except OSError:
                continue


def download_cover(release_id: str) -> Path | None:
    if not release_id:
        return None

    COVER_DIR.mkdir(exist_ok=True)
    cover = COVER_DIR / f"{release_id}.jpg"

    if cover.exists():
        return cover

    legacy_cover = LEGACY_COVER_CACHE / cover.name
    if legacy_cover.exists():
        try:
            shutil.copy2(legacy_cover, cover)
            return cover
        except OSError:
            return None

    try:
        response = SESSION.get(
            COVER_ART_URL.format(release_id),
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )

        if response.status_code != 200:
            return None

        cover.write_bytes(response.content)
        return cover

    except (OSError, requests.RequestException):
        return None


def musicbrainz_get(params: dict[str, str]) -> requests.Response:
    global LAST_MUSICBRAINZ_REQUEST

    elapsed = time.monotonic() - LAST_MUSICBRAINZ_REQUEST
    wait_time = MUSICBRAINZ_MIN_REQUEST_INTERVAL - elapsed
    if wait_time > 0:
        time.sleep(wait_time)

    response = SESSION.get(
        MUSICBRAINZ_RECORDING_URL,
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    LAST_MUSICBRAINZ_REQUEST = time.monotonic()
    return response


def search_musicbrainz(
    title: str,
    artist: str,
    cache: dict[str, dict[str, str]],
) -> MusicBrainzResult | None:
    cached = cached_musicbrainz_result(title, artist, cache)
    if cached:
        return cached

    key = cache_key(title, artist)
    if key in cache:
        return None

    for attempt in range(5):
        try:
            query = f'recording:"{title}"'
            if artist:
                query += f' AND artist:"{artist}"'

            response = musicbrainz_get(
                {
                    "query": query,
                    "fmt": "json",
                    "limit": 10,
                    "inc": "artist-credits+releases+media+isrcs+genres+release-groups",
                }
            )

            response.raise_for_status()
            break

        except requests.RequestException as exc:
            if attempt == 4:
                raise MusicBrainzUnavailable(f"MusicBrainz cannot be reached: {exc}") from exc
            time.sleep(2 ** attempt)

    best: MusicBrainzResult | None = None
    best_score = 0.0
    min_score = MIN_MATCH_SCORE if artist else TITLE_ONLY_MIN_MATCH_SCORE
    for recording in response.json().get("recordings") or []:
        candidate = result_from_recording(recording, artist)
        score = match_score(title, artist, candidate.title, candidate.artist)
        if score >= min_score and score > best_score:
            best = candidate
            best_score = score

    cache[key] = best.__dict__ if best else {}
    return best


def search_musicbrainz_with_retry(
    title: str,
    artist: str,
    cache: dict[str, dict[str, str]],
    ) -> MusicBrainzResult | None:
    for search_title, search_artist in search_pairs(title, artist):
        match = search_musicbrainz(search_title, search_artist, cache)
        if match:
            cache_search_aliases(title, artist, match, cache)
            return match
    return None


def enrich(track: TrackMetadata, cache: dict[str, dict[str, str]]) -> TrackMetadata:
    try:
        match = search_musicbrainz_with_retry(track.title, track.artist, cache)
    except MusicBrainzUnavailable:
        if track.path.name in MANUAL_TRACK_OVERRIDES:
            return track
        raise

    if not match:
        if track.path.name in MANUAL_TRACK_OVERRIDES:
            return track
        raise MusicBrainzNoMatch("no confident MusicBrainz match found after title/artist and artist/title attempts")

    return replace(
        track,

        title=match.title or track.title,
        artist=match.artist or track.artist,

        album=track.album or match.album,
        album_artist=match.album_artist or match.artist,

        date=track.date or match.date,

        track=track.track or match.track,
        total_tracks=match.total_tracks,

        disc=match.disc,

        genre=match.genre,

        isrc=match.isrc,

        recording_id=match.recording_id,
        release_id=match.release_id,
    )


def ffmpeg_metadata_args(track: TrackMetadata) -> list[str]:
    tags = {
        "title": track.title,
        "artist": track.artist,

        "album": track.album,
        "album_artist": track.album_artist,

        "track": track.track,
        "totaltracks": track.total_tracks,

        "disc": track.disc,

        "genre": track.genre,

        "date": track.date,

        "isrc": track.isrc,

        "musicbrainz_trackid": track.recording_id,
        "musicbrainz_albumid": track.release_id,
        "musicmeta_version": CACHE_VERSION,
    }

    args: list[str] = []
    for key, value in tags.items():
        if value:
            args.extend(["-metadata", f"{key}={value}"])
    return args


def is_mp4_audio(path: Path) -> bool:
    return path.suffix.lower() in MP4_EXTENSIONS


def numeric_tag_part(value: str) -> int:
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else 0


def cover_image_format(path: Path) -> int:
    with path.open("rb") as handle:
        header = handle.read(8)
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return MP4Cover.FORMAT_PNG
    return MP4Cover.FORMAT_JPEG


def set_mp4_text_tag(tags: dict, atom: str, value: str) -> None:
    if value:
        tags[atom] = [value]
    else:
        tags.pop(atom, None)


def set_mp4_freeform_tag(tags: dict, name: str, value: str) -> None:
    atom = f"----:com.apple.iTunes:{name}"
    if value:
        tags[atom] = [value.encode("utf-8")]
    else:
        tags.pop(atom, None)


def write_mp4_itunes_tags(path: Path, track: TrackMetadata, cover: Path | None) -> None:
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()

    tags = audio.tags
    set_mp4_text_tag(tags, "\xa9nam", track.title)
    set_mp4_text_tag(tags, "\xa9ART", track.artist)
    set_mp4_text_tag(tags, "\xa9alb", track.album)
    set_mp4_text_tag(tags, "aART", track.album_artist or track.artist)
    set_mp4_text_tag(tags, "\xa9day", track.date)
    set_mp4_text_tag(tags, "\xa9gen", track.genre)

    track_number = numeric_tag_part(track.track)
    total_tracks = numeric_tag_part(track.total_tracks)
    if track_number:
        tags["trkn"] = [(track_number, total_tracks)]
    else:
        tags.pop("trkn", None)

    disc_number = numeric_tag_part(track.disc)
    if disc_number:
        tags["disk"] = [(disc_number, 0)]
    else:
        tags.pop("disk", None)

    if cover:
        tags["covr"] = [MP4Cover(cover.read_bytes(), imageformat=cover_image_format(cover))]
    else:
        tags.pop("covr", None)

    set_mp4_freeform_tag(tags, "ISRC", track.isrc)
    set_mp4_freeform_tag(tags, "MusicBrainz Track Id", track.recording_id)
    set_mp4_freeform_tag(tags, "MusicBrainz Album Id", track.release_id)
    set_mp4_freeform_tag(tags, "musicmeta_version", CACHE_VERSION)
    audio.save()


def write_metadata(track: TrackMetadata, include_cover: bool = True) -> None:
    temp_path = track.path.with_name(f"{track.path.stem}.tagging{track.path.suffix}")
    if temp_path.exists():
        temp_path.unlink()

    cover = download_cover(track.release_id) if include_cover else None
    mp4_audio = is_mp4_audio(track.path)

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(track.path),
    ]

    if cover and not mp4_audio:
        command += [
            "-i",
            str(cover),
        ]

    command += [
        "-map",
        "0:a",
    ]

    if cover and not mp4_audio:
        command += [
            "-map",
            "1",
            "-c:v",
            "mjpeg",
            "-disposition:v",
            "attached_pic",
        ]

    command += [
        "-c:a",
        "copy",
        "-map_metadata",
        "-1",
        str(temp_path),
    ]

    if not mp4_audio:
        command[-1:-1] = ffmpeg_metadata_args(track)

    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode:
        if temp_path.exists():
            temp_path.unlink()
        raise RuntimeError(f"ffmpeg failed for {track.path}: {result.stderr.strip()}")

    if mp4_audio:
        try:
            write_mp4_itunes_tags(temp_path, track, cover)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

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


def process_file(
    path: Path,
    output_dir: Path,
    cache: dict[str, dict[str, str]],
    include_cover: bool = True,
) -> None:
    track = enrich(parse_filename(path), cache)
    write_metadata(track, include_cover=include_cover)
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
    COVER_DIR.mkdir(exist_ok=True)
    migrate_legacy_cover_cache()

    paths = audio_files(SONGS_DIR)
    cache = load_cache()
    moved = 0
    failures: list[tuple[Path, str]] = []
    render_progress(0, len(paths), moved, len(failures))
    for index, path in enumerate(paths, start=1):
        try:
            process_file(path, output_dir, cache, include_cover=not args.no_cover)
            moved += 1
            if moved % 100 == 0:
                save_cache(cache)
        except Exception as exc:
            failures.append((path, str(exc)))
        render_progress(index, len(paths), moved, len(failures))

    finish_progress(len(paths), len(paths), moved, len(failures))
    save_cache(cache)
    print(f"Updated and moved {moved} audio files to {display_path(output_dir)}.")
    if failures:
        print(f"Left {len(failures)} unsuccessful audio files in {display_path(SONGS_DIR)}.")
        print_failures(failures)


if __name__ == "__main__":
    main()
