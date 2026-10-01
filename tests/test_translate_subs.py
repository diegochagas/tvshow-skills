"""subtitle-translate against a fake Ollama: what lands where, resuming, retries."""

import json
import os
import shutil
import subprocess

import pytest
import translate_subs
from conftest import make_ass, tree

FRENCH = [
    "Bonjour à tous",
    "{\\i1}Je ne sais pas{\\i0}",
    "{\\an8}SALLE DE BOXE",
    "Au revoir\\Nles amis",
]


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A season folder with two 'videos' whose subtitles are sidecar .fr.ass files."""
    monkeypatch.setenv("SHOW_OUTPUT_DIR", str(tmp_path / "downloads"))
    season = tmp_path / "library" / "Some Show" / "Season 01"
    season.mkdir(parents=True)
    for n in (1, 2):
        (season / f"Some Show - S01E0{n}.mkv").write_bytes(b"not a real video")
        (season / f"Some Show - S01E0{n}.fr.ass").write_text(make_ass(FRENCH), encoding="utf-8")
    return tmp_path / "library" / "Some Show"


def run(*argv):
    return translate_subs.main([str(a) for a in argv])


def test_writes_srt_next_to_each_video_and_work_files_in_downloads(library, ollama, tmp_path):
    before = tree(library)
    assert run(library, "--to", "en") == 0
    after = tree(library)
    new = sorted(set(after) - set(before))
    assert new == ["Season 01/Some Show - S01E01.en.srt", "Season 01/Some Show - S01E02.en.srt"]
    assert all(after[name] == content for name, content in before.items())  # sources untouched
    srt = after[new[0]].decode()
    assert "00:00:00,000 --> 00:00:02,500\nEN Bonjour à tous" in srt
    assert "<i>EN Je ne sais pas</i>" in srt
    assert "{\\an8}EN SALLE DE BOXE" in srt
    assert "EN Au revoir\nles amis" in srt
    work = tmp_path / "downloads" / "Some Show subtitles"
    assert (work / "src" / "Season 01__Some Show - S01E01.ass").exists()
    assert (work / "en" / "Season 01__Some Show - S01E01.srt").read_text() == srt


def test_prompt_names_languages_show_and_glossary(library, ollama, tmp_path):
    glossary = tmp_path / "glossary.txt"
    glossary.write_text("crochet = hook")
    run(library, "--to", "en", "--show", "a boxing anime", "--glossary", glossary, "--limit", "1")
    system = ollama.requests[0]["body"]["messages"][0]["content"]
    assert "French subtitles into English" in system
    assert "a boxing anime" in system and "crochet = hook" in system
    assert ollama.requests[0]["body"]["model"] == "qwen3:8b"


def test_second_run_asks_the_model_nothing(library, ollama):
    run(library)
    asked = len(ollama.requests)
    assert run(library) == 0
    assert len(ollama.requests) == asked


def test_resumes_a_half_translated_video(library, ollama, tmp_path):
    run(library, "--extract-only")
    assert ollama.requests == []
    tr = tmp_path / "downloads" / "Some Show subtitles" / "tr" / "en"
    tr.mkdir(parents=True, exist_ok=True)
    (tr / "Season 01__Some Show - S01E01.json").write_text(
        json.dumps({"1": "Hello", "2": "No idea"})
    )
    run(library, "--limit", "1")
    assert ollama.lines_asked() == [3, 4]
    srt = (library / "Season 01" / "Some Show - S01E01.en.srt").read_text()
    assert "Hello" in srt and "<i>No idea</i>" in srt and "EN Au revoir" in srt


def test_line_the_model_drops_is_asked_again(library, ollama):
    seen = []

    def flaky(i, text):
        seen.append(i)
        return None if i == 2 and seen.count(2) == 1 else "EN " + text

    ollama.translate = flaky
    run(library, "--limit", "1")
    assert seen.count(2) == 2
    assert (
        "<i>EN Je ne sais pas</i>"
        in (library / "Season 01" / "Some Show - S01E01.en.srt").read_text()
    )


def test_answer_with_a_real_line_break_keeps_both_rows(library, ollama):
    ollama.translate = lambda i, text: "EN " + text.replace("\\N", "\n")
    run(library, "--limit", "1")
    srt = (library / "Season 01" / "Some Show - S01E01.en.srt").read_text()
    assert "EN Au revoir\nles amis" in srt


def test_line_that_lost_its_second_row_is_asked_again(library, ollama):
    seen = []

    def cut_once(i, text):
        seen.append(i)
        return "Bye" if i == 4 and seen.count(4) == 1 else "EN " + text

    ollama.translate = cut_once
    run(library, "--limit", "1")
    assert seen.count(4) == 2
    assert (
        "EN Au revoir\nles amis"
        in (library / "Season 01" / "Some Show - S01E01.en.srt").read_text()
    )


def test_extra_rows_after_an_answer_are_not_glued_on(library, ollama):
    ollama.translate = lambda i, text: (
        "EN " + text + ("\nNote: translated freely." if i == 1 else "")
    )
    run(library, "--limit", "1")
    assert "Note" not in (library / "Season 01" / "Some Show - S01E01.en.srt").read_text()


def test_line_that_never_translates_keeps_its_source_text(library, ollama, capsys):
    ollama.translate = lambda i, text: None if i == 1 else "EN " + text
    run(library, "--limit", "1")
    srt = (library / "Season 01" / "Some Show - S01E01.en.srt").read_text()
    assert "\nBonjour à tous\n" in srt  # never dropped, timing kept
    assert "1 untranslated" in capsys.readouterr().out


def test_nothing_is_installed_when_the_model_translates_nothing(library, ollama, capsys):
    ollama.translate = lambda i, text: None
    run(library)
    assert not list(library.rglob("*.en.srt"))
    assert "NOT INSTALLED" in capsys.readouterr().out
    ollama.translate = lambda i, text: "EN " + text  # the next run starts those lines again
    ollama.requests.clear()
    run(library, "--limit", "1")
    assert sorted(set(ollama.lines_asked())) == [1, 2, 3, 4]
    assert (library / "Season 01" / "Some Show - S01E01.en.srt").exists()


def test_run_stops_when_ollama_is_unreachable(library, monkeypatch, capsys):
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")
    monkeypatch.setattr(translate_subs, "RETRY_WAIT", 0)
    assert run(library, "--model", "any") == 1
    assert not list(library.rglob("*.en.srt"))
    assert "Ollama" in capsys.readouterr().out


def test_unreadable_track_skips_that_video_only(library, ollama, monkeypatch, capsys):
    def broken(video, track, dest):
        dest.write_text("partial")
        raise subprocess.CalledProcessError(1, "ffmpeg")

    track = {"index": 2, "codec": "subrip", "language": "fre", "title": ""}
    monkeypatch.setattr(
        translate_subs, "probe_tracks", lambda v: [track] if "S01E01" in v.name else []
    )
    monkeypatch.setattr(translate_subs, "extract", broken)
    assert run(library) == 0
    out = capsys.readouterr().out
    assert "SKIP Some Show - S01E01.mkv" in out
    assert not (library / "Season 01" / "Some Show - S01E01.en.srt").exists()
    assert (library / "Season 01" / "Some Show - S01E02.en.srt").exists()
    work_src = library.parent.parent / "downloads" / "Some Show subtitles" / "src"
    assert [p.name for p in work_src.glob("*S01E01*")] == []


def test_another_language_is_translated_from_the_source_again(library, ollama):
    run(library, "--to", "en", "--limit", "1")
    ollama.translate = lambda i, text: "PT " + text
    ollama.requests.clear()
    run(library, "--to", "pt-BR", "--from", "fr", "--limit", "1")
    assert sorted(ollama.lines_asked()) == [1, 2, 3, 4]
    assert "PT Bonjour" in (library / "Season 01" / "Some Show - S01E01.pt-BR.srt").read_text()
    assert "EN Bonjour" in (library / "Season 01" / "Some Show - S01E01.en.srt").read_text()


def test_asking_for_another_track_extracts_again(library, ollama, monkeypatch):
    calls = []

    def fake_extract(video, track, dest):
        calls.append(track["index"])
        dest.write_text(make_ass([f"Piste {track['index']} du film"]), encoding="utf-8")

    tracks = [
        {"index": 2, "codec": "ass", "language": "fre", "title": ""},
        {"index": 3, "codec": "ass", "language": "spa", "title": ""},
    ]
    monkeypatch.setattr(translate_subs, "probe_tracks", lambda v: tracks)
    monkeypatch.setattr(translate_subs, "extract", fake_extract)
    run(library, "--limit", "1")
    run(library, "--limit", "1", "--force")
    assert calls == [2]  # same request: the cached source is reused
    run(library, "--limit", "1", "--force", "--track", "3")
    assert calls == [2, 3]
    assert "EN Piste 3" in (library / "Season 01" / "Some Show - S01E01.en.srt").read_text()


def test_recheck_retries_unchanged_lines(library, ollama, capsys):
    ollama.translate = lambda i, text: text if i == 1 else "EN " + text
    run(library, "--limit", "1")
    ollama.translate = lambda i, text: "Hello everyone"
    ollama.requests.clear()
    run(library, "--recheck")
    assert ollama.lines_asked() == [1]
    assert "Hello everyone" in (library / "Season 01" / "Some Show - S01E01.en.srt").read_text()
    assert "1 of 1 flagged lines re-translated" in capsys.readouterr().out


def test_falls_back_when_the_model_rejects_think(library, ollama):
    ollama.reject_think = True
    run(library, "--limit", "1")
    assert (library / "Season 01" / "Some Show - S01E01.en.srt").exists()
    assert all("think" not in r["body"] for r in ollama.requests)


def test_status_and_only(library, ollama, capsys):
    run(library, "--only", "S01E02")
    capsys.readouterr()
    run(library, "--status")
    out = capsys.readouterr().out
    assert "pending  Some Show - S01E01.mkv" in out
    assert "1 done, 0 partial, 1 pending of 2" in out


def test_video_without_subtitles_is_skipped_with_a_reason(library, ollama, capsys):
    (library / "Season 01" / "Some Show - S01E03.mkv").write_bytes(b"x")
    run(library)
    assert "SKIP Some Show - S01E03.mkv" in capsys.readouterr().out
    assert not (library / "Season 01" / "Some Show - S01E03.en.srt").exists()


def test_pick_model_prefers_the_strongest_installed(ollama, monkeypatch):
    ollama.models = ["gemma3:4b", "qwen3:30b-a3b-instruct-2507-q4_K_M", "qwen3:8b"]
    assert translate_subs.pick_model(None) == "qwen3:30b-a3b-instruct-2507-q4_K_M"
    assert translate_subs.pick_model("mine") == "mine"
    monkeypatch.setenv("SUBTITLE_MODEL", "env-model")
    assert translate_subs.pick_model(None) == "env-model"


def test_pick_track():
    tracks = [
        {"index": 2, "codec": "hdmv_pgs_subtitle", "language": "eng", "title": ""},
        {"index": 3, "codec": "ass", "language": "fre", "title": ""},
        {"index": 4, "codec": "subrip", "language": "eng", "title": ""},
    ]
    assert translate_subs.pick_track(tracks, None, None, "en")[0]["index"] == 3
    assert translate_subs.pick_track(tracks, "fr", None, "en")[0]["index"] == 3
    assert translate_subs.pick_track(tracks, None, 4, "pt-BR")[0]["index"] == 4
    assert "image-based" in translate_subs.pick_track(tracks, None, 2, "en")[1]
    assert "image-based" in translate_subs.pick_track(tracks[:1], None, None, "en")[1]
    assert translate_subs.pick_track([], None, None, "en")[0] is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_extracts_the_embedded_track_of_a_real_video(tmp_path, monkeypatch, ollama):
    monkeypatch.setenv("SHOW_OUTPUT_DIR", str(tmp_path / "downloads"))
    srt = tmp_path / "in.srt"
    srt.write_text("1\n00:00:00,100 --> 00:00:00,900\nBonjour <i>toi</i>\n\n")
    video = tmp_path / "Clip.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1", "-i", str(srt),
         "-map", "0:v", "-map", "1:s", "-c:v", "mpeg4", "-c:s", "srt",
         "-metadata:s:s:0", "language=fre", str(video)],
        check=True,
    )  # fmt: skip
    srt.unlink()
    assert run(video) == 0
    out = (tmp_path / "Clip.en.srt").read_text()
    assert "00:00:00,100 --> 00:00:00,900" in out
    assert "EN Bonjour <i>toi</i>" in out
    assert "French subtitles" in ollama.requests[0]["body"]["messages"][0]["content"]


def test_a_replaced_sidecar_is_translated_again_not_served_from_the_old_translation(
    library, ollama
):
    run(library, "--to", "en", "--limit", "1")
    season = library / "Season 01"
    (season / "Some Show - S01E01.en.srt").unlink()
    (season / "Some Show - S01E01.fr.ass").write_text(
        make_ass(["Nouvelle ligne", "Autre ligne"]), encoding="utf-8"
    )
    ollama.requests.clear()
    assert run(library, "--to", "en", "--limit", "1") == 0
    srt = (season / "Some Show - S01E01.en.srt").read_text()
    assert "EN Nouvelle ligne" in srt and "EN Bonjour" not in srt
    assert srt.count("-->") == 2


def test_a_sidecar_newer_than_an_old_record_without_hash_is_translated_again(library, ollama):
    run(library, "--to", "en", "--limit", "1")
    work = next(library.parent.parent.glob("downloads/Some Show subtitles/src"))
    record = work / "Season 01__Some Show - S01E01.json"
    info = json.loads(record.read_text())
    del info["sidecar_sha"]  # how records looked before the hash was stored
    record.write_text(json.dumps(info))
    season = library / "Season 01"
    (season / "Some Show - S01E01.en.srt").unlink()
    sidecar = season / "Some Show - S01E01.fr.ass"
    sidecar.write_text(make_ass(["Nouvelle ligne", "Autre ligne"]), encoding="utf-8")
    later = (work / "Season 01__Some Show - S01E01.ass").stat().st_mtime + 10
    os.utime(sidecar, (later, later))
    assert run(library, "--to", "en", "--limit", "1") == 0
    assert "EN Nouvelle ligne" in (season / "Some Show - S01E01.en.srt").read_text()
