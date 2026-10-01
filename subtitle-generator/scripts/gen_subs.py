#!/usr/bin/env python3
"""
gen_subs.py - Generate synchronized subtitles for ONE video file using
faster-whisper. Designed for Japanese (or any-language) audio -> English
subtitles via Whisper's built-in translate task, but works for plain
transcription too.

Key feature: RESUMABLE in chunks. A long episode is processed in fixed
time windows so it can run inside environments that cap command runtime
(e.g. ~45s sandbox calls). Progress is saved to a sidecar JSON; re-running
continues where it left off and writes the final .srt when complete.

Examples
--------
# Whole episode in one go (e.g. on your own computer, no time cap):
python3 gen_subs.py "Power Stone - S01E01 ... .mp4"

# One chunk per call (for capped sandboxes); call repeatedly until DONE:
python3 gen_subs.py "episode.mp4" --max-chunks 1

# Higher quality (slower), or keep original-language subtitles:
python3 gen_subs.py "episode.mp4" --model medium
python3 gen_subs.py "episode.mp4" --task transcribe --language ja
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

CHUNK_SEC_DEFAULT = 300  # 5 min windows: ~20s compute on small model, safe under a 45s cap


def fmt_ts(t):
    total_ms = max(0, round(t * 1000))
    s, ms = divmod(total_ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def probe_duration(path):
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def write_srt(segments, path):
    segments = sorted(segments, key=lambda s: s["start"])
    with open(path, "w", encoding="utf-8") as f:
        i = 1
        for seg in segments:
            text = seg["text"].strip()
            if not text:
                continue
            f.write(f"{i}\n{fmt_ts(seg['start'])} --> {fmt_ts(seg['end'])}\n{text}\n\n")
            i += 1
    return i - 1


def main():
    ap = argparse.ArgumentParser(description="Generate subtitles for one video file.")
    ap.add_argument("video", help="Path to the video/audio file")
    ap.add_argument(
        "--model",
        default="small",
        help="Whisper model: tiny, base, small, medium, large-v3 (default: small)",
    )
    ap.add_argument(
        "--task",
        default="translate",
        choices=["translate", "transcribe"],
        help="translate=output English; transcribe=keep source language (default: translate)",
    )
    ap.add_argument(
        "--language", default=None, help="Source language code (e.g. ja). Default: auto-detect"
    )
    ap.add_argument("--beam", type=int, default=5)
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    ap.add_argument(
        "--chunk-sec",
        type=int,
        default=CHUNK_SEC_DEFAULT,
        help="Window size in seconds for resumable processing",
    )
    ap.add_argument(
        "--max-chunks",
        type=int,
        default=0,
        help="Process at most N windows this run (0 = until finished)",
    )
    ap.add_argument("--output", default=None, help="Output .srt path (default: alongside video)")
    args = ap.parse_args()

    video = args.video
    if not os.path.exists(video):
        print(f"ERROR: file not found: {video}", file=sys.stderr)
        sys.exit(1)

    base = os.path.splitext(video)[0]
    suffix = ".en.srt" if args.task == "translate" else f".{args.language or 'orig'}.srt"
    out_srt = args.output or (base + suffix)
    prog_path = base + ".subprogress.json"

    if os.path.exists(out_srt) and os.path.getsize(out_srt) > 0 and not os.path.exists(prog_path):
        print(f"DONE: already exists -> {out_srt}")
        return

    duration = probe_duration(video)

    if os.path.exists(prog_path):
        with open(prog_path) as f:
            prog = json.load(f)
    else:
        prog = {
            "offset": 0.0,
            "duration": duration,
            "segments": [],
            "model": args.model,
            "task": args.task,
        }

    from faster_whisper import WhisperModel

    print(f"Loading model '{args.model}' (cpu/int8)...", flush=True)
    model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=args.threads)

    chunks_done = 0
    while prog["offset"] < duration:
        if args.max_chunks and chunks_done >= args.max_chunks:
            break
        start = prog["offset"]
        dur = min(args.chunk_sec, duration - start)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav = tf.name
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(start),
                "-t",
                str(dur),
                "-i",
                video,
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                wav,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        t0 = time.time()
        segs, _ = model.transcribe(
            wav,
            task=args.task,
            language=args.language,
            beam_size=args.beam,
            vad_filter=True,
            condition_on_previous_text=False,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        n = 0
        for s in segs:
            txt = s.text.strip()
            if txt:
                prog["segments"].append(
                    {"start": s.start + start, "end": s.end + start, "text": txt}
                )
                n += 1
        os.remove(wav)
        prog["offset"] = start + dur
        with open(prog_path, "w") as f:
            json.dump(prog, f)
        chunks_done += 1
        pct = 100.0 * prog["offset"] / duration
        print(
            f"  window {fmt_ts(start)}-{fmt_ts(start + dur)}: +{n} lines "
            f"({time.time() - t0:.0f}s)  progress {pct:.0f}%",
            flush=True,
        )

    if prog["offset"] >= duration:
        total = write_srt(prog["segments"], out_srt)
        os.remove(prog_path)
        print(f"DONE: wrote {total} subtitle lines -> {out_srt}")
    else:
        pct = 100.0 * prog["offset"] / duration
        print(f"PARTIAL: {pct:.0f}% done. Re-run the same command to continue.")


if __name__ == "__main__":
    main()
