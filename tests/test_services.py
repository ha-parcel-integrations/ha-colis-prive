"""Tests for the Colis Privé services (track_parcel / untrack_parcel)."""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.colis_prive.const import (
    CONF_COUNTRY,
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DOMAIN,
)

from .payloads import ACTIVE_CODE, OTHER_CODE, active_sample

_PATCH = "custom_components.colis_prive.api.ColisPrivApiClient.async_get_parcel"


async def _hub(hass, country="BE", postcode="1000", parcels=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Colis Privé ({postcode})",
        unique_id=f"{country}:{postcode}",
        options={
            CONF_COUNTRY: country,
            CONF_POSTAL_CODE: postcode,
            CONF_PARCELS: parcels or [],
        },
    )
    entry.add_to_hass(hass)
    with patch(_PATCH, new=AsyncMock(return_value=active_sample())):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _call(hass, service, data):
    with patch(_PATCH, new=AsyncMock(return_value=active_sample())):
        await hass.services.async_call(DOMAIN, service, data, blocking=True)
        await hass.async_block_till_done()


def _codes(entry) -> list[str]:
    return [p[CONF_TRACKING_CODE] for p in entry.options[CONF_PARCELS]]


async def test_track_parcel_adds_to_the_only_hub_without_hub_fields(hass):
    entry = await _hub(hass)
    await _call(hass, "track_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})
    assert _codes(entry) == [ACTIVE_CODE]


async def test_track_parcel_normalizes_code(hass):
    entry = await _hub(hass)
    await _call(hass, "track_parcel", {CONF_TRACKING_CODE: "test-0000 0001"})
    assert _codes(entry) == [ACTIVE_CODE]


async def test_track_parcel_accepts_any_non_empty_code(hass):
    """A short/odd-shaped code is accepted — formats vary too much to gate on."""
    entry = await _hub(hass)
    await _call(hass, "track_parcel", {CONF_TRACKING_CODE: "abc"})
    assert _codes(entry) == ["ABC"]


@pytest.mark.parametrize("bad", ["", " - "])
async def test_track_parcel_rejects_empty_code(hass, bad):
    await _hub(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "track_parcel", {CONF_TRACKING_CODE: bad}, blocking=True
        )


async def test_track_parcel_duplicate_is_noop(hass):
    entry = await _hub(hass)
    for _ in range(2):
        await _call(hass, "track_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})
    assert _codes(entry) == [ACTIVE_CODE]


async def test_track_parcel_without_any_hub_raises(hass):
    entry = await _hub(hass)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    from custom_components.colis_prive.services import _resolve_entry

    with pytest.raises(ServiceValidationError, match="not set up"):
        _resolve_entry(hass)


async def test_two_hubs_require_both_fields_naming_the_missing_one(hass):
    await _hub(hass, "BE", "1000")
    await _hub(hass, "BE", "2000")

    with pytest.raises(ServiceValidationError, match="country"):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_POSTAL_CODE: "1000"},
            blocking=True,
        )
    with pytest.raises(ServiceValidationError, match="postal_code"):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_COUNTRY: "be"},
            blocking=True,
        )
    with pytest.raises(ServiceValidationError, match="country"):
        await hass.services.async_call(
            DOMAIN, "track_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE}, blocking=True
        )


async def test_two_hubs_in_one_country_are_targeted_by_postcode(hass):
    first = await _hub(hass, "BE", "1000")
    second = await _hub(hass, "BE", "2000")

    await _call(
        hass,
        "track_parcel",
        {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_COUNTRY: "BE", CONF_POSTAL_CODE: " 2000 "},
    )

    assert _codes(first) == []
    assert _codes(second) == [ACTIVE_CODE]


async def test_same_postcode_in_two_countries_is_told_apart_by_country(hass):
    belgium = await _hub(hass, "BE", "1234")
    luxembourg = await _hub(hass, "LU", "1234")

    await _call(
        hass,
        "track_parcel",
        {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_COUNTRY: "lu", CONF_POSTAL_CODE: "L-1234"},
    )

    assert _codes(belgium) == []
    assert _codes(luxembourg) == [ACTIVE_CODE]


async def test_unknown_hub_raises(hass):
    await _hub(hass, "BE", "1000")
    await _hub(hass, "BE", "2000")
    with pytest.raises(ServiceValidationError, match="No Colis Privé hub"):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_COUNTRY: "BE", CONF_POSTAL_CODE: "3000"},
            blocking=True,
        )


async def test_single_hub_with_a_mismatching_field_raises(hass):
    await _hub(hass, "BE", "1000")
    with pytest.raises(ServiceValidationError, match="No Colis Privé hub"):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_COUNTRY: "LU"},
            blocking=True,
        )


async def test_untrack_removes_from_the_only_holder(hass):
    entry = await _hub(hass, parcels=[{CONF_TRACKING_CODE: ACTIVE_CODE}])
    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})
    assert _codes(entry) == []


async def test_untrack_finds_the_hub_that_holds_the_code(hass):
    first = await _hub(hass, "BE", "1000", parcels=[{CONF_TRACKING_CODE: ACTIVE_CODE}])
    second = await _hub(hass, "BE", "2000", parcels=[{CONF_TRACKING_CODE: OTHER_CODE}])

    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: OTHER_CODE})

    assert _codes(first) == [ACTIVE_CODE]
    assert _codes(second) == []


async def test_untrack_in_several_hubs_needs_country_and_postcode(hass):
    first = await _hub(hass, "BE", "1234", parcels=[{CONF_TRACKING_CODE: ACTIVE_CODE}])
    second = await _hub(hass, "LU", "1234", parcels=[{CONF_TRACKING_CODE: ACTIVE_CODE}])

    with pytest.raises(ServiceValidationError, match="country"):
        await hass.services.async_call(
            DOMAIN, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE}, blocking=True
        )
    with pytest.raises(ServiceValidationError, match="country"):
        await hass.services.async_call(
            DOMAIN,
            "untrack_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_POSTAL_CODE: "1234"},
            blocking=True,
        )

    await _call(
        hass,
        "untrack_parcel",
        {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_COUNTRY: "LU", CONF_POSTAL_CODE: "1234"},
    )
    assert _codes(first) == [ACTIVE_CODE]
    assert _codes(second) == []


async def test_untrack_unknown_code_is_noop(hass):
    entry = await _hub(hass, parcels=[{CONF_TRACKING_CODE: ACTIVE_CODE}])
    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: OTHER_CODE})
    assert _codes(entry) == [ACTIVE_CODE]


async def test_services_survive_until_the_last_hub_unloads(hass):
    first = await _hub(hass, "BE", "1000")
    second = await _hub(hass, "BE", "2000")

    assert await hass.config_entries.async_unload(first.entry_id)
    assert hass.services.has_service(DOMAIN, "track_parcel")

    assert await hass.config_entries.async_unload(second.entry_id)
    assert not hass.services.has_service(DOMAIN, "track_parcel")
