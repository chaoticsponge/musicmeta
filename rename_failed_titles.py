#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
SONGS_DIR = APP_DIR / "songs"

EXTENSIONS = {".m4a", ".mp3", ".flac", ".ogg", ".opus", ".mp4", ".m4b", ".mov"}

TITLE_SUFFIX_PATTERNS = (
    re.compile(r"^(?P<title>.+?)\s+(?:by|performed by|sung by)\s+(?P<artist>.+)$", re.IGNORECASE),
    re.compile(r"^(?P<title>.+?)\s*[-–—]\s*(?P<artist>.+)$", re.IGNORECASE),
)


def normalize_text(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[＿]", "_", value)
    value = re.sub(r"[，]", ",", value)
    value = re.sub(r"[：]", ":", value)
    value = re.sub(r"[⧸]", "/", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -–—_:：")


def clean_part(value: str) -> str:
    value = normalize_text(value)
    value = re.sub(r"(?i)\s*(?:lyrics?|official(?:\s+music)?\s+video|music video|lyric video|audio only)\b.*$", "", value)
    return normalize_text(value)


def safe_filename(value: str) -> str:
    value = normalize_text(value)
    value = re.sub(r"[\\/:*?\"<>|]", "_", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" .")


def split_title_artist(stem: str) -> tuple[str, str] | None:
    stem = clean_part(stem)
    for pattern in TITLE_SUFFIX_PATTERNS:
        match = pattern.match(stem)
        if match:
            title = clean_part(match.group("title"))
            artist = clean_part(match.group("artist"))
            if title and artist:
                return title, artist
    return None


def existing_target(path: Path, title: str, artist: str) -> Path:
    return path.with_name(f"{safe_filename(title)}_{safe_filename(artist)}{path.suffix}")


def rename_candidates(folder: Path) -> list[tuple[Path, Path]]:
    renames: list[tuple[Path, Path]] = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            continue

        if "_" in path.stem:
            continue

        inferred = split_title_artist(path.stem)
        if not inferred:
            continue

        title, artist = inferred
        target = existing_target(path, title, artist)
        if target.name != path.name:
            renames.append((path, target))

    return renames


def main() -> None:
    if not SONGS_DIR.exists():
        print(f"{SONGS_DIR} does not exist.")
        return

    renames = rename_candidates(SONGS_DIR)
    if not renames:
        print("No confident title/artist renames found.")
        return

    applied = 0
    for source, target in renames:
        if target.exists():
            continue
        source.rename(target)
        applied += 1
        print(f"{source.name} -> {target.name}")

    print(f"Renamed {applied} file(s).")


if __name__ == "__main__":
    main()
