# dupfind

Find duplicate files by content — fast, because it only hashes files that
actually might be duplicates.

## How it works

1. Walk the given directories and group every file by its size.
2. SHA-256-hash only the files whose size bucket has more than one entry.
3. Files sharing both size and hash are duplicates.

Two files with the same size but different content are never flagged, and
unreadable files are reported as warnings instead of crashing the scan.

## Requirements

Python 3.10+ — standard library only, no dependencies to install.

## Usage

```bash
python -m dupfind <path> [<path> ...] [options]
```

| Flag | What it does |
| --- | --- |
| `--min-size SIZE` | Skip files smaller than `SIZE` (`1024`, `10K`, `5MB`) |
| `--exclude PATTERN` | Glob pattern to skip; repeatable (`--exclude '*.tmp'`) |
| `--json` | Print machine-readable JSON instead of the human report |
| `--delete` | Interactively delete duplicate copies (keeps the first file of each group); asks per group |
| `--dry-run` | With `--delete`, only show what would be deleted |
| `--follow-symlinks` | Follow symbolic links (off by default; links are skipped) |

Exit codes: `0` = no duplicates, `1` = duplicates found, `2` = usage error.

## Examples

### 1. Scan your Downloads folder

```bash
$ python -m dupfind ~/Downloads
Group 1: 2 files, 2.4 MiB each, sha256 9f2c41ab07d1…
  /home/ujjwal/Downloads/report.pdf
  /home/ujjwal/Downloads/report (1).pdf

Scanned 318 file(s); 1 duplicate group(s); 1 duplicate file(s); 2.4 MiB reclaimable (2516582 bytes).
```

### 2. Scan two folders, ignore caches, skip tiny files, get JSON

```bash
$ python -m dupfind ~/Photos ~/Pictures --min-size 10K --exclude '.thumbs' --json
{
  "files_scanned": 1240,
  "duplicate_groups": [
    {
      "sha256": "4e2a…",
      "size": 184320,
      "files": [
        "/home/ujjwal/Photos/IMG_2041.jpg",
        "/home/ujjwal/Pictures/IMG_2041.jpg"
      ]
    }
  ],
  "duplicate_files": 1,
  "bytes_reclaimable": 184320,
  "warnings": []
}
```

### 3. Preview a cleanup, then delete for real

```bash
$ python -m dupfind ~/Documents --delete --dry-run
Would delete 1 file(s) from group (18.2 KiB each), keeping /home/ujjwal/Documents/notes.txt:
  /home/ujjwal/Documents/notes-copy.txt

Dry run: 1 file(s) would be deleted, freeing 18.2 KiB.
```

```bash
$ python -m dupfind ~/Documents --delete
Group: 2 files, 18.2 KiB each; keep /home/ujjwal/Documents/notes.txt
  /home/ujjwal/Documents/notes-copy.txt
Delete these copies? [y/N] y
Deleted /home/ujjwal/Documents/notes-copy.txt

Deleted 1 file(s).
```

## Running the tests

```bash
python -m pytest
```

## Author

Ujjwal Bhatia
