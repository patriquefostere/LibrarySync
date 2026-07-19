from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from .ledger import (
    FileRecord,
    Snapshot,
    format_bytes,
    latest_snapshot_by_role,
    load_records,
    load_snapshots,
    records_match,
    split_relative,
)


@dataclass(frozen=True)
class DiffResult:
    source_only: list[FileRecord]
    target_only: list[FileRecord]
    conflicts: list[tuple[FileRecord, FileRecord]]


@dataclass(frozen=True)
class Group:
    kind: str
    relative_path: str
    file_count: int
    size: int


def artist_path(relative_path: str) -> str:
    parts = split_relative(relative_path)
    if not parts:
        return ""
    if parts[0].casefold() == "playlists":
        return "Playlists"
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def album_path(relative_path: str) -> str:
    parts = split_relative(relative_path)
    if not parts:
        return ""
    if parts[0].casefold() == "playlists":
        return "Playlists"
    if len(parts) >= 3:
        return f"{parts[0]}/{parts[1]}/{parts[2]}"
    return artist_path(relative_path)


def comparable_parent(relative_path: str) -> str:
    parts = split_relative(relative_path)
    if len(parts) <= 1:
        return ""
    return str(PurePosixPath(*parts[:-1]))


def diff_records(
    source_records: dict[str, FileRecord], target_records: dict[str, FileRecord]
) -> DiffResult:
    source_only = [record for key, record in source_records.items() if key not in target_records]
    target_only = [record for key, record in target_records.items() if key not in source_records]
    conflicts = [
        (record, target_records[key])
        for key, record in source_records.items()
        if key in target_records and not records_match(record, target_records[key])
    ]
    return DiffResult(
        source_only=sorted(source_only, key=lambda record: record.relative_path.casefold()),
        target_only=sorted(target_only, key=lambda record: record.relative_path.casefold()),
        conflicts=sorted(
            conflicts,
            key=lambda pair: pair[0].relative_path.casefold(),
        ),
    )


def target_artists(records: dict[str, FileRecord]) -> set[str]:
    return {artist_path(record.relative_path).casefold() for record in records.values()}


def group_candidates(
    records: list[FileRecord],
    target_records: dict[str, FileRecord],
    artist_level_overrides: set[str] | None = None,
) -> list[Group]:
    overrides = {value.casefold().replace("\\", "/") for value in artist_level_overrides or set()}
    existing_artists = target_artists(target_records)
    groups: dict[tuple[str, str], list[FileRecord]] = {}
    for record in records:
        artist = artist_path(record.relative_path)
        if not artist:
            continue
        if artist.casefold() in overrides or artist.casefold() not in existing_artists:
            group_key = ("artist", artist)
        else:
            group_key = ("album", album_path(record.relative_path))
        groups.setdefault(group_key, []).append(record)

    result = [
        Group(
            kind=kind,
            relative_path=relative_path,
            file_count=len(group_records),
            size=sum(record.size for record in group_records),
        )
        for (kind, relative_path), group_records in groups.items()
    ]
    return sorted(result, key=lambda group: group.relative_path.casefold())


def render_known_drives(snapshots_by_role: dict[str, Snapshot], grouped: dict[str, list[Snapshot]]) -> list[str]:
    lines = ["Known drives:"]
    if not snapshots_by_role:
        return lines + ["  none"]
    for role in ["laptop", "archive", "phone", "backup"]:
        snapshot = snapshots_by_role.get(role)
        if snapshot is None:
            lines.append(f"  {role}: not scanned")
            continue
        extra = ""
        if len(grouped.get(role, [])) > 1:
            extra = f" ({len(grouped[role])} cached, newest selected)"
        lines.append(
            "  "
            f"{role}: scanned {snapshot.drive.last_scan_at or 'never'} "
            f"files {snapshot.drive.reportable_count or 0} reportable{extra}"
        )
    return lines


def render_pair(
    title: str,
    source: Snapshot | None,
    target: Snapshot | None,
    artist_level_overrides: set[str] | None = None,
) -> list[str]:
    lines = [f"{title}:"]
    if source is None or target is None:
        missing = []
        if source is None:
            missing.append("source")
        if target is None:
            missing.append("target")
        return lines + [f"  skipped: missing {' and '.join(missing)} scan"]

    source_records = load_records(source.manifest_path, reportable_only=True)
    target_records = load_records(target.manifest_path, reportable_only=True)
    diff = diff_records(source_records, target_records)
    groups = group_candidates(diff.source_only, target_records, artist_level_overrides)

    source_size = sum(record.size for record in diff.source_only)
    lines.append(
        f"  add candidates: {len(diff.source_only)} files, {format_bytes(source_size)}, {len(groups)} groups"
    )
    for group in groups[:25]:
        lines.append(
            f"    [{group.kind}] {group.relative_path} "
            f"({group.file_count} files, {format_bytes(group.size)})"
        )
    if len(groups) > 25:
        lines.append(f"    ... {len(groups) - 25} more groups")

    if diff.conflicts:
        lines.append(f"  conflicts: {len(diff.conflicts)}")
        for source_record, _ in diff.conflicts[:25]:
            lines.append(f"    {source_record.relative_path}")
        if len(diff.conflicts) > 25:
            lines.append(f"    ... {len(diff.conflicts) - 25} more conflicts")
    else:
        lines.append("  conflicts: 0")
    return lines


def render_phone_only(laptop: Snapshot | None, phone: Snapshot | None) -> list[str]:
    lines = ["Phone-only reportable files:"]
    if laptop is None or phone is None:
        return lines + ["  skipped: missing laptop or phone scan"]
    laptop_records = load_records(laptop.manifest_path, reportable_only=True)
    phone_records = load_records(phone.manifest_path, reportable_only=True)
    diff = diff_records(laptop_records, phone_records)
    size = sum(record.size for record in diff.target_only)
    lines.append(f"  files: {len(diff.target_only)}, {format_bytes(size)}")
    for record in diff.target_only[:25]:
        lines.append(f"    {record.relative_path}")
    if len(diff.target_only) > 25:
        lines.append(f"    ... {len(diff.target_only) - 25} more files")
    return lines


def render_report(cache_dir: str | None = None, artist_level_overrides: set[str] | None = None) -> str:
    snapshots = load_snapshots(cache_dir)
    latest, grouped = latest_snapshot_by_role(snapshots)
    laptop = latest.get("laptop")
    archive = latest.get("archive")
    phone = latest.get("phone")
    backup = latest.get("backup")

    lines: list[str] = ["LibrarySync report"]
    if cache_dir:
        lines.append(f"Cache: {cache_dir}")
    lines.append("")
    lines.extend(render_known_drives(latest, grouped))
    lines.append("")
    lines.extend(render_pair("Laptop -> archive", laptop, archive, artist_level_overrides))
    lines.append("")
    lines.extend(render_pair("Archive -> backup", archive, backup, artist_level_overrides))
    lines.append("")
    lines.extend(render_pair("Laptop -> phone", laptop, phone, artist_level_overrides))
    lines.append("")
    lines.extend(render_phone_only(laptop, phone))
    return "\n".join(lines)
