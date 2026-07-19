from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

LEDGER_DIR_NAME = ".music-ledger"
DEFAULT_CACHE_ENV = "LIBRARYSYNC_CACHE"
REPORT_MUSIC_EXTENSIONS = {".mp3", ".flac", ".m4a", ".wav", ".mp4", ".m4v", ".mka"}
ROLES = {"laptop", "archive", "phone", "backup"}
MTIME_TOLERANCE_NS = 2_000_000_000


@dataclass(frozen=True)
class FileRecord:
    path_key: str
    relative_path: str
    size: int
    mtime_ns: int
    mtime_iso: str
    first_seen: str
    last_seen: str
    is_reportable: bool


@dataclass(frozen=True)
class DriveInfo:
    drive_id: str
    role: str
    label: str
    created_at: str
    last_scan_at: str | None = None
    last_scan_root: str | None = None
    file_count: int | None = None
    reportable_count: int | None = None


@dataclass(frozen=True)
class Snapshot:
    drive: DriveInfo
    folder: Path
    manifest_path: Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def mtime_to_iso(mtime_ns: int) -> str:
    return datetime.fromtimestamp(mtime_ns / 1_000_000_000, timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def default_cache_dir() -> Path:
    env_value = os.environ.get(DEFAULT_CACHE_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return Path.home() / ".music-ledger" / "known-drives"


def normalize_music_root(root: str | Path) -> Path:
    path = Path(root).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Music root does not exist: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"Music root is not a directory: {path}")
    return path.resolve()


def relative_to_music_root(music_root: Path, file_path: Path) -> str:
    return file_path.relative_to(music_root).as_posix()


def path_key(relative_path: str) -> str:
    return relative_path.replace("\\", "/").casefold()


def split_relative(relative_path: str) -> list[str]:
    return [part for part in relative_path.replace("\\", "/").split("/") if part]


def is_under_ledger(relative_path: str) -> bool:
    parts = split_relative(relative_path)
    return bool(parts) and parts[0].casefold() == LEDGER_DIR_NAME.casefold()


def is_reportable_relative_path(relative_path: str) -> bool:
    parts = split_relative(relative_path)
    if not parts:
        return False
    suffix = Path(parts[-1]).suffix.casefold()
    if suffix == ".cue":
        return parts[0].casefold() == "playlists"
    return suffix in REPORT_MUSIC_EXTENSIONS


def records_match(left: FileRecord, right: FileRecord) -> bool:
    if left.size != right.size:
        return False
    return abs(left.mtime_ns - right.mtime_ns) <= MTIME_TOLERANCE_NS


def stats_match(src_path: Path, target_path: Path) -> bool:
    if not target_path.exists() or not target_path.is_file():
        return False
    src_stat = src_path.stat()
    target_stat = target_path.stat()
    if src_stat.st_size != target_stat.st_size:
        return False
    return abs(src_stat.st_mtime_ns - target_stat.st_mtime_ns) <= MTIME_TOLERANCE_NS


def ledger_dir(music_root: Path) -> Path:
    return music_root / LEDGER_DIR_NAME


def drive_json_path(music_root: Path) -> Path:
    return ledger_dir(music_root) / "drive.json"


def manifest_path(music_root: Path) -> Path:
    return ledger_dir(music_root) / "manifest.sqlite"


def _drive_info_from_dict(data: dict[str, object]) -> DriveInfo:
    return DriveInfo(
        drive_id=str(data["drive_id"]),
        role=str(data.get("role", "unknown")),
        label=str(data.get("label", "")),
        created_at=str(data.get("created_at", "")),
        last_scan_at=data.get("last_scan_at") and str(data["last_scan_at"]),
        last_scan_root=data.get("last_scan_root") and str(data["last_scan_root"]),
        file_count=int(data["file_count"]) if data.get("file_count") is not None else None,
        reportable_count=int(data["reportable_count"])
        if data.get("reportable_count") is not None
        else None,
    )


def _drive_info_to_dict(info: DriveInfo) -> dict[str, object]:
    data: dict[str, object] = {
        "drive_id": info.drive_id,
        "role": info.role,
        "label": info.label,
        "created_at": info.created_at,
    }
    if info.last_scan_at is not None:
        data["last_scan_at"] = info.last_scan_at
    if info.last_scan_root is not None:
        data["last_scan_root"] = info.last_scan_root
    if info.file_count is not None:
        data["file_count"] = info.file_count
    if info.reportable_count is not None:
        data["reportable_count"] = info.reportable_count
    return data


def write_drive_info(music_root: Path, info: DriveInfo) -> None:
    ledger_dir(music_root).mkdir(parents=True, exist_ok=True)
    drive_json_path(music_root).write_text(
        json.dumps(_drive_info_to_dict(info), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_drive_info_from_file(path: Path) -> DriveInfo:
    return _drive_info_from_dict(json.loads(path.read_text(encoding="utf-8")))


def read_drive_info(music_root: Path) -> DriveInfo:
    return read_drive_info_from_file(drive_json_path(music_root))


def load_or_create_drive_info(music_root: Path, role: str | None) -> DriveInfo:
    if role is not None and role not in ROLES:
        raise ValueError(f"Unknown role: {role}. Expected one of: {', '.join(sorted(ROLES))}")

    info_path = drive_json_path(music_root)
    if info_path.exists():
        info = read_drive_info(music_root)
        changed = False
        next_role = info.role
        if role is not None and role != info.role:
            next_role = role
            changed = True
        next_label = next_role
        if next_label != info.label:
            changed = True
        if changed:
            info = DriveInfo(
                drive_id=info.drive_id,
                role=next_role,
                label=next_label,
                created_at=info.created_at,
                last_scan_at=info.last_scan_at,
                last_scan_root=info.last_scan_root,
                file_count=info.file_count,
                reportable_count=info.reportable_count,
            )
            write_drive_info(music_root, info)
        return info

    now = utc_now_iso()
    info = DriveInfo(
        drive_id=str(uuid.uuid4()),
        role=role or "unknown",
        label=role or "unknown",
        created_at=now,
    )
    write_drive_info(music_root, info)
    return info


def _connect_manifest(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    return conn


def _create_manifest_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE files (
            path_key TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            mtime_iso TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            is_reportable INTEGER NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX idx_files_reportable ON files(is_reportable)")
    conn.execute(
        """
        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def _load_first_seen(existing_manifest: Path) -> dict[str, str]:
    if not existing_manifest.exists():
        return {}
    conn = sqlite3.connect(existing_manifest)
    try:
        return {row[0]: row[1] for row in conn.execute("SELECT path_key, first_seen FROM files")}
    finally:
        conn.close()


def iter_files(music_root: Path) -> Iterable[Path]:
    for current_root, dirnames, filenames in os.walk(music_root, followlinks=False):
        current_path = Path(current_root)
        rel_current = current_path.relative_to(music_root).as_posix()
        if rel_current == ".":
            dirnames[:] = [
                name for name in dirnames if name.casefold() != LEDGER_DIR_NAME.casefold()
            ]
        elif is_under_ledger(rel_current):
            dirnames[:] = []
            continue
        for filename in filenames:
            file_path = current_path / filename
            rel_file = relative_to_music_root(music_root, file_path)
            if not is_under_ledger(rel_file):
                yield file_path


def scan_music_root(
    root: str | Path,
    role: str | None = None,
    cache_dir: str | Path | None = None,
) -> DriveInfo:
    music_root = normalize_music_root(root)
    ledger_dir(music_root).mkdir(parents=True, exist_ok=True)
    info = load_or_create_drive_info(music_root, role)

    now = utc_now_iso()
    existing_first_seen = _load_first_seen(manifest_path(music_root))
    temp_manifest = ledger_dir(music_root) / "manifest.sqlite.tmp"
    if temp_manifest.exists():
        temp_manifest.unlink()

    conn = _connect_manifest(temp_manifest)
    file_count = 0
    reportable_count = 0
    try:
        _create_manifest_schema(conn)
        for file_path in iter_files(music_root):
            try:
                stat = file_path.stat()
            except FileNotFoundError:
                continue
            relative_path = relative_to_music_root(music_root, file_path)
            key = path_key(relative_path)
            reportable = is_reportable_relative_path(relative_path)
            if reportable:
                reportable_count += 1
            file_count += 1
            conn.execute(
                """
                INSERT OR REPLACE INTO files (
                    path_key,
                    relative_path,
                    size,
                    mtime_ns,
                    mtime_iso,
                    first_seen,
                    last_seen,
                    is_reportable
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    relative_path,
                    stat.st_size,
                    stat.st_mtime_ns,
                    mtime_to_iso(stat.st_mtime_ns),
                    existing_first_seen.get(key, now),
                    now,
                    1 if reportable else 0,
                ),
            )
        meta_values = {
            "scan_at": now,
            "root": str(music_root),
            "drive_id": info.drive_id,
            "role": info.role,
            "label": info.label,
            "file_count": str(file_count),
            "reportable_count": str(reportable_count),
        }
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            sorted(meta_values.items()),
        )
        conn.commit()
    finally:
        conn.close()

    target_manifest = manifest_path(music_root)
    if target_manifest.exists():
        target_manifest.unlink()
    temp_manifest.replace(target_manifest)

    info = DriveInfo(
        drive_id=info.drive_id,
        role=info.role,
        label=info.label,
        created_at=info.created_at,
        last_scan_at=now,
        last_scan_root=str(music_root),
        file_count=file_count,
        reportable_count=reportable_count,
    )
    write_drive_info(music_root, info)

    copy_snapshot_to_cache(music_root, cache_dir or default_cache_dir())
    return info


def copy_snapshot_to_cache(music_root: Path, cache_dir: str | Path) -> Snapshot:
    info = read_drive_info(music_root)
    cache_root = Path(cache_dir).expanduser()
    snapshot_dir = cache_root / info.drive_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(drive_json_path(music_root), snapshot_dir / "drive.json")
    shutil.copy2(manifest_path(music_root), snapshot_dir / "manifest.sqlite")
    return Snapshot(info, snapshot_dir, snapshot_dir / "manifest.sqlite")


def load_records(manifest: Path, reportable_only: bool = False) -> dict[str, FileRecord]:
    conn = sqlite3.connect(manifest)
    try:
        where = "WHERE is_reportable = 1" if reportable_only else ""
        rows = conn.execute(
            f"""
            SELECT
                path_key,
                relative_path,
                size,
                mtime_ns,
                mtime_iso,
                first_seen,
                last_seen,
                is_reportable
            FROM files
            {where}
            ORDER BY relative_path
            """
        )
        return {
            row[0]: FileRecord(
                path_key=row[0],
                relative_path=row[1],
                size=int(row[2]),
                mtime_ns=int(row[3]),
                mtime_iso=row[4],
                first_seen=row[5],
                last_seen=row[6],
                is_reportable=bool(row[7]),
            )
            for row in rows
        }
    finally:
        conn.close()


def load_meta(manifest: Path) -> dict[str, str]:
    conn = sqlite3.connect(manifest)
    try:
        return {row[0]: row[1] for row in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()


def load_snapshots(cache_dir: str | Path | None = None) -> list[Snapshot]:
    cache_root = Path(cache_dir).expanduser() if cache_dir is not None else default_cache_dir()
    if not cache_root.exists():
        return []
    snapshots: list[Snapshot] = []
    for child in sorted(cache_root.iterdir()):
        if not child.is_dir():
            continue
        drive_path = child / "drive.json"
        manifest = child / "manifest.sqlite"
        if not drive_path.exists() or not manifest.exists():
            continue
        snapshots.append(Snapshot(read_drive_info_from_file(drive_path), child, manifest))
    return snapshots


def latest_snapshot_by_role(snapshots: Iterable[Snapshot]) -> tuple[dict[str, Snapshot], dict[str, list[Snapshot]]]:
    grouped: dict[str, list[Snapshot]] = {}
    for snapshot in snapshots:
        grouped.setdefault(snapshot.drive.role, []).append(snapshot)
    latest: dict[str, Snapshot] = {}
    for role, role_snapshots in grouped.items():
        latest[role] = max(
            role_snapshots,
            key=lambda snapshot: snapshot.drive.last_scan_at or snapshot.drive.created_at,
        )
    return latest, grouped


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
