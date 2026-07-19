from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def write_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def fake_audio(name: str) -> bytes:
    return f"FAKE AUDIO FILE FOR LIBRARYSYNC TESTS: {name}\n".encode("utf-8")


def create_roots(base: Path, force: bool = False) -> None:
    if base.exists():
        if not force:
            raise SystemExit(f"{base} already exists. Re-run with --force to recreate it.")
        shutil.rmtree(base)

    laptop = base / "Laptop" / "Music"
    archive = base / "Archive" / "Music"
    phone = base / "Phone" / "Music"
    backup = base / "Backup" / "Music"

    write_file(laptop / "D" / "David Bowie" / "1977 - Low" / "Speed of Life.flac", fake_audio("Speed of Life"))
    write_file(laptop / "D" / "David Bowie" / "1977 - Low" / "Breaking Glass.mp3", fake_audio("Breaking Glass"))
    write_file(laptop / "D" / "David Bowie" / "1977 - Low" / "cover.jpg", b"fake cover art\n")
    write_file(laptop / "D" / "David Bowie" / "1977 - Low" / "notes.txt", b"fake notes\n")
    write_file(laptop / "B" / "Boards of Canada" / "1998 - Music Has the Right to Children" / "Roygbiv.m4a", fake_audio("Roygbiv"))
    write_file(laptop / "Playlists" / "New stuff.cue", b'FILE "../D/David Bowie/1977 - Low/Speed of Life.flac" WAVE\n')

    write_file(laptop / "F" / "Frank Zappa" / "1960s" / "1969 - Hot Rats" / "Peaches en Regalia.mp3", fake_audio("Peaches en Regalia"))
    write_file(laptop / "F" / "Frank Zappa" / "1970s" / "1973 - Over-Nite Sensation" / "Camarillo Brillo.flac", fake_audio("Camarillo Brillo"))
    write_file(laptop / "B" / "Beethoven" / "Symphonies" / "Symphony 1" / "01 - Adagio molto.wav", fake_audio("Beethoven Symphony 1"))
    write_file(laptop / "B" / "Beethoven" / "String Quartets" / "String Quartet No. 14" / "01 - Adagio.mka", fake_audio("Beethoven Quartet 14"))
    write_file(laptop / "C" / "Chopin" / "Mazurkas" / "Mazurka Op. 17 No. 4.m4a", fake_audio("Chopin Mazurka"))
    write_file(laptop / "C" / "Chopin" / "Nocturne Op. 9 No. 2.wav", fake_audio("Chopin flat piece"))

    write_file(archive / "D" / "David Bowie" / "1977 - Low" / "Speed of Life.flac", fake_audio("older Speed of Life"))
    write_file(archive / "F" / "Frank Zappa" / "1960s" / "1966 - Freak Out!" / "Who Are the Brain Police.mp3", fake_audio("Who Are the Brain Police"))
    write_file(archive / "B" / "Beethoven" / "Symphonies" / "Symphony 5" / "01 - Allegro con brio.wav", fake_audio("Beethoven Symphony 5"))
    write_file(archive / "C" / "Chopin" / "Mazurkas" / "Mazurka Op. 7 No. 1.m4a", fake_audio("Chopin older Mazurka"))
    write_file(archive / "K" / "Kraftwerk" / "1978 - The Man-Machine" / "The Robots.mp3", fake_audio("The Robots"))

    write_file(phone / "D" / "David Bowie" / "1977 - Low" / "Speed of Life.flac", fake_audio("Speed of Life"))
    write_file(phone / "B" / "Beethoven" / "Symphonies" / "Symphony 5" / "01 - Allegro con brio.wav", fake_audio("Beethoven Symphony 5"))
    write_file(phone / "O" / "Old Phone Artist" / "2010 - Phone Only" / "Old Song.mp3", fake_audio("Old Song"))

    write_file(backup / "K" / "Kraftwerk" / "1978 - The Man-Machine" / "The Robots.mp3", fake_audio("The Robots"))

    print(f"Created fake Music roots under {base}")
    print("These files are not playable audio. They are path/copy/ledger fixtures.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base",
        default="manual-test-libraries",
        help="Output folder for fake roots. Default: manual-test-libraries",
    )
    parser.add_argument("--force", action="store_true", help="Delete and recreate output folder.")
    args = parser.parse_args()
    create_roots(Path(args.base), force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
