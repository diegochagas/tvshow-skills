#!/usr/bin/env python3
"""Rename a downloaded show into the layout Jellyfin recognises, seasons and movies apart.

  jellyfin_rename.py plan <folder> --series "Title" ...
  jellyfin_rename.py apply <plan.json> [--apply]
  jellyfin_rename.py undo <rename-log.json> [--apply]

`plan` only writes a plan file. `apply` and `undo` are dry runs until `--apply` is given.
Files are moved, never deleted or overwritten; every move is logged so it can be undone.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".webm", ".mov", ".ts"}
EPISODE = re.compile(
    r"(?<![a-z0-9])s(\d{1,2})[ ._-]?e(\d{1,3})(?:-?e(\d{1,3}))?(?!\d)", re.IGNORECASE
)
EPISODE_X = re.compile(r"(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?!\d)", re.IGNORECASE)
ABSOLUTE = re.compile(
    r"(?:^|[ _\-\[(])(?:ep?\.?\s*)?(\d{1,3})(?:v\d)?(?=[ _\-\]).]|$)", re.IGNORECASE
)
FORBIDDEN = re.compile(r'[<>"|?*\x00-\x1f]')


def output_root():
    return Path(os.environ.get("SHOW_OUTPUT_DIR") or Path.home() / "Downloads")


def safe_name(name):
    """A file-system friendly version of a title."""
    name = name.replace(": ", " - ").replace(":", "-").replace("/", "-").replace("\\", "-")
    return re.sub(r"\s+", " ", FORBIDDEN.sub("", name)).strip(" .")


def parse_episode(stem, default_season=None):
    """(season, first episode, last episode or None) found in a file name, or None."""
    m = EPISODE.search(stem)
    if m:
        last = int(m.group(3)) if m.group(3) else None
        return int(m.group(1)), int(m.group(2)), last
    m = EPISODE_X.search(stem)
    if m:
        return int(m.group(1)), int(m.group(2)), None
    if default_season is not None:
        cleaned = re.sub(
            r"\[[^\]]*\]|\([^)]*\)|\d{3,4}p|x26[45]|h\.?26[45]|\d+bit",
            " ",
            stem,
            flags=re.IGNORECASE,
        )
        # the number after " - " when there is one, otherwise the last number in the name:
        # the first one often belongs to the title ("Mob Psycho 100 - 03")
        m = re.search(r" - (\d{1,3})(?:v\d)?(?=[ _\-\]).]|$)", cleaned)
        found = [m] if m else list(ABSOLUTE.finditer(cleaned))
        if found:
            return default_season, int(found[-1].group(1)), None
    return None


def code(season, episode, last=None):
    text = f"S{season:02d}E{episode:02d}"
    return text + (f"-E{last:02d}" if last else "")


def parse_pairs(values, what):
    pairs = []
    for value in values or []:
        if "=" not in value:
            raise SystemExit(f"--{what} needs KEY=VALUE, got: {value}")
        key, _, val = value.partition("=")
        pairs.append((key.strip(), val.strip()))
    return pairs


def parse_movie(value):
    """'Title|2003' (or a ready-made 'Title (2003)') -> folder/file name.

    Movies carry their year; series folders and episode files do not.
    """
    if "|" in value:
        title, year = [part.strip() for part in value.split("|")][:2]
        return safe_name(title) + (f" ({year})" if year else "")
    return safe_name(value)


def sidecars(video):
    """Files that belong to a video: same folder, name = video stem + '.' + anything.

    When two videos' names both fit ("Movie" and "Movie.Extended"), the file goes
    with the longer one.
    """
    entries = sorted(p for p in video.parent.iterdir() if p.is_file())
    longer = [
        p.stem
        for p in entries
        if p.suffix.lower() in VIDEO_EXT and p.stem.startswith(video.stem + ".")
    ]
    found = []
    for p in entries:
        if p.suffix.lower() in VIDEO_EXT or not p.name.startswith(video.stem + "."):
            continue
        if not any(p.name.startswith(stem + ".") for stem in longer):
            found.append(p)
    return found


def build_plan(root, args):
    series = safe_name(args.series)
    specials = {}
    for key, val in parse_pairs(args.special, "special"):
        src = parse_episode(key)
        target, _, title = val.partition(":")
        dst = parse_episode(target)
        if not src or not dst:
            raise SystemExit(f"--special needs S01E76=S00E01[:Title], got: {key}={val}")
        specials[src[:2]] = (dst[0], dst[1], title.strip())
    movies = [(key, parse_movie(val)) for key, val in parse_pairs(args.movie, "movie")]
    titles = {}
    for key, val in parse_pairs(args.title, "title"):
        ep = parse_episode(key)
        if not ep:
            raise SystemExit(f"--title needs S01E01=Title, got: {key}={val}")
        titles[ep[:2]] = val

    moves, unmatched = [], []
    videos = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXT)
    for video in videos:
        rel = video.relative_to(root)
        movie = next((name for key, name in movies if key.lower() in video.name.lower()), None)
        if movie:
            new_stem, folder, kind = movie, Path("Movies") / movie, "movie"
        else:
            ep = parse_episode(video.stem, args.season)
            if not ep:
                unmatched.append(rel.as_posix())
                continue
            season, episode, last = ep
            kind, title = "episode", titles.get((season, episode), "")
            if (season, episode) in specials:
                season, episode, special_title = specials[(season, episode)]
                kind, last, title = "special", None, special_title or title
            new_stem = f"{series} - {code(season, episode, last)}"
            if title:
                new_stem += f" - {safe_name(title)}"
            elif video.stem.startswith(new_stem + " - "):
                new_stem = video.stem  # already named, keep the episode title it carries
            folder = Path("Shows") / series / f"Season {season:02d}"
        target = folder / (new_stem + video.suffix)
        if target != rel:
            moves.append({"from": rel.as_posix(), "to": target.as_posix(), "kind": kind})
        for extra in sidecars(video):
            extra_target = folder / (new_stem + extra.name[len(video.stem) :])
            if extra_target != extra.relative_to(root):
                moves.append(
                    {
                        "from": extra.relative_to(root).as_posix(),
                        "to": extra_target.as_posix(),
                        "kind": "sidecar",
                    }
                )
    return {"root": str(root), "moves": moves, "unmatched": unmatched}


def validate(root, moves):
    """Every reason the moves cannot be applied safely."""
    problems = []
    targets = {}
    sources = set()
    for m in moves:
        src, dst = root / m["from"], root / m["to"]
        if not src.exists():
            problems.append(f"missing: {m['from']}")
        if m["from"] in sources:
            problems.append(f"listed twice: {m['from']}")
        sources.add(m["from"])
        if root.resolve() not in src.resolve().parents:
            problems.append(f"outside the folder: {m['from']}")
        if root.resolve() not in dst.resolve().parents:
            problems.append(f"outside the folder: {m['to']}")
        if dst.exists():
            problems.append(f"already exists: {m['to']}")
        if m["to"] in targets:
            problems.append(f"two files would become {m['to']}: {targets[m['to']]} and {m['from']}")
        targets[m["to"]] = m["from"]
    return problems


def run_moves(root, moves, do_it, log_path=None):
    problems = validate(root, moves)
    if problems:
        print("Cannot proceed:")
        for p in problems:
            print(f"  {p}")
        return 1
    for m in moves:
        print(f"{m['from']}\n  -> {m['to']}")
    if not do_it:
        print(f"\nDRY RUN: {len(moves)} moves. Nothing was changed; add --apply to rename.")
        return 0
    done = []
    for m in moves:
        dst = root / m["to"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        (root / m["from"]).rename(dst)
        done.append({"from": m["to"], "to": m["from"], "kind": m.get("kind", "")})
        if log_path:  # written after every move, so an interrupted run can still be undone
            log_path.write_text(
                json.dumps({"root": str(root), "moves": done}, indent=1), encoding="utf-8"
            )
    for folder in sorted({(root / m["from"]).parent for m in moves}, key=lambda p: -len(p.parts)):
        while folder != root and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
            folder = folder.parent
    print(f"\nRENAMED {len(done)} files." + (f" Undo log: {log_path}" if log_path else ""))
    return 0


def cmd_plan(args):
    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"not a folder: {root}")
    plan = build_plan(root, args)
    out = Path(args.output).expanduser() if args.output else output_root() / f"{root.name} rename"
    out.mkdir(parents=True, exist_ok=True)
    plan_path = out / "plan.json"
    plan_path.write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
    kinds = {}
    for m in plan["moves"]:
        kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
    print(", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "nothing to rename")
    for name in plan["unmatched"]:
        print(f"UNMATCHED (left where it is): {name}")
    print(f"Plan: {plan_path}")
    return 0


def cmd_apply(args):
    plan_path = Path(args.plan).expanduser().resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    log = plan_path.with_name("rename-log.json")
    if args.apply and log.exists():
        raise SystemExit(
            f"{log} already exists: this plan was applied. Undo it or make a new plan."
        )
    return run_moves(Path(plan["root"]), plan["moves"], args.apply, log)


def cmd_undo(args):
    log_path = Path(args.log).expanduser().resolve()
    log = json.loads(log_path.read_text(encoding="utf-8"))
    status = run_moves(Path(log["root"]), log["moves"], args.apply)
    if status == 0 and args.apply:
        log_path.rename(log_path.with_name("rename-log.undone.json"))
    return status


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan", help="scan a folder and write plan.json")
    p.add_argument("folder")
    p.add_argument("--series", required=True, help="series title as the metadata provider has it")
    p.add_argument("--season", type=int, help="season for files numbered without SxxEyy")
    p.add_argument("--special", action="append", metavar="S01E76=S00E01[:Title]")
    p.add_argument("--movie", action="append", metavar="TEXT=Title|Year")
    p.add_argument("--title", action="append", metavar="S01E01=Episode title")
    p.add_argument("--output", help="where plan.json goes (default: ~/Downloads/<folder> rename)")
    p.set_defaults(func=cmd_plan)
    p = sub.add_parser("apply", help="show the moves of a plan; --apply performs them")
    p.add_argument("plan")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_apply)
    p = sub.add_parser("undo", help="show the moves that undo a rename; --apply performs them")
    p.add_argument("log")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_undo)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
