# musicmeta

`musicmeta` tags audio files in `songs/`, moves finished files to `meta-enriched/`, and can add lyrics automatically.

## What goes where

- `songs/`: input files
- `meta-enriched/`: successful output
- `lyrics/`: generated lyric cache and missing-lyrics reports

## Quick start

```bash
git clone https://github.com/chaoticsponge/musicmeta
cd musicmeta
python3 -m pip install -r requirements.txt
brew install ffmpeg
```

Put your audio files in `songs/`, then run:

```bash
python3 metadata_update.py
python3 add_lyrics.py
```

Files that are tagged successfully are moved to `meta-enriched/`. Files that fail stay in `songs/` so you can fix the names and try again.

## Supported files

`.opus`, `.m4a`, `.mp3`, `.flac`, `.ogg`

## Lyrics

Lyrics are fetched automatically and embedded into the file.

- MP3: ID3 `USLT` and `SYLT`
- M4A/AAC: Apple `©lyr`
- Cached sidecar lyrics are written to `lyrics/`

Already-tagged lyrics are skipped by default. Use `--force` or `--refresh` to reprocess them.

Fast run on a typical Mac:

```bash
python3 add_lyrics.py --jobs 4
```

## Useful options

Skip cover art:

```bash
python3 metadata_update.py --no-cover
```

Move output somewhere else:

```bash
python3 metadata_update.py --output /path/to/output
```

Verify the library:

```bash
python3 misc/metadata_test.py --verify
python3 misc/metadata_test.py --verify --strict-v4
```

## Notes

For best results, keep filenames close to `Title_Artist.ext`. The updater will do some cleanup, but clearer names produce better MusicBrainz matches.
