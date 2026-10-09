"""Tests for the Colis Privé coordinator: fetching, caching and events.

The parcel mapping itself is covered by ``test_parcels.py``.
"""
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.colis_prive.api import ColisPrivApiError
from custom_components.colis_prive.const import (
    CONF_COUNTRY,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DOMAIN,
    ParcelStatus,
)
from custom_components.colis_prive.coordinator import ColisPrivCoordinator

from .payloads import (
    ACTIVE_CODE,
    DELIVERED_CODE,
    OTHER_CODE,
    POSTCODE,
    active_sample,
    delivered_sample,
    in_transit_sample,
)

pytestmark = pytest.mark.usefixtures("delivered_keyword")


def _entry_with(parcels: list[dict]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        # Keep-most-recent-100 so the delivered-retention filter never trims
        # the (old, fixed-date) sample parcels these tests assert on.
        options={
            CONF_COUNTRY: "BE",
            CONF_POSTAL_CODE: POSTCODE,
            CONF_PARCELS: parcels,
            CONF_DELIVERED_FILTER_TYPE: "parcels",
            CONF_DELIVERED_FILTER_AMOUNT: 100,
        },
        unique_id=f"BE:{POSTCODE}",
    )


def _in_transit(code: str = ACTIVE_CODE) -> dict:
    return in_transit_sample(code)


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------


async def test_update_merges_multiple_parcels(hass):
    entry = _entry_with(
        [{CONF_TRACKING_CODE: ACTIVE_CODE}, {CONF_TRACKING_CODE: DELIVERED_CODE}]
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.side_effect = lambda code, postal_code, lang: (
        active_sample() if code == ACTIVE_CODE else delivered_sample()
    )
    coordinator = ColisPrivCoordinator(hass, client, entry)

    data = await coordinator._async_update_data()

    assert len(data) == 1  # one active
    assert data[0]["barcode"] == ACTIVE_CODE
    assert len(coordinator.delivered) == 1
    assert coordinator.last_success_time is not None


async def test_update_not_found_shows_pending_placeholder(hass):
    entry = _entry_with([{CONF_TRACKING_CODE: OTHER_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = None  # not found
    coordinator = ColisPrivCoordinator(hass, client, entry)

    data = await coordinator._async_update_data()

    assert len(data) == 1
    assert data[0]["barcode"] == OTHER_CODE
    assert data[0]["status"] == ParcelStatus.UNKNOWN


async def test_update_keeps_cached_payload_on_error(hass):
    entry = _entry_with([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = delivered_sample()
    coordinator = ColisPrivCoordinator(hass, client, entry)
    await coordinator._async_update_data()  # populates the cache

    client.async_get_parcel.side_effect = ColisPrivApiError("HTTP 500")
    await coordinator._async_update_data()  # error -> cached raw reused
    assert len(coordinator.delivered) == 1


async def test_update_raises_when_every_parcel_fails(hass):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    entry = _entry_with([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.side_effect = ColisPrivApiError("HTTP 500")
    coordinator = ColisPrivCoordinator(hass, client, entry)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_update_reraises_unexpected_exceptions(hass):
    """Only API and network errors are tolerated; a bug must not be swallowed."""
    entry = _entry_with([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.side_effect = ValueError("boom")
    coordinator = ColisPrivCoordinator(hass, client, entry)

    with pytest.raises(ValueError):
        await coordinator._async_update_data()


async def test_update_skips_items_missing_a_tracking_code(hass):
    entry = _entry_with(
        [{CONF_TRACKING_CODE: ""}, {CONF_TRACKING_CODE: DELIVERED_CODE}]
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = delivered_sample()
    coordinator = ColisPrivCoordinator(hass, client, entry)

    await coordinator._async_update_data()
    assert client.async_get_parcel.await_count == 1  # empty item never fetched


async def test_update_prunes_cache_for_untracked_parcels(hass):
    entry = _entry_with([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = delivered_sample()
    coordinator = ColisPrivCoordinator(hass, client, entry)
    coordinator._raw_cache["GONE"] = {"trackingNumber": "GONE"}

    await coordinator._async_update_data()

    assert "GONE" not in coordinator._raw_cache
    assert DELIVERED_CODE in coordinator._raw_cache


async def test_delivered_code_skipped_from_fetch(hass):
    """A delivered code stops being fetched from the next cycle on."""
    entry = _entry_with(
        [{CONF_TRACKING_CODE: ACTIVE_CODE}, {CONF_TRACKING_CODE: DELIVERED_CODE}]
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.side_effect = (
        lambda code, postal_code, lang: active_sample(code) if code == ACTIVE_CODE else delivered_sample()
    )
    coordinator = ColisPrivCoordinator(hass, client, entry)

    await coordinator._async_update_data()
    assert client.async_get_parcel.call_count == 2
    assert coordinator.delivered_codes == {DELIVERED_CODE}

    client.async_get_parcel.reset_mock()
    data = await coordinator._async_update_data()

    # Only the still-active code is fetched — the delivered one is skipped.
    client.async_get_parcel.assert_called_once_with(ACTIVE_CODE, POSTCODE, "en")
    # The delivered parcel's sensor still keeps its data, from the cache.
    assert any(parcel["barcode"] == DELIVERED_CODE for parcel in coordinator.delivered)
    assert data[0]["barcode"] == ACTIVE_CODE


async def test_delivered_code_forgotten_when_untracked(hass):
    """Untracking a delivered code drops it from the skip set too."""
    entry = _entry_with([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = delivered_sample()
    coordinator = ColisPrivCoordinator(hass, client, entry)

    await coordinator._async_update_data()
    assert coordinator.delivered_codes == {DELIVERED_CODE}

    hass.config_entries.async_update_entry(entry, options={CONF_PARCELS: []})
    await coordinator._async_update_data()
    assert coordinator.delivered_codes == set()


async def test_update_fetches_parcels_concurrently(hass):
    """All tracked parcels go out in one gather, not one-by-one."""
    import asyncio

    entry = _entry_with(
        [{CONF_TRACKING_CODE: ACTIVE_CODE}, {CONF_TRACKING_CODE: DELIVERED_CODE}]
    )
    entry.add_to_hass(hass)
    in_flight = 0
    peak = 0

    async def _slow_fetch(code, postal_code, lang):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return active_sample(code)

    client = AsyncMock()
    client.async_get_parcel.side_effect = _slow_fetch
    coordinator = ColisPrivCoordinator(hass, client, entry)

    await coordinator._async_update_data()
    assert peak == 2


async def test_cache_only_poll_does_not_stamp_last_success(hass):
    """A poll served entirely from cache must not look like a success."""
    # Must still be active (not delivered) — a delivered code is skipped from
    # the fetch entirely from the next cycle on, which is covered separately
    # by test_delivered_code_skipped_from_fetch.
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = active_sample(ACTIVE_CODE)
    coordinator = ColisPrivCoordinator(hass, client, entry)
    await coordinator._async_update_data()
    stamp = coordinator.last_success_time
    assert stamp is not None

    client.async_get_parcel.side_effect = ColisPrivApiError("HTTP 500")
    await coordinator._async_update_data()  # served from cache
    assert coordinator.last_success_time == stamp


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------


async def test_first_refresh_fires_nothing(hass):
    """Otherwise every restart floods the user with "registered" events."""
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = active_sample()
    coordinator = ColisPrivCoordinator(hass, client, entry)

    fired = []
    for suffix in (
        "parcel_registered",
        "parcel_status_changed",
        "parcel_delivered",
        "parcel_delivery_time_changed",
    ):
        hass.bus.async_listen(f"{DOMAIN}_{suffix}", lambda e: fired.append(e))

    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert fired == []


async def test_event_carries_device_id(hass):
    from homeassistant.helpers import device_registry as dr

    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
    )
    client = AsyncMock()
    coordinator = ColisPrivCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_status_changed", lambda e: events.append(e)
    )

    client.async_get_parcel.return_value = _in_transit()
    await coordinator._async_update_data()
    client.async_get_parcel.return_value = active_sample()
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert events[0].data["device_id"] == device.id


async def test_fires_status_changed_event(hass):
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = ColisPrivCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_status_changed", lambda e: events.append(e)
    )

    client.async_get_parcel.return_value = _in_transit()
    await coordinator._async_update_data()  # first refresh: suppressed

    client.async_get_parcel.return_value = active_sample()  # out for delivery
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["old_status"] == ParcelStatus.IN_TRANSIT
    assert events[0].data["new_status"] == ParcelStatus.OUT_FOR_DELIVERY


async def test_delivery_fires_delivered_event_and_not_status_changed(hass):
    """The hop to delivered fires exactly one, dedicated event."""
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = ColisPrivCoordinator(hass, client, entry)

    delivered = []
    changed = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_delivered", lambda e: delivered.append(e))
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_status_changed", lambda e: changed.append(e)
    )

    client.async_get_parcel.return_value = active_sample(ACTIVE_CODE)
    await coordinator._async_update_data()
    client.async_get_parcel.return_value = delivered_sample(ACTIVE_CODE)
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert changed == []
    assert len(delivered) == 1
    assert delivered[0].data["barcode"] == ACTIVE_CODE
    assert delivered[0].data["status"] == ParcelStatus.DELIVERED


async def test_no_events_for_parcel_first_seen_delivered(hass):
    """A parcel already delivered when first tracked fires nothing at all."""
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.side_effect = lambda code, postal_code, lang: (
        active_sample(code) if code == ACTIVE_CODE else delivered_sample(code)
    )
    coordinator = ColisPrivCoordinator(hass, client, entry)

    fired = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", lambda e: fired.append(e))
    hass.bus.async_listen(f"{DOMAIN}_parcel_delivered", lambda e: fired.append(e))

    await coordinator._async_update_data()  # first refresh seeds the state

    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_PARCELS: [
                {CONF_TRACKING_CODE: ACTIVE_CODE},
                {CONF_TRACKING_CODE: DELIVERED_CODE},
            ],
        },
    )
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert fired == []


async def test_fires_registered_event_for_new_parcel(hass):
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = active_sample(ACTIVE_CODE)
    coordinator = ColisPrivCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", lambda e: events.append(e))

    await coordinator._async_update_data()  # first refresh: suppressed

    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_PARCELS: [
                {CONF_TRACKING_CODE: ACTIVE_CODE},
                {CONF_TRACKING_CODE: OTHER_CODE},
            ],
        },
    )
    client.async_get_parcel.side_effect = lambda code, postal_code, lang: active_sample(code)
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["barcode"] == OTHER_CODE


def _with_eta(monkeypatch, etas: list[tuple[str | None, str | None]]):
    """Make normalize_parcel report successive ETAs (this carrier has none)."""
    from custom_components.colis_prive import coordinator as coordinator_module

    real = coordinator_module.normalize_parcel
    pending = iter(etas)

    def _normalize(raw, **kwargs):
        parcel = real(raw, **kwargs)
        parcel["planned_from"], parcel["planned_to"] = next(pending)
        return parcel

    monkeypatch.setattr(coordinator_module, "normalize_parcel", _normalize)


async def test_fires_delivery_time_changed_event(hass, monkeypatch):
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = active_sample()
    coordinator = ColisPrivCoordinator(hass, client, entry)
    _with_eta(
        monkeypatch,
        [
            ("2026-04-29T13:00:00+00:00", None),
            ("2026-04-29T16:00:00+00:00", "2026-04-29T18:00:00+00:00"),
        ],
    )

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_delivery_time_changed", lambda e: events.append(e)
    )

    await coordinator._async_update_data()  # first refresh: suppressed
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["old_planned_from"] == "2026-04-29T13:00:00+00:00"
    assert events[0].data["new_planned_from"] == "2026-04-29T16:00:00+00:00"


async def test_losing_the_eta_is_silent(hass, monkeypatch):
    """value -> null just means the carrier lost the window; not worth an alert."""
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = active_sample()
    coordinator = ColisPrivCoordinator(hass, client, entry)
    _with_eta(monkeypatch, [("2026-04-29T13:00:00+00:00", None), (None, None)])

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_delivery_time_changed", lambda e: events.append(e)
    )

    await coordinator._async_update_data()
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert events == []


# ---------------------------------------------------------------------------
# language, hubs and failure handling
# ---------------------------------------------------------------------------


async def test_request_uses_hub_postcode_and_instance_language(hass):
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = active_sample(lang="fr")
    coordinator = ColisPrivCoordinator(hass, client, entry)

    hass.config.language = "fr-CA"
    await coordinator._async_update_data()
    client.async_get_parcel.assert_awaited_with(ACTIVE_CODE, POSTCODE, "fr")

    hass.config.language = "nl-BE"
    await coordinator._async_update_data()
    client.async_get_parcel.assert_awaited_with(ACTIVE_CODE, POSTCODE, "nl")

    hass.config.language = "de"
    await coordinator._async_update_data()
    client.async_get_parcel.assert_awaited_with(ACTIVE_CODE, POSTCODE, "en")


async def test_language_change_rewrites_raw_status_but_fires_no_event(hass):
    """Only the sentence changes with the language; the canonical status does not."""
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = ColisPrivCoordinator(hass, client, entry)
    fired = []
    for suffix in ("registered", "status_changed", "delivered"):
        hass.bus.async_listen(f"{DOMAIN}_parcel_{suffix}", lambda e: fired.append(e))

    hass.config.language = "fr"
    client.async_get_parcel.side_effect = lambda code, pc, lang: active_sample(code, lang)
    first = await coordinator._async_update_data()
    hass.config.language = "nl"
    second = await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert first[0]["raw_status"] != second[0]["raw_status"]
    assert first[0]["status"] == second[0]["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert fired == []


async def test_cached_payload_keeps_its_own_language_after_a_failure(hass):
    """A stale French payload must not be matched against the English table."""
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = active_sample(lang="fr")
    coordinator = ColisPrivCoordinator(hass, client, entry)
    hass.config.language = "fr"
    await coordinator._async_update_data()

    hass.config.language = "en"
    client.async_get_parcel.side_effect = ColisPrivApiError("changed page layout")
    data = await coordinator._async_update_data()

    assert data[0]["status"] == ParcelStatus.OUT_FOR_DELIVERY


async def test_changed_layout_keeps_last_good_data(hass):
    """A parse failure never flips a parcel to delivered, removed or unknown."""
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = active_sample()
    coordinator = ColisPrivCoordinator(hass, client, entry)
    await coordinator._async_update_data()

    client.async_get_parcel.side_effect = ColisPrivApiError("changed page layout")
    data = await coordinator._async_update_data()

    assert data[0]["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert client.async_get_parcel.await_count == 2  # no retry burst


async def test_rate_limit_raises_update_failed_with_retry_after(hass):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.side_effect = ColisPrivApiError(
        "HTTP 429", status_code=429, retry_after=120
    )
    coordinator = ColisPrivCoordinator(hass, client, entry)

    with pytest.raises(UpdateFailed) as err:
        await coordinator._async_update_data()
    assert err.value.retry_after == 120

    client.async_get_parcel.side_effect = ColisPrivApiError("HTTP 429", status_code=429)
    with pytest.raises(UpdateFailed) as err:
        await coordinator._async_update_data()
    assert err.value.retry_after == 60 * 2**2


async def test_address_never_reaches_the_parcel_or_events(hass):
    from custom_components.colis_prive.api import ColisPrivApiClient

    from .payloads import FAKE_NAME_FRAGMENT, result_html

    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    response = AsyncMock()
    response.status = 200
    response.text = AsyncMock(return_value=result_html())
    ctx = AsyncMock()
    ctx.__aenter__.return_value = response
    session = AsyncMock()
    session.get = lambda *args, **kwargs: ctx
    coordinator = ColisPrivCoordinator(hass, ColisPrivApiClient(session), entry)

    data = await coordinator._async_update_data()

    assert FAKE_NAME_FRAGMENT not in repr(data)
    assert FAKE_NAME_FRAGMENT not in repr(coordinator.delivered)
