"""jellyfin-organizer: naming rules and the promise that nothing is lost or overwritten."""

import json

import jellyfin_organizer
import pytest
from conftest import tree

RELEASE = "[Grp] My Show"


@pytest.fixture
def download(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOW_OUTPUT_DIR", str(tmp_path / "downloads"))
    root = tmp_path / "My Show"
    files = {
        f"{RELEASE} S01/{RELEASE} S01E01 BDRIP 1080p X265 10bit.mkv": b"e1",
        f"{RELEASE} S01/{RELEASE} S01E01 BDRIP 1080p X265 10bit.en.srt": b"sub1",
        f"{RELEASE} S01/{RELEASE} S01E02 BDRIP 1080p X265 10bit.mkv": b"e2",
        f"{RELEASE} S01/{RELEASE} S01E76 BDRIP 720p.mkv": b"special",
        f"{RELEASE} S01/{RELEASE} Champion Road.mkv": b"movie",
        f"{RELEASE} S02/{RELEASE} S02E01 WEBRIP.mkv": b"s2e1",
        f"{RELEASE} S02/notes.txt": b"keep me",
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return root


def plan(root, *extra):
    args = ["plan", str(root), "--series", "My Show", *extra]
    assert jellyfin_organizer.main(args) == 0
    return root.parent / "downloads" / f"{root.name} rename" / "plan.json"


FULL = (
    "--special",
    "S01E76=S00E01:The Special",
    "--movie",
    "Champion Road=My Show - Champion Road|2003",
)


@pytest.mark.parametrize(
    "stem, season, expected",
    [
        ("[Grp] Show S01E02 1080p X265 10bit", None, (1, 2, None)),
        ("show.s03e11.720p", None, (3, 11, None)),
        ("Show 2x05", None, (2, 5, None)),
        ("Show S01E01-E02", None, (1, 1, 2)),
        ("[Grp] Show - 07 [1080p][x264]", 1, (1, 7, None)),
        ("[Grp] Show - 07 [1080p]", None, None),
        ("Mob Psycho 100 - 03 [1080p]", 1, (1, 3, None)),
        ("Show 2 - 05 - Chapter 9", 1, (1, 5, None)),
        ("Show_07_[1080p]", 2, (2, 7, None)),
        ("Champion Road 1080p", None, None),
    ],
)
def test_parse_episode(stem, season, expected):
    assert jellyfin_organizer.parse_episode(stem, season) == expected


def test_safe_name():
    assert jellyfin_organizer.safe_name("Show: The Movie?") == "Show - The Movie"
    assert jellyfin_organizer.safe_name("A/B  C.") == "A-B C"


def test_plan_names_series_without_year_and_movies_with_year(download):
    moves = {m["from"]: m for m in json.loads(plan(download, *FULL).read_text())["moves"]}
    s1 = f"{RELEASE} S01/{RELEASE}"
    show = "Shows/My Show"
    assert (
        moves[f"{s1} S01E01 BDRIP 1080p X265 10bit.mkv"]["to"]
        == f"{show}/Season 01/My Show - S01E01.mkv"
    )
    assert (
        moves[f"{s1} S01E01 BDRIP 1080p X265 10bit.en.srt"]["to"]
        == f"{show}/Season 01/My Show - S01E01.en.srt"
    )
    assert moves[f"{s1} S01E76 BDRIP 720p.mkv"] == {
        "from": f"{s1} S01E76 BDRIP 720p.mkv",
        "to": f"{show}/Season 00/My Show - S00E01 - The Special.mkv",
        "kind": "special",
    }
    movie = "My Show - Champion Road (2003)"
    assert moves[f"{s1} Champion Road.mkv"]["to"] == f"Movies/{movie}/{movie}.mkv"
    assert f"{RELEASE} S02/notes.txt" not in moves


def test_plan_reports_unmatched_videos_and_leaves_them(download, capsys):
    data = json.loads(plan(download).read_text())
    assert data["unmatched"] == [f"{RELEASE} S01/{RELEASE} Champion Road.mkv"]
    assert "UNMATCHED" in capsys.readouterr().out


def test_plan_and_dry_run_change_nothing(download):
    before = tree(download)
    plan_path = plan(download, *FULL)
    assert jellyfin_organizer.main(["apply", str(plan_path)]) == 0
    assert tree(download) == before
    assert not plan_path.with_name("rename-log.json").exists()


def test_apply_moves_every_file_and_undo_restores_the_folder(download, capsys):
    before = tree(download)
    plan_path = plan(download, *FULL)
    capsys.readouterr()
    assert jellyfin_organizer.main(["apply", str(plan_path), "--apply"]) == 0
    assert "FINISHED My Show - S02E01.mkv" in capsys.readouterr().out
    after = tree(download)
    assert sorted(after.values()) == sorted(before.values())  # nothing lost, nothing duplicated
    assert after["Shows/My Show/Season 02/My Show - S02E01.mkv"] == b"s2e1"
    assert after[f"{RELEASE} S02/notes.txt"] == b"keep me"
    assert not (download / f"{RELEASE} S01").exists()  # emptied folders are removed
    log = plan_path.with_name("rename-log.json")
    assert jellyfin_organizer.main(["undo", str(log)]) == 0
    assert tree(download) == after  # undo is a dry run too
    assert jellyfin_organizer.main(["undo", str(log), "--apply"]) == 0
    assert tree(download) == before


def test_apply_refuses_to_overwrite(download, capsys):
    plan_path = plan(download, *FULL)
    target = download / "Shows/My Show/Season 01/My Show - S01E02.mkv"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"already here")
    before = tree(download)
    assert jellyfin_organizer.main(["apply", str(plan_path), "--apply"]) == 1
    assert "already exists" in capsys.readouterr().out
    assert tree(download) == before


def test_two_files_cannot_take_the_same_name(download, capsys):
    twin = download / f"{RELEASE} S01" / "My.Show.S01E02.repack.mkv"
    twin.write_bytes(b"dup")
    before = tree(download)
    plan_path = plan(download, *FULL)
    assert jellyfin_organizer.main(["apply", str(plan_path), "--apply"]) == 1
    assert "two files would become" in capsys.readouterr().out
    assert tree(download) == before


def test_a_sidecar_belongs_to_one_video_only(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOW_OUTPUT_DIR", str(tmp_path / "downloads"))
    root = tmp_path / "Films"
    root.mkdir()
    for name in ("Movie.mkv", "Movie.Extended.mkv", "Movie.Extended.en.srt", "Movie.en.srt"):
        (root / name).write_bytes(name.encode())
    before = tree(root)
    args = ["plan", str(root), "--series", "x", "--movie", "Movie.Extended=Movie Extended|2001"]
    assert jellyfin_organizer.main([*args, "--movie", "Movie=Movie|2001"]) == 0
    plan_path = tmp_path / "downloads" / "Films rename" / "plan.json"
    sources = [m["from"] for m in json.loads(plan_path.read_text())["moves"]]
    assert sorted(sources) == sorted(before)
    assert jellyfin_organizer.main(["apply", str(plan_path), "--apply"]) == 0
    after = tree(root)
    extended = "Movies/Movie Extended (2001)/Movie Extended (2001)"
    assert after[extended + ".en.srt"] == b"Movie.Extended.en.srt"
    assert after["Movies/Movie (2001)/Movie (2001).en.srt"] == b"Movie.en.srt"


def test_a_plan_that_moves_one_file_twice_is_refused(download, capsys):
    plan_path = plan(download, *FULL)
    data = json.loads(plan_path.read_text())
    data["moves"].append({**data["moves"][0], "to": "Shows/elsewhere.mkv"})
    plan_path.write_text(json.dumps(data))
    before = tree(download)
    assert jellyfin_organizer.main(["apply", str(plan_path), "--apply"]) == 1
    assert "listed twice" in capsys.readouterr().out
    assert tree(download) == before


def test_a_plan_is_applied_only_once(download):
    plan_path = plan(download, *FULL)
    jellyfin_organizer.main(["apply", str(plan_path), "--apply"])
    with pytest.raises(SystemExit, match="already exists"):
        jellyfin_organizer.main(["apply", str(plan_path), "--apply"])


def test_moves_cannot_leave_the_folder(download, capsys):
    plan_path = plan(download, *FULL)
    data = json.loads(plan_path.read_text())
    data["moves"][0]["to"] = "../escaped.mkv"
    plan_path.write_text(json.dumps(data))
    before = tree(download)
    assert jellyfin_organizer.main(["apply", str(plan_path), "--apply"]) == 1
    assert "outside the folder" in capsys.readouterr().out
    assert tree(download) == before


def test_already_renamed_folder_needs_no_moves(download, capsys):
    jellyfin_organizer.main(["apply", str(plan(download, *FULL)), "--apply"])
    capsys.readouterr()
    again = download.parent / "again"
    plan(download, *FULL, "--output", str(again))
    data = json.loads((again / "plan.json").read_text())
    assert data["moves"] == []
    assert data["unmatched"] == []
