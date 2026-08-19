# LibrarySync

Offline-aware command-line tools for tracking and copying a large `Music/` library across:

- laptop Music folder
- archive Music hard drive
- phone microSD/USB Music folder
- total backup drive

V1 rules:

- File identity is relative path under `Music/`, case-insensitive.
- Reports compare only music files: `.mp3`, `.flac`, `.m4a`, `.wav`, `.mp4`, `.m4v`, `.mka`.
- `.cue` counts only under `Music/Playlists/`.
- Copy operations copy whole selected folders, including album art, text, logs, and other support files.
- Copy never deletes files.
- Copy preserves timestamps.
- Empty folders are not copied.
- Same-path differences are conflicts. Use `--replace-conflicts` only when you explicitly want source files to overwrite target files.

## Quick start

From this folder:

```powershell
python -m pip install -e .
```

Then:

```powershell
music-scan --root "C:\Users\You\Music" --role laptop
music-scan --root "E:\Music" --role archive
music-report
music-copy --source "C:\Users\You\Music" --target "F:\Music" --source-role laptop --target-role phone --execute
```

If the Python `Scripts` folder is not on `PATH`, use the module entry point instead:

```powershell
python -m librarysync scan --root "C:\Users\You\Music" --role laptop
python -m librarysync scan --root "E:\Music" --role archive
python -m librarysync report
python -m librarysync copy --source "C:\Users\You\Music" --target "F:\Music" --source-role laptop --target-role phone --execute
```

For testing without touching real drives:

```powershell
python -m unittest discover -s tests
```

## Cache

Each connected `Music/` folder gets its own ledger:

```text
Music/.music-ledger/drive.json
Music/.music-ledger/manifest.sqlite
```

The machine running the command also keeps cached drive snapshots. Default:

```text
~/.music-ledger/known-drives/
```

Override for tests or a laptop-local cache:

```powershell
music-scan --root "E:\Music" --role archive --cache "C:\Users\You\Music\.music-ledger\known-drives"
```

Or:

```powershell
$env:LIBRARYSYNC_CACHE = "C:\Users\You\Music\.music-ledger\known-drives"
```

## Commands

### Scan

```powershell
music-scan --root "E:\Music" --role archive
```

Updates the drive ledger and writes a cached copy. First scan creates the drive id.

Valid `--role` values:

- `laptop`
- `archive`
- `phone`
- `backup`

### Report

```powershell
music-report
```

Uses cached ledgers, so all drives do not need to be connected. `music-report --cache ".\.music-ledger-cache"` means “read cached snapshots from this project-local test cache.” It does not scan drives, copy files, or delete files.

LibrarySync groups candidates at album level once the target already has that artist.
When an artist uses an exceptional layout, it automatically groups that artist at artist
level instead. This covers nested layouts like
`F/Frank Zappa/1970s/1973 - Over-Nite Sensation/tracks`, where `1970s` is only a
container, or `B/Beethoven/Symphonies/Symphony 1/tracks`, where `Symphonies` is a form
folder rather than the work itself.

Report sections:

- laptop to archive candidates
- archive to backup candidates
- laptop to phone candidates
- phone-only files
- same-path conflicts

### Copy

```powershell
music-copy --source "C:\Users\You\Music" --target "F:\Music" --source-role laptop --target-role phone --execute
```

Without `--execute`, copy runs as dry-run and prints plan only.

Interactive grouping:

- if artist absent from target, prompt once for artist
- if artist exists on target, prompt per album
- if artist has an exceptional nested or flat layout, prompt once for artist automatically

Conflict replacement is explicit:

```powershell
music-copy --source "C:\Users\You\Music" --target "E:\Music" --source-role laptop --target-role archive --execute --replace-conflicts
```


## Fake test libraries

LibrarySync does not parse audio bytes in v1. It only uses path, extension, size, and timestamp, so fake `.mp3` files are fine for testing. They will not play in a media player, but they exercise scan/report/copy behavior.

Fake roots include normal artist/album folders plus exceptional layouts:

- `F/Frank Zappa/1960s/1969 - Hot Rats/tracks`
- `F/Frank Zappa/1970s/1973 - Over-Nite Sensation/tracks`
- `B/Beethoven/Symphonies/Symphony 1/tracks`
- `B/Beethoven/String Quartets/String Quartet No. 14/tracks`
- `C/Chopin/Mazurkas/tracks`
- `C/Chopin/Nocturne Op. 9 No. 2.wav`

Create ignored fake roots:

```powershell
python scripts/create_fake_music_roots.py
```

Then try:

```powershell
music-scan --root ".\manual-test-libraries\Laptop\Music" --role laptop --cache ".\.music-ledger-cache"
music-scan --root ".\manual-test-libraries\Archive\Music" --role archive --cache ".\.music-ledger-cache"
music-scan --root ".\manual-test-libraries\Phone\Music" --role phone --cache ".\.music-ledger-cache"
music-scan --root ".\manual-test-libraries\Backup\Music" --role backup --cache ".\.music-ledger-cache"
music-report --cache ".\.music-ledger-cache"
music-copy --source ".\manual-test-libraries\Laptop\Music" --target ".\manual-test-libraries\Phone\Music" --source-role laptop --target-role phone --cache ".\.music-ledger-cache"
```
