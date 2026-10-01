#!/usr/bin/env python3
"""Check that `.srt` files are in sync with the audio of their video, and fix their timing.

  sync_subs.py <video or folder> [--apply]          check (dry run), or fix with --apply
  sync_subs.py --undo <sync-log.json> [--apply]     put the original files back

The audio is turned into a speech-activity curve (energy in the voice band, 10 ms steps)
and compared with the "a subtitle is on screen" curve of each `.srt` next to the video.
The best match gives a constant offset; a second pass over 5-minute windows finds a
linear drift (a frame-rate mismatch). Anything else (cuts, ads, a broken file) is
reported and left alone. Needs ffmpeg and numpy (the repo venv has it).

Without --apply nothing is written into the folder. With --apply the original `.srt`
goes to `~/Downloads/<source name> sync/backup/` first, and only the cue times change.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".webm", ".mov", ".ts"}
RATE = 8000  # audio sample rate used for the analysis
HOP = RATE // 100  # 10 ms
WINDOW = 2 * HOP
STEP = 0.01  # seconds per frame
TIMING = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})(\s*-->\s*)(\d+):(\d\d):(\d\d)[,.](\d{1,3})")
MIN_Z = 6.0  # how far the best offset must stand out from the rest to be trusted


def output_root():
    return Path(os.environ.get("SHOW_OUTPUT_DIR") or Path.home() / "Downloads")


# --- subtitles -------------------------------------------------------------------------


def to_seconds(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def fmt(seconds):
    ms = max(0, round(seconds * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def read_cues(text):
    """[(start, end)] of every cue that has a duration."""
    cues = []
    for m in TIMING.finditer(text):
        start = to_seconds(*m.group(1, 2, 3, 4))
        end = to_seconds(*m.group(6, 7, 8, 9))
        if end > start:
            cues.append((start, end))
    return cues


def retime(text, fn):
    """The same document with every timing line passed through fn(seconds) -> seconds."""

    def sub(m):
        start = fn(to_seconds(*m.group(1, 2, 3, 4)))
        end = fn(to_seconds(*m.group(6, 7, 8, 9)))
        return f"{fmt(start)}{m.group(5)}{fmt(end)}"

    return TIMING.sub(sub, text)


def sub_signal(cues, frames):
    signal = np.zeros(frames, dtype=np.float32)
    for start, end in cues:
        a, b = int(start / STEP), int(end / STEP)
        if a < frames:
            signal[max(a, 0) : min(b, frames)] = 1.0
    return signal


# --- audio -----------------------------------------------------------------------------


def decode_audio(video, track=0):
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(video), "-map", f"0:a:{track}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
    out = subprocess.run(cmd, capture_output=True, check=False)
    if out.returncode != 0 or not out.stdout:
        return None
    return np.frombuffer(out.stdout, dtype=np.int16).astype(np.float32)


def speech_curve(samples):
    """Voice-band energy per 10 ms, scaled to 0..1 between the quiet and loud parts."""
    frames = (len(samples) - WINDOW) // HOP + 1
    if frames < 100:
        return None
    window = np.hanning(WINDOW).astype(np.float32)
    lo, hi = int(300 / (RATE / WINDOW)), int(3400 / (RATE / WINDOW))
    energy = np.empty(frames, dtype=np.float32)
    for first in range(0, frames, 20000):
        last = min(first + 20000, frames)
        idx = np.arange(first, last)[:, None] * HOP + np.arange(WINDOW)[None, :]
        spectrum = np.abs(np.fft.rfft(samples[idx] * window, axis=1)) ** 2
        energy[first:last] = np.log10(spectrum[:, lo:hi].sum(axis=1) + 1.0)
    quiet, loud = np.percentile(energy, [10, 95])
    if loud - quiet < 0.5:
        return None
    curve = np.clip((energy - quiet) / (loud - quiet), 0, 1)
    return np.convolve(curve, np.ones(5) / 5, mode="same").astype(np.float32)


# --- matching --------------------------------------------------------------------------


def correlate(audio, subs, max_lag):
    """(best shift in frames, z score, correlation) to add to the subtitle times."""
    n = len(audio)
    size = 1 << int(np.ceil(np.log2(n + max_lag + 1)))
    a = audio - audio.mean()
    s = subs - subs.mean()
    norm = np.linalg.norm(a) * np.linalg.norm(s)
    if norm == 0:
        return 0, 0.0, 0.0
    cross = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(s, size)), size) / norm
    lags = np.arange(-max_lag, max_lag + 1)
    scores = cross[lags % size]
    best = int(scores.argmax())
    away = np.abs(lags - lags[best]) > 200  # outside +-2 s of the peak
    rest = scores[away]
    z = float((scores[best] - rest.mean()) / (rest.std() + 1e-9)) if len(rest) else 0.0
    return int(lags[best]), z, float(scores[best])


def shift_signal(signal, frames):
    out = np.zeros_like(signal)
    if frames >= 0:
        out[frames:] = signal[: len(signal) - frames] if frames else signal
    else:
        out[:frames] = signal[-frames:]
    return out


RATIOS = [1.0] + [
    x
    for a, b in ((25, 23.976), (24, 23.976), (25, 24), (30, 29.97), (30, 23.976), (30, 25))
    for x in (a / b, b / a)
]


def analyse(audio, cues, max_shift, tolerance, window_s=300):
    """Verdict and the transform (new = old * scale + offset) for one subtitle file."""
    duration = len(audio) * STEP
    last = max((c[1] for c in cues), default=0)
    if len(cues) < 20 or last < 0.4 * duration:
        return {
            "verdict": "broken",
            "why": f"{len(cues)} cues, the last ends at {last:.0f}s of {duration:.0f}s",
        }
    # the common frame-rate ratios first, each with its best offset; keep the best match
    best = None
    for ratio in RATIOS:
        scaled = [(a * ratio, b * ratio) for a, b in cues]
        lag, z, corr = correlate(audio, sub_signal(scaled, len(audio)), int(max_shift / STEP))
        if best is None or corr > best[3]:
            best = (ratio, lag, z, corr)
    ratio, lag, z, corr = best
    info = {"z": round(z, 1), "correlation": round(corr, 3)}
    if z < MIN_Z:
        return {
            "verdict": "low-confidence",
            "why": f"no clear match with the audio (z={z:.1f})",
            **info,
        }
    offset = lag * STEP
    moved = shift_signal(sub_signal([(a * ratio, b * ratio) for a, b in cues], len(audio)), lag)
    centres, local, weights = [], [], []
    size = int(window_s / STEP)
    for first in range(0, len(audio) - size // 2, size):
        seg = slice(first, min(first + size, len(audio)))
        if seg.stop - seg.start < size // 2:
            continue
        lag_w, z_w, _ = correlate(audio[seg], moved[seg], int(5 / STEP))
        if z_w >= MIN_Z:
            centres.append((seg.start + seg.stop) / 2 * STEP)
            local.append(lag_w * STEP)
            weights.append(z_w)
    slope, icpt = 0.0, 0.0
    if len(local) >= 3:
        slope, icpt = (float(x) for x in np.polyfit(centres, local, 1, w=weights))
        residual = max(abs(np.array(local) - (slope * np.array(centres) + icpt)))
        spread = max(local) - min(local)
        if spread > tolerance and residual > tolerance:
            return {
                "verdict": "variable",
                "offset": round(offset, 2),
                "why": f"the offset changes by {spread:.1f}s along the video without a "
                "straight line (cuts or ads)",
                **info,
            }
        if spread <= tolerance:
            slope, icpt = 0.0, float(np.mean(local)) if abs(np.mean(local)) > tolerance else 0.0
    # new = (old * ratio + offset) * (1 + slope) + icpt
    scale = ratio * (1 + slope)
    total = offset * (1 + slope) + icpt
    drift = (scale - 1) * duration
    info.update(offset=round(total, 2), scale=round(scale, 6), drift=round(drift, 2))
    if abs(total) <= tolerance and abs(drift) <= tolerance:
        return {"verdict": "ok", **info, "scale": 1.0, "offset": 0.0}
    kind = "drift" if abs(drift) > tolerance else "offset"
    if kind == "offset":
        scale = 1.0  # a tiny scale next to a plain offset is measurement noise
    return {"verdict": kind, **info, "transform": [scale, total]}


# --- files -----------------------------------------------------------------------------


def find_videos(source):
    if source.is_file():
        return [source]
    return sorted(p for p in source.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXT)


def subtitles_of(video):
    return sorted(
        p
        for p in video.parent.iterdir()
        if p.is_file() and p.suffix.lower() == ".srt" and p.name.startswith(video.stem + ".")
    )


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_text(path):
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


def work_dir(source, args):
    if args.output:
        return Path(args.output).expanduser()
    return output_root() / f"{(source if source.is_dir() else source.parent).name} sync"


def run_check(args):
    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"not found: {source}")
    base = source if source.is_dir() else source.parent
    work = work_dir(source, args)
    videos = [v for v in find_videos(source) if subtitles_of(v)]
    if args.only:
        videos = [v for v in videos if any(s in str(v) for s in args.only)]
    if args.limit:
        videos = videos[: args.limit]
    log_path, report = work / "sync-log.json", []
    # an earlier run into the same work folder keeps its undo records
    done = json.loads(log_path.read_text())["moves"] if log_path.exists() else []
    fixed_now = 0
    started = time.time()
    for n, video in enumerate(videos, 1):
        rel = video.relative_to(base).as_posix()
        audio = decode_audio(video, args.audio_track)
        curve = speech_curve(audio) if audio is not None else None
        for sub in subtitles_of(video):
            entry = {"video": rel, "subtitle": sub.relative_to(base).as_posix()}
            if curve is None:
                entry.update(verdict="no-audio", why="no readable audio track")
            else:
                text, _ = read_text(sub)
                entry.update(analyse(curve, read_cues(text), args.max_shift, args.tolerance))
            report.append(entry)
            line = f"{entry['verdict']:<14} {entry['subtitle']}"
            if "transform" in entry:
                line += f"  shift {entry['transform'][1]:+.2f}s"
                if entry["transform"][0] != 1.0:
                    line += f", scale {entry['transform'][0]:.6f}"
            elif "why" in entry:
                line += f"  ({entry['why']})"
            print(line, flush=True)
            if args.apply and "transform" in entry and entry["verdict"] in args.fix:
                done.append(apply_fix(base, work, entry, sub, log_path, done))
                fixed_now += 1
        elapsed = time.time() - started
        print(
            f"FINISHED {video.name} [{n}/{len(videos)}] ETA {elapsed / n * (len(videos) - n) / 60:.0f} min",
            flush=True,
        )
    work.mkdir(parents=True, exist_ok=True)
    (work / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    counts = {}
    for e in report:
        counts[e["verdict"]] = counts.get(e["verdict"], 0) + 1
    print("\n" + (", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "nothing to check"))
    fixable = sum(1 for e in report if "transform" in e)
    if args.apply:
        print(f"FIXED {fixed_now} files. Undo log: {log_path}" if fixed_now else "Nothing to fix.")
    elif fixable:
        print(f"DRY RUN: {fixable} files would change. Nothing was written; add --apply to fix.")
    print(f"Report: {work / 'report.json'}")
    return 0


def apply_fix(base, work, entry, sub, log_path, done):
    scale, total = entry["transform"]
    text, enc = read_text(sub)
    new_text = retime(text, lambda t: t * scale + total)
    backup = work / "backup" / entry["subtitle"]
    backup.parent.mkdir(parents=True, exist_ok=True)
    n = 1
    while backup.exists():  # never overwrite an older backup: the next one gets a number
        n += 1
        backup = work / "backup" / f"{entry['subtitle']}.{n}"
        backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sub, backup)
    sub.write_bytes(new_text.encode("utf-8" if enc == "utf-8-sig" else enc))
    record = {"file": str(sub), "backup": str(backup), "written": sha(sub), "original": sha(backup)}
    log_path.write_text(json.dumps({"moves": done + [record]}, indent=1), encoding="utf-8")
    return record


def run_undo(args):
    log_path = Path(args.undo).expanduser().resolve()
    records = json.loads(log_path.read_text(encoding="utf-8"))["moves"]
    problems = []
    for r in records:
        f, b = Path(r["file"]), Path(r["backup"])
        if not b.exists():
            problems.append(f"backup missing: {b}")
        elif not f.exists():
            problems.append(f"file missing: {f}")
        elif sha(f) != r["written"]:
            problems.append(f"changed since the fix, not restoring: {f}")
    if problems:
        print("Cannot proceed:")
        for p in problems:
            print(f"  {p}")
        return 1
    for r in records:
        print(f"{r['backup']}\n  -> {r['file']}")
    if not args.apply:
        print(f"\nDRY RUN: {len(records)} files. Nothing was changed; add --apply to restore.")
        return 0
    for r in records:
        shutil.copy2(r["backup"], r["file"])
    log_path.rename(log_path.with_name("sync-log.undone.json"))
    print(f"\nRESTORED {len(records)} files.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", nargs="?", help="a video or a folder of videos")
    ap.add_argument("--apply", action="store_true", help="write the fixes (default: report only)")
    ap.add_argument("--undo", metavar="LOG", help="restore the originals listed in a sync-log.json")
    ap.add_argument(
        "--fix",
        default="offset,drift",
        help="which verdicts --apply fixes: offset, drift or both (default offset,drift)",
    )
    ap.add_argument("--max-shift", type=float, default=120, help="largest offset to look for (s)")
    ap.add_argument(
        "--tolerance", type=float, default=0.3, help="offset/drift below this is ok (s)"
    )
    ap.add_argument("--audio-track", type=int, default=0, help="audio stream number (0 = first)")
    ap.add_argument("--only", nargs="+", metavar="STR", help="only videos whose path has STR")
    ap.add_argument("--limit", type=int, help="check at most N videos")
    ap.add_argument("--output", help="work folder (default ~/Downloads/<source name> sync)")
    args = ap.parse_args(argv)
    args.fix = {k.strip() for k in args.fix.split(",")}
    if args.undo:
        return run_undo(args)
    if not args.source:
        ap.error("a video or folder is required")
    return run_check(args)


if __name__ == "__main__":
    sys.exit(main())
