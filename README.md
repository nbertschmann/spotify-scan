# spotify-scan

Finds songs in your Spotify library whose **title contains a place name**
(city, street, island, river, landmark, country, region...). Built for the
"I've Been Everywhere" music-league round.

Written against the Spotify Web API **as changed in Feb/Mar 2026**
(Development Mode restrictions, `/playlists/{id}/items`, no artist top-tracks,
no popularity).

## 1. Create the Spotify app

1. Go to <https://developer.spotify.com/dashboard> and log in with your
   (Premium) Spotify account. Accept the developer terms if asked.
2. Click **Create app**.
   - **App name**: `spotify-scan` (anything works)
   - **App description**: `Scan my library for place names`
   - **Redirect URIs**: `http://127.0.0.1:8888/callback`, then click **Add**.
     It must match exactly. Use `127.0.0.1`, not `localhost`, which Spotify
     no longer allows.
   - **Which API/SDKs are you planning to use?** tick **Web API**.
   - Accept the terms, then **Save**.
3. Open the app and go to **Settings**. Copy the **Client ID**, then click
   **View client secret** and copy the **Client secret**.
4. The app starts in **Development Mode**, which is fine. You're the owner,
   so you're automatically allowed to use it. To let a family member run it
   on *their* account, add their Spotify email under **User Management**
   (up to 5 users).

## 2. Install

```bash
git clone <this repo> && cd spotify-scan
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then paste your Client ID / secret into .env
```

`.env`:

```
SPOTIPY_CLIENT_ID=...
SPOTIPY_CLIENT_SECRET=...
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

`.env`, the token cache, `cache/` and `data/` are all git-ignored.

Optional: `pip install wordfreq` (about 50 MB) for a more precise
pre-filter. Without it, a built-in list of common words is used.

## 3. Run it step by step

Check the counts after each step before moving on.

```bash
python scan.py auth                          # 0. log in (browser opens once), probe each scope
python scan.py playlists                     # 1. Liked Songs + every playlist you own/collaborate on
python scan.py top                           # 2. top 30 artists -> full discographies
python scan.py others --dry-run              # 3a. list the artists section 3 would pull
python scan.py others --min-artist-tracks 2  # 3b. pull their discographies
python scan.py prefilter                     # 4. gazetteer pass -> review/titles.csv
#   Claude reviews review/titles.csv        ->  review/decisions.csv + review/picks.csv
python scan.py build                         # 5. data/candidates.csv + shortlist.md
```

Login uses the Authorization Code flow. spotipy catches the redirect on
`127.0.0.1:8888` and saves the token to `.spotify_token_cache`.

Scopes: `playlist-read-private playlist-read-collaborative user-top-read`
plus `user-library-read`, which Liked Songs (`current_user_saved_tracks`)
needs.

Common flags: `--refresh` (ignore `cache/`), `--top-n 30`, `--delay 0.05`,
`--market from_token`.

## What each step collects

```
Section 1  playlists         Liked Songs + every playlist that returns items
                                 |
                                 |  primary artist of each song, counted
                                 v
Section 2  top_artists       /me/top/artists long_term + medium_term
                             -> dedupe -> first 30 -> full discographies
Section 3  playlist_artists  playlist artists NOT in the top 30,
                             on >= N playlist songs (--min-artist-tracks)
                             -> full discographies

discography = /artists/{id}/albums?include_groups=album,single
              -> /albums/{id}/tracks
```

**Duplicates.** Within a section, rows are one per song + primary artist.
Remasters, "(Live)", "- Remastered 2011", "(Deluxe Edition)" and the same
song on several playlists all collapse to one row. The kept version is the
plain studio title, then the earliest release. In section 1,
`source_playlist` lists every playlist the song was on.

**Skipped playlists.** Followed playlists you don't own return no items in
Development Mode. They are skipped and listed in `data/skipped_playlists.log`.

## Classification

1. **`prefilter`** writes `review/titles.csv`: one row per unique song
   across all sections, with the gazetteer's guess (`gazetteer_place`,
   `gazetteer_type`, `gazetteer_conf`). A song in several sections is
   listed under the first one (playlists, then top artists, then others),
   and `also_in` names the rest.
2. **Claude reviews every title** in batches, not just gazetteer hits. This
   catches things like "Penny Lane", "Kokomo" and "Mull of Kintyre", and
   rejects person names like "Jolene". The review writes:
   - `review/decisions.csv`: `id, track_name, artist, place, place_type,
     confidence (yes|maybe), note`. Only places go in; anything not listed
     is a no.
   - `review/picks.csv`: `rank, track_name, artist, why` for the top 10.
3. **`build`** joins those into:
   - `data/candidates.csv`: `section, track_name, artist, place, place_type,
     confidence, spotify_url`
   - `shortlist.md`: the top 10 first, then the 3 sections, sorted by
     confidence then artist

   Without `review/decisions.csv`, `build` falls back to the gazetteer
   alone. You can force that with `--gazetteer-only`.

To get the titles to Claude, commit and push `review/titles.csv`, or attach
it in the chat. `review/` is not git-ignored, so it's easy to push. It does
contain your listening data, so keep the repo private.

## Output files

| File | Contents |
|---|---|
| `data/songs.csv` | `section, track_name, artist, album, release_year, spotify_url, track_id, source_playlist` |
| `data/sections/*.csv` | the same per section, plus the primary artist id (working files) |
| `data/top_artists.csv` | your top artists, ranked |
| `data/skipped_playlists.log` | playlists with no readable items, and why |
| `review/titles.csv` | unique songs + gazetteer guess, the input for review |
| `data/candidates.csv` | the classified places |
| `shortlist.md` | the final shortlist |

## How the gazetteer pre-filter works

`spotify_scan/places.py`:

1. Cleans the title by dropping ` - Remastered 2011`, `(Live at ...)` and
   `(feat. ...)`.
2. Looks up word sequences (longest first) in a gazetteer built from:
   - a curated list of song-famous places (`CURATED`, easy to extend)
   - countries, US states and continents (geonamescache), and Canadian
     provinces
   - world cities with population of 15k or more, skipping names that are
     everyday words or first names ("Time", "Best", "Mary") unless the city
     is large
3. Pattern-matches `Route 66` / `Highway 61` style names, and
   `<Capitalized words> Street/Avenue/River/Island/Bay/...`.

Each guess is rated high, medium or low. To add a place, add a line to
`CURATED`, prefixed with `?` for medium. Then re-run `python scan.py
prefilter`, which doesn't call the API.

## API notes (2026 changes)

- Playlist contents come from `GET /playlists/{id}/items`. Each entry's track
  is under `item` (it used to be `track`), and both are handled.
- No `/artists/{id}/top-tracks`. Discographies are built from albums and
  album tracks instead.
- `/artists/{id}/albums` max page size is now 10. Any endpoint that rejects
  a page size with a 400 is retried with half the limit.
- Popularity fields aren't used.
- 429s sleep for `Retry-After` and retry. 5xx errors back off exponentially.
- Every raw response is cached as JSON in `cache/`, as are 403/404 errors, so
  re-runs make almost no API calls. Delete `cache/` or pass `--refresh` to
  start fresh.
