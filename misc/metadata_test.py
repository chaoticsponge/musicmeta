#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent.parent
SONGS_DIR = APP_DIR / "songs"
ENRICHED_DIR = APP_DIR / "meta-enriched"
AUDIO_EXTENSIONS = {".opus", ".m4a", ".mp3", ".flac", ".ogg"}

TAG_FIELDS = (
    "title",
    "artist",
    "album",
    "date",
    "track",
    "musicbrainz_trackid",
    "musicbrainz_albumid",
)


@dataclass(frozen=True)
class MetadataRow:
    file: str
    location: str
    title: str = ""
    artist: str = ""
    album: str = ""
    date: str = ""
    track: str = ""
    musicbrainz_trackid: str = ""
    musicbrainz_albumid: str = ""
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show metadata before/after comparison for musicmeta.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def audio_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS)


def ffprobe(path: Path) -> dict:
    output = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format_tags:stream_tags",
            "-of",
            "json",
            str(path),
        ],
        text=True,
        stderr=subprocess.STDOUT,
    )
    return json.loads(output)


def flatten_tags(data: dict) -> dict[str, str]:
    tags: dict[str, str] = {}

    for stream in data.get("streams", []):
        for key, value in (stream.get("tags") or {}).items():
            tags[key.lower()] = str(value)

    for key, value in (data.get("format", {}).get("tags") or {}).items():
        tags[key.lower()] = str(value)

    return tags


def read_metadata(path: Path, location: str) -> MetadataRow:
    try:
        tags = flatten_tags(ffprobe(path))
    except subprocess.CalledProcessError as exc:
        return MetadataRow(file=path.name, location=location, error=exc.output.strip())
    except json.JSONDecodeError as exc:
        return MetadataRow(file=path.name, location=location, error=str(exc))

    values = {field: tags.get(field, "") for field in TAG_FIELDS}
    return MetadataRow(file=path.name, location=location, **values)


def collect_metadata() -> dict[str, dict[str, MetadataRow]]:
    rows: dict[str, dict[str, MetadataRow]] = {}

    for path in audio_files(SONGS_DIR):
        rows.setdefault(path.name, {})["before"] = read_metadata(path, "songs")

    for path in audio_files(ENRICHED_DIR):
        rows.setdefault(path.name, {})["after"] = read_metadata(path, "meta-enriched")

    return rows


def trim(value: str, width: int) -> str:
    value = value or "-"
    if len(value) <= width:
        return value
    return value[: width - 1] + "…"


def print_row(file_name: str, state: str, row: MetadataRow) -> None:
    has_musicbrainz_id = bool(row.musicbrainz_trackid or row.musicbrainz_albumid)
    print(
        f"{trim(file_name, 40):40}  "
        f"{state:6}  "
        f"{trim(row.title, 28):28}  "
        f"{trim(row.artist, 24):24}  "
        f"{trim(row.album, 24):24}  "
        f"{trim(row.date, 16):16}  "
        f"{trim(row.track, 5):5}  "
        f"{'yes' if has_musicbrainz_id else '-':8}"
    )

    if row.error:
        print(f"{'':40}  {'error':6}  {trim(row.error, 100)}")


def print_table(rows: dict[str, dict[str, MetadataRow]]) -> None:
    if not rows:
        print("No audio files found in songs or meta-enriched.")
        return

    header = (
        f"{'file':40}  {'state':6}  {'title':28}  {'artist':24}  "
        f"{'album':24}  {'date':16}  {'track':5}  {'mbid':8}"
    )
    print(header)
    print("-" * len(header))

    for file_name in sorted(rows):
        states = rows[file_name]
        for state in ("before", "after"):
            if state in states:
                print_row(file_name, state, states[state])


def print_json(rows: dict[str, dict[str, MetadataRow]]) -> None:
    payload = {
        file_name: {state: asdict(row) for state, row in states.items()}
        for file_name, states in rows.items()
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> None:
    args = parse_args()
    rows = collect_metadata()

    if args.json:
        print_json(rows)
    else:
        print_table(rows)


if __name__ == "__main__":
    main()
