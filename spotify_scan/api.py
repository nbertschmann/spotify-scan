"""Thin Spotify Web API wrapper: disk cache, 429 backoff, offset pagination.

Every raw response is stored as JSON under cache/ so re-runs don't re-fetch.
Calls go through spotipy's low-level ``_get`` so we control the exact
endpoint paths (the Feb/Mar 2026 API changes renamed several of them).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlencode

import spotipy
from spotipy.exceptions import SpotifyException

API_ROOT = "https://api.spotify.com/v1/"

# Errors that won't change on a re-run (no access / gone). Cached so we
# don't keep asking for playlists we can't read.
CACHEABLE_ERRORS = {403, 404}


class ApiError(Exception):
    def __init__(self, status: int, message: str, url: str):
        super().__init__(f"HTTP {status} for {url}: {message}")
        self.status = status
        self.message = message
        self.url = url


def make_client(scope: str, cache_path: str = ".spotify_token_cache") -> spotipy.Spotify:
    """Spotify client using the Authorization Code flow.

    Reads SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET / SPOTIPY_REDIRECT_URI from
    the environment (load .env before calling). spotipy's own retry logic is
    disabled so that 429s surface here with their Retry-After header.
    """
    auth = spotipy.SpotifyOAuth(scope=scope, cache_path=cache_path, open_browser=True)
    return spotipy.Spotify(
        auth_manager=auth,
        requests_timeout=30,
        retries=0,
        status_retries=0,
        # spotipy treats an empty tuple as "use defaults (incl. 429)", so pass
        # a status that never occurs to switch automatic status retries off.
        status_forcelist=(599,),
        backoff_factor=0,
    )


class Api:
    def __init__(
        self,
        sp: spotipy.Spotify,
        cache_dir: Path,
        refresh: bool = False,
        delay: float = 0.0,
        log=print,
    ):
        self.sp = sp
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.refresh = refresh
        self.delay = delay
        self.log = log
        self.live_calls = 0
        self.cache_hits = 0

    # -- cache ---------------------------------------------------------------

    def _cache_file(self, url: str) -> Path:
        path = url[len(API_ROOT):] if url.startswith(API_ROOT) else url
        slug = re.sub(r"[^A-Za-z0-9]+", "_", path.split("?")[0]).strip("_")[:80]
        digest = hashlib.sha1(url.encode()).hexdigest()[:12]
        return self.cache_dir / f"{slug}__{digest}.json"

    @staticmethod
    def build_url(path: str, params: dict | None = None) -> str:
        url = path if path.startswith("http") else API_ROOT + path.lstrip("/")
        params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        if params:
            url += "?" + urlencode(sorted(params.items()))
        return url

    # -- requests ------------------------------------------------------------

    def get(self, path: str, **params) -> dict:
        url = self.build_url(path, params)
        cache_file = self._cache_file(url)

        if cache_file.exists() and not self.refresh:
            self.cache_hits += 1
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and "__error__" in payload:
                err = payload["__error__"]
                raise ApiError(err["status"], err["message"] + " (cached)", url)
            return payload

        data = self._fetch(url, cache_file)
        cache_file.write_text(json.dumps({} if data is None else data), encoding="utf-8")
        return {} if data is None else data

    def _fetch(self, url: str, cache_file: Path):
        server_errors = 0
        while True:
            if self.delay:
                time.sleep(self.delay)
            try:
                self.live_calls += 1
                return self.sp._get(url)
            except SpotifyException as e:
                status = e.http_status
                if status == 429:
                    headers = e.headers or {}
                    retry_after = headers.get("Retry-After") or headers.get("retry-after") or 5
                    wait = int(float(retry_after)) + 1
                    self.log(f"  429 rate limited - sleeping {wait}s (Retry-After)")
                    time.sleep(wait)
                    continue
                if status in (500, 502, 503, 504) and server_errors < 5:
                    server_errors += 1
                    wait = 2 ** server_errors
                    self.log(f"  HTTP {status} - retrying in {wait}s")
                    time.sleep(wait)
                    continue
                message = str(e.msg).splitlines()[-1].strip() if e.msg else ""
                if status in CACHEABLE_ERRORS:
                    cache_file.write_text(
                        json.dumps({"__error__": {"status": status, "message": message}}),
                        encoding="utf-8",
                    )
                raise ApiError(status, message, url) from e

    def paginate(self, path: str, limit: int = 50, max_items: int | None = None, **params):
        """Yield every item of an offset-paged endpoint.

        Uses explicit limit/offset (not the ``next`` URL) so cache keys are
        stable. If the API rejects the page size with a 400 (several max
        limits were lowered in 2026), the limit is halved and retried.
        """
        offset = 0
        yielded = 0
        while True:
            try:
                page = self.get(path, limit=limit, offset=offset, **params)
            except ApiError as e:
                if e.status == 400 and limit > 1:
                    limit = max(1, limit // 2)
                    self.log(f"  400 from {path} - retrying with limit={limit}")
                    continue
                raise
            items = page.get("items") or []
            for item in items:
                yield item
                yielded += 1
                if max_items and yielded >= max_items:
                    return
            offset += len(items)
            total = page.get("total")
            if not items or not page.get("next") or (total is not None and offset >= total):
                return
