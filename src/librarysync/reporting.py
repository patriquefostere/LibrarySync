from __future__ import annotations

from dataclasses import dataclass

from .ledger import (
    FileRecord,
    Snapshot,
    format_bytes,
    latest_snapshot_by_role,
    load_records,
    load_snapshots,
    path_key,
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
    records: tuple[FileRecord, ...]


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
        source_only=sorted(source_only, key=lambda record: path_key(record.relative_path)),
        target_only=sorted(target_only, key=lambda record: path_key(record.relative_path)),
        conflicts=sorted(
            conflicts,
            key=lambda pair: path_key(pair[0].relative_path),
        ),
    )


def target_artists(records: dict[str, FileRecord]) -> set[str]:
    return {artist_path(record.relative_path).casefold() for record in records.values()}


def normalize_artist_level_overrides(artist_level_overrides: set[str] | None) -> set[str]:
    return {value.casefold().replace("\\", "/") for value in artist_level_overrides or set()}


def candidate_group_key(
    record: FileRecord,
    existing_artists: set[str],
    artist_level_overrides: set[str],
) -> tuple[str, str] | None:
    artist = artist_path(record.relative_path)
    if not artist:
        return None
    if artist.casefold() in artist_level_overrides or artist.casefold() not in existing_artists:
        return "artist", artist
    return "album", album_path(record.relative_path)


def group_candidates(
    records: list[FileRecord],
    target_records: dict[str, FileRecord],
    artist_level_overrides: set[str] | None = None,
) -> list[Group]:
    overrides = normalize_artist_level_overrides(artist_level_overrides)
    existing_artists = target_artists(target_records)
    groups: dict[tuple[str, str], list[FileRecord]] = {}
    for record in records:
        group_key = candidate_group_key(record, existing_artists, overrides)
        if group_key is None:
            continue
        groups.setdefault(group_key, []).append(record)

    result = [
        Group(
            kind=kind,
            relative_path=relative_path,
            file_count=len(group_records),
            size=sum(record.size for record in group_records),
            records=tuple(group_records),
        )
        for (kind, relative_path), group_records in groups.items()
    ]
    return sorted(result, key=lambda group: path_key(group.relative_path))


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
            f"files {snapshot.drive.file_count or 0}, "
            f"reportable {snapshot.drive.reportable_count or 0}{extra}"
        )
    return lines


def render_pair(
    title: str,
    source_records: dict[str, FileRecord] | None,
    target_records: dict[str, FileRecord] | None,
    artist_level_overrides: set[str] | None = None,
) -> list[str]:
    lines = [f"{title}:"]
    if source_records is None or target_records is None:
        missing = []
        if source_records is None:
            missing.append("source")
        if target_records is None:
            missing.append("target")
        return lines + [f"  skipped: missing {' and '.join(missing)} scan"]

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


def render_phone_only(
    laptop_records: dict[str, FileRecord] | None,
    phone_records: dict[str, FileRecord] | None,
) -> list[str]:
    lines = ["Phone-only reportable files:"]
    if laptop_records is None or phone_records is None:
        return lines + ["  skipped: missing laptop or phone scan"]
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
    records_by_role = {
        role: load_records(snapshot.manifest_path, reportable_only=True)
        for role, snapshot in latest.items()
    }

    lines: list[str] = ["LibrarySync report"]
    if cache_dir:
        lines.append(f"Cache: {cache_dir}")
    lines.append("")
    lines.extend(render_known_drives(latest, grouped))
    lines.append("")
    lines.extend(
        render_pair(
            "Laptop -> archive",
            records_by_role.get("laptop") if laptop else None,
            records_by_role.get("archive") if archive else None,
            artist_level_overrides,
        )
    )
    lines.append("")
    lines.extend(
        render_pair(
            "Archive -> backup",
            records_by_role.get("archive") if archive else None,
            records_by_role.get("backup") if backup else None,
            artist_level_overrides,
        )
    )
    lines.append("")
    lines.extend(
        render_pair(
            "Laptop -> phone",
            records_by_role.get("laptop") if laptop else None,
            records_by_role.get("phone") if phone else None,
            artist_level_overrides,
        )
    )
    lines.append("")
    lines.extend(
        render_phone_only(
            records_by_role.get("laptop") if laptop else None,
            records_by_role.get("phone") if phone else None,
        )
    )
    return "\n".join(lines)
