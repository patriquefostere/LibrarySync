from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .ledger import (
    FileRecord,
    format_bytes,
    is_under_ledger,
    load_records,
    manifest_path,
    normalize_music_root,
    path_key,
    scan_music_root,
    stats_match,
)
from .reporting import diff_records, group_candidates


@dataclass(frozen=True)
class CopyChunk:
    kind: str
    relative_path: str
    candidate_records: list[FileRecord]


@dataclass(frozen=True)
class FileAction:
    relative_path: str
    source_path: Path
    target_path: Path
    size: int
    status: str


@dataclass(frozen=True)
class CopyActionResult:
    copied: int
    skipped: int
    conflicts: int
    missing: int


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


def plan_chunks(
    source_records: dict[str, FileRecord],
    target_records: dict[str, FileRecord],
) -> list[CopyChunk]:
    diff = diff_records(source_records, target_records)
    conflict_source_records = [source for source, _ in diff.conflicts]
    candidate_records = diff.source_only + conflict_source_records
    groups = group_candidates(candidate_records, target_records)

    chunks = [
        CopyChunk(
            kind=group.kind,
            relative_path=group.relative_path,
            candidate_records=list(group.records),
        )
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
    seen_source_keys: set[str] = set()
    for source_path in iter_chunk_files(source_root, chunk.relative_path):
        rel = source_path.relative_to(source_root).as_posix()
        seen_source_keys.add(path_key(rel))
        target_path = target_root / Path(*rel.split("/"))
        try:
            size = source_path.stat().st_size
        except FileNotFoundError:
            actions.append(FileAction(rel, source_path, target_path, 0, "missing"))
            continue
        if target_path.exists():
            if stats_match(source_path, target_path):
                status = "same"
            else:
                status = "replace" if replace_conflicts else "conflict"
        else:
            status = "copy"
        actions.append(FileAction(rel, source_path, target_path, size, status))

    for record in chunk.candidate_records:
        if record.path_key in seen_source_keys:
            continue
        source_path = source_root / Path(*record.relative_path.split("/"))
        target_path = target_root / Path(*record.relative_path.split("/"))
        actions.append(
            FileAction(record.relative_path, source_path, target_path, record.size, "missing")
        )

    return sorted(actions, key=lambda action: action.relative_path.casefold())


def action_bytes(actions: list[FileAction]) -> int:
    return sum(action.size for action in actions if action.status in {"copy", "replace"})


def prompt_chunk(chunk: CopyChunk, actions: list[FileAction], assume_yes: bool) -> str:
    if assume_yes:
        return "yes"
    pending = [action for action in actions if action.status in {"copy", "replace"}]
    conflicts = [action for action in actions if action.status == "conflict"]
    missing = [action for action in actions if action.status == "missing"]
    print(
        f"[{chunk.kind}] {chunk.relative_path}: "
        f"{len(pending)} files, {format_bytes(action_bytes(actions))}, "
        f"{len(conflicts)} conflicts, {len(missing)} missing"
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


def copy_actions(actions: list[FileAction]) -> CopyActionResult:
    copied = 0
    skipped = 0
    conflicts = 0
    missing = 0
    for action in actions:
        if action.status == "same":
            skipped += 1
            continue
        if action.status == "conflict":
            conflicts += 1
            continue
        if action.status == "missing":
            missing += 1
            continue
        action.target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(action.source_path, action.target_path)
        except FileNotFoundError:
            missing += 1
            continue
        copied += 1
    return CopyActionResult(copied, skipped, conflicts, missing)


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
        missing = len([action for action in actions if action.status == "missing"])
        replacements = len([action for action in actions if action.status == "replace"])
        copies = len([action for action in actions if action.status == "copy"])
        lines.append(
            f"[{chunk.kind}] {chunk.relative_path}: "
            f"{copies} new, {replacements} replacements, "
            f"{conflicts} conflicts, {missing} missing, "
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

    source_records = load_records(manifest_path(source_root), reportable_only=False)
    target_records = load_records(manifest_path(target_root), reportable_only=False)
    chunks = plan_chunks(source_records, target_records)

    if not options.execute:
        print(render_dry_run(chunks, source_root, target_root, options.replace_conflicts))
        return 0

    if not chunks:
        print("No copy candidates.")
        return 0

    assume_yes = options.yes
    copied_total = 0
    skipped_total = 0
    conflicts_total = 0
    missing_total = 0
    for chunk in chunks:
        actions = build_file_actions(source_root, target_root, chunk, options.replace_conflicts)
        pending_actions = [action for action in actions if action.status in {"copy", "replace"}]
        if not pending_actions:
            skipped_total += len([action for action in actions if action.status == "same"])
            conflicts = len([action for action in actions if action.status == "conflict"])
            missing = len([action for action in actions if action.status == "missing"])
            conflicts_total += conflicts
            missing_total += missing
            if conflicts:
                print(
                    f"Skipped {conflicts} conflicts in {chunk.relative_path}; "
                    "rerun with --replace-conflicts to overwrite."
                )
            if missing:
                print(f"Skipped {missing} missing source files in {chunk.relative_path}.")
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
        result = copy_actions(actions)
        copied_total += result.copied
        skipped_total += result.skipped
        conflicts_total += result.conflicts
        missing_total += result.missing
        print(
            f"Copied {result.copied} files from {chunk.relative_path}; "
            f"skipped {result.skipped}; conflicts {result.conflicts}; missing {result.missing}."
        )

    scan_music_root(
        target_root,
        role=options.target_role,
        cache_dir=options.cache_dir,
    )
    print(
        f"Done. Copied {copied_total} files; skipped {skipped_total}; "
        f"conflicts {conflicts_total}; missing {missing_total}."
    )
    return 0
