"""Collect tracks in three sections: playlists, top artists, other playlist artists."""

from __future__ import annotations

from collections import Counter

from .api import Api, ApiError
from .places import clean_title, song_key

SECTION_PLAYLISTS = "playlists"
SECTION_TOP_ARTISTS = "top_artists"
SECTION_OTHER_ARTISTS = "playlist_artists"
SECTIONS = [SECTION_PLAYLISTS, SECTION_TOP_ARTISTS, SECTION_OTHER_ARTISTS]


def _year(album: dict | None) -> str:
    date = (album or {}).get("release_date") or ""
    return date[:4]


def _rank(track: dict, album: dict | None = None) -> tuple:
    """Lower is better: plain titles beat "(Live)" / "- Remastered" versions,
    then the earliest release wins."""
    name = (track.get("name") or "").strip()
    is_version = clean_title(name) != name
    return (is_version, _year(album or track.get("album")) or "9999")


def track_row(track: dict, section: str, album: dict | None = None,
              source_playlist: str = "") -> dict:
    album = album or track.get("album") or {}
    artists = track.get("artists") or []
    return {
        "section": section,
        "track_name": track.get("name") or "",
        "artist": ", ".join(a.get("name", "") for a in artists),
        "album": album.get("name") or "",
        "release_year": _year(album),
        "spotify_url": (track.get("external_urls") or {}).get("spotify", ""),
        "track_id": track.get("id") or "",
        "source_playlist": source_playlist,
        # internal, used by section 3 - not written to songs.csv
        "primary_artist_id": artists[0].get("id", "") if artists else "",
        "primary_artist": artists[0].get("name", "") if artists else "",
    }


def _entry_track(entry: dict) -> dict | None:
    """Pull the track out of a playlist / saved-tracks entry.

    New (2026) playlist responses use ``item``; saved tracks still use
    ``track``. Episodes and local files are skipped.
    """
    track = entry.get("item") or entry.get("track")
    if not track or track.get("type", "track") != "track":
        return None
    if track.get("is_local") or not track.get("id"):
        return None
    return track


# -- Auth test -----------------------------------------------------------------


def auth_check(api: Api, log=print) -> bool:
    """Log in and probe one endpoint per scope."""
    me = api.get("me")
    log(f"Logged in as: {me.get('display_name') or me.get('id')} (id {me.get('id')}, "
        f"country {me.get('country', '?')}, product {me.get('product', '?')})")
    ok = True
    probes = [
        ("playlist-read-private", "me/playlists", {}),
        ("user-library-read (Liked Songs)", "me/tracks", {}),
        ("user-top-read", "me/top/artists", {"time_range": "long_term"}),
    ]
    for label, path, params in probes:
        try:
            page = api.get(path, limit=1, **params)
            log(f"  OK    {label:34} total={page.get('total', '?')}")
        except ApiError as e:
            ok = False
            log(f"  FAIL  {label:34} HTTP {e.status} {e.message}")
    return ok


# -- Section 1: playlists ------------------------------------------------------


def collect_playlists(api: Api, log=print) -> tuple[list[dict], list[str], dict]:
    """Every track in Liked Songs and every playlist that returns items.

    One row per song + primary artist: the same track on several playlists,
    and remaster / live / deluxe versions of it, collapse to one row whose
    ``source_playlist`` lists every playlist it was on.

    Returns (rows, skipped, stats).
    """
    me = api.get("me")
    my_id = me.get("id")

    rows: dict[str, dict] = {}
    seen_ids: set[str] = set()
    stats = Counter()
    skipped: list[str] = []

    def add(track: dict, playlist: str):
        stats["entries"] += 1
        seen_ids.add(track["id"])
        artists = track.get("artists") or [{}]
        key = song_key(track.get("name", ""), artists[0].get("id", ""))
        row = rows.get(key)
        if row is None:
            rows[key] = track_row(track, SECTION_PLAYLISTS, source_playlist=playlist)
            rows[key]["_rank"] = _rank(track)
            return
        if playlist not in row["source_playlist"].split("; "):
            row["source_playlist"] += f"; {playlist}"
        if _rank(track) < row["_rank"]:
            rows[key] = track_row(track, SECTION_PLAYLISTS, source_playlist=row["source_playlist"])
            rows[key]["_rank"] = _rank(track)

    # Liked Songs
    try:
        n = 0
        for entry in api.paginate("me/tracks", 50):
            track = _entry_track(entry)
            if track:
                add(track, "Liked Songs")
                n += 1
        stats["playlists_read"] += 1
        log(f"  + Liked Songs: {n} tracks")
    except ApiError as e:
        skipped.append(f"Liked Songs\t-\t-\tHTTP {e.status}: {e.message}")
        log(f"  - Liked Songs: skipped (HTTP {e.status} {e.message})")

    playlists = [p for p in api.paginate("me/playlists", 50) if p]
    log(f"  {len(playlists)} playlists in your library")

    for pl in playlists:
        name = pl.get("name") or pl.get("id")
        owner = (pl.get("owner") or {}).get("id")
        relation = "owned" if owner == my_id else ("collaborative" if pl.get("collaborative") else "followed")
        try:
            entries = list(api.paginate(f"playlists/{pl['id']}/items", 50, additional_types="track"))
        except ApiError as e:
            skipped.append(f"{name}\t{relation}\t{owner}\tHTTP {e.status}: {e.message}")
            log(f"  - {name}: skipped ({relation}, HTTP {e.status})")
            continue
        tracks = [t for t in (_entry_track(e) for e in entries) if t]
        if not tracks:
            skipped.append(f"{name}\t{relation}\t{owner}\tno items returned")
            log(f"  - {name}: skipped ({relation}, no items returned)")
            continue
        for t in tracks:
            add(t, name)
        stats["playlists_read"] += 1
        log(f"  + {name}: {len(tracks)} tracks ({relation})")

    stats["unique_track_ids"] = len(seen_ids)
    stats["songs"] = len(rows)
    stats["playlists_skipped"] = len(skipped)
    return list(rows.values()), skipped, dict(stats)


# -- Section 2: top artists ----------------------------------------------------


def top_artists(api: Api, n: int = 30, log=print) -> list[dict]:
    """Top artists: long_term first, then medium_term, deduped, first ``n``."""
    merged: dict[str, dict] = {}
    for time_range in ("long_term", "medium_term"):
        count = 0
        try:
            for artist in api.paginate("me/top/artists", 50, max_items=50, time_range=time_range):
                merged.setdefault(artist["id"], artist)
                count += 1
        except ApiError as e:
            log(f"  top artists ({time_range}) failed: HTTP {e.status} {e.message}")
        log(f"  {time_range}: {count} artists")
    return list(merged.values())[:n]


# -- Discographies -------------------------------------------------------------


def discography(api: Api, artist_id: str, artist_name: str, section: str,
                market: str | None = "from_token", log=print) -> list[dict]:
    """All tracks on the artist's albums + singles, one row per song.

    /artists/{id}/top-tracks is gone, so this walks /artists/{id}/albums and
    then /albums/{id}/tracks. Each song keeps one version: a plain title over
    live / remastered / deluxe versions, then the earliest release.
    """
    try:
        albums = list(api.paginate(
            f"artists/{artist_id}/albums", 10,
            include_groups="album,single", market=market,
        ))
    except ApiError as e:
        log(f"    {artist_name}: albums failed (HTTP {e.status} {e.message})")
        return []

    best: dict[str, tuple[tuple, dict, dict]] = {}  # song key -> (rank, track, album)
    for album in albums:
        try:
            tracks = list(api.paginate(f"albums/{album['id']}/tracks", 50, market=market))
        except ApiError as e:
            log(f"    {artist_name} / {album.get('name')}: tracks failed (HTTP {e.status})")
            continue
        for t in tracks:
            if not t or not t.get("id"):
                continue
            if artist_id not in {a.get("id") for a in t.get("artists") or []}:
                continue
            key = song_key(t.get("name") or "", artist_id)
            rank = _rank(t, album)
            if key not in best or rank < best[key][0]:
                best[key] = (rank, t, album)
    rows = [track_row(t, section, album=album) for _, t, album in best.values()]
    log(f"    {artist_name}: {len(albums)} releases -> {len(rows)} songs")
    return rows


def collect_discographies(api: Api, artists: list[tuple[str, str]], section: str,
                          market: str | None = "from_token", log=print) -> list[dict]:
    rows: list[dict] = []
    for i, (aid, name) in enumerate(artists, 1):
        log(f"  [{i}/{len(artists)}] {name}")
        rows += discography(api, aid, name, section, market, log)
    return rows


def other_artists(playlist_rows: list[dict], exclude_ids: set[str],
                  min_tracks: int = 2) -> list[tuple[str, str, int]]:
    """Primary artists from section 1 not in ``exclude_ids``, on >= min_tracks songs."""
    counts = Counter(r["primary_artist_id"] for r in playlist_rows if r.get("primary_artist_id"))
    names = {r["primary_artist_id"]: r["primary_artist"] for r in playlist_rows}
    return [
        (aid, names[aid], c) for aid, c in counts.most_common()
        if c >= min_tracks and aid not in exclude_ids
    ]
