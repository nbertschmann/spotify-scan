#!/usr/bin/env python3
"""Scan your Spotify library for songs whose titles contain a place name.

    python scan.py                     # collect everything, then match places
    python scan.py --match-only        # re-run place matching on data/songs.csv
    python scan.py --sections playlists,top_artists
    python scan.py --min-artist-tracks 3

Outputs:
    data/songs.csv              every collected track, with place columns
    data/place_matches.csv      only the tracks whose title has a place
    data/skipped_playlists.log  playlists that returned no items
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from dotenv import load_dotenv

from spotify_scan import collect
from spotify_scan.api import Api, make_client
from spotify_scan.places import CONF_RANK, annotate

SCOPE = "playlist-read-private playlist-read-collaborative user-top-read user-library-read"
ROOT = Path(__file__).resolve().parent
COLUMNS = [
    "section", "track_name", "artist", "album", "release_year", "spotify_url",
    "place_match", "place_type", "confidence", "source", "track_id",
]
SECTION_ORDER = {
    collect.SECTION_PLAYLISTS: 0,
    collect.SECTION_TOP_ARTISTS: 1,
    collect.SECTION_OTHER_ARTISTS: 2,
}


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel shows accented titles correctly
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def match_and_write(rows: list[dict], data_dir: Path):
    for r in rows:
        annotate(r)
    rows.sort(key=lambda r: (SECTION_ORDER.get(r["section"], 9), r["artist"].lower(), r["track_name"].lower()))
    write_csv(data_dir / "songs.csv", rows)

    matches = [r for r in rows if r["place_match"]]
    matches.sort(key=lambda r: (-CONF_RANK[r["confidence"]], SECTION_ORDER.get(r["section"], 9),
                                r["artist"].lower(), r["track_name"].lower()))
    write_csv(data_dir / "place_matches.csv", matches)

    by_conf = {c: sum(1 for r in matches if r["confidence"] == c) for c in CONF_RANK}
    print(f"\nWrote {len(rows)} tracks to {data_dir / 'songs.csv'}")
    print(f"Wrote {len(matches)} place matches to {data_dir / 'place_matches.csv'} "
          f"(high {by_conf['high']}, medium {by_conf['medium']}, low {by_conf['low']})")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sections", default="playlists,top_artists,playlist_artists",
                   help="comma-separated sections to collect (default: all three)")
    p.add_argument("--top-n", type=int, default=30, help="number of top artists (default 30)")
    p.add_argument("--min-artist-tracks", type=int, default=2,
                   help="section 3: only artists on at least this many playlist tracks (default 2)")
    p.add_argument("--market", default="from_token",
                   help='market for album lookups (default "from_token" = your country; "" to omit)')
    p.add_argument("--refresh", action="store_true", help="ignore cache/ and re-fetch everything")
    p.add_argument("--delay", type=float, default=0.05,
                   help="seconds to pause between live API calls (default 0.05)")
    p.add_argument("--match-only", action="store_true",
                   help="skip the API; re-run place matching on the existing data/songs.csv")
    p.add_argument("--data-dir", default=str(ROOT / "data"))
    p.add_argument("--cache-dir", default=str(ROOT / "cache"))
    args = p.parse_args(argv)

    data_dir = Path(args.data_dir)

    if args.match_only:
        songs = data_dir / "songs.csv"
        if not songs.exists():
            print(f"{songs} not found - run without --match-only first", file=sys.stderr)
            return 1
        match_and_write(read_csv(songs), data_dir)
        return 0

    load_dotenv(ROOT / ".env")
    sections = {s.strip() for s in args.sections.split(",") if s.strip()}
    unknown = sections - set(SECTION_ORDER)
    if unknown:
        p.error(f"unknown section(s): {', '.join(sorted(unknown))}")

    api = Api(make_client(SCOPE, cache_path=str(ROOT / ".spotify_token_cache")),
              Path(args.cache_dir), refresh=args.refresh, delay=args.delay)
    market = args.market or None
    rows: list[dict] = []

    # Section 1 is always fetched (cheap once cached): section 3 needs it.
    print("== Section 1: playlists ==")
    playlist_rows, skipped = collect.collect_playlists(api)
    if collect.SECTION_PLAYLISTS in sections:
        rows += playlist_rows
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "skipped_playlists.log").write_text(
        "playlist\trelation\towner\treason\n" + "\n".join(skipped) + "\n", encoding="utf-8")
    print(f"  {len(playlist_rows)} unique playlist tracks; {len(skipped)} playlists skipped "
          f"(see {data_dir / 'skipped_playlists.log'})")

    top_ids: set[str] = set()
    if collect.SECTION_TOP_ARTISTS in sections or collect.SECTION_OTHER_ARTISTS in sections:
        print(f"\n== Section 2: top {args.top_n} artists ==")
        if collect.SECTION_TOP_ARTISTS in sections:
            top_rows, top_ids = collect.collect_top_artists(api, args.top_n, market)
            rows += top_rows
        else:
            top_ids = {a["id"] for a in collect.top_artists(api, args.top_n)}
            print("  (only used to exclude them from section 3)")

    if collect.SECTION_OTHER_ARTISTS in sections:
        print("\n== Section 3: other playlist artists ==")
        rows += collect.collect_other_artists(
            api, playlist_rows, top_ids, args.min_artist_tracks, market)

    print(f"\nAPI calls: {api.live_calls} live, {api.cache_hits} from cache")
    match_and_write(rows, data_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
