from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .ledger import (
    FileRecord,
    format_bytes,
    is_under_ledger,
    manifest_path,
    normalize_music_root,
    path_key,
    scan_music_root,
    stats_match,
)
from .reporting import album_path, artist_path, diff_records, group_candidates
from .ledger import load_records


@dataclass(frozen=True)
class CopyChunk:
    kind: str
    relative_path: str
    reportable_records: list[FileRecord]


@dataclass(frozen=True)
class FileAction:
    relative_path: str
    source_path: Path
    target_path: Path
    size: int
    status: str


@dataclass
class CopyOptions:
    source: str
    target: str
    source_role: str | None = None
    target_role: str | None = None
    cache_dir: str | None = None
    execute: bool = False
    yes: bool = False
    replace_conflicts: bool = False
    artist_level: set[str] | None = None


def chunk_path_for_record(
    record: FileRecord,
    target_records: dict[str, FileRecord],
    artist_level_overrides: set[str],
) -> tuple[str, str]:
    artist = artist_path(record.relative_path)
    existing_artists = {artist_path(target.relative_path).casefold() for target in target_records.values()}
    if artist.casefold() in artist_level_overrides or artist.casefold() not in existing_artists:
        return "artist", artist
    return "album", album_path(record.relative_path)


def plan_chunks(
    source_records: dict[str, FileRecord],
    target_records: dict[str, FileRecord],
    artist_level_overrides: set[str] | None = None,
) -> list[CopyChunk]:
    overrides = {value.casefold().replace("\\", "/") for value in artist_level_overrides or set()}
    diff = diff_records(source_records, target_records)
    conflict_source_records = [source for source, _ in diff.conflicts]
    candidate_records = diff.source_only + conflict_source_records
    groups = group_candidates(candidate_records, target_records, overrides)

    records_by_group: dict[tuple[str, str], list[FileRecord]] = {}
    for record in candidate_records:
        group_key = chunk_path_for_record(record, target_records, overrides)
        records_by_group.setdefault(group_key, []).append(record)

    chunks = [
        CopyChunk(kind=group.kind, relative_path=group.relative_path, reportable_records=records_by_group[(group.kind, group.relative_path)])
        for group in groups
    ]
    return sorted(chunks, key=lambda chunk: chunk.relative_path.casefold())


def iter_chunk_files(source_root: Path, chunk_relative_path: str) -> list[Path]:
    chunk_root = source_root / Path(*chunk_relative_path.split("/"))
    if not chunk_root.exists():
        return []
    if chunk_root.is_file():
        return [chunk_root]
    files: list[Path] = []
    for path in chunk_root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(source_root).as_posix()
            if not is_under_ledger(rel):
                files.append(path)
    return sorted(files, key=lambda path: path.relative_to(source_root).as_posix().casefold())


def build_file_actions(
    source_root: Path,
    target_root: Path,
    chunk: CopyChunk,
    replace_conflicts: bool,
) -> list[FileAction]:
    actions: list[FileAction] = []
    for source_path in iter_chunk_files(source_root, chunk.relative_path):
        rel = source_path.relative_to(source_root).as_posix()
        target_path = target_root / Path(*rel.split("/"))
        size = source_path.stat().st_size
        if target_path.exists():
            if stats_match(source_path, target_path):
                status = "same"
            else:
                status = "replace" if replace_conflicts else "conflict"
        else:
            status = "copy"
        actions.append(FileAction(rel, source_path, target_path, size, status))
    return actions


def action_bytes(actions: list[FileAction]) -> int:
    return sum(action.size for action in actions if action.status in {"copy", "replace"})


def prompt_chunk(chunk: CopyChunk, actions: list[FileAction], assume_yes: bool) -> str:
    if assume_yes:
        return "yes"
    pending = [action for action in actions if action.status in {"copy", "replace"}]
    conflicts = [action for action in actions if action.status == "conflict"]
    print(
        f"[{chunk.kind}] {chunk.relative_path}: "
        f"{len(pending)} files, {format_bytes(action_bytes(actions))}, "
        f"{len(conflicts)} conflicts"
    )
    while True:
        answer = input("Copy this group? [y/N/q/a] ").strip().casefold()
        if answer in {"", "n", "no"}:
            return "no"
        if answer in {"y", "yes"}:
            return "yes"
        if answer in {"q", "quit"}:
            return "quit"
        if answer in {"a", "all"}:
            return "all"
        print("Answer y, n, q, or a.")


def copy_actions(actions: list[FileAction]) -> tuple[int, int]:
    copied = 0
    skipped = 0
    for action in actions:
        if action.status in {"same", "conflict"}:
            skipped += 1
            continue
        action.target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(action.source_path, action.target_path)
        copied += 1
    return copied, skipped


def render_dry_run(chunks: list[CopyChunk], source_root: Path, target_root: Path, replace_conflicts: bool) -> str:
    lines = ["LibrarySync copy dry-run", "Add --execute to copy files.", ""]
    if not chunks:
        lines.append("No copy candidates.")
        return "\n".join(lines)
    total_bytes = 0
    for chunk in chunks:
        actions = build_file_actions(source_root, target_root, chunk, replace_conflicts)
        bytes_needed = action_bytes(actions)
        total_bytes += bytes_needed
        conflicts = len([action for action in actions if action.status == "conflict"])
        replacements = len([action for action in actions if action.status == "replace"])
        copies = len([action for action in actions if action.status == "copy"])
        lines.append(
            f"[{chunk.kind}] {chunk.relative_path}: "
            f"{copies} new, {replacements} replacements, {conflicts} conflicts, "
            f"{format_bytes(bytes_needed)}"
        )
    lines.append("")
    lines.append(f"Total bytes to write: {format_bytes(total_bytes)}")
    return "\n".join(lines)


def run_copy(options: CopyOptions) -> int:
    source_root = normalize_music_root(options.source)
    target_root = normalize_music_root(options.target)

    scan_music_root(
        source_root,
        role=options.source_role,
        cache_dir=options.cache_dir,
    )
    scan_music_root(
        target_root,
        role=options.target_role,
        cache_dir=options.cache_dir,
    )

    source_records = load_records(manifest_path(source_root), reportable_only=True)
    target_records = load_records(manifest_path(target_root), reportable_only=True)
    chunks = plan_chunks(source_records, target_records, options.artist_level)

    if not options.execute:
        print(render_dry_run(chunks, source_root, target_root, options.replace_conflicts))
        return 0

    if not chunks:
        print("No copy candidates.")
        return 0

    assume_yes = options.yes
    copied_total = 0
    skipped_total = 0
    for chunk in chunks:
        actions = build_file_actions(source_root, target_root, chunk, options.replace_conflicts)
        pending_actions = [action for action in actions if action.status in {"copy", "replace"}]
        if not pending_actions:
            conflicts = len([action for action in actions if action.status == "conflict"])
            if conflicts:
                print(
                    f"Skipped {conflicts} conflicts in {chunk.relative_path}; "
                    "rerun with --replace-conflicts to overwrite."
                )
            continue
        answer = prompt_chunk(chunk, actions, assume_yes)
        if answer == "quit":
            print("Stopped.")
            return 2
        if answer == "all":
            assume_yes = True
            answer = "yes"
        if answer != "yes":
            continue

        bytes_needed = action_bytes(actions)
        free_bytes = shutil.disk_usage(target_root).free
        if bytes_needed > free_bytes:
            print(
                f"No more room on target. Need {format_bytes(bytes_needed)}, "
                f"free {format_bytes(free_bytes)}. Delete files manually, then rerun."
            )
            return 3
        copied, skipped = copy_actions(actions)
        copied_total += copied
        skipped_total += skipped
        print(f"Copied {copied} files from {chunk.relative_path}; skipped {skipped}.")

    scan_music_root(
        target_root,
        role=options.target_role,
        cache_dir=options.cache_dir,
    )
    print(f"Done. Copied {copied_total} files; skipped {skipped_total}.")
    return 0
