# musicmeta

`musicmeta` is a small workflow for turning playlist downloads into audio files with usable embedded metadata for media player apps such as Apple Music, iTunes, MusicBee, Plexamp, and similar libraries.

The expected flow is:

1. Download a YouTube playlist with [Stacher](https://stacher.io).
2. Save the downloaded audio files into `musicmeta/songs`.
3. Run `metadata_update.py`.
4. Use the enriched files from `musicmeta/meta-enriched`.

## Folder Layout

```text
musicmeta/
  metadata_update.py
  songs/
  meta-enriched/
```

`songs/` is the intake folder. Put new downloads there.

`meta-enriched/` is the success folder. Files are moved here only after metadata is written successfully.

Files that fail parsing, tagging, or lookup stay in `songs/` so they can be fixed and rerun.

## Stacher Setup

Download playlists with Stacher:

https://stacher.io

Set Stacher's filename template to:

```text
title_artist.ext
```

![Stacher filename template](template.png)

This script depends on that format. The underscore separates the song title from the artist name.

Examples:

```text
A Pearl_Mitski.opus
BIRDS OF A FEATHER_Billie Eilish.m4a
No Surprises_Radiohead.mp3
```

Avoid filenames without the underscore separator, because they will be left in `songs/` as unsuccessful.

The script also cleans common YouTube-style extras from names. For example,
`Alicia Keys - A Woman's Worth (Official HD Video)_NA.opus` is treated as
artist `Alicia Keys` and title `A Woman's Worth`.

## Requirements

Install or have available:

- Python 3
- `ffmpeg`
- Python package `requests`

Check locally:

```bash
python3 --version
ffmpeg -version
python3 -c "import requests"
```

## Usage

From the repository root:

```bash
python3 Music/musicmeta/metadata_update.py
```

Or from inside `Music/musicmeta`:

```bash
python3 metadata_update.py
```

By default, the script:

- Reads audio files from `musicmeta/songs`
- Parses title and artist from `title_artist.ext`
- Searches MusicBrainz for album, date, track number, and MusicBrainz IDs
- Writes metadata with `ffmpeg` without re-encoding the audio
- Moves successful files to `musicmeta/meta-enriched`
- Leaves unsuccessful files in `musicmeta/songs`

## Before/After Metadata Check

To inspect metadata before and after enrichment:

```bash
python3 Music/musicmeta/metadata_test.py
```

The test script reads files still in `musicmeta/songs` as the before state and files in `musicmeta/meta-enriched` as the after state.

For machine-readable output:

```bash
python3 Music/musicmeta/metadata_test.py --json
```

## Offline Mode

To skip MusicBrainz and only write title/artist from the filename:

```bash
python3 Music/musicmeta/metadata_update.py --no-musicbrainz
```

This is useful when you do not have internet access or only need basic title and artist tags.

## Custom Output Folder

To move successful files somewhere else:

```bash
python3 Music/musicmeta/metadata_update.py --output /path/to/output
```

## Supported Audio Types

The script currently processes:

```text
.opus
.m4a
.mp3
.flac
.ogg
```

Other files are ignored.

## Notes

MusicBrainz lookups are cached in `.musicbrainz_cache.json` to avoid repeated API calls for the same title and artist.

If MusicBrainz cannot be reached, the script still writes title and artist metadata from the filename and moves the file after successful tagging.

If a file remains in `songs/`, check that its name follows:

```text
title_artist.ext
```
