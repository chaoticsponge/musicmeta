#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import html
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urljoin

import requests
from mutagen import File
from mutagen.id3 import ID3, ID3NoHeaderError, SYLT, USLT
from mutagen.mp4 import MP4


APP_DIR = Path(__file__).resolve().parent
DEFAULT_AUDIO_DIR = APP_DIR / "meta-enriched"
DEFAULT_LYRICS_DIR = APP_DIR / "lyrics"

AUDIO_EXTENSIONS = {".opus", ".m4a", ".mp3", ".flac", ".ogg", ".aac", ".mp4"}
MP4_EXTENSIONS = {".m4a", ".mp4", ".m4b", ".mov", ".aac"}
LRCLIB_GET_URL = "https://lrclib.net/api/get"
LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
HYMNARY_BASE_URL = "https://hymnary.org"
HYMNARY_SEARCH_URL = f"{HYMNARY_BASE_URL}/search"
OPEN_HYMNAL_URL = "https://openhymnal.org/"
PICARD_PLUGINS_URL = "https://picard.musicbrainz.org/plugins/"
USER_AGENT = "musicmeta-lyrics/1.0 (local lyrics tagger)"
MIN_REQUEST_INTERVAL = 0.35
REQUEST_INTERVAL = MIN_REQUEST_INTERVAL
PROGRESS_WIDTH = 28
LRC_TIMESTAMP_PATTERN = re.compile(r"\[(\d{1,3}):(\d{2})(?:\.(\d{1,3}))?\]")
HYMNARY_TEXT_LINK_PATTERN = re.compile(
    r"<a\b[^>]*href=[\"'](?P<href>/text/[^\"'#?]+)[\"'][^>]*>(?P<label>.*?)</a>",
    flags=re.IGNORECASE | re.DOTALL,
)
HYMN_HINTS = {
    "alleluia",
    "bless",
    "christ",
    "come to us",
    "domini",
    "eleison",
    "god",
    "grace",
    "holy",
    "hymn",
    "jesus",
    "kyrie",
    "lord",
    "misericordias",
    "psalm",
    "spirit",
    "taize",
}

THREAD_LOCAL = threading.local()
REQUEST_LOCK = threading.Lock()
LAST_REQUEST = 0.0


@dataclass(frozen=True)
class Track:
    path: Path
    title: str
    artist: str
    album: str = ""
    duration: int = 0


@dataclass(frozen=True)
class LyricsResult:
    plain: str
    synced: str = ""
    instrumental: bool = False
    source_name: str = ""


class LyricsNotFound(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessResult:
    path: Path
    status: str
    message: str
    track: Track | None = None
    reason: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch lyrics from LRCLIB for enriched audio files and embed them.",
    )
    parser.add_argument(
        "audio_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_AUDIO_DIR,
        help="Folder containing enriched audio files. Defaults to musicmeta/meta-enriched.",
    )
    parser.add_argument(
        "lyrics_dir",
        nargs="?",
        type=Path,
        help="Folder for cached .txt/.lrc lyrics. Kept for compatibility with the old two-argument form.",
    )
    parser.add_argument(
        "--lyrics-dir",
        dest="lyrics_dir_option",
        type=Path,
        default=None,
        help="Folder for cached .txt/.lrc lyrics. Defaults to musicmeta/lyrics.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore cached lyrics files and ask providers again. Also reprocesses files that already have embedded lyrics.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess files even when lyrics are already embedded or cached.",
    )
    parser.add_argument(
        "--no-embed",
        action="store_true",
        help="Only save lyrics sidecar files; do not write lyrics into audio tags.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be fetched or embedded without writing files.",
    )
    parser.add_argument(
        "--hymn-fallback",
        choices=("auto", "always", "never"),
        default="auto",
        help="Use Hymnary.org after LRCLIB misses. Defaults to auto for hymn-like tracks.",
    )
    parser.add_argument(
        "--missing-report",
        type=Path,
        default=None,
        help="Write unresolved tracks and fallback links here. Defaults to lyrics/missing_lyrics.md.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Number of worker threads. Defaults to 1. Try 4 on a normal Mac.",
    )
    parser.add_argument(
        "--request-interval",
        type=float,
        default=MIN_REQUEST_INTERVAL,
        help="Minimum seconds between starting provider requests. Defaults to 0.35.",
    )
    return parser.parse_args()


def audio_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS)


def http_session() -> requests.Session:
    session = getattr(THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        THREAD_LOCAL.session = session
    return session


def first_value(values: object) -> str:
    if not values:
        return ""
    if isinstance(values, list | tuple):
        return str(values[0]) if values else ""
    return str(values)


def first_mp4_text(tags: dict, atom: str) -> str:
    return first_value(tags.get(atom)).strip()


def first_generic_text(tags: object, key: str) -> str:
    try:
        return first_value(tags.get(key, [""])).strip()
    except AttributeError:
        return ""


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize(value: str) -> str:
    value = value.lower().replace("&", "and")
    value = re.sub(r"[\W_]+", " ", value)
    return " ".join(value.split())


def rough_similarity(left: str, right: str) -> float:
    left_norm = normalize(left)
    right_norm = normalize(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return 0.85

    left_words = set(left_norm.split())
    right_words = set(right_norm.split())
    if not left_words or not right_words:
        return 0.0
    return len(left_words & right_words) / len(left_words | right_words)


def read_track(path: Path) -> Track:
    audio = File(path, easy=True)
    if audio is None:
        raise ValueError("unsupported or unreadable audio file")

    title = clean_text(first_generic_text(audio, "title"))
    artist = clean_text(first_generic_text(audio, "artist"))
    album = clean_text(first_generic_text(audio, "album"))

    if path.suffix.lower() in MP4_EXTENSIONS:
        mp4 = MP4(path)
        tags = mp4.tags or {}
        title = title or first_mp4_text(tags, "\xa9nam")
        artist = artist or first_mp4_text(tags, "\xa9ART")
        album = album or first_mp4_text(tags, "\xa9alb")

    full_audio = File(path)
    duration = round(getattr(getattr(full_audio, "info", None), "length", 0) or 0)

    if not title or not artist:
        raise ValueError("missing title or artist metadata")

    return Track(path=path, title=title, artist=artist, album=album, duration=duration)


def safe_filename(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:180].rstrip(". ")


def lyrics_stem(track: Track) -> str:
    return safe_filename(f"{track.artist} - {track.title}")


def track_search_text(track: Track) -> str:
    return " ".join(part for part in (track.title, track.artist, track.album) if part)


def looks_hymn_like(track: Track) -> bool:
    normalized = normalize(track_search_text(track))
    return any(hint in normalized for hint in HYMN_HINTS)


def strip_lrc_timestamps(value: str) -> str:
    lines: list[str] = []
    for line in value.splitlines():
        line = LRC_TIMESTAMP_PATTERN.sub("", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def lrc_timestamp_ms(match: re.Match[str]) -> int:
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    fraction = (match.group(3) or "0").ljust(3, "0")[:3]
    return ((minutes * 60) + seconds) * 1000 + int(fraction)


def parse_lrc_for_sylt(value: str) -> list[tuple[str, int]]:
    entries: list[tuple[str, int]] = []
    for line in value.splitlines():
        timestamps = list(LRC_TIMESTAMP_PATTERN.finditer(line))
        if not timestamps:
            continue

        text = LRC_TIMESTAMP_PATTERN.sub("", line).strip()
        if not text:
            continue

        for timestamp in timestamps:
            entries.append((text, lrc_timestamp_ms(timestamp)))

    entries.sort(key=lambda item: item[1])
    return entries


def cached_lyrics(track: Track, lyrics_dir: Path) -> LyricsResult | None:
    stem = lyrics_stem(track)
    plain_path = lyrics_dir / f"{stem}.txt"
    synced_path = lyrics_dir / f"{stem}.lrc"

    plain = plain_path.read_text(encoding="utf-8").strip() if plain_path.exists() else ""
    synced = synced_path.read_text(encoding="utf-8").strip() if synced_path.exists() else ""

    if not plain and synced:
        plain = strip_lrc_timestamps(synced)

    if not plain:
        return None

    return LyricsResult(plain=plain, synced=synced, source_name=plain_path.name)


def has_text(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, bytes):
        return bool(value.strip(b"\x00 \t\r\n"))
    if isinstance(value, list | tuple):
        return any(has_text(item) for item in value)
    return bool(str(value).strip())


def has_mp3_lyrics(path: Path) -> bool:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return False

    for frame in tags.getall("USLT"):
        if has_text(getattr(frame, "text", "")):
            return True
    for frame in tags.getall("SYLT"):
        if has_text(getattr(frame, "text", "")):
            return True
    return False


def has_mp4_lyrics(path: Path) -> bool:
    tags = MP4(path).tags or {}
    return has_text(tags.get("\xa9lyr"))


def has_generic_lyrics(path: Path) -> bool:
    audio = File(path)
    tags = getattr(audio, "tags", None)
    if not tags:
        return False

    for key, value in tags.items():
        normalized_key = str(key).lower()
        if normalized_key in {"lyrics", "unsyncedlyrics", "unsynchronised lyrics"} and has_text(value):
            return True
    return False


def has_embedded_lyrics(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        return has_mp3_lyrics(path)
    if suffix in MP4_EXTENSIONS:
        return has_mp4_lyrics(path)
    return has_generic_lyrics(path)


def already_done(track: Track, lyrics_dir: Path, embed: bool) -> str:
    if embed and has_embedded_lyrics(track.path):
        return "embedded lyrics already present"
    if not embed and cached_lyrics(track, lyrics_dir):
        return "cached lyrics already present"
    return ""


def write_cached_lyrics(track: Track, lyrics: LyricsResult, lyrics_dir: Path) -> None:
    lyrics_dir.mkdir(parents=True, exist_ok=True)
    stem = lyrics_stem(track)

    plain_path = lyrics_dir / f"{stem}.txt"
    plain_path.write_text(lyrics.plain.strip() + "\n", encoding="utf-8")

    if lyrics.synced:
        synced_path = lyrics_dir / f"{stem}.lrc"
        synced_path.write_text(lyrics.synced.strip() + "\n", encoding="utf-8")


def lrclib_get(url: str, params: dict[str, str | int]) -> requests.Response:
    global LAST_REQUEST

    with REQUEST_LOCK:
        elapsed = time.monotonic() - LAST_REQUEST
        wait_time = REQUEST_INTERVAL - elapsed
        if wait_time > 0:
            time.sleep(wait_time)

        LAST_REQUEST = time.monotonic()

    return http_session().get(url, params=params, timeout=15)


def lyrics_from_payload(payload: dict, track: Track) -> LyricsResult:
    plain = (payload.get("plainLyrics") or "").strip()
    synced = (payload.get("syncedLyrics") or "").strip()
    instrumental = bool(payload.get("instrumental"))

    if instrumental:
        raise LyricsNotFound("LRCLIB match is marked instrumental")

    if not plain and synced:
        plain = strip_lrc_timestamps(synced)

    if not plain:
        raise LyricsNotFound("LRCLIB match has no plain lyrics")

    return LyricsResult(
        plain=plain,
        synced=synced,
        instrumental=instrumental,
        source_name="LRCLIB",
    )


def payload_score(track: Track, payload: dict) -> float:
    title_score = rough_similarity(track.title, payload.get("trackName") or payload.get("name") or "")
    artist_score = rough_similarity(track.artist, payload.get("artistName") or "")

    duration_score = 0.0
    found_duration = payload.get("duration")
    if track.duration and isinstance(found_duration, int | float):
        duration_delta = abs(track.duration - int(found_duration))
        duration_score = max(0.0, 1.0 - (duration_delta / 12))

    return (title_score * 0.55) + (artist_score * 0.30) + (duration_score * 0.15)


def search_lyrics(track: Track) -> LyricsResult:
    params: dict[str, str | int] = {
        "track_name": track.title,
        "artist_name": track.artist,
    }
    if track.album:
        params["album_name"] = track.album

    response = lrclib_get(LRCLIB_SEARCH_URL, params)
    response.raise_for_status()

    payloads = response.json()
    if not isinstance(payloads, list) or not payloads:
        raise LyricsNotFound("no LRCLIB search match")

    ranked = sorted(payloads, key=lambda payload: payload_score(track, payload), reverse=True)
    for payload in ranked:
        if payload_score(track, payload) < 0.65:
            break
        try:
            return lyrics_from_payload(payload, track)
        except LyricsNotFound:
            continue

    raise LyricsNotFound("no confident LRCLIB search match")


def text_from_html(value: str) -> str:
    value = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|h[1-6]|tr)>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    return "\n".join(line.strip() for line in value.splitlines())


def candidate_hymnary_urls(track: Track) -> list[str]:
    slugs = [
        re.sub(r"[^a-z0-9]+", "_", normalize(track.title)).strip("_"),
        re.sub(r"[^a-z0-9]+", "_", normalize(f"{track.title} {track.artist}")).strip("_"),
    ]
    candidates = [
        urljoin(HYMNARY_BASE_URL, f"/text/{slug}")
        for slug in dict.fromkeys(slug for slug in slugs if slug)
    ]

    params = {"qu": f"{track.title} in:texts"}
    response = lrclib_get(HYMNARY_SEARCH_URL, params)
    if response.status_code == 403:
        return candidates
    response.raise_for_status()

    search_candidates: list[tuple[float, str]] = []
    seen: set[str] = set()
    for match in HYMNARY_TEXT_LINK_PATTERN.finditer(response.text):
        href = match.group("href")
        if href in seen:
            continue
        seen.add(href)

        label = text_from_html(match.group("label"))
        score = rough_similarity(track.title, label)
        if score >= 0.45:
            search_candidates.append((score, urljoin(HYMNARY_BASE_URL, href)))

    search_candidates.sort(reverse=True, key=lambda item: item[0])
    candidates.extend(url for _score, url in search_candidates[:5])
    return list(dict.fromkeys(candidates))


def extract_hymnary_representative_text(page_html: str) -> str:
    page_text = text_from_html(page_html)
    if not re.search(r"Copyright:\s*Public Domain\b", page_text, flags=re.IGNORECASE):
        raise LyricsNotFound("Hymnary match is not marked public domain")

    match = re.search(
        r"Representative Text\s*(?P<body>.+?)(?:\n\s*(?:All representative texts|Author:|Text Information|Tune:|Source:)\b)",
        page_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise LyricsNotFound("Hymnary match has no representative text")

    lines = []
    for line in match.group("body").splitlines():
        line = line.strip()
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        if re.search(r"\b(?:text size|regular|large|\^ top)\b", line, flags=re.IGNORECASE):
            continue
        if re.match(r"^[A-Z][^,]{2,60},\s*(?:1[6-9]\d{2}|20\d{2})$", line):
            continue
        lines.append(line)

    lyrics = "\n".join(lines).strip()
    if len(lyrics.split()) < 12:
        raise LyricsNotFound("Hymnary representative text is too short")
    return lyrics


def fetch_hymnary_lyrics(track: Track) -> LyricsResult:
    last_reason = "no Hymnary text candidates"
    for url in candidate_hymnary_urls(track):
        try:
            response = lrclib_get(url, {})
            response.raise_for_status()
            plain = extract_hymnary_representative_text(response.text)
            return LyricsResult(plain=plain, source_name="Hymnary.org")
        except (LyricsNotFound, requests.RequestException) as exc:
            last_reason = str(exc)
            continue

    raise LyricsNotFound(last_reason)


def should_try_hymnary(track: Track, hymn_fallback: str) -> bool:
    if hymn_fallback == "always":
        return True
    if hymn_fallback == "never":
        return False
    return looks_hymn_like(track)


def fetch_lyrics(track: Track, hymn_fallback: str) -> LyricsResult:
    params: dict[str, str | int] = {
        "track_name": track.title,
        "artist_name": track.artist,
    }
    if track.album:
        params["album_name"] = track.album
    if track.duration:
        params["duration"] = track.duration

    lrclib_reason = "no LRCLIB match"
    try:
        response = lrclib_get(LRCLIB_GET_URL, params)
        if response.status_code == 404:
            raise LyricsNotFound("no LRCLIB exact match")
        response.raise_for_status()
        return lyrics_from_payload(response.json(), track)
    except LyricsNotFound as exc:
        lrclib_reason = str(exc)

    try:
        return search_lyrics(track)
    except LyricsNotFound as exc:
        lrclib_reason = str(exc)

    if should_try_hymnary(track, hymn_fallback):
        try:
            return fetch_hymnary_lyrics(track)
        except (LyricsNotFound, requests.RequestException) as exc:
            raise LyricsNotFound(f"{lrclib_reason}; Hymnary.org fallback failed: {exc}") from exc

    raise LyricsNotFound(lrclib_reason)


def write_mp3_lyrics(path: Path, lyrics: LyricsResult) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    tags.delall("USLT")
    tags.delall("SYLT")
    tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics.plain))

    synced_entries = parse_lrc_for_sylt(lyrics.synced)
    if synced_entries:
        tags.add(SYLT(
            encoding=3,
            lang="eng",
            format=2,
            type=1,
            desc="",
            text=synced_entries,
        ))

    tags.save(path)


def write_mp4_lyrics(path: Path, lyrics: LyricsResult) -> None:
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    audio.tags["\xa9lyr"] = [lyrics.plain]
    audio.save()


def write_generic_lyrics(path: Path, lyrics: LyricsResult) -> None:
    audio = File(path, easy=True)
    if audio is None:
        raise ValueError("unsupported or unreadable audio file")
    if audio.tags is None:
        audio.add_tags()
    audio["lyrics"] = [lyrics.plain]
    audio.save()


def embed_lyrics(track: Track, lyrics: LyricsResult) -> None:
    suffix = track.path.suffix.lower()
    if suffix == ".mp3":
        write_mp3_lyrics(track.path, lyrics)
    elif suffix in MP4_EXTENSIONS:
        write_mp4_lyrics(track.path, lyrics)
    else:
        write_generic_lyrics(track.path, lyrics)


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(APP_DIR))
    except ValueError:
        return str(path)


def progress_line(done: int, total: int, success: int, skipped: int, failed: int) -> str:
    if total == 0:
        percent = 100
        filled = PROGRESS_WIDTH
    else:
        percent = round((done / total) * 100)
        filled = round((done / total) * PROGRESS_WIDTH)

    bar = "#" * filled + "-" * (PROGRESS_WIDTH - filled)
    return f"[{bar}] {done}/{total} {percent:3d}%  success:{success} skipped:{skipped} failed:{failed}"


def render_progress(done: int, total: int, success: int, skipped: int, failed: int) -> None:
    if not sys.stdout.isatty():
        return
    print("\r" + progress_line(done, total, success, skipped, failed), end="", flush=True)


def finish_progress(done: int, total: int, success: int, skipped: int, failed: int) -> None:
    if sys.stdout.isatty():
        print("\r" + progress_line(done, total, success, skipped, failed))


def process_file(
    path: Path,
    lyrics_dir: Path,
    refresh: bool,
    force: bool,
    embed: bool,
    dry_run: bool,
    hymn_fallback: str,
) -> ProcessResult:
    track = read_track(path)

    skip_reason = "" if force or refresh else already_done(track, lyrics_dir, embed)
    if skip_reason:
        return ProcessResult(
            path=path,
            status="skipped",
            message=f"skipped: {path.name} ({skip_reason})",
            track=track,
            reason=skip_reason,
        )

    lyrics = None if refresh else cached_lyrics(track, lyrics_dir)
    source = "cache"

    if not lyrics:
        lyrics = fetch_lyrics(track, hymn_fallback)
        source = lyrics.source_name
        if not dry_run:
            write_cached_lyrics(track, lyrics, lyrics_dir)

    if embed and not dry_run:
        embed_lyrics(track, lyrics)

    action = "would update" if dry_run else "updated"
    sidecar = f", saved {lyrics_stem(track)}.txt" if source != "cache" else ""
    return ProcessResult(
        path=path,
        status="success",
        message=f"{action}: {path.name} ({source}{sidecar})",
        track=track,
    )


def process_path(
    path: Path,
    lyrics_dir: Path,
    refresh: bool,
    force: bool,
    embed: bool,
    dry_run: bool,
    hymn_fallback: str,
) -> ProcessResult:
    try:
        return process_file(
            path=path,
            lyrics_dir=lyrics_dir,
            refresh=refresh,
            force=force,
            embed=embed,
            dry_run=dry_run,
            hymn_fallback=hymn_fallback,
        )
    except LyricsNotFound as exc:
        try:
            track = read_track(path)
        except (OSError, ValueError):
            track = None
        return ProcessResult(
            path=path,
            status="failed",
            message=f"No lyrics: {path.name}: {exc}",
            track=track,
            reason=str(exc),
        )
    except (OSError, ValueError, requests.RequestException) as exc:
        return ProcessResult(
            path=path,
            status="failed",
            message=f"Failed: {path.name}: {exc}",
            reason=str(exc),
        )


def fallback_links(track: Track) -> list[tuple[str, str]]:
    lrclib_query = urlencode({"track_name": track.title, "artist_name": track.artist})
    hymnary_query = urlencode({"qu": f"{track.title} in:texts"})
    return [
        ("LRCLIB API search", f"{LRCLIB_SEARCH_URL}?{lrclib_query}"),
        ("Hymnary.org text search", f"{HYMNARY_SEARCH_URL}?{hymnary_query}"),
        ("Open Hymnal Project", OPEN_HYMNAL_URL),
        ("MusicBrainz Picard plugins", PICARD_PLUGINS_URL),
    ]


def write_missing_report(rows: list[tuple[Track | None, Path, str]], report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Missing lyrics",
        "",
        "Provider order used by add_lyrics.py:",
        "1. Local lyrics cache",
        "2. LRCLIB exact metadata lookup",
        "3. LRCLIB search fallback",
        "4. Hymnary.org public-domain text fallback for hymn-like tracks",
        "5. Report-only links for Open Hymnal Project and MusicBrainz Picard plugins",
        "",
    ]

    for track, path, reason in rows:
        lines.append(f"## {path.name}")
        lines.append(f"- Reason: {reason}")
        if track:
            lines.append(f"- Metadata: {track.title} - {track.artist}")
            for label, url in fallback_links(track):
                lines.append(f"- {label}: {url}")
        lines.append("")

    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    global REQUEST_INTERVAL

    args = parse_args()
    REQUEST_INTERVAL = max(0.0, args.request_interval)
    audio_dir = args.audio_dir.expanduser()
    lyrics_dir = (args.lyrics_dir_option or args.lyrics_dir or DEFAULT_LYRICS_DIR).expanduser()
    missing_report = (args.missing_report or (lyrics_dir / "missing_lyrics.md")).expanduser()

    if not audio_dir.is_dir():
        print(f"Error: audio dir not found: {audio_dir}", file=sys.stderr)
        raise SystemExit(1)

    paths = audio_files(audio_dir)
    if not paths:
        print(f"No audio files found in {display_path(audio_dir)}.")
        return

    if not args.dry_run:
        lyrics_dir.mkdir(parents=True, exist_ok=True)

    jobs = max(1, args.jobs)
    success = 0
    skipped = 0
    failures: list[ProcessResult] = []

    def handle_result(index: int, result: ProcessResult) -> None:
        nonlocal success, skipped
        if result.status == "success":
            success += 1
        elif result.status == "skipped":
            skipped += 1
        else:
            failures.append(result)

        if not sys.stdout.isatty():
            stream = sys.stderr if result.message.startswith("Failed:") else sys.stdout
            print(result.message, file=stream)
        render_progress(index, len(paths), success, skipped, len(failures))

    render_progress(0, len(paths), success, skipped, len(failures))
    if jobs == 1:
        for index, path in enumerate(paths, start=1):
            result = process_path(
                path=path,
                lyrics_dir=lyrics_dir,
                refresh=args.refresh,
                force=args.force,
                embed=not args.no_embed,
                dry_run=args.dry_run,
                hymn_fallback=args.hymn_fallback,
            )
            handle_result(index, result)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = [
                executor.submit(
                    process_path,
                    path,
                    lyrics_dir,
                    args.refresh,
                    args.force,
                    not args.no_embed,
                    args.dry_run,
                    args.hymn_fallback,
                )
                for path in paths
            ]
            for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                handle_result(index, future.result())

    finish_progress(len(paths), len(paths), success, skipped, len(failures))
    print(f"Lyrics complete: {success} success, {len(failures)} fail, {skipped} skipped.")
    if failures and not args.dry_run:
        write_missing_report(
            [(result.track, result.path, result.reason) for result in failures],
            missing_report,
        )
        print(f"Wrote missing lyrics report: {display_path(missing_report)}")


if __name__ == "__main__":
    main()
