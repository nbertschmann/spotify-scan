#!/usr/bin/env python3
"""Scan your Spotify library for songs whose titles contain a place name.

Run the steps in order, checking the counts after each:

    python scan.py auth                        # 0. log in, probe each scope
    python scan.py playlists                   # 1. Liked Songs + your playlists
    python scan.py top                         # 2. top 30 artists' discographies
    python scan.py others --dry-run            # 3a. list which artists section 3 would pull
    python scan.py others --min-artist-tracks 2  # 3b. pull their discographies
    python scan.py prefilter                   # 4. gazetteer pass -> review/titles.csv
    (Claude reviews review/titles.csv -> review/decisions.csv + review/picks.csv)
    python scan.py build                       # 5. data/candidates.csv + shortlist.md

data/songs.csv is rebuilt after every collection step.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from spotify_scan import collect, review
from spotify_scan.api import Api, make_client
from spotify_scan.collect import SECTIONS

# user-library-read is needed for Liked Songs (current_user_saved_tracks).
SCOPE = "playlist-read-private playlist-read-collaborative user-top-read user-library-read"
ROOT = Path(__file__).resolve().parent

SONG_COLUMNS = [
    "section", "track_name", "artist", "album", "release_year", "spotify_url",
    "track_id", "source_playlist",
]
# Per-section working files keep the primary artist for section 3 / dedupe.
SECTION_COLUMNS = SONG_COLUMNS + ["primary_artist_id", "primary_artist"]


class Paths:
    def __init__(self, data_dir: Path, review_dir: Path):
        self.data = data_dir
        self.sections = data_dir / "sections"
        self.songs = data_dir / "songs.csv"
        self.skipped = data_dir / "skipped_playlists.log"
        self.top_artists = data_dir / "top_artists.csv"
        self.candidates = data_dir / "candidates.csv"
        self.review = review_dir
        self.titles = review_dir / "titles.csv"
        self.decisions = review_dir / "decisions.csv"
        self.picks = review_dir / "picks.csv"
        self.shortlist = ROOT / "shortlist.md"

    def section(self, name: str) -> Path:
        return self.sections / f"{name}.csv"


def load_sections(paths: Paths) -> dict[str, list[dict]]:
    return {s: review.read_csv(paths.section(s)) for s in SECTIONS if paths.section(s).exists()}


def save_section(paths: Paths, name: str, rows: list[dict]):
    rows.sort(key=lambda r: (r["artist"].lower(), r["track_name"].lower()))
    review.write_csv(paths.section(name), rows, SECTION_COLUMNS)
    sections = load_sections(paths)
    all_rows = [r for s in SECTIONS for r in sections.get(s, [])]
    review.write_csv(paths.songs, all_rows, SONG_COLUMNS)
    print(f"\ndata/songs.csv now has {len(all_rows)} rows: " +
          ", ".join(f"{s} {len(sections[s])}" for s in SECTIONS if s in sections))


def make_api(args, refresh: bool | None = None) -> Api:
    load_dotenv(ROOT / ".env")
    sp = make_client(SCOPE, cache_path=str(ROOT / ".spotify_token_cache"))
    return Api(sp, Path(args.cache_dir), refresh=args.refresh if refresh is None else refresh,
               delay=args.delay)


def print_calls(api: Api):
    print(f"API calls: {api.live_calls} live, {api.cache_hits} from cache")


def load_top_ids(api: Api, paths: Paths, n: int) -> set[str]:
    if paths.top_artists.exists():
        return {r["artist_id"] for r in review.read_csv(paths.top_artists)}
    return {a["id"] for a in collect.top_artists(api, n)}


# -- steps ---------------------------------------------------------------------


def cmd_auth(args, paths: Paths) -> int:
    api = make_api(args, refresh=True)  # always hit the live API here
    ok = collect.auth_check(api)
    print("\nAuth OK." if ok else "\nSome scopes failed - see above.")
    return 0 if ok else 1


def cmd_playlists(args, paths: Paths) -> int:
    api = make_api(args)
    print("== Section 1: playlists ==")
    rows, skipped, stats = collect.collect_playlists(api)
    paths.data.mkdir(parents=True, exist_ok=True)
    paths.skipped.write_text("playlist\trelation\towner\treason\n" + "\n".join(skipped) + "\n",
                             encoding="utf-8")
    artists = Counter(r["primary_artist_id"] for r in rows)
    print(f"""
Section 1 counts
  playlists read (incl. Liked Songs)  {stats.get('playlists_read', 0)}
  playlists skipped                   {stats.get('playlists_skipped', 0)}  (see {paths.skipped})
  playlist entries                    {stats.get('entries', 0)}
  unique track ids                    {stats.get('unique_track_ids', 0)}
  songs after collapsing duplicates   {stats.get('songs', 0)}
  distinct primary artists            {len(artists)}
  ...on 2+ songs                      {sum(c >= 2 for c in artists.values())}""")
    print_calls(api)
    save_section(paths, collect.SECTION_PLAYLISTS, rows)
    return 0


def cmd_top(args, paths: Paths) -> int:
    api = make_api(args)
    print(f"== Section 2: top {args.top_n} artists ==")
    artists = collect.top_artists(api, args.top_n)
    review.write_csv(paths.top_artists,
                     [{"rank": i, "artist_id": a["id"], "artist": a["name"]} for i, a in enumerate(artists, 1)],
                     ["rank", "artist_id", "artist"])
    print(f"  {len(artists)} artists: " + ", ".join(a["name"] for a in artists))
    rows = collect.collect_discographies(api, [(a["id"], a["name"]) for a in artists],
                                         collect.SECTION_TOP_ARTISTS, args.market or None)
    print(f"""
Section 2 counts
  top artists                {len(artists)}
  songs (deduped per artist) {len(rows)}""")
    print_calls(api)
    save_section(paths, collect.SECTION_TOP_ARTISTS, rows)
    return 0


def cmd_others(args, paths: Paths) -> int:
    if not paths.section(collect.SECTION_PLAYLISTS).exists():
        print("Run `python scan.py playlists` first.", file=sys.stderr)
        return 1
    playlist_rows = review.read_csv(paths.section(collect.SECTION_PLAYLISTS))
    api = make_api(args)
    top_ids = load_top_ids(api, paths, args.top_n)
    chosen = collect.other_artists(playlist_rows, top_ids, args.min_artist_tracks)
    print(f"== Section 3: playlist artists not in top {len(top_ids)}, "
          f"on >= {args.min_artist_tracks} playlist songs: {len(chosen)} artists ==")
    if args.dry_run:
        for aid, name, c in chosen:
            print(f"  {c:3}  {name}")
        print("\n(dry run - nothing fetched. Raise/lower --min-artist-tracks, then run without --dry-run.)")
        return 0
    rows = collect.collect_discographies(api, [(a, n) for a, n, _ in chosen],
                                         collect.SECTION_OTHER_ARTISTS, args.market or None)
    print(f"""
Section 3 counts
  artists                    {len(chosen)}
  songs (deduped per artist) {len(rows)}""")
    print_calls(api)
    save_section(paths, collect.SECTION_OTHER_ARTISTS, rows)
    return 0


def cmd_prefilter(args, paths: Paths) -> int:
    sections = load_sections(paths)
    if not sections:
        print("No section data yet - run the collection steps first.", file=sys.stderr)
        return 1
    titles = review.prefilter(sections)
    review.write_csv(paths.titles, titles, review.TITLE_COLUMNS)
    print("== Pre-filter (gazetteer) ==")
    print(f"  unique songs across sections: {len(titles)}  -> {paths.titles}")
    for s in SECTIONS:
        rows = [t for t in titles if t["section"] == s]
        if not rows:
            continue
        conf = Counter(t["gazetteer_conf"] for t in rows if t["gazetteer_conf"])
        print(f"  {s:17} {len(rows):6} songs, gazetteer hits: "
              f"high {conf['high']}, medium {conf['medium']}, low {conf['low']}")
    print("\nNext: Claude reviews review/titles.csv in batches -> review/decisions.csv, review/picks.csv")
    return 0


def cmd_build(args, paths: Paths) -> int:
    if not paths.titles.exists():
        print("Run `python scan.py prefilter` first.", file=sys.stderr)
        return 1
    titles = review.read_csv(paths.titles)
    if args.gazetteer_only or not paths.decisions.exists():
        if not args.gazetteer_only:
            print(f"No {paths.decisions} yet - building from the gazetteer only.")
        decisions = review.gazetteer_decisions(titles)
        gazetteer_only = True
    else:
        decisions = review.read_csv(paths.decisions)
        gazetteer_only = False
    picks = review.read_csv(paths.picks) if paths.picks.exists() else []

    cands, unmatched = review.candidates(titles, decisions)
    review.write_csv(paths.candidates, cands, review.CANDIDATE_COLUMNS)
    paths.shortlist.write_text(review.shortlist_md(cands, picks, gazetteer_only), encoding="utf-8")

    print("== Build ==")
    for s in SECTIONS:
        rows = [c for c in cands if c["section"] == s]
        print(f"  {s:17} yes {sum(c['confidence'] == 'yes' for c in rows):4}   "
              f"maybe {sum(c['confidence'] == 'maybe' for c in rows):4}")
    print(f"  -> {paths.candidates}\n  -> {paths.shortlist}")
    if unmatched:
        print(f"  WARNING: {len(unmatched)} decisions didn't match a title: " + "; ".join(unmatched[:10]))
    return 0


def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--refresh", action="store_true", help="ignore cache/ and re-fetch")
    common.add_argument("--delay", type=float, default=0.05, help="pause between live API calls (s)")
    common.add_argument("--market", default="from_token",
                        help='market for album lookups (default "from_token"; "" to omit)')
    common.add_argument("--top-n", type=int, default=30, help="number of top artists (default 30)")
    common.add_argument("--data-dir", default=str(ROOT / "data"))
    common.add_argument("--review-dir", default=str(ROOT / "review"))
    common.add_argument("--cache-dir", default=str(ROOT / "cache"))

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", parents=[common], help="log in and test each scope")
    sub.add_parser("playlists", parents=[common], help="section 1: Liked Songs + playlists")
    sub.add_parser("top", parents=[common], help="section 2: top artists' discographies")
    o = sub.add_parser("others", parents=[common], help="section 3: other playlist artists")
    o.add_argument("--min-artist-tracks", type=int, default=2,
                   help="only artists on at least this many playlist songs (default 2)")
    o.add_argument("--dry-run", action="store_true", help="list the artists, fetch nothing")
    sub.add_parser("prefilter", parents=[common], help="gazetteer pass -> review/titles.csv")
    b = sub.add_parser("build", parents=[common], help="candidates.csv + shortlist.md")
    b.add_argument("--gazetteer-only", action="store_true",
                   help="ignore review/decisions.csv and use the gazetteer guesses")
    args = p.parse_args(argv)

    paths = Paths(Path(args.data_dir), Path(args.review_dir))
    return {
        "auth": cmd_auth, "playlists": cmd_playlists, "top": cmd_top, "others": cmd_others,
        "prefilter": cmd_prefilter, "build": cmd_build,
    }[args.cmd](args, paths)


if __name__ == "__main__":
    sys.exit(main())
