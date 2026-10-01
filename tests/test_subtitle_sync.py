"""subtitle-sync: finds the offset and drift of a .srt against the audio, fixes only on --apply."""

import json
import shutil
import subprocess

import numpy as np
import pytest
import sync_subs

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

DURATION = 900


def make_cues(seed=1, count=220):
    rng = np.random.default_rng(seed)
    cues, t = [], 5.0
    for _ in range(count):
        length = float(rng.uniform(0.8, 4.0))
        cues.append((t, t + length))
        t += length + float(rng.uniform(0.4, 6.0))
        if t > DURATION - 10:
            break
    return cues


def srt(cues, fn=lambda t: t):
    blocks = []
    for n, (a, b) in enumerate(cues, 1):
        blocks.append(f"{n}\n{sync_subs.fmt(fn(a))} --> {sync_subs.fmt(fn(b))}\nLine {n}\n")
    return "\n".join(blocks)


def make_video(path, cues, seed=2):
    """An .mkv whose audio is loud noise while a cue is spoken and quiet noise otherwise."""
    rng = np.random.default_rng(seed)
    rate = 16000
    audio = rng.normal(0, 150, DURATION * rate)
    for a, b in cues:
        i, j = int(a * rate), int(b * rate)
        audio[i:j] += rng.normal(0, 6000, j - i)
    wav = path.with_suffix(".raw")
    wav.write_bytes(np.clip(audio, -32000, 32000).astype("<i2").tobytes())
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", str(wav)]
        + ["-c:a", "flac", str(path)],
        check=True,
    )
    wav.unlink()


@pytest.fixture(scope="module")
def cues():
    return make_cues()


@pytest.fixture(scope="module")
def video_audio(tmp_path_factory, cues):
    path = tmp_path_factory.mktemp("audio") / "ep.mkv"
    make_video(path, cues)
    return sync_subs.speech_curve(sync_subs.decode_audio(path))


def test_in_sync_subtitles_are_left_alone(video_audio, cues):
    result = sync_subs.analyse(video_audio, cues, 120, 0.3)
    assert result["verdict"] == "ok"


@pytest.mark.parametrize("late", [3.7, -12.4, 41.0])
def test_constant_offset_is_found(video_audio, cues, late):
    # subtitles that are `late` seconds late need `-late` added
    shifted = [(a + late, b + late) for a, b in cues if a + late > 0]
    result = sync_subs.analyse(video_audio, shifted, 120, 0.3)
    assert result["verdict"] == "offset"
    assert result["transform"][1] == pytest.approx(-late, abs=0.15)
    assert result["transform"][0] == 1.0


def test_frame_rate_drift_is_found(video_audio, cues):
    # a 25 fps subtitle on a 23.976 fps video stretches every time by 25/23.976
    factor = 25 / 23.976
    stretched = [(a * factor, b * factor) for a, b in cues]
    result = sync_subs.analyse(video_audio, stretched, 120, 0.3)
    assert result["verdict"] == "drift"
    scale, offset = result["transform"]
    assert scale == pytest.approx(1 / factor, rel=2e-4)
    assert abs(offset) < 0.3


def test_truncated_and_unrelated_subtitles_are_reported_not_fixed(video_audio, cues):
    assert sync_subs.analyse(video_audio, cues[:5], 120, 0.3)["verdict"] == "broken"
    unrelated = make_cues(seed=99)
    assert sync_subs.analyse(video_audio, unrelated, 120, 0.3)["verdict"] == "low-confidence"


def test_retime_changes_only_the_timings():
    text = "1\n00:00:01,000 --> 00:00:02,500\n{\\an8}Hello --> world\n\n2\n00:01:00,000 --> 00:01:01,000\nBye\n"
    out = sync_subs.retime(text, lambda t: t + 2.0)
    assert "00:00:03,000 --> 00:00:04,500" in out
    assert "00:01:02,000 --> 00:01:03,000" in out
    assert "{\\an8}Hello --> world" in out and "Bye" in out
    assert sync_subs.retime(text, lambda t: t - 10).count("00:00:00,000") == 2  # never negative


@pytest.fixture
def season(tmp_path, monkeypatch, cues):
    monkeypatch.setenv("SHOW_OUTPUT_DIR", str(tmp_path / "downloads"))
    folder = tmp_path / "Show"
    folder.mkdir()
    make_video(folder / "Show - S01E01.mkv", cues)
    shutil.copy(folder / "Show - S01E01.mkv", folder / "Show - S01E02.mkv")
    (folder / "Show - S01E01.en.srt").write_text(srt(cues), encoding="utf-8")
    (folder / "Show - S01E02.en.srt").write_text(srt(cues, lambda t: t + 8.0), encoding="utf-8")
    (folder / "Show - S01E02.fr.srt").write_text(srt(cues[:3]), encoding="utf-8")
    return folder


def snapshot(folder):
    return {p.name: p.read_bytes() for p in folder.iterdir()}


def test_check_without_apply_changes_nothing(season, tmp_path, capsys):
    before = snapshot(season)
    assert sync_subs.main([str(season)]) == 0
    assert snapshot(season) == before
    out = capsys.readouterr().out
    assert "DRY RUN: 1 files would change" in out
    assert not (tmp_path / "downloads" / "Show sync" / "backup").exists()
    report = json.loads((tmp_path / "downloads" / "Show sync" / "report.json").read_text())
    assert {e["subtitle"]: e["verdict"] for e in report} == {
        "Show - S01E01.en.srt": "ok",
        "Show - S01E02.en.srt": "offset",
        "Show - S01E02.fr.srt": "broken",
    }


def test_apply_fixes_only_the_bad_file_keeps_a_backup_and_undo_restores(season, tmp_path):
    before = snapshot(season)
    work = tmp_path / "downloads" / "Show sync"
    assert sync_subs.main([str(season), "--apply"]) == 0
    after = snapshot(season)
    assert after["Show - S01E01.en.srt"] == before["Show - S01E01.en.srt"]
    assert after["Show - S01E02.fr.srt"] == before["Show - S01E02.fr.srt"]
    assert after["Show - S01E02.en.srt"] != before["Show - S01E02.en.srt"]
    assert after["Show - S01E02.en.srt"].decode() == srt(make_cues())  # back on the audio
    assert (work / "backup" / "Show - S01E02.en.srt").read_bytes() == before["Show - S01E02.en.srt"]
    assert sync_subs.main(["--undo", str(work / "sync-log.json")]) == 0  # dry run
    assert snapshot(season) == after
    assert sync_subs.main(["--undo", str(work / "sync-log.json"), "--apply"]) == 0
    assert snapshot(season) == before


def test_undo_refuses_a_file_edited_after_the_fix(season, tmp_path, capsys):
    work = tmp_path / "downloads" / "Show sync"
    sync_subs.main([str(season), "--apply"])
    edited = season / "Show - S01E02.en.srt"
    edited.write_text(edited.read_text() + "\n999\n00:00:01,000 --> 00:00:02,000\nmine\n")
    assert sync_subs.main(["--undo", str(work / "sync-log.json"), "--apply"]) == 1
    assert "mine" in edited.read_text()
    assert "changed since the fix" in capsys.readouterr().out


def test_a_second_run_finds_everything_in_sync(season, capsys):
    sync_subs.main([str(season), "--apply"])
    capsys.readouterr()
    assert sync_subs.main([str(season), "--output", str(season.parent / "second")]) == 0
    assert "would change" not in capsys.readouterr().out


def test_fix_option_limits_which_verdicts_are_written(season, capsys):
    before = snapshot(season)
    assert sync_subs.main([str(season), "--apply", "--fix", "drift"]) == 0
    assert snapshot(season) == before  # the one bad file is an offset, not a drift
    assert "Nothing to fix" in capsys.readouterr().out


def test_two_runs_with_the_same_work_folder_keep_both_undo_logs(tmp_path, monkeypatch, cues):
    monkeypatch.setenv("SHOW_OUTPUT_DIR", str(tmp_path / "downloads"))
    shows = []
    for name in ("A", "B"):
        folder = tmp_path / name / "Season 01"  # both end up as "Season 01 sync"
        folder.mkdir(parents=True)
        make_video(folder / "Show - S01E01.mkv", cues)
        (folder / "Show - S01E01.en.srt").write_text(srt(cues, lambda t: t + 8.0), encoding="utf-8")
        shows.append(folder)
    originals = [(f / "Show - S01E01.en.srt").read_bytes() for f in shows]
    for folder in shows:
        assert sync_subs.main([str(folder), "--apply"]) == 0
    log = tmp_path / "downloads" / "Season 01 sync" / "sync-log.json"
    assert sync_subs.main(["--undo", str(log), "--apply"]) == 0
    assert [(f / "Show - S01E01.en.srt").read_bytes() for f in shows] == originals


def test_apply_again_after_undo_does_not_abort_on_the_old_backup(season, tmp_path):
    log = tmp_path / "downloads" / "Show sync" / "sync-log.json"
    sync_subs.main([str(season), "--apply"])
    sync_subs.main(["--undo", str(log), "--apply"])
    assert sync_subs.main([str(season), "--apply"]) == 0
    assert (season / "Show - S01E02.en.srt").read_text() == srt(make_cues())
