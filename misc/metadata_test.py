#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
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
    "album_artist",
    "date",
    "track",
    "totaltracks",
    "disc",
    "genre",
    "isrc",
    "musicbrainz_trackid",
    "musicbrainz_albumid",
    "musicmeta_version",
)
CURRENT_METADATA_VERSION = "v4-normalized-match"
CORE_REQUIRED_FIELDS = ("title", "artist")
STRICT_V4_REQUIRED_FIELDS = (
    "title",
    "artist",
    "album",
    "album_artist",
    "musicbrainz_trackid",
    "musicbrainz_albumid",
    "musicmeta_version",
)
V4_RECOMMENDED_FIELDS = ("date", "track", "totaltracks", "disc", "genre", "isrc")


@dataclass(frozen=True)
class MetadataRow:
    file: str
    location: str
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    date: str = ""
    track: str = ""
    totaltracks: str = ""
    disc: str = ""
    genre: str = ""
    isrc: str = ""
    musicbrainz_trackid: str = ""
    musicbrainz_albumid: str = ""
    musicmeta_version: str = ""
    error: str = ""


@dataclass(frozen=True)
class VerificationIssue:
    file: str
    severity: str
    field: str
    message: str
    value: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show metadata before/after comparison for musicmeta.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify every file in meta-enriched has required metadata.",
    )
    parser.add_argument(
        "--strict-v4",
        action="store_true",
        help="With --verify, require current v4 metadata version and MusicBrainz-grade tags.",
    )
    parser.add_argument(
        "--warnings-as-errors",
        action="store_true",
        help="With --verify, return a failing exit code for warning-level missing recommended fields.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="With --verify, print at most this many issue rows. Defaults to 100.",
    )
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


def verify_row(row: MetadataRow, strict_v4: bool) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []

    if row.error:
        return [
            VerificationIssue(
                file=row.file,
                severity="error",
                field="file",
                message=row.error,
            )
        ]

    required_fields = STRICT_V4_REQUIRED_FIELDS if strict_v4 else CORE_REQUIRED_FIELDS
    for field in required_fields:
        value = getattr(row, field)
        if not value:
            issues.append(
                VerificationIssue(
                    file=row.file,
                    severity="error",
                    field=field,
                    message=f"missing required {field}",
                )
            )

    if strict_v4 and row.musicmeta_version and row.musicmeta_version != CURRENT_METADATA_VERSION:
        issues.append(
            VerificationIssue(
                file=row.file,
                severity="error",
                field="musicmeta_version",
                message=f"expected {CURRENT_METADATA_VERSION}",
                value=row.musicmeta_version,
            )
        )

    if strict_v4:
        for field in V4_RECOMMENDED_FIELDS:
            value = getattr(row, field)
            if not value:
                issues.append(
                    VerificationIssue(
                        file=row.file,
                        severity="warning",
                        field=field,
                        message=f"missing recommended {field}",
                    )
                )

    return issues


def verify_enriched(strict_v4: bool) -> tuple[list[MetadataRow], list[VerificationIssue]]:
    rows = [read_metadata(path, "meta-enriched") for path in audio_files(ENRICHED_DIR)]
    issues: list[VerificationIssue] = []
    for row in rows:
        issues.extend(verify_row(row, strict_v4))
    return rows, issues


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


def print_verification(rows: list[MetadataRow], issues: list[VerificationIssue], limit: int) -> None:
    errors = [issue for issue in issues if issue.severity == "error"]
    warnings = [issue for issue in issues if issue.severity == "warning"]
    print(f"Verified {len(rows)} files in meta-enriched.")
    print(f"errors: {len(errors)}  warnings: {len(warnings)}")

    if not issues:
        print("All checked metadata fields are present.")
        return

    counts = Counter((issue.severity, issue.field) for issue in issues)
    print()
    print("Issue summary:")
    for (severity, field), count in sorted(counts.items()):
        print(f"  {severity:7} {field:20} {count}")

    shown = issues[:max(limit, 0)]
    if shown:
        print()
        print(f"First {len(shown)} issues:")
    for issue in shown:
        detail = f" ({issue.value})" if issue.value else ""
        print(f"{issue.severity}: {issue.file}: {issue.message}{detail}")

    remaining = len(issues) - len(shown)
    if remaining > 0:
        print(f"... {remaining} more issues not shown. Use --json for full details or --limit to change this.")


def main() -> None:
    args = parse_args()

    if args.verify:
        rows, issues = verify_enriched(args.strict_v4)
        if args.json:
            print(json.dumps(
                {
                    "files_checked": len(rows),
                    "issues": [asdict(issue) for issue in issues],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ))
        else:
            print_verification(rows, issues, args.limit)

        has_errors = any(issue.severity == "error" for issue in issues)
        has_warnings = any(issue.severity == "warning" for issue in issues)
        raise SystemExit(1 if has_errors or (args.warnings_as_errors and has_warnings) else 0)

    rows = collect_metadata()

    if args.json:
        print_json(rows)
    else:
        print_table(rows)


if __name__ == "__main__":
    main()
