"""Collect tracks in three sections: playlists, top artists, other playlist artists."""

from __future__ import annotations

from collections import Counter

from .api import Api, ApiError
from .places import clean_title

SECTION_PLAYLISTS = "playlists"
SECTION_TOP_ARTISTS = "top_artists"
SECTION_OTHER_ARTISTS = "playlist_artists"


def _year(album: dict | None) -> str:
    date = (album or {}).get("release_date") or ""
    return date[:4]


def track_row(track: dict, section: str, source: str, album: dict | None = None) -> dict:
    album = album or track.get("album") or {}
    artists = track.get("artists") or []
    return {
        "section": section,
        "track_name": track.get("name") or "",
        "artist": ", ".join(a.get("name", "") for a in artists),
        "album": album.get("name") or "",
        "release_year": _year(album),
        "spotify_url": (track.get("external_urls") or {}).get("spotify", ""),
        "source": source,
        "track_id": track.get("id") or "",
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


# -- Section 1: playlists ----------------------------------------------------


def collect_playlists(api: Api, log=print) -> tuple[list[dict], list[str]]:
    """Every track in Liked Songs and every playlist that returns items.

    Returns (rows, skipped) where ``skipped`` describes playlists with no
    readable items (typically followed playlists in Development Mode).
    """
    me = api.get("me")
    my_id = me.get("id")
    log(f"Logged in as {me.get('display_name') or my_id}")

    rows_by_id: dict[str, dict] = {}
    skipped: list[str] = []

    def add(track: dict, source: str):
        row = rows_by_id.get(track["id"])
        if row is None:
            rows_by_id[track["id"]] = track_row(track, SECTION_PLAYLISTS, source)
        elif source not in row["source"].split("; "):
            row["source"] += f"; {source}"

    # Liked Songs
    try:
        n = 0
        for entry in api.paginate("me/tracks", 50):
            track = _entry_track(entry)
            if track:
                add(track, "Liked Songs")
                n += 1
        log(f"  Liked Songs: {n} tracks")
    except ApiError as e:
        skipped.append(f"Liked Songs\t(HTTP {e.status}: {e.message})")
        log(f"  Liked Songs: skipped (HTTP {e.status} {e.message})")

    playlists = list(api.paginate("me/playlists", 50))
    log(f"  Found {len(playlists)} playlists in your library")

    for pl in playlists:
        if not pl:
            continue
        name = pl.get("name") or pl.get("id")
        owner = (pl.get("owner") or {}).get("id")
        relation = "owned" if owner == my_id else ("collaborative" if pl.get("collaborative") else "followed")
        try:
            entries = list(api.paginate(f"playlists/{pl['id']}/items", 50, additional_types="track"))
        except ApiError as e:
            skipped.append(f"{name}\t{relation}\towner={owner}\t(HTTP {e.status}: {e.message})")
            log(f"  - {name}: skipped ({relation}, HTTP {e.status})")
            continue
        tracks = [t for t in (_entry_track(e) for e in entries) if t]
        if not tracks:
            skipped.append(f"{name}\t{relation}\towner={owner}\t(no items returned)")
            log(f"  - {name}: skipped ({relation}, no items returned)")
            continue
        for t in tracks:
            add(t, name)
        log(f"  + {name}: {len(tracks)} tracks ({relation})")

    return list(rows_by_id.values()), skipped


# -- Section 2: top artists --------------------------------------------------


def top_artists(api: Api, n: int = 30, log=print) -> list[dict]:
    """Top artists: long_term first, then medium_term, deduped, first ``n``."""
    merged: dict[str, dict] = {}
    for time_range in ("long_term", "medium_term"):
        try:
            for artist in api.paginate("me/top/artists", 50, max_items=50, time_range=time_range):
                merged.setdefault(artist["id"], artist)
        except ApiError as e:
            log(f"  top artists ({time_range}) failed: HTTP {e.status} {e.message}")
    return list(merged.values())[:n]


# -- Discographies -----------------------------------------------------------


def discography(api: Api, artist_id: str, artist_name: str, section: str,
                market: str | None = "from_token", log=print) -> list[dict]:
    """All tracks on the artist's albums + singles, deduped by title.

    /artists/{id}/top-tracks is gone, so this walks /artists/{id}/albums and
    then /albums/{id}/tracks. Albums are processed oldest first so the
    earliest release of a song wins over later remasters / deluxe editions.
    """
    try:
        albums = list(api.paginate(
            f"artists/{artist_id}/albums", 10,
            include_groups="album,single", market=market,
        ))
    except ApiError as e:
        log(f"    {artist_name}: albums failed (HTTP {e.status} {e.message})")
        return []

    albums.sort(key=lambda a: a.get("release_date") or "9999")
    seen: set[str] = set()
    rows: list[dict] = []
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
            key = clean_title(t.get("name") or "").lower()
            if key in seen:
                continue
            seen.add(key)
            rows.append(track_row(t, section, artist_name, album=album))
    log(f"    {artist_name}: {len(albums)} releases, {len(rows)} unique tracks")
    return rows


def collect_top_artists(api: Api, n: int = 30, market: str | None = "from_token",
                        log=print) -> tuple[list[dict], set[str]]:
    artists = top_artists(api, n, log=log)
    log(f"  {len(artists)} top artists: " + ", ".join(a["name"] for a in artists))
    rows: list[dict] = []
    for i, a in enumerate(artists, 1):
        log(f"  [{i}/{len(artists)}] {a['name']}")
        rows += discography(api, a["id"], a["name"], SECTION_TOP_ARTISTS, market, log)
    return rows, {a["id"] for a in artists}


def collect_other_artists(api: Api, playlist_rows: list[dict], exclude_ids: set[str],
                          min_tracks: int = 2, market: str | None = "from_token",
                          log=print) -> list[dict]:
    """Primary artists from playlists that aren't top artists, on >= min_tracks tracks."""
    counts = Counter(r["primary_artist_id"] for r in playlist_rows if r["primary_artist_id"])
    names = {r["primary_artist_id"]: r["primary_artist"] for r in playlist_rows}
    chosen = [
        aid for aid, c in counts.most_common()
        if c >= min_tracks and aid not in exclude_ids
    ]
    log(f"  {len(chosen)} other artists appear on >= {min_tracks} playlist tracks")
    rows: list[dict] = []
    for i, aid in enumerate(chosen, 1):
        log(f"  [{i}/{len(chosen)}] {names[aid]} ({counts[aid]} playlist tracks)")
        rows += discography(api, aid, names[aid], SECTION_OTHER_ARTISTS, market, log)
    return rows
