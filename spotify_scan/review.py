"""Classification: gazetteer pre-filter -> human/Claude review -> outputs.

    prefilter   data/sections/*.csv  ->  review/titles.csv
                (one row per unique song, with the gazetteer's guess)
    (review)    Claude reads review/titles.csv in batches and writes
                review/decisions.csv (yes/maybe rows) and review/picks.csv
    build       titles + decisions + picks  ->  data/candidates.csv, shortlist.md
"""

from __future__ import annotations

import csv
from pathlib import Path

from .collect import SECTIONS
from .places import CONF_RANK, find_places, song_key

TITLE_COLUMNS = [
    "id", "section", "also_in", "track_name", "artist", "release_year", "spotify_url",
    "gazetteer_place", "gazetteer_type", "gazetteer_conf",
]
DECISION_COLUMNS = ["id", "track_name", "artist", "place", "place_type", "confidence", "note"]
PICK_COLUMNS = ["rank", "track_name", "artist", "why"]
CANDIDATE_COLUMNS = ["section", "track_name", "artist", "place", "place_type", "confidence", "spotify_url"]

SECTION_TITLES = {
    "playlists": "Section 1 - From my playlists",
    "top_artists": "Section 2 - Top artists' discographies",
    "playlist_artists": "Section 3 - Other playlist artists' discographies",
}
CONFIDENCE_ORDER = {"yes": 0, "maybe": 1}


def read_csv(path: Path) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], columns: list[str]):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# -- prefilter -----------------------------------------------------------------


def unique_songs(section_rows: dict[str, list[dict]]) -> list[dict]:
    """One row per song + primary artist across all sections.

    A song is listed under the first section it appears in (playlists, then
    top artists, then other artists); ``also_in`` names the others.
    """
    songs: dict[str, dict] = {}
    for section in SECTIONS:
        for r in section_rows.get(section, []):
            key = song_key(r["track_name"], r.get("primary_artist_id") or r["artist"])
            if key in songs:
                also = songs[key]["also_in"]
                if section != songs[key]["section"] and section not in also.split("; "):
                    songs[key]["also_in"] = f"{also}; {section}" if also else section
                continue
            songs[key] = {**r, "section": section, "also_in": ""}
    return list(songs.values())


def prefilter(section_rows: dict[str, list[dict]]) -> list[dict]:
    out = []
    for r in unique_songs(section_rows):
        matches = find_places(r["track_name"])
        r["gazetteer_place"] = "; ".join(m[0] for m in matches)
        r["gazetteer_type"] = "; ".join(m[1] for m in matches)
        r["gazetteer_conf"] = matches[0][2] if matches else ""
        out.append(r)
    out.sort(key=lambda r: (SECTIONS.index(r["section"]), r["artist"].lower(), r["track_name"].lower()))
    for i, r in enumerate(out, 1):
        r["id"] = str(i)
    return out


# -- build ---------------------------------------------------------------------


def _ident(track_name: str, artist: str) -> tuple[str, str]:
    return (track_name.strip().lower(), artist.strip().lower())


def gazetteer_decisions(titles: list[dict]) -> list[dict]:
    """Fallback when there is no review: high -> yes, medium/low -> maybe."""
    out = []
    for t in titles:
        if not t.get("gazetteer_place"):
            continue
        out.append({
            "id": t["id"], "track_name": t["track_name"], "artist": t["artist"],
            "place": t["gazetteer_place"].split("; ")[0],
            "place_type": t["gazetteer_type"].split("; ")[0],
            "confidence": "yes" if t["gazetteer_conf"] == "high" else "maybe",
            "note": "gazetteer only",
        })
    return out


def candidates(titles: list[dict], decisions: list[dict]) -> tuple[list[dict], list[str]]:
    """Join decisions to titles. Returns (candidate rows, unmatched decision labels)."""
    by_ident = {_ident(t["track_name"], t["artist"]): t for t in titles}
    rows, unmatched = [], []
    for d in decisions:
        conf = (d.get("confidence") or "").strip().lower()
        if conf not in CONFIDENCE_ORDER:
            continue
        t = by_ident.get(_ident(d["track_name"], d["artist"]))
        if t is None:
            unmatched.append(f"{d['track_name']} - {d['artist']}")
            continue
        rows.append({
            "section": t["section"], "track_name": t["track_name"], "artist": t["artist"],
            "place": d["place"], "place_type": d["place_type"], "confidence": conf,
            "spotify_url": t["spotify_url"],
        })
    rows.sort(key=lambda r: (SECTIONS.index(r["section"]), CONFIDENCE_ORDER[r["confidence"]],
                             r["artist"].lower(), r["track_name"].lower()))
    return rows, unmatched


def _line(c: dict) -> str:
    return (f"- **{c['track_name']}** - {c['artist']} ({c['place']}, {c['place_type']}) "
            f"[Spotify]({c['spotify_url']})")


def shortlist_md(cands: list[dict], picks: list[dict], gazetteer_only: bool = False) -> str:
    by_ident = {_ident(c["track_name"], c["artist"]): c for c in cands}
    out = ["# I've Been Everywhere - shortlist", ""]
    yes = sum(c["confidence"] == "yes" for c in cands)
    out.append(f"{len(cands)} candidates ({yes} yes, {len(cands) - yes} maybe).")
    if gazetteer_only:
        out.append("")
        out.append("> Built from the gazetteer only - not yet reviewed. Expect misses and false positives.")
    out.append("")

    out.append("## Top 10 picks")
    out.append("")
    if picks:
        for p in sorted(picks, key=lambda p: int(p["rank"]))[:10]:
            c = by_ident.get(_ident(p["track_name"], p["artist"]))
            where = f" ({c['place']}, {c['place_type']})" if c else ""
            link = f" [Spotify]({c['spotify_url']})" if c else ""
            out.append(f"{p['rank']}. **{p['track_name']}** - {p['artist']}{where}{link}  ")
            out.append(f"   {p['why']}")
    else:
        out.append("_Not picked yet._")
    out.append("")

    for section in SECTIONS:
        rows = [c for c in cands if c["section"] == section]
        out.append(f"## {SECTION_TITLES[section]} ({len(rows)})")
        out.append("")
        for conf, label in (("yes", "Yes"), ("maybe", "Maybe")):
            group = [c for c in rows if c["confidence"] == conf]
            if not group:
                continue
            out.append(f"### {label} ({len(group)})")
            out.append("")
            out += [_line(c) for c in group]
            out.append("")
        if not rows:
            out.append("_None._")
            out.append("")
    return "\n".join(out)
