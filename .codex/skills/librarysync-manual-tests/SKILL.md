---
name: librarysync-manual-tests
description: Use when running, planning, or documenting manual tests for the LibrarySync repo, especially fake Music roots, `music-scan`, `music-report`, `music-copy`, or `scripts/create_fake_music_roots.py`, to ensure all persistent manual-test data is written only under `manual-test-libraries-*` run directories.
---

# LibrarySync Manual Tests

## Overview

Keep manual-test data contained in ignored, uniquely named run folders. Do not let manual testing write to real Music folders, home-directory caches, repo-root cache folders, or the unsuffixed `manual-test-libraries/` directory.

## Write Boundary

Allowed persistent manual-test write root:

- `<repo>/manual-test-libraries-*`

Forbidden for manual-test data:

- Real user music folders such as `C:\Users\...\Music`, removable drives, network shares, or phone/archive paths.
- Default LibrarySync cache locations such as `~/.music-ledger/known-drives`.
- Repo-root caches such as `.music-ledger-cache` or `.music-ledger-cache-*`.
- Unsuffixed `manual-test-libraries/`.
- Any path outside a fresh `manual-test-libraries-*` folder unless the user explicitly asks for that exact external target.

## Manual Test Workflow

1. Create a unique run root before generating fixtures:

```powershell
$run = "manual-test-libraries-codex-run-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

2. Generate fake roots only under that run root:

```powershell
python scripts/create_fake_music_roots.py --base $run
```

3. Put scan/report/copy cache inside the same run root:

```powershell
$cache = Join-Path $run ".music-ledger-cache"
```

4. Pass explicit roots and cache on every command:

```powershell
music-scan --root (Join-Path $run "Laptop\Music") --role laptop --cache $cache
music-scan --root (Join-Path $run "Archive\Music") --role archive --cache $cache
music-scan --root (Join-Path $run "Phone\Music") --role phone --cache $cache
music-report --cache $cache --artist-level "F/Frank Zappa"
```

5. For `music-copy --execute`, both `--source` and `--target` must resolve inside the same `manual-test-libraries-*` run root.

## Preflight

Before running any manual-test command that can write files:

- Confirm every `--root`, `--source`, `--target`, `--cache`, and fixture `--base` path is inside a `manual-test-libraries-*` run root.
- Use dry-run copy first unless the user explicitly wants write behavior verified.
- If a command would use a default cache, add `--cache` pointing inside the run root.
- If uncertain where a command writes, inspect the code path before running it.

## Git Hygiene

Run roots should remain ignored by `.gitignore` via `manual-test-libraries-*/`. If new manual-test artifacts appear in `git status`, stop and fix the write path or ignore rule before continuing.
