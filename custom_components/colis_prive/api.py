"""Colis Privé public tracking page client.

Contract the coordinator relies on:

* ``async_get_parcel`` returns the parsed parcel dict on success,
* returns ``None`` when the carrier redirects to its search form (unknown
  number, wrong postcode and not-yet-scanned are indistinguishable),
* raises :class:`ColisPrivApiError` for anything else, with ``status_code``
  set on a non-2xx response and ``retry_after`` set when a 429 carried a
  ``Retry-After`` in seconds,
* lets ``aiohttp.ClientError`` propagate untouched.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import TRACKING_API_URL
from .parser import ColisPrivParseError, parse_result

_LOGGER = logging.getLogger(__name__)


class ColisPrivApiError(Exception):
    """Raised when a Colis Privé request returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"Colis Privé request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class ColisPrivApiClient:
    """Client for the keyless Colis Privé result page."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session
        self._layout_warned = False

    async def async_get_parcel(
        self, tracking_code: str, postal_code: str, lang: str
    ) -> dict[str, Any] | None:
        """Fetch and parse one parcel's result panel.

        Redirects stay off: followed, a miss lands on the search form with a
        200, which would read as a changed template instead of not-found.
        """
        async with self._session.get(
            TRACKING_API_URL,
            params={"numColis": f"{tracking_code}{postal_code}", "lang": lang},
            allow_redirects=False,
        ) as response:
            status = response.status
            if status == 429:
                header = response.headers.get("Retry-After")
                try:
                    retry_after = float(header) if header else None
                except ValueError:
                    retry_after = None
                raise ColisPrivApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if 300 <= status < 400:
                location = response.headers.get("Location", "")
                if "default.aspx" in location.lower():
                    return None
                raise ColisPrivApiError(
                    f"unexpected redirect (HTTP {status})", status_code=status
                )
            if status != 200:
                raise ColisPrivApiError(f"HTTP {status}", status_code=status)
            html = await response.text()

        try:
            parsed = parse_result(html)
        except ColisPrivParseError as err:
            # One-shot: with N parcels a changed layout would otherwise log N
            # lines on every poll.
            if not self._layout_warned:
                self._layout_warned = True
                _LOGGER.warning(
                    "Colis Privé page layout not recognised (%s); keeping previous data",
                    err,
                )
            raise ColisPrivApiError(f"changed page layout ({err})") from err

        parsed["trackingNumber"] = tracking_code
        parsed["lang"] = lang
        return parsed
