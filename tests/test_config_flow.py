"""Tests for the Colis Privé config and options flow."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.colis_prive.config_flow import (
    normalize_postcode,
    normalize_tracking_code,
    valid_postcode,
    valid_tracking_code,
)
from custom_components.colis_prive.const import (
    CONF_COUNTRY,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DOMAIN,
)

from .payloads import ACTIVE_CODE, POSTCODE


def test_normalize_tracking_code_strips_and_uppercases():
    assert normalize_tracking_code("test 0000 0001") == ACTIVE_CODE
    assert normalize_tracking_code("") == ""
    assert normalize_tracking_code(None) == ""


@pytest.mark.parametrize(
    ("code", "ok"),
    [
        (ACTIVE_CODE, True),
        ("ABC", True),
        (f"{ACTIVE_CODE}{POSTCODE}", True),
        ("", False),
        (None, False),
    ],
)
def test_valid_tracking_code_accepts_every_non_empty_code(code, ok):
    assert valid_tracking_code(code) is ok


@pytest.mark.parametrize(
    ("raw", "clean"),
    [("1000", "1000"), (" 10 00 ", "1000"), ("L-1234", "1234"), ("b-1000", "1000"), ("75001", "75001"), (None, "")],
)
def test_normalize_postcode(raw, clean):
    assert normalize_postcode(raw) == clean


@pytest.mark.parametrize(
    ("value", "country", "ok"),
    [
        ("75001", "FR", True),
        ("7500", "FR", False),
        ("1000", "BE", True),
        ("10000", "BE", False),
        ("1234", "LU", True),
        ("12a4", "LU", False),
        ("1000", "XX", False),
        ("", "BE", False),
    ],
)
def test_valid_postcode_is_digits_of_the_country_length(value, country, ok):
    assert valid_postcode(value, country) is ok


async def _start(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})


async def test_user_flow_creates_hub_without_network(hass):
    result = await _start(hass)
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COUNTRY: "be", CONF_POSTAL_CODE: " 1000 "}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Colis Privé (1000)"
    assert result["result"].unique_id == "BE:1000"
    assert result["options"][CONF_COUNTRY] == "BE"
    assert result["options"][CONF_POSTAL_CODE] == "1000"
    assert result["options"][CONF_PARCELS] == []


async def test_user_flow_strips_the_luxembourg_prefix(hass):
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COUNTRY: "lu", CONF_POSTAL_CODE: "L-1234"}
    )
    assert result["result"].unique_id == "LU:1234"


async def test_user_flow_defaults_to_the_instance_country_when_supported(hass):
    hass.config.country = "BE"
    result = await _start(hass)
    default = next(k.default() for k in result["data_schema"].schema if k == CONF_COUNTRY)
    assert default == "be"


async def test_user_flow_defaults_to_france_for_other_countries(hass):
    hass.config.country = "DE"
    result = await _start(hass)
    default = next(k.default() for k in result["data_schema"].schema if k == CONF_COUNTRY)
    assert default == "fr"


async def test_invalid_postcode_keeps_the_country_and_shows_an_error(hass):
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COUNTRY: "fr", CONF_POSTAL_CODE: "1000"}
    )
    assert result["type"] == "form"
    assert result["errors"] == {CONF_POSTAL_CODE: "invalid_postcode"}
    default = next(k.default() for k in result["data_schema"].schema if k == CONF_COUNTRY)
    assert default == "fr"


async def test_second_hub_in_the_same_country_is_allowed(hass):
    """No single_config_entry: one country can have any number of postcodes."""
    for postcode in ("1000", "2000"):
        result = await _start(hass)
        assert result["type"] == "form"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COUNTRY: "be", CONF_POSTAL_CODE: postcode}
        )
        assert result["type"] == "create_entry"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_same_postcode_in_two_countries_is_two_hubs(hass):
    for country in ("be", "lu"):
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COUNTRY: country, CONF_POSTAL_CODE: "1234"}
        )
        assert result["type"] == "create_entry"
    ids = {e.unique_id for e in hass.config_entries.async_entries(DOMAIN)}
    assert ids == {"BE:1234", "LU:1234"}


async def test_same_country_and_postcode_twice_aborts(hass):
    MockConfigEntry(domain=DOMAIN, unique_id="BE:1000").add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COUNTRY: "be", CONF_POSTAL_CODE: "1000"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


def test_manifest_allows_multiple_hubs():
    import json
    from pathlib import Path

    import custom_components.colis_prive as pkg

    manifest = json.loads((Path(pkg.__file__).parent / "manifest.json").read_text())
    assert "single_config_entry" not in manifest


def _hub(parcels: list[dict]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="BE:1000",
        options={
            CONF_COUNTRY: "BE",
            CONF_POSTAL_CODE: "1000",
            CONF_PARCELS: parcels,
        },
    )


def _settings_input(
    *,
    history=False,
    filter_type="days",
    amount=7,
) -> dict:
    """Build the settings-form submission."""
    return {
        CONF_DELIVERED_FILTER_TYPE: filter_type,
        CONF_DELIVERED_FILTER_AMOUNT: amount,
        CONF_INCLUDE_HISTORY: history,
    }


async def _open_options_step(hass, entry, step_id: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["menu_options"] == ["parcels", "settings"]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step_id}
    )


async def test_options_add_parcel(hass):
    entry = _hub([])
    entry.add_to_hass(hass)

    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["test00000001"]}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_options_add_code_with_separators(hass):
    """The page shows the number grouped by three, so pasted codes carry spaces."""
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["test 0000 0001"]}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_options_keep_the_hub_fields(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": [ACTIVE_CODE]}
    )
    assert result["data"][CONF_COUNTRY] == "BE"
    assert result["data"][CONF_POSTAL_CODE] == "1000"


async def test_options_de_duplicates_tracking_codes(hass):
    entry = _hub([{CONF_TRACKING_CODE: "TEST11111111"}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["TEST11111111", "test11111111"]}
    )
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: "TEST11111111"}]


async def test_options_remove_parcel(hass):
    entry = _hub(
        [
            {CONF_TRACKING_CODE: "TEST11111111"},
            {CONF_TRACKING_CODE: "TEST22222222"},
        ]
    )
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["TEST22222222"]}
    )
    assert result["type"] == "create_entry"
    codes = {p[CONF_TRACKING_CODE] for p in result["data"][CONF_PARCELS]}
    assert codes == {"TEST22222222"}


async def test_options_can_clear_the_tracked_code_list(hass):
    entry = _hub([{CONF_TRACKING_CODE: "TEST11111111"}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": []}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == []


async def test_options_changes_history_and_delivered(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _settings_input(
            history=True,
            filter_type="parcels",
            amount=5,
        ),
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_COUNTRY] == "BE"
    assert result["data"][CONF_POSTAL_CODE] == "1000"
    assert result["data"][CONF_INCLUDE_HISTORY] is True
    assert result["data"][CONF_DELIVERED_FILTER_TYPE] == "parcels"
    assert result["data"][CONF_DELIVERED_FILTER_AMOUNT] == 5
