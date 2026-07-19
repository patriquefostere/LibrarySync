from __future__ import annotations

import sqlite3
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
    return {path_key(artist_path(record.relative_path)) for record in records.values()}


def normalize_artist_level_overrides(artist_level_overrides: set[str] | None) -> set[str]:
    return {
        path_key("/".join(parts))
        for value in artist_level_overrides or set()
        if (parts := split_relative(value))
    }


def candidate_group_key(
    record: FileRecord,
    existing_artists: set[str],
    artist_level_overrides: set[str],
) -> tuple[str, str] | None:
    artist = artist_path(record.relative_path)
    if not artist:
        return None
    artist_key = path_key(artist)
    if artist_key in artist_level_overrides or artist_key not in existing_artists:
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
    canonical_roles = ["laptop", "archive", "phone", "backup"]
    extra_roles = sorted(
        (role for role in snapshots_by_role if role not in canonical_roles),
        key=path_key,
    )
    for role in [*canonical_roles, *extra_roles]:
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
    target_group_records: dict[str, FileRecord] | None,
    source_issue: str | None = None,
    target_issue: str | None = None,
    artist_level_overrides: set[str] | None = None,
) -> list[str]:
    lines = [f"{title}:"]
    if source_records is None or target_records is None or target_group_records is None:
        issues = []
        if source_records is None:
            issues.append(source_issue or "missing source scan")
        if target_records is None:
            issues.append(target_issue or "missing target scan")
        return lines + [f"  skipped: {' and '.join(issues)}"]

    diff = diff_records(source_records, target_records)
    groups = group_candidates(diff.source_only, target_group_records, artist_level_overrides)

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
    laptop_issue: str | None = None,
    phone_issue: str | None = None,
) -> list[str]:
    lines = ["Phone-only reportable files:"]
    if laptop_records is None or phone_records is None:
        issues = []
        if laptop_records is None:
            issues.append(laptop_issue or "missing laptop scan")
        if phone_records is None:
            issues.append(phone_issue or "missing phone scan")
        return lines + [f"  skipped: {' and '.join(issues)}"]
    diff = diff_records(laptop_records, phone_records)
    size = sum(record.size for record in diff.target_only)
    lines.append(f"  files: {len(diff.target_only)}, {format_bytes(size)}")
    for record in diff.target_only[:25]:
        lines.append(f"    {record.relative_path}")
    if len(diff.target_only) > 25:
        lines.append(f"    ... {len(diff.target_only) - 25} more files")
    return lines


def load_report_records(snapshot: Snapshot) -> dict[str, FileRecord] | None:
    try:
        return load_records(snapshot.manifest_path, reportable_only=False)
    except sqlite3.DatabaseError:
        return None


def reportable_records(records: dict[str, FileRecord] | None) -> dict[str, FileRecord] | None:
    if records is None:
        return None
    return {key: record for key, record in records.items() if record.is_reportable}


def role_issue(role: str, snapshot: Snapshot | None, records: dict[str, FileRecord] | None) -> str | None:
    if snapshot is None:
        return f"missing {role} scan"
    if records is None:
        return f"unreadable {role} manifest"
    return None


def render_report(cache_dir: str | None = None, artist_level_overrides: set[str] | None = None) -> str:
    snapshots = load_snapshots(cache_dir)
    latest, grouped = latest_snapshot_by_role(snapshots)
    laptop = latest.get("laptop")
    archive = latest.get("archive")
    phone = latest.get("phone")
    backup = latest.get("backup")
    records_by_role = {role: load_report_records(snapshot) for role, snapshot in latest.items()}
    reportable_by_role = {
        role: reportable_records(records)
        for role, records in records_by_role.items()
    }
    issues_by_role = {
        role: role_issue(role, latest.get(role), records_by_role.get(role))
        for role in {"laptop", "archive", "phone", "backup"}
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
            reportable_by_role.get("laptop") if laptop else None,
            reportable_by_role.get("archive") if archive else None,
            records_by_role.get("archive") if archive else None,
            issues_by_role["laptop"],
            issues_by_role["archive"],
            artist_level_overrides,
        )
    )
    lines.append("")
    lines.extend(
        render_pair(
            "Archive -> backup",
            reportable_by_role.get("archive") if archive else None,
            reportable_by_role.get("backup") if backup else None,
            records_by_role.get("backup") if backup else None,
            issues_by_role["archive"],
            issues_by_role["backup"],
            artist_level_overrides,
        )
    )
    lines.append("")
    lines.extend(
        render_pair(
            "Laptop -> phone",
            reportable_by_role.get("laptop") if laptop else None,
            reportable_by_role.get("phone") if phone else None,
            records_by_role.get("phone") if phone else None,
            issues_by_role["laptop"],
            issues_by_role["phone"],
            artist_level_overrides,
        )
    )
    lines.append("")
    lines.extend(
        render_phone_only(
            reportable_by_role.get("laptop") if laptop else None,
            reportable_by_role.get("phone") if phone else None,
            issues_by_role["laptop"],
            issues_by_role["phone"],
        )
    )
    return "\n".join(lines)
