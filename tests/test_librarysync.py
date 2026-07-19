from __future__ import annotations

import contextlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import librarysync.cli as cli
import librarysync.ledger as ledger
import librarysync.reporting as reporting
from librarysync.copying import CopyOptions, build_file_actions, plan_chunks, run_copy
from librarysync.ledger import (
    iter_files,
    is_reportable_relative_path,
    ledger_dir,
    load_meta,
    load_records,
    load_snapshots,
    manifest_path,
    path_key,
    read_drive_info,
    scan_music_root,
)
from librarysync.reporting import render_report


def write_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class LibrarySyncTests(unittest.TestCase):
    def test_main_dispatches_report_with_artist_levels(self) -> None:
        calls = []
        original_render_report = cli.render_report

        def fake_render_report(cache_dir, artist_level_overrides):
            calls.append((cache_dir, artist_level_overrides))
            return "report text"

        output = io.StringIO()
        try:
            cli.render_report = fake_render_report
            with contextlib.redirect_stdout(output):
                result = cli.main(
                    [
                        "report",
                        "--cache",
                        "cache-dir",
                        "--artist-level",
                        "F/Frank Zappa",
                    ]
                )
        finally:
            cli.render_report = original_render_report

        self.assertEqual(result, 0)
        self.assertEqual(calls, [("cache-dir", {"F/Frank Zappa"})])
        self.assertEqual(output.getvalue(), "report text\n")

    def test_scan_main_prepends_scan_command(self) -> None:
        calls = []
        original_scan_music_root = cli.scan_music_root

        def fake_scan_music_root(root, role=None, cache_dir=None):
            calls.append((root, role, cache_dir))
            return SimpleNamespace(
                role=role,
                file_count=2,
                reportable_count=1,
                last_scan_at="2026-07-19T12:00:00Z",
            )

        output = io.StringIO()
        try:
            cli.scan_music_root = fake_scan_music_root
            with contextlib.redirect_stdout(output):
                result = cli.scan_main(
                    [
                        "--root",
                        "Music",
                        "--role",
                        "laptop",
                        "--cache",
                        "cache-dir",
                    ]
                )
        finally:
            cli.scan_music_root = original_scan_music_root

        self.assertEqual(result, 0)
        self.assertEqual(calls, [("Music", "laptop", "cache-dir")])
        self.assertIn(
            "Scanned role=laptop files=2 reportable=1 at 2026-07-19T12:00:00Z",
            output.getvalue(),
        )

    def test_copy_main_propagates_copy_options(self) -> None:
        calls = []
        original_run_copy = cli.run_copy

        def fake_run_copy(options):
            calls.append(options)
            return 7

        try:
            cli.run_copy = fake_run_copy
            result = cli.copy_main(
                [
                    "--source",
                    "Laptop/Music",
                    "--target",
                    "Phone/Music",
                    "--source-role",
                    "laptop",
                    "--target-role",
                    "phone",
                    "--cache",
                    "cache-dir",
                    "--execute",
                    "--yes",
                    "--replace-conflicts",
                    "--artist-level",
                    "F/Frank Zappa",
                ]
            )
        finally:
            cli.run_copy = original_run_copy

        self.assertEqual(result, 7)
        self.assertEqual(
            calls,
            [
                CopyOptions(
                    source="Laptop/Music",
                    target="Phone/Music",
                    source_role="laptop",
                    target_role="phone",
                    cache_dir="cache-dir",
                    execute=True,
                    yes=True,
                    replace_conflicts=True,
                    artist_level={"F/Frank Zappa"},
                )
            ],
        )

    def test_copy_help_describes_execute_scoped_options(self) -> None:
        output = io.StringIO()

        with self.assertRaises(SystemExit) as context:
            with contextlib.redirect_stdout(output):
                cli.main(["copy", "--help"])

        self.assertEqual(context.exception.code, 0)
        self.assertIn("With --execute, accept every copy group.", output.getvalue())
        self.assertIn("Treat conflicts as replacements; only writes with", output.getvalue())
        self.assertIn("--execute.", output.getvalue())

    def test_main_reports_missing_subcommand_error(self) -> None:
        stderr = io.StringIO()

        with self.assertRaises(SystemExit) as context:
            with contextlib.redirect_stderr(stderr):
                cli.main([])

        self.assertEqual(context.exception.code, 2)
        self.assertIn("required: command", stderr.getvalue())

    def test_scan_uses_role_as_drive_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Laptop" / "Music"
            cache = Path(temp) / "cache"
            root.mkdir(parents=True)

            info = scan_music_root(root, role="laptop", cache_dir=cache)
            drive_info = read_drive_info(root)

            self.assertEqual(info.label, "laptop")
            self.assertEqual(drive_info.label, "laptop")

    def test_scan_uses_unique_temp_manifest_without_touching_legacy_temp_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Laptop" / "Music"
            cache = Path(temp) / "cache"
            write_file(root / "A" / "Artist" / "Album" / "Song.mp3", b"audio")
            ledger_dir(root).mkdir(parents=True)
            legacy_temp = ledger_dir(root) / "manifest.sqlite.tmp"
            legacy_temp.write_text("other scan temp", encoding="utf-8")

            scan_music_root(root, role="laptop", cache_dir=cache)

            self.assertTrue(legacy_temp.exists())
            self.assertTrue(manifest_path(root).exists())
            self.assertEqual(list(ledger_dir(root).glob("manifest.*.sqlite.tmp")), [])

    def test_scan_writes_schema_version_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Laptop" / "Music"
            cache = Path(temp) / "cache"
            root.mkdir(parents=True)

            scan_music_root(root, role="laptop", cache_dir=cache)
            meta = load_meta(manifest_path(root))

            self.assertEqual(meta["schema_version"], "1")
            conn = sqlite3.connect(manifest_path(root))
            try:
                user_version = conn.execute("PRAGMA user_version").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(user_version, 1)

    def test_path_key_normalizes_unicode_and_case(self) -> None:
        composed = "E/\u00c9lodie/song.mp3"
        decomposed = "E/E\u0301lodie/SONG.MP3"

        self.assertEqual(path_key(composed), path_key(decomposed))
        self.assertEqual(path_key(composed), "e/\u00e9lodie/song.mp3")

    def test_iter_files_uses_deterministic_order_and_skips_ledger_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Music"
            write_file(root / "b" / "Two.mp3", b"two")
            write_file(root / "A" / "two.mp3", b"two")
            write_file(root / "A" / "One.mp3", b"one")
            write_file(root / ".music-ledger" / "ignored.mp3", b"ignored")

            relative_paths = [path.relative_to(root).as_posix() for path in iter_files(root)]

            self.assertEqual(relative_paths, ["A/One.mp3", "A/two.mp3", "b/Two.mp3"])

    def test_scan_skips_path_key_collisions_with_warning(self) -> None:
        class FakePath:
            def __init__(self, relative_path: str) -> None:
                self.relative_path = relative_path

            def relative_to(self, _root: Path) -> PurePosixPath:
                return PurePosixPath(self.relative_path)

            def stat(self) -> SimpleNamespace:
                return SimpleNamespace(st_size=5, st_mtime_ns=1_000_000_000)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Laptop" / "Music"
            cache = Path(temp) / "cache"
            root.mkdir(parents=True)
            fake_files = [
                FakePath("A/Artist/Album/Song.mp3"),
                FakePath("a/artist/album/song.mp3"),
            ]
            original_iter_files = ledger.iter_files
            stderr = io.StringIO()
            try:
                ledger.iter_files = lambda _root: fake_files
                with contextlib.redirect_stderr(stderr):
                    info = scan_music_root(root, role="laptop", cache_dir=cache)
            finally:
                ledger.iter_files = original_iter_files

            records = load_records(manifest_path(root))
            self.assertEqual(info.file_count, 1)
            self.assertEqual(info.reportable_count, 1)
            self.assertEqual(
                [record.relative_path for record in records.values()],
                ["A/Artist/Album/Song.mp3"],
            )
            self.assertIn("Warning: skipped path collision", stderr.getvalue())

    def test_load_snapshots_skips_bad_cached_drive_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Laptop" / "Music"
            cache = Path(temp) / "cache"
            root.mkdir(parents=True)
            scan_music_root(root, role="laptop", cache_dir=cache)
            bad_snapshot = cache / "bad"
            bad_snapshot.mkdir()
            (bad_snapshot / "drive.json").write_text("{not json", encoding="utf-8")
            (bad_snapshot / "manifest.sqlite").write_bytes(b"placeholder")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                snapshots = load_snapshots(cache)

            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0].drive.role, "laptop")
            self.assertIn("Warning: skipped cached snapshot", stderr.getvalue())

    def test_report_shows_total_and_reportable_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Laptop" / "Music"
            cache = Path(temp) / "cache"
            write_file(root / "A" / "Artist" / "Album" / "Song.mp3", b"audio")
            write_file(root / "A" / "Artist" / "Album" / "cover.jpg", b"cover")

            scan_music_root(root, role="laptop", cache_dir=cache)
            report = render_report(str(cache))

            self.assertIn("laptop: scanned ", report)
            self.assertIn("files 2, reportable 1", report)

    def test_report_loads_each_role_manifest_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            laptop = Path(temp) / "Laptop" / "Music"
            phone = Path(temp) / "Phone" / "Music"
            cache = Path(temp) / "cache"
            write_file(laptop / "A" / "Artist" / "Album" / "Song.mp3", b"audio")
            write_file(phone / "A" / "Artist" / "Album" / "Song.mp3", b"audio")
            scan_music_root(laptop, role="laptop", cache_dir=cache)
            scan_music_root(phone, role="phone", cache_dir=cache)

            original_load_records = reporting.load_records
            load_count = 0

            def counting_load_records(*args, **kwargs):
                nonlocal load_count
                load_count += 1
                return original_load_records(*args, **kwargs)

            try:
                reporting.load_records = counting_load_records
                render_report(str(cache))
            finally:
                reporting.load_records = original_load_records

            self.assertEqual(load_count, 2)

    def test_report_groups_against_all_target_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            laptop = Path(temp) / "Laptop" / "Music"
            archive = Path(temp) / "Archive" / "Music"
            cache = Path(temp) / "cache"
            write_file(laptop / "A" / "Artist" / "New Album" / "Song.mp3", b"audio")
            write_file(archive / "A" / "Artist" / "Existing Album" / "cover.jpg", b"cover")

            scan_music_root(laptop, role="laptop", cache_dir=cache)
            scan_music_root(archive, role="archive", cache_dir=cache)
            report = render_report(str(cache))

            self.assertIn("[album] A/Artist/New Album", report)
            self.assertNotIn("[artist] A/Artist", report)

    def test_report_artist_level_overrides_normalize_path_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            laptop = Path(temp) / "Laptop" / "Music"
            archive = Path(temp) / "Archive" / "Music"
            cache = Path(temp) / "cache"
            write_file(laptop / "F" / "Frank Zappa" / "1970s" / "Album" / "Song.mp3", b"new")
            write_file(archive / "F" / "Frank Zappa" / "1960s" / "Old" / "Song.mp3", b"old")

            scan_music_root(laptop, role="laptop", cache_dir=cache)
            scan_music_root(archive, role="archive", cache_dir=cache)
            report = render_report(str(cache), {"F//Frank Zappa/"})

            self.assertIn("[artist] F/Frank Zappa", report)
            self.assertNotIn("[album] F/Frank Zappa/1970s", report)

    def test_report_skips_pairs_with_unreadable_cached_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            laptop = Path(temp) / "Laptop" / "Music"
            archive = Path(temp) / "Archive" / "Music"
            cache = Path(temp) / "cache"
            write_file(laptop / "A" / "Artist" / "Album" / "Song.mp3", b"audio")
            archive.mkdir(parents=True)
            scan_music_root(laptop, role="laptop", cache_dir=cache)
            scan_music_root(archive, role="archive", cache_dir=cache)
            for child in cache.iterdir():
                if (child / "drive.json").read_text(encoding="utf-8").find('"role": "laptop"') >= 0:
                    (child / "manifest.sqlite").write_text("not a sqlite database", encoding="utf-8")

            report = render_report(str(cache))

            self.assertIn("Laptop -> archive:", report)
            self.assertIn("skipped: unreadable laptop manifest", report)
            self.assertIn("Phone-only reportable files:", report)
            self.assertIn("skipped: unreadable laptop manifest and missing phone scan", report)

    def test_report_lists_unknown_cached_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Unknown" / "Music"
            cache = Path(temp) / "cache"
            write_file(root / "A" / "Artist" / "Album" / "Song.mp3", b"audio")

            scan_music_root(root, cache_dir=cache)
            report = render_report(str(cache))

            self.assertIn("unknown: scanned ", report)
            self.assertIn("files 1, reportable 1", report)

    def test_scan_records_all_files_but_reportable_filter_is_music_plus_playlist_cue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Laptop" / "Music"
            cache = Path(temp) / "cache"
            write_file(root / "D" / "David Bowie" / "1977 - Low" / "Speed of Life.flac", b"audio")
            write_file(root / "D" / "David Bowie" / "1977 - Low" / "cover.jpg", b"cover")
            write_file(root / "Playlists" / "Bowie.cue", b"cue")
            write_file(root / "D" / "David Bowie" / "1977 - Low" / "album.cue", b"cue")

            scan_music_root(root, role="laptop", cache_dir=cache)
            all_records = load_records(manifest_path(root))
            reportable_records = load_records(manifest_path(root), reportable_only=True)

            self.assertEqual(len(all_records), 4)
            self.assertEqual(
                sorted(record.relative_path for record in reportable_records.values()),
                [
                    "D/David Bowie/1977 - Low/Speed of Life.flac",
                    "Playlists/Bowie.cue",
                ],
            )
            self.assertTrue(is_reportable_relative_path("A/Artist/Album/song.mp3"))
            self.assertFalse(is_reportable_relative_path("A/Artist/Album/album.cue"))

    def test_copy_copies_whole_selected_folder_without_empty_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "Laptop" / "Music"
            target = Path(temp) / "Phone" / "Music"
            cache = Path(temp) / "cache"
            write_file(source / "D" / "David Bowie" / "1977 - Low" / "Speed of Life.flac", b"audio")
            write_file(source / "D" / "David Bowie" / "1977 - Low" / "cover.jpg", b"cover")
            write_file(source / "D" / "David Bowie" / "1977 - Low" / "notes.txt", b"notes")
            (source / "D" / "David Bowie" / "1977 - Low" / "Empty").mkdir(parents=True)
            target.mkdir(parents=True)

            result = run_copy(
                CopyOptions(
                    source=str(source),
                    target=str(target),
                    source_role="laptop",
                    target_role="phone",
                    cache_dir=str(cache),
                    execute=True,
                    yes=True,
                )
            )

            self.assertEqual(result, 0)
            self.assertTrue((target / "D" / "David Bowie" / "1977 - Low" / "Speed of Life.flac").exists())
            self.assertTrue((target / "D" / "David Bowie" / "1977 - Low" / "cover.jpg").exists())
            self.assertTrue((target / "D" / "David Bowie" / "1977 - Low" / "notes.txt").exists())
            self.assertFalse((target / "D" / "David Bowie" / "1977 - Low" / "Empty").exists())

    def test_conflicts_are_reported_and_replaced_only_when_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "Laptop" / "Music"
            target = Path(temp) / "Archive" / "Music"
            cache = Path(temp) / "cache"
            song = Path("D") / "David Bowie" / "1977 - Low" / "Speed of Life.flac"
            write_file(source / song, b"new audio")
            write_file(target / song, b"old audio")
            old_mtime = time.time() - 100
            os.utime(target / song, (old_mtime, old_mtime))

            scan_music_root(source, role="laptop", cache_dir=cache)
            scan_music_root(target, role="archive", cache_dir=cache)
            report = render_report(str(cache))
            self.assertIn("conflicts: 1", report)

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                run_copy(
                    CopyOptions(
                        source=str(source),
                        target=str(target),
                        source_role="laptop",
                        target_role="archive",
                        cache_dir=str(cache),
                        execute=True,
                        yes=True,
                        replace_conflicts=False,
                    )
                )
            self.assertEqual((target / song).read_bytes(), b"old audio")
            self.assertIn(
                "Done. Copied 0 files; skipped 0; conflicts 1; missing 0.",
                output.getvalue(),
            )

            run_copy(
                CopyOptions(
                    source=str(source),
                    target=str(target),
                    source_role="laptop",
                    target_role="archive",
                    cache_dir=str(cache),
                    execute=True,
                    yes=True,
                    replace_conflicts=True,
                )
            )
            self.assertEqual((target / song).read_bytes(), b"new audio")

    def test_copy_plans_support_only_changes_while_report_stays_reportable_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "Laptop" / "Music"
            target = Path(temp) / "Archive" / "Music"
            cache = Path(temp) / "cache"
            song = Path("D") / "David Bowie" / "1977 - Low" / "Speed of Life.flac"
            cover = Path("D") / "David Bowie" / "1977 - Low" / "cover.jpg"
            write_file(source / song, b"audio")
            (target / song).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / song, target / song)
            write_file(source / cover, b"new cover")
            write_file(target / cover, b"old cover")
            old_mtime = time.time() - 100
            os.utime(target / cover, (old_mtime, old_mtime))

            scan_music_root(source, role="laptop", cache_dir=cache)
            scan_music_root(target, role="archive", cache_dir=cache)
            report = render_report(str(cache))
            self.assertIn("add candidates: 0 files", report)
            self.assertIn("conflicts: 0", report)

            result = run_copy(
                CopyOptions(
                    source=str(source),
                    target=str(target),
                    source_role="laptop",
                    target_role="archive",
                    cache_dir=str(cache),
                    execute=True,
                    yes=True,
                    replace_conflicts=True,
                )
            )

            self.assertEqual(result, 0)
            self.assertEqual((target / cover).read_bytes(), b"new cover")

    def test_build_file_actions_reports_source_files_missing_after_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "Laptop" / "Music"
            target = Path(temp) / "Archive" / "Music"
            cache = Path(temp) / "cache"
            song = Path("D") / "David Bowie" / "1977 - Low" / "Speed of Life.flac"
            write_file(source / song, b"audio")
            target.mkdir(parents=True)

            scan_music_root(source, role="laptop", cache_dir=cache)
            scan_music_root(target, role="archive", cache_dir=cache)
            source_records = load_records(manifest_path(source))
            target_records = load_records(manifest_path(target))
            chunks = plan_chunks(source_records, target_records)
            (source / song).unlink()

            actions = build_file_actions(source, target, chunks[0], replace_conflicts=False)

            self.assertEqual(
                [(action.relative_path, action.status) for action in actions],
                [("D/David Bowie/1977 - Low/Speed of Life.flac", "missing")],
            )

    def test_artist_level_overrides_group_exceptional_layouts_by_artist(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "Laptop" / "Music"
            target = Path(temp) / "Archive" / "Music"
            cache = Path(temp) / "cache"
            write_file(
                source / "F" / "Frank Zappa" / "1970s" / "1973 - Over-Nite Sensation" / "Camarillo Brillo.mp3",
                b"zappa new",
            )
            write_file(
                target / "F" / "Frank Zappa" / "1960s" / "1966 - Freak Out!" / "Who Are the Brain Police.mp3",
                b"zappa old",
            )
            write_file(
                source / "B" / "Beethoven" / "Symphonies" / "Symphony 1" / "01 - Adagio molto.wav",
                b"beethoven new",
            )
            write_file(
                target / "B" / "Beethoven" / "Symphonies" / "Symphony 5" / "01 - Allegro con brio.wav",
                b"beethoven old",
            )
            write_file(source / "C" / "Chopin" / "Nocturne Op. 9 No. 2.wav", b"chopin flat")
            write_file(target / "C" / "Chopin" / "Mazurkas" / "Mazurka Op. 7 No. 1.m4a", b"chopin old")

            scan_music_root(source, role="laptop", cache_dir=cache)
            scan_music_root(target, role="archive", cache_dir=cache)
            source_records = load_records(manifest_path(source), reportable_only=True)
            target_records = load_records(manifest_path(target), reportable_only=True)

            chunks = plan_chunks(
                source_records,
                target_records,
                {"F/Frank Zappa", "B/Beethoven", "C/Chopin"},
            )
            report = render_report(
                str(cache),
                {"F/Frank Zappa", "B/Beethoven", "C/Chopin"},
            )

            self.assertEqual(
                [(chunk.kind, chunk.relative_path) for chunk in chunks],
                [
                    ("artist", "B/Beethoven"),
                    ("artist", "C/Chopin"),
                    ("artist", "F/Frank Zappa"),
                ],
            )
            self.assertIn("[artist] F/Frank Zappa", report)
            self.assertNotIn("[album] F/Frank Zappa/1970s", report)


if __name__ == "__main__":
    unittest.main()
