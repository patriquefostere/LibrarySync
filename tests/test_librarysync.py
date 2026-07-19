from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from librarysync.copying import CopyOptions, plan_chunks, run_copy
from librarysync.ledger import (
    is_reportable_relative_path,
    load_records,
    manifest_path,
    read_drive_info,
    scan_music_root,
)
from librarysync.reporting import render_report


def write_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class LibrarySyncTests(unittest.TestCase):
    def test_scan_uses_role_as_drive_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Laptop" / "Music"
            cache = Path(temp) / "cache"
            root.mkdir(parents=True)

            info = scan_music_root(root, role="laptop", cache_dir=cache)
            drive_info = read_drive_info(root)

            self.assertEqual(info.label, "laptop")
            self.assertEqual(drive_info.label, "laptop")

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
