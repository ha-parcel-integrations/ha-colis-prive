"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    COUNTRY_TIMEZONES,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-colis-prive/issues/new"
    "?template=unrecognised_status.yml"
)

def _fold(text: str) -> str:
    """Normalise a sentence for matching: whitespace, apostrophes, case."""
    for quote in ("\u2019", "\u2018", "\u02bc"):
        text = text.replace(quote, "'")
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip().casefold()


# One keyword table per page language. The sentences are the carrier's own and
# are not translations of each other, so a sentence is only ever matched
# against the table of the language it was requested in. Each keyword was
# chosen to match exactly one observed sentence: the FR "pris en charge"
# sentence also mentions "agence régionale de distribution" and the NL
# "nationaal platform" sentence also mentions "regionale bezorgpartner", so
# the shorter fragments would match two stages.
_KEYWORDS: dict[str, tuple[tuple[str, ParcelStatus], ...]] = {
    "fr": (
        ("en cours de distribution", ParcelStatus.OUT_FOR_DELIVERY),
        ("arrivé sur notre agence régionale", ParcelStatus.IN_TRANSIT),
        ("pris en charge par Colis Privé", ParcelStatus.IN_TRANSIT),
        ("préparation par l'expéditeur", ParcelStatus.REGISTERED),
    ),
    "nl": (
        ("onderweg naar jou", ParcelStatus.OUT_FOR_DELIVERY),
        ("toegekomen bij onze regionale bezorgpartner", ParcelStatus.IN_TRANSIT),
        ("nationaal platform", ParcelStatus.IN_TRANSIT),
        ("klaargemaakt door de webshop", ParcelStatus.REGISTERED),
    ),
    "en": (
        ("being delivered by the driver", ParcelStatus.OUT_FOR_DELIVERY),
        ("arrived at our regional distribution office", ParcelStatus.IN_TRANSIT),
        ("well received by a Colis Privé branch", ParcelStatus.IN_TRANSIT),
        ("prepared by your webmerchant", ParcelStatus.REGISTERED),
    ),
}
_TABLES: dict[str, tuple[tuple[str, ParcelStatus], ...]] = {
    lang: tuple((_fold(keyword), status) for keyword, status in table)
    for lang, table in _KEYWORDS.items()
}

# Sentences we have already warned about, keyed on language + sentence, so
# each unmapped one is logged only once per HA session instead of every poll.
_unmapped_statuses_logged: set[str] = set()


def resolve_lang(language: str | None) -> str:
    """Pick the page language for a Home Assistant language code."""
    base = (language or "").lower().replace("_", "-").split("-")[0]
    return base if base in ("fr", "nl") else "en"


def _warn_unmapped_status(text: str, lang: str, outcome: str) -> None:
    """Log an unmapped carrier sentence once, with a copy-paste issue link."""
    key = f"{lang}:{_fold(text)}"
    if key in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(key)
    _LOGGER.warning(
        "Unrecognised Colis Privé status — help us map it. Open an issue "
        "and paste this line: %s\n  lang=%s status=%s → %s",
        NEW_ISSUE_URL,
        lang,
        text,
        outcome,
    )


def _match(text: str | None, lang: str) -> ParcelStatus | None:
    """Return the status for ``text`` from the ``lang`` table, if any."""
    if not text:
        return None
    folded = _fold(text)
    for keyword, status in _TABLES.get(lang, ()):
        if keyword in folded:
            return status
    return None


def map_parcel_status(
    text: str | None, lang: str, history_texts: list[str] | None = None
) -> ParcelStatus:
    """Map the current status sentence to a canonical :class:`ParcelStatus`.

    An unmapped sentence falls back to the newest history sentence that maps
    (``history_texts`` is newest first), then to ``unknown``. Either way the
    unrecognised sentence is logged once so the table can grow. ``None`` (a
    not-yet-scanned parcel) reports ``unknown`` silently.
    """
    if not text:
        return ParcelStatus.UNKNOWN
    mapped = _match(text, lang)
    if mapped is not None:
        return mapped
    for earlier in history_texts or []:
        mapped = _match(earlier, lang)
        if mapped is not None:
            _warn_unmapped_status(text, lang, f"reported as '{mapped.value}'")
            return mapped
    _warn_unmapped_status(text, lang, "reported as 'unknown'")
    return ParcelStatus.UNKNOWN


def map_event_status(text: str | None, lang: str) -> ParcelStatus | None:
    """Map a history sentence to a canonical status, or ``None``.

    Unmapped sentences keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to unknown")
    and warn once.
    """
    if not text:
        return None
    mapped = _match(text, lang)
    if mapped is None:
        _warn_unmapped_status(text, lang, "history entry left unmapped")
    return mapped


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. Strings pass through untouched; their
    consumers are guarded by :func:`parse_iso`. Adjust the numeric branch if
    your carrier stamps in seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
    }


def row_timestamp(date: str, country: str) -> str | None:
    """Turn ``DD/MM/YYYY`` into local midnight in the hub country's timezone."""
    try:
        day, month, year = (int(part) for part in date.split("/"))
        zone = ZoneInfo(COUNTRY_TIMEZONES.get(country, "Europe/Paris"))
        return datetime(year, month, day, tzinfo=zone).isoformat()
    except (ValueError, TypeError, AttributeError):
        return None


def build_history(
    rows: list | None,
    lang: str,
    country: str,
    *,
    max_events: int = HISTORY_MAX_EVENTS,
) -> list[dict]:
    """Build the canonical ``history`` list from the table rows.

    ``rows`` is newest first, as on the page. The whole list is reversed
    instead of sorted: the dates carry no time, so a sort would lose the
    order of events within one day. Capped to the most recent ``max_events``.
    """
    events: list[dict] = []
    for row in reversed(rows or []):
        if not isinstance(row, dict):
            continue
        timestamp = row_timestamp(row.get("date", ""), country)
        if not timestamp:
            continue
        events.append(
            {
                "timestamp": timestamp,
                "status": map_event_status(row.get("text"), lang),
                "raw_status": row.get("text"),
            }
        )
    return events[-max_events:]


def tracking_url(
    tracking_code: str | None, postal_code: str | None, lang: str
) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code or not postal_code:
        return None
    return TRACKING_URL.format(
        tracking_code=tracking_code, postal_code=postal_code, lang=lang
    )


def normalize_parcel(
    raw: dict,
    *,
    include_history: bool = False,
    country: str = "FR",
    postal_code: str | None = None,
) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The status sentences are matched against the table of the language stored
    in ``raw`` (the language the page was requested in), so a cached payload
    stays consistent after the Home Assistant language changes. ``raw`` holds
    only parsed fields — never the page or the recipient block.
    """
    tracking_code = raw.get("trackingNumber")
    lang = raw.get("lang") or "en"
    rows = raw.get("history") or []
    status_text = raw.get("statusText")
    status = map_parcel_status(
        status_text, lang, [row.get("text") for row in rows if isinstance(row, dict)]
    )
    delivered = status is ParcelStatus.DELIVERED
    delivered_at = None
    if delivered and rows and isinstance(rows[0], dict):
        delivered_at = row_timestamp(rows[0].get("date", ""), country)

    return {
        "carrier": "Colis Privé",
        "barcode": tracking_code,
        "sender": raw.get("sender") or None,
        "receiver": None,
        "status": status,
        "raw_status": status_text,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": None,
        "planned_to": None,
        "pickup": status is ParcelStatus.AT_PICKUP_POINT,
        "pickup_point": None,
        "url": tracking_url(tracking_code, postal_code, lang),
        "weight": None,
        "dimensions": None,
        "history": build_history(rows, lang, country) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
