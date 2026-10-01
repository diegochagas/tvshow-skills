---
name: library-move
description: Move a prepared show folder (the Shows/ and Movies/ layout jellyfin-organizer produces) from this computer into the media library of the home server - copy with rsync over SSH, verify every file on the server, and only then send the local copies to the trash. Dry run first; never overwrites a file already in the library and never deletes anything. Use when Diego asks to "move the episodes to the server", "send this show to the server / the NAS / Jellyfin", "put it in the library", "manda pro servidor", or after jellyfin-organizer and subtitle-translate when the show is ready to watch.
---

# library-move — prepared folder → the server's media library

One script, `library-move/scripts/library_move.py` (standard library only;
needs `rsync`, and `ssh` key access when the library is on another machine).
`<repo>` is the tvshow-skills checkout.

```bash
python3 <repo>/library-move/scripts/library_move.py "<folder>"            # dry run
python3 <repo>/library-move/scripts/library_move.py "<folder>" --apply
```

`<folder>` holds `Shows/<Series>/…` and/or `Movies/<Title (year)>/…`. Every
series folder goes into the library's shows folder and every movie folder
into its movies folder, subtitles and other sidecar files included.

| Flag | Use |
| --- | --- |
| `--apply` | copy, verify, then trash the local copies (without it: a dry run) |
| `--keep-local` | copy and verify only; nothing goes to the trash |
| `--verify checksum\|size` | how the copy is checked (default `checksum`: every file is read on both sides; `size` is fast and only compares sizes) |
| `--as show\|movie` | `<folder>` itself is one series or one movie folder, not a folder with `Shows/` and `Movies/` |
| `--config FILE` | another config (`$LIBRARY_MOVE_CONFIG`; default `library-move/config.json`) |

**Progress:** the script prints `FINISHED <episode file name>` as soon as each episode is done; relay those names to Diego as they appear in the log, in the final report too.

## Config

`library-move/config.json` is **required and gitignored**: it holds the real
server address and paths, which never go into the repo. Copy
`config.example.json` and fill it in; the script refuses to run while a
value is still a `<placeholder>`.

| Key | Meaning |
| --- | --- |
| `host` | `user@server` for SSH; empty when the library is a local or mounted folder |
| `shows_dir`, `movies_dir` | the library folders series and movies go into |
| `remote_rsync` | rsync command on the server, e.g. one run through sudo when the library is not writable by the SSH user |
| `chown`, `chmod` | owner and modes for the copied files (rsync `--chown` / `--chmod` syntax), to match the rest of the library |
| `jellyfin_url`, `jellyfin_api_key` | optional: start a Jellyfin library scan when the move is done |

## Workflow

1. Dry run. It lists each series and movie with its size, where it goes, how
   many files are new and how many are already there. A file that exists in
   the library **with different content** stops everything (`ALREADY THERE
   AND DIFFERENT`): tell Diego, do not work around it.
2. Show Diego the dry run. Moving sends the local copies to the trash, so
   run `--apply` only when the request was to *move* (or Diego agrees);
   "copy" means `--apply --keep-local`.
3. `--apply` in the background for anything big, output to a log (a 70 GB
   show takes ~20 min to copy on the home network, plus about as long for
   the checksum verification). Interrupted copies resume with the same
   command.
4. Report: what went where, that it was verified and how, whether the local
   copies are in the trash (the disk space comes back only when the trash is
   emptied), and whether the Jellyfin scan was started.

## Safety

- Dry run by default; `--apply` is the only thing that copies or trashes.
- Existing library files are never overwritten (`rsync --ignore-existing`);
  adding a season to a show that is already there works, replacing a file
  does not.
- The local copies go to the trash (`gio trash` / `trash-put`) and only
  after every file was verified on the other side. If verification fails,
  or no trash tool is installed, they stay where they are.
- Nothing is ever deleted on the server.
