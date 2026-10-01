"""library-move on local folders: nothing is lost, nothing is overwritten, trash only after verify."""

import json
import os
import shutil
import subprocess

import library_move
import pytest
from conftest import tree

pytestmark = pytest.mark.skipif(not shutil.which("rsync"), reason="needs rsync")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    prepared = tmp_path / "My Show download"
    files = {
        "Shows/My Show/Season 01/My Show - S01E01.mkv": b"episode one",
        "Shows/My Show/Season 01/My Show - S01E01.en.srt": b"subs",
        "Shows/My Show/Season 02/My Show - S02E01.mkv": b"episode two",
        "Movies/My Movie (2003)/My Movie (2003).mkv": b"the movie",
    }
    for name, content in files.items():
        path = prepared / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    library = tmp_path / "library"
    (library / "TV Shows").mkdir(parents=True)
    (library / "Movies").mkdir()
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "host": "",
                "shows_dir": str(library / "TV Shows"),
                "movies_dir": str(library / "Movies"),
            }
        )
    )
    trashed = []

    def fake_trash(path):
        trashed.append(path)
        shutil.move(str(path), str(tmp_path / f"trash-{len(trashed)}"))
        return True

    monkeypatch.setattr(library_move, "trash", fake_trash)
    return prepared, library, config, trashed


def run(prepared, config, *extra):
    return library_move.main([str(prepared), "--config", str(config), *extra])


def test_dry_run_changes_nothing(setup, capsys):
    prepared, library, config, trashed = setup
    before = tree(prepared)
    assert run(prepared, config) == 0
    assert tree(prepared) == before and tree(library) == {} and trashed == []
    out = capsys.readouterr().out
    assert "My Show: 3 files" in out and "DRY RUN" in out


def test_apply_copies_verifies_then_trashes(setup, capsys):
    prepared, library, config, trashed = setup
    before = tree(prepared)
    assert run(prepared, config, "--apply") == 0
    assert tree(library) == {
        name.replace("Shows/", "TV Shows/", 1): content for name, content in before.items()
    }
    assert [p.name for p in trashed] == ["My Show", "My Movie (2003)"]
    assert not (prepared / "Shows").exists() and not (prepared / "Movies").exists()
    assert "verified (checksum): My Show" in capsys.readouterr().out


def test_keep_local(setup):
    prepared, library, config, trashed = setup
    before = tree(prepared)
    assert run(prepared, config, "--apply", "--keep-local") == 0
    assert tree(prepared) == before and trashed == []
    assert len(tree(library)) == 4


def test_a_different_file_in_the_library_is_never_overwritten(setup, capsys):
    prepared, library, config, trashed = setup
    existing = library / "TV Shows/My Show/Season 01/My Show - S01E01.mkv"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"what was already in the library")
    before = tree(prepared)
    assert run(prepared, config, "--apply") == 1
    assert existing.read_bytes() == b"what was already in the library"
    assert tree(prepared) == before and trashed == []
    assert len(tree(library)) == 1  # nothing else was copied either
    assert "ALREADY THERE AND DIFFERENT" in capsys.readouterr().out


def test_adding_a_season_to_a_show_already_in_the_library(setup):
    prepared, library, config, _ = setup
    assert run(prepared, config, "--apply", "--keep-local") == 0
    new = prepared / "Shows/My Show/Season 03/My Show - S03E01.mkv"
    new.parent.mkdir()
    new.write_bytes(b"new season")
    assert run(prepared, config, "--apply") == 0
    assert (
        library / "TV Shows/My Show/Season 03/My Show - S03E01.mkv"
    ).read_bytes() == b"new season"
    assert (
        library / "TV Shows/My Show/Season 01/My Show - S01E01.mkv"
    ).read_bytes() == b"episode one"


def test_local_copies_stay_when_verification_fails(setup, monkeypatch, capsys):
    prepared, library, config, trashed = setup
    real_copy = library_move.copy

    def copy_then_corrupt(cfg, item, dest):
        real_copy(cfg, item, dest)
        for p in (library / "Movies").rglob("*.mkv"):
            p.write_bytes(b"the m0vie")  # same size, other content

    monkeypatch.setattr(library_move, "copy", copy_then_corrupt)
    before = tree(prepared)
    assert run(prepared, config, "--apply") == 1
    assert tree(prepared) == before and trashed == []
    assert "NOT VERIFIED My Movie (2003)" in capsys.readouterr().out


def test_single_folder_as_movie(setup):
    prepared, library, config, trashed = setup
    movie = prepared / "Movies/My Movie (2003)"
    assert run(movie, config, "--as", "movie", "--apply") == 0
    assert (library / "Movies/My Movie (2003)/My Movie (2003).mkv").read_bytes() == b"the movie"
    assert [p.name for p in trashed] == ["My Movie (2003)"]


def test_config_must_exist_and_be_filled(setup, tmp_path):
    prepared, _, _, _ = setup
    with pytest.raises(SystemExit, match="No config"):
        run(prepared, tmp_path / "missing.json")
    example = library_move.SKILL / "config.example.json"
    with pytest.raises(SystemExit, match="placeholder"):
        run(prepared, example)


def test_folder_without_shows_or_movies_is_refused(setup, tmp_path):
    _, _, config, _ = setup
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit, match="no Shows/ or Movies/"):
        run(empty, config)


def test_no_trash_tool_keeps_local_copies(setup, monkeypatch, capsys):
    prepared, _, config, _ = setup
    monkeypatch.setattr(library_move, "trash", lambda path: False)
    before = tree(prepared)
    assert run(prepared, config, "--apply") == 0
    assert tree(prepared) == before
    assert "local copies kept" in capsys.readouterr().out


def test_a_failing_copy_stops_without_trashing(setup, tmp_path, capsys):
    prepared, _, _, trashed = setup
    config = tmp_path / "broken.json"
    missing = str(tmp_path / "no" / "such" / "library")
    config.write_text(json.dumps({"host": "", "shows_dir": missing, "movies_dir": missing}))
    before = tree(prepared)
    assert run(prepared, config, "--apply") == 1
    assert tree(prepared) == before and trashed == []
    assert "STOPPED" in capsys.readouterr().out


def test_same_content_with_another_timestamp_is_not_a_conflict(setup):
    prepared, library, config, trashed = setup
    existing = library / "TV Shows/My Show/Season 01/My Show - S01E01.mkv"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"episode one")
    os.utime(existing, (1_000_000_000, 1_000_000_000))
    assert run(prepared, config, "--apply") == 0
    assert existing.read_bytes() == b"episode one"
    assert len(tree(library)) == 4 and len(trashed) == 2


def test_a_failure_while_trashing_says_what_was_already_trashed(setup, monkeypatch, capsys):
    prepared, _, config, _ = setup
    calls = []

    def second_one_fails(path):
        calls.append(path.name)
        if len(calls) == 2:
            raise subprocess.CalledProcessError(1, ["gio", "trash"])
        return True

    monkeypatch.setattr(library_move, "trash", second_one_fails)
    assert run(prepared, config, "--apply") == 1
    out = capsys.readouterr().out
    assert "Nothing was removed locally" not in out
    assert "already in the trash: My Show" in out
    assert "still in place: My Movie (2003)" in out
