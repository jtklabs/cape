"""UXI API client: OAuth token exchange, rate-limited GETs, pagination."""
from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

from .config import API_BASES, MIN_REQUEST_INTERVAL, TOKEN_URL
from .secrets import Credentials

log = logging.getLogger(__name__)


class UXIClient:
    """Thin client around the UXI REST API.

    Handles client-credentials auth (with transparent token refresh) and
    cursor-based pagination. One instance is safe to reuse across collectors.
    """

    def __init__(self, creds: Credentials, region: str = "us-west", timeout: int = 30):
        if region not in API_BASES:
            raise ValueError(f"Unknown region '{region}'; choose from {list(API_BASES)}")
        import requests  # deferred so the package imports without it installed

        self._creds = creds
        self.base = API_BASES[region]
        self.timeout = timeout
        self._session = requests.Session()
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._last_request: float = 0.0

    # --- auth -------------------------------------------------------------
    def _ensure_token(self) -> str:
        # Refresh 60s before expiry to avoid edge-of-expiry failures.
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        log.info("Requesting new access token")
        resp = self._session.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._creds.client_id,
                "client_secret": self._creds.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout,
        )
        if not resp.ok:
            raise RuntimeError(f"Token request failed ({resp.status_code}): {resp.text}")
        body = resp.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + int(body.get("expires_in", 7199))
        return self._token

    # --- rate limiting ----------------------------------------------------
    def _throttle(self) -> None:
        delta = time.time() - self._last_request
        if delta < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - delta)
        self._last_request = time.time()

    # --- requests ---------------------------------------------------------
    def _get(self, url: str, params: dict | None = None) -> dict:
        self._throttle()
        token = self._ensure_token()
        resp = self._session.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=self.timeout,
        )
        if resp.status_code == 401:  # token may have been revoked early; retry once
            self._token = None
            token = self._ensure_token()
            self._throttle()
            resp = self._session.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=self.timeout,
            )
        if not resp.ok:
            raise RuntimeError(f"GET {url} failed ({resp.status_code}): {resp.text}")
        return resp.json()

    def get_one(self, path: str, params: dict | None = None) -> dict:
        """GET a single (non-paginated) resource, e.g. /sensors/{id}/status."""
        return self._get(self.base + path, params=params)

    def get_all(self, path: str, params: dict | None = None) -> list[dict]:
        """Fetch every page of a paginated collection.

        The UXI API returns {"items": [...], "count": N, "next": <token|null>}.
        The next-page token is passed back as the `next` query parameter
        (verified against the live API — `cursor` is silently ignored).
        `next` may also be a full URL on some endpoints; both are handled.
        A repeated token aborts the loop as a safety net against spinning.
        """
        items: list[dict] = []
        url = self.base + path
        query = dict(params or {})
        page = 0
        prev_token = None
        while True:
            payload = self._get(url, params=query)
            batch = payload.get("items", payload if isinstance(payload, list) else [])
            items.extend(batch)
            page += 1
            log.debug("%s page %d: +%d (total %d)", path, page, len(batch), len(items))

            nxt = payload.get("next") if isinstance(payload, dict) else None
            if not nxt:
                break
            if nxt == prev_token:
                log.warning("%s: pagination token did not advance; stopping", path)
                break
            prev_token = nxt
            if urlparse(str(nxt)).scheme in ("http", "https"):
                url, query = str(nxt), None  # next is a full URL
            else:
                url, query = self.base + path, {**(params or {}), "next": nxt}
        return items
