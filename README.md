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

Optional: `pip install wordfreq` (about 50 MB) to filter city names that are
also common words more precisely. Without it, a built-in list is used.

## 3. Run

```bash
python scan.py
```

The first run opens a browser for Spotify login. After you approve, spotipy
catches the redirect on `127.0.0.1:8888` and saves the token to
`.spotify_token_cache`, so you only log in once.

Scopes requested: `playlist-read-private playlist-read-collaborative
user-top-read user-library-read`. `user-library-read` is needed for Liked
Songs.

### Options

| Flag | Default | What it does |
|---|---|---|
| `--min-artist-tracks N` | 2 | Section 3: only artists on at least N playlist tracks |
| `--top-n N` | 30 | Number of top artists |
| `--sections a,b` | all | Any of `playlists,top_artists,playlist_artists` |
| `--match-only` | | No API calls: re-run place matching on `data/songs.csv` |
| `--refresh` | | Ignore `cache/` and re-fetch |
| `--market` | `from_token` | Market for album lookups (`""` to omit) |
| `--delay S` | 0.05 | Pause between live API calls |

## What it collects

```
Section 1  playlists         Liked Songs + every playlist that returns items
                                 |
                                 |  primary artists, counted
                                 v
Section 2  top_artists       /me/top/artists long_term + medium_term
                             -> dedupe -> first 30 -> full discographies
Section 3  playlist_artists  playlist artists NOT in top 30,
                             on >= 2 playlist tracks -> full discographies

discography = /artists/{id}/albums?include_groups=album,single
              -> /albums/{id}/tracks   (oldest release first,
                                         remasters/live/deluxe deduped)
```

Playlists that return no items, typically followed playlists you don't own
(Development Mode only returns contents of playlists you own or collaborate
on), are skipped and listed in `data/skipped_playlists.log`.

## Output

| File | Contents |
|---|---|
| `data/songs.csv` | every collected track |
| `data/place_matches.csv` | only titles with a place, sorted high to low confidence |
| `data/skipped_playlists.log` | playlists skipped, and why |

Columns: `section, track_name, artist, album, release_year, spotify_url,
place_match, place_type, confidence, source, track_id`

- `source`: the playlist(s) a track came from, or the artist whose
  discography it came from
- `place_type`: `city`, `country`, `us_state`, `continent`, `river`,
  `street`, `island`, `landmark`, `region`, or `pattern`
- `confidence`:
  - **high**: unambiguous (Memphis, Penny Lane, Route 66, Alabama)
  - **medium**: could be a place or something else (Phoenix, Downtown,
    Chelsea, Neverland)
  - **low**: only matched the "<Capitalized> Street/River/Island..."
    pattern, e.g. "Mercy Street"

Review the medium and low rows by hand.

## How place matching works

`spotify_scan/places.py`:

1. Cleans the title by dropping ` - Remastered 2011`, `(Live at ...)` and
   `(feat. ...)`.
2. Looks up word sequences (longest first) in a gazetteer built from:
   - a curated list of song-famous places (`CURATED`, easy to extend)
   - countries, US states and continents (geonamescache)
   - world cities with population of 15k or more, skipping names that are
     everyday words or first names ("Time", "Best", "Mary") unless the city
     is large
3. Pattern-matches `Route 66` / `Highway 61` style names, and
   `<Capitalized words> Street/Avenue/River/Island/Bay/...`.

To add a place, add a line to the right block in `CURATED`. Prefix it with
`?` to mark it medium confidence. Then run `python scan.py --match-only`,
which re-matches without calling the API.

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
