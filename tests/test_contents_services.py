"""Tests for the contents action, the track_parcel field and the store lifecycle."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.colis_prive import coordinator as module
from custom_components.colis_prive.const import (
    CONF_COUNTRY,
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DOMAIN,
)
from custom_components.colis_prive.coordinator import ColisPrivCoordinator
from custom_components.colis_prive.services import async_setup_services

from .payloads import ACTIVE_CODE, DELIVERED_CODE, OTHER_CODE, active_sample

GET = "custom_components.colis_prive.api.ColisPrivApiClient.async_get_parcel"
UNTRACKED = "TEST00000999"
STORE_KEY = f"{DOMAIN}.contents.%s"


def _payload(code, *_args):
    return active_sample(code)


async def _hub(hass, postcode="1000", codes=()) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Colis Privé ({postcode})",
        unique_id=f"BE:{postcode}",
        options={
            CONF_COUNTRY: "BE",
            CONF_POSTAL_CODE: postcode,
            CONF_PARCELS: [{CONF_TRACKING_CODE: code} for code in codes],
        },
    )
    entry.add_to_hass(hass)
    with patch(GET, new=AsyncMock(side_effect=_payload)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _call(hass, service: str, data: dict) -> None:
    with patch(GET, new=AsyncMock(side_effect=_payload)):
        await hass.services.async_call(DOMAIN, service, data, blocking=True)
        await hass.async_block_till_done()
        # A refresh requested inside the debouncer's cooldown runs after it.
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
        await hass.async_block_till_done()


def _contents(entry, code):
    parcels = {p["barcode"]: p for p in entry.runtime_data.coordinator.data}
    return parcels[code]["contents"]


def _store(entry):
    return entry.runtime_data.coordinator.contents_store


async def test_a_parcel_has_no_contents_without_a_stored_value(hass):
    entry = await _hub(hass, codes=[ACTIVE_CODE])
    assert _contents(entry, ACTIVE_CODE) is None


async def test_update_contents_sets_replaces_and_clears(hass):
    entry = await _hub(hass, codes=[ACTIVE_CODE])
    data = {CONF_TRACKING_CODE: ACTIVE_CODE}

    await _call(hass, "update_contents", {**data, "contents": " Running  shoes "})
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"

    await _call(hass, "update_contents", {**data, "contents": "Winter socks"})
    assert _contents(entry, ACTIVE_CODE) == "Winter socks"

    await _call(hass, "update_contents", {**data, "contents": "  "})
    assert _contents(entry, ACTIVE_CODE) is None


async def test_update_contents_without_text_clears(hass):
    entry = await _hub(hass, codes=[ACTIVE_CODE])
    data = {CONF_TRACKING_CODE: ACTIVE_CODE}
    await _call(hass, "update_contents", {**data, "contents": "Running shoes"})

    await _call(hass, "update_contents", data)

    assert _contents(entry, ACTIVE_CODE) is None


async def test_update_contents_normalizes_the_code(hass):
    entry = await _hub(hass, codes=[ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: " test00000001 ", "contents": "Running shoes"},
    )
    assert _store(entry).get(ACTIVE_CODE) == "Running shoes"


async def test_update_contents_works_for_a_code_that_is_not_tracked(hass):
    entry = await _hub(hass, codes=[ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: UNTRACKED, "contents": "Running shoes"},
    )

    assert _store(entry).get(UNTRACKED) == "Running shoes"
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_update_contents_refreshes_only_when_something_changed(hass):
    await _hub(hass, codes=[ACTIVE_CODE])
    data = {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"}
    with patch.object(
        ColisPrivCoordinator, "async_request_refresh", AsyncMock()
    ) as refresh:
        await _call(hass, "update_contents", data)
        assert refresh.await_count == 1
        await _call(hass, "update_contents", data)
        assert refresh.await_count == 1
        await _call(hass, "update_contents", {CONF_TRACKING_CODE: UNTRACKED})
        assert refresh.await_count == 1


async def test_an_unchanged_value_writes_nothing(hass):
    entry = await _hub(hass, codes=[ACTIVE_CODE])
    store = _store(entry)
    with patch.object(store._store, "async_delay_save") as save:
        assert store.async_set(ACTIVE_CODE, "Running shoes") is True
        assert save.call_count == 1
        assert store.async_set(ACTIVE_CODE, " Running shoes ") is False
        assert store.async_set(UNTRACKED, None) is False
        assert save.call_count == 1


async def test_update_contents_rejects_an_empty_code(hass):
    await _hub(hass)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "update_contents", {CONF_TRACKING_CODE: " "})


async def test_update_contents_needs_a_loaded_entry(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "update_contents", {CONF_TRACKING_CODE: ACTIVE_CODE}, blocking=True
        )


async def test_update_contents_ignores_an_entry_that_is_not_loaded(hass):
    entry = await _hub(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
        )


async def test_track_parcel_stores_contents_for_a_new_code(hass):
    entry = await _hub(hass)
    await _call(
        hass,
        "track_parcel",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"


async def test_track_parcel_stores_contents_in_the_hub_chosen_for_the_code(hass):
    first = await _hub(hass, "1000")
    second = await _hub(hass, "2000")
    await _call(
        hass,
        "track_parcel",
        {
            CONF_TRACKING_CODE: ACTIVE_CODE,
            CONF_COUNTRY: "BE",
            CONF_POSTAL_CODE: "2000",
            "contents": "Running shoes",
        },
    )

    assert _store(first).get(ACTIVE_CODE) is None
    assert _contents(second, ACTIVE_CODE) == "Running shoes"


async def test_track_parcel_without_contents_is_unchanged(hass):
    entry = await _hub(hass)
    await _call(hass, "track_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})

    assert _store(entry).get(ACTIVE_CODE) is None
    assert _contents(entry, ACTIVE_CODE) is None


async def test_track_parcel_replaces_and_clears_contents_of_a_tracked_code(hass):
    entry = await _hub(hass, codes=[ACTIVE_CODE])
    track = {CONF_TRACKING_CODE: ACTIVE_CODE}

    await _call(hass, "track_parcel", {**track, "contents": "Running shoes"})
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"

    await _call(hass, "track_parcel", {**track, "contents": "Winter socks"})
    assert _contents(entry, ACTIVE_CODE) == "Winter socks"

    await _call(hass, "track_parcel", {**track, "contents": ""})
    assert _contents(entry, ACTIVE_CODE) is None
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_track_parcel_unchanged_contents_requests_no_refresh(hass):
    await _hub(hass, codes=[ACTIVE_CODE])
    data = {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"}
    with patch.object(
        ColisPrivCoordinator, "async_request_refresh", AsyncMock()
    ) as refresh:
        await _call(hass, "track_parcel", data)
        await _call(hass, "track_parcel", data)
    assert refresh.await_count == 1


async def test_track_parcel_with_contents_needs_a_loaded_hub(hass):
    entry = await _hub(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
        )


async def test_untrack_parcel_drops_the_contents(hass):
    entry = await _hub(hass, codes=[ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})

    assert _store(entry).get(ACTIVE_CODE) is None
    assert entry.options[CONF_PARCELS] == []


async def test_untrack_parcel_skips_a_hub_that_is_not_loaded(hass):
    entry = await _hub(hass, codes=[ACTIVE_CODE])
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)

    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})

    assert entry.options[CONF_PARCELS] == []


async def test_a_stored_value_survives_a_restart_of_the_entry(hass, hass_storage):
    entry = await _hub(hass, codes=[ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )
    await _store(entry)._store.async_save(_store(entry)._data_to_save())

    assert await hass.config_entries.async_unload(entry.entry_id)
    with patch(GET, new=AsyncMock(side_effect=_payload)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert _contents(entry, ACTIVE_CODE) == "Running shoes"


async def test_an_entry_without_a_store_file_sets_up_and_loads_empty(
    hass, hass_storage
):
    assert not [key for key in hass_storage if key.startswith(f"{DOMAIN}.contents.")]
    entry = await _hub(hass, codes=[ACTIVE_CODE])
    assert entry.state is ConfigEntryState.LOADED
    assert _contents(entry, ACTIVE_CODE) is None


async def test_removing_the_entry_removes_its_store(hass, hass_storage):
    entry = await _hub(hass, codes=[ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )
    store = _store(entry)
    await store._store.async_save(store._data_to_save())
    key = STORE_KEY % entry.entry_id
    assert key in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert key not in hass_storage


async def test_a_contents_change_alone_fires_no_event(hass):
    await _hub(hass, codes=[ACTIVE_CODE])
    events = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_status_changed", events.append)
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", events.append)
    hass.bus.async_listen(f"{DOMAIN}_parcel_delivered", events.append)

    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    assert events == []


# ---------------------------------------------------------------------------
# overlay on the coordinator
# ---------------------------------------------------------------------------


async def test_user_contents_is_keyed_on_the_requested_code(hass):
    """A payload carrying another number still finds the value stored for the code."""
    entry = await _hub(hass, codes=[ACTIVE_CODE])
    coordinator = entry.runtime_data.coordinator
    coordinator.contents_store.async_set(ACTIVE_CODE, "Running shoes")

    with patch(GET, new=AsyncMock(return_value=active_sample(DELIVERED_CODE))):
        data = await coordinator._async_update_data()

    assert data[0]["barcode"] == DELIVERED_CODE
    assert data[0]["contents"] == "Running shoes"


async def test_user_contents_overlays_the_carrier_value(hass):
    entry = await _hub(hass, codes=[ACTIVE_CODE])
    coordinator = entry.runtime_data.coordinator
    real = module.normalize_parcel

    def carrier_text(raw, **kwargs):
        parcel = real(raw, **kwargs)
        parcel["contents"] = "Carrier text"
        return parcel

    with (
        patch.object(module, "normalize_parcel", side_effect=carrier_text),
        patch(GET, new=AsyncMock(return_value=active_sample(ACTIVE_CODE))),
    ):
        assert (await coordinator._async_update_data())[0]["contents"] == (
            "Carrier text"
        )
        coordinator.contents_store.async_set(ACTIVE_CODE, "Running shoes")
        assert (await coordinator._async_update_data())[0]["contents"] == (
            "Running shoes"
        )
        coordinator.contents_store.async_set(ACTIVE_CODE, None)
        assert (await coordinator._async_update_data())[0]["contents"] == (
            "Carrier text"
        )


async def test_known_codes_list_every_code_the_hub_holds(hass):
    entry = await _hub(hass, codes=[ACTIVE_CODE, UNTRACKED])
    assert entry.runtime_data.coordinator.known_codes == {ACTIVE_CODE, UNTRACKED}


async def test_known_codes_include_a_tracked_code_with_no_data_yet(hass):
    entry = await _hub(hass)
    coordinator = entry.runtime_data.coordinator
    hass.config_entries.async_update_entry(
        entry,
        options={**entry.options, CONF_PARCELS: [{CONF_TRACKING_CODE: OTHER_CODE}]},
    )
    with patch(GET, new=AsyncMock(return_value=None)):
        await coordinator._async_update_data()

    assert OTHER_CODE in coordinator.known_codes


# ---------------------------------------------------------------------------
# routing across several hubs
# ---------------------------------------------------------------------------


async def test_update_contents_goes_to_the_hub_that_lists_the_code(hass):
    first = await _hub(hass, "1000", [ACTIVE_CODE])
    second = await _hub(hass, "2000", [OTHER_CODE])

    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    assert _store(first).get(ACTIVE_CODE) == "Running shoes"
    assert _store(second).get(ACTIVE_CODE) is None


async def test_update_contents_goes_to_every_hub_that_lists_the_code(hass):
    first = await _hub(hass, "1000", [ACTIVE_CODE])
    second = await _hub(hass, "2000", [ACTIVE_CODE])
    third = await _hub(hass, "3000", [OTHER_CODE])

    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    assert _store(first).get(ACTIVE_CODE) == "Running shoes"
    assert _store(second).get(ACTIVE_CODE) == "Running shoes"
    assert _store(third).get(ACTIVE_CODE) is None


async def test_update_contents_with_several_hubs_and_no_match_needs_an_entry(hass):
    first = await _hub(hass, "1000", [ACTIVE_CODE])
    second = await _hub(hass, "2000", [OTHER_CODE])

    with pytest.raises(ServiceValidationError) as error:
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: UNTRACKED, "contents": "Running shoes"},
        )

    assert error.value.translation_key == "update_contents_entry_required"
    assert _store(first).get(UNTRACKED) is None
    assert _store(second).get(UNTRACKED) is None


async def test_config_entry_id_restricts_and_stores_an_unknown_code(hass):
    first = await _hub(hass, "1000", [ACTIVE_CODE])
    second = await _hub(hass, "2000", [OTHER_CODE])

    await _call(
        hass,
        "update_contents",
        {
            CONF_TRACKING_CODE: UNTRACKED,
            "contents": "Running shoes",
            "config_entry_id": second.entry_id,
        },
    )
    await _call(
        hass,
        "update_contents",
        {
            CONF_TRACKING_CODE: ACTIVE_CODE,
            "contents": "Winter socks",
            "config_entry_id": first.entry_id,
        },
    )

    assert _store(second).get(UNTRACKED) == "Running shoes"
    assert _store(first).get(UNTRACKED) is None
    assert _store(first).get(ACTIVE_CODE) == "Winter socks"
    assert _store(second).get(ACTIVE_CODE) is None


async def test_config_entry_id_must_name_a_loaded_entry(hass):
    await _hub(hass, codes=[ACTIVE_CODE])
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "config_entry_id": "missing"},
        )


async def test_a_single_loaded_hub_is_targeted_directly(hass):
    """The only hub takes a code it does not list yet, with no entry id given."""
    entry = await _hub(hass)
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: UNTRACKED, "contents": "Running shoes"},
    )
    assert _store(entry).get(UNTRACKED) == "Running shoes"


async def test_update_contents_stays_until_the_last_hub_unloads(hass):
    first = await _hub(hass, "1000")
    second = await _hub(hass, "2000")

    assert await hass.config_entries.async_unload(first.entry_id)
    assert hass.services.has_service(DOMAIN, "update_contents")

    assert await hass.config_entries.async_unload(second.entry_id)
    assert not hass.services.has_service(DOMAIN, "update_contents")
