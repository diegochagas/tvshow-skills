#!/usr/bin/env python3
"""Move a prepared show folder (Shows/ and Movies/) into the media library of a server.

  library_move.py <folder> [--apply] [--keep-local] [--verify checksum|size]

Copies every series under <folder>/Shows and every movie under <folder>/Movies to the
library folders named in config.json (rsync, over SSH when a host is set), verifies
each file on the other side, and only then moves the local copies to the trash.
A dry run unless --apply is given. Never overwrites a file that is already in the
library and never deletes anything: local copies go to the trash.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
KINDS = {"Shows": "shows_dir", "Movies": "movies_dir"}


VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".webm", ".mov", ".ts"}


def load_config(path):
    if not path.exists():
        raise SystemExit(
            f"No config at {path}. Copy config.example.json to config.json and fill it in."
        )
    cfg = json.loads(path.read_text(encoding="utf-8"))
    unfilled = [k for k, v in cfg.items() if isinstance(v, str) and "<" in v]
    if unfilled:
        raise SystemExit(f"{path}: still has placeholder values: {', '.join(sorted(unfilled))}")
    return cfg


def find_items(folder, cfg, kind):
    """[(local folder, destination folder)] for every series and movie to move."""
    if kind:
        key = KINDS["Shows" if kind == "show" else "Movies"]
        return [(folder, required(cfg, key))]
    items = []
    for name, key in KINDS.items():
        base = folder / name
        if base.is_dir():
            items += [(p, required(cfg, key)) for p in sorted(base.iterdir()) if p.is_dir()]
    if not items:
        raise SystemExit(
            f"{folder} has no Shows/ or Movies/ folder with something in it. "
            "Run jellyfin-organizer first, or pass --as show / --as movie for a single folder."
        )
    return items


def required(cfg, key):
    if not cfg.get(key):
        raise SystemExit(f"config.json needs a value for {key}")
    return cfg[key].rstrip("/")


def rsync_base(cfg):
    cmd = ["rsync", "-rlt", "-s", "--partial-dir=.rsync-partial"]
    if cfg.get("chown"):
        cmd.append(f"--chown={cfg['chown']}")
    if cfg.get("chmod"):
        cmd.append(f"--chmod={cfg['chmod']}")
    if cfg.get("host"):
        cmd += ["-e", "ssh -o BatchMode=yes"]
        if cfg.get("remote_rsync"):
            cmd.append(f"--rsync-path={cfg['remote_rsync']}")
    return cmd


def target(cfg, dest):
    return f"{cfg['host']}:{dest}/" if cfg.get("host") else f"{dest}/"


def compare(cfg, item, dest, how):
    """Files that are missing or different on the other side: (new, different)."""
    cmd = [*rsync_base(cfg), "-n", "--itemize-changes", "--out-format=%i %n"]
    if how == "checksum":
        cmd.append("--checksum")
    elif how == "size":
        cmd.append("--size-only")
    out = subprocess.run(
        [*cmd, str(item), target(cfg, dest)], capture_output=True, text=True, check=True
    ).stdout
    new, different = [], []
    for line in out.splitlines():
        flags, _, name = line.partition(" ")
        if flags.startswith((">f", "<f")):
            (new if "+++++" in flags else different).append(name)
    return new, different


def copy(cfg, item, dest):
    cmd = [*rsync_base(cfg), "--ignore-existing", "--info=progress2", str(item), target(cfg, dest)]
    subprocess.run(cmd, check=True)


def trash(path):
    """Move a local folder to the trash. Returns False when no trash tool is installed."""
    for tool in (["gio", "trash"], ["trash-put"]):
        if shutil.which(tool[0]):
            subprocess.run([*tool, str(path)], check=True)
            return True
    return False


def refresh_jellyfin(cfg):
    if not (cfg.get("jellyfin_url") and cfg.get("jellyfin_api_key")):
        return
    req = urllib.request.Request(
        cfg["jellyfin_url"].rstrip("/") + "/Library/Refresh",
        data=b"",
        headers={"X-Emby-Token": cfg["jellyfin_api_key"]},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            print("Jellyfin: library scan started")
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"Jellyfin: could not start a library scan ({e})")


def size_of(folder):
    files = [p for p in folder.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", help="folder with Shows/ and/or Movies/ inside")
    ap.add_argument("--as", dest="kind", choices=["show", "movie"], help="the folder itself is one")
    ap.add_argument(
        "--apply", action="store_true", help="copy, verify and trash (default: dry run)"
    )
    ap.add_argument("--keep-local", action="store_true", help="copy and verify only")
    ap.add_argument("--verify", choices=["checksum", "size"], default="checksum")
    ap.add_argument("--config", help="config file (default: <skill>/config.json)")
    args = ap.parse_args(argv)

    default_config = os.environ.get("LIBRARY_MOVE_CONFIG") or SKILL / "config.json"
    cfg = load_config(Path(args.config or default_config).expanduser())
    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        raise SystemExit(f"not a folder: {folder}")
    items = find_items(folder, cfg, args.kind)
    try:
        return move(items, folder, cfg, args)
    except subprocess.CalledProcessError as e:
        print(f"\nSTOPPED: {e.cmd[0]} failed (exit {e.returncode}). Nothing was removed locally.")
        return 1


def move(items, folder, cfg, args):
    conflicts = 0
    for item, dest in items:
        count, size = size_of(item)
        new, different = compare(cfg, item, dest, "time")
        if different:  # same size or date is not the question: is the content different?
            _, different = compare(cfg, item, dest, "checksum")
        print(
            f"{item.name}: {count} files, {size / 2**30:.1f} GB -> {target(cfg, dest)}"
            f"  ({len(new)} to copy, {count - len(new) - len(different)} already there)"
        )
        for name in different:
            print(f"  ALREADY THERE AND DIFFERENT (not overwritten): {name}")
        conflicts += len(different)
    if conflicts:
        print(f"\nRefusing: {conflicts} files exist in the library with different content.")
        return 1
    if not args.apply:
        print("\nDRY RUN: nothing was copied or removed; add --apply to move.")
        return 0

    for item, dest in items:
        print(f"\nCopying {item.name}", flush=True)
        copy(cfg, item, dest)
    failed = False
    for item, dest in items:
        new, different = compare(cfg, item, dest, args.verify)
        if new or different:
            failed = True
            print(f"NOT VERIFIED {item.name}: {len(new)} missing, {len(different)} different")
            for name in (new + different)[:10]:
                print(f"  {name}")
        else:
            print(f"verified ({args.verify}): {item.name}")
            for video in sorted(p for p in item.rglob("*") if p.suffix.lower() in VIDEO_EXT):
                print(f"FINISHED {video.name}")
    if failed:
        print("\nThe local copies were left where they are.")
        return 1
    if args.keep_local:
        print("\nCopied and verified; local copies kept (--keep-local).")
    else:
        trashed = []
        for item, _ in items:
            try:
                if not trash(item):
                    print("No trash tool (gio, trash-put) found: local copies kept.")
                    break
            except subprocess.CalledProcessError:
                left = [i.name for i, _ in items if i.name not in trashed]
                print(
                    "\nSTOPPED: could not move a local copy to the trash. Everything is "
                    f"verified in the library; already in the trash: {', '.join(trashed) or 'none'}; "
                    f"still in place: {', '.join(left)}"
                )
                return 1
            trashed.append(item.name)
            print(f"moved to trash: {item}")
        for name in KINDS:  # the emptied Shows/ and Movies/ shells
            base = folder / name
            if not args.kind and base.is_dir() and not any(base.iterdir()):
                base.rmdir()
    refresh_jellyfin(cfg)
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
