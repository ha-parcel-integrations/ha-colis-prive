"""Tests for Colis Privé diagnostics."""
from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.colis_prive.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .payloads import ACTIVE_CODE

REDACTED = "**REDACTED**"


async def test_diagnostics_redacts_everything_identifying(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    from custom_components.colis_prive.parcels import normalize_parcel

    from .payloads import FAKE_NAME_FRAGMENT, POSTCODE, active_sample

    parcel = normalize_parcel(
        active_sample(), include_history=True, country="BE", postal_code=POSTCODE
    )
    entry = MagicMock()
    entry.options = {
        "country": "BE",
        "postal_code": POSTCODE,
        "parcels": [{"tracking_code": ACTIVE_CODE}],
    }
    entry.runtime_data.coordinator.current_tier_minutes = 15
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=15)
    entry.runtime_data.coordinator.data = [parcel]
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.delivered_codes = set()

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {
        "incoming_active": 1,
        "delivered": 0,
        "skipped_from_fetch": 0,
    }
    assert result["polling"] == {
        "tier_minutes": 15,
        "update_interval_seconds": 900.0,
        "suspended": False,
    }
    assert result["entry_options"]["postal_code"] == REDACTED
    assert result["entry_options"]["parcels"][0]["tracking_code"] == REDACTED
    assert result["entry_options"]["country"] == "BE"

    incoming = result["incoming"][0]
    for key in ("barcode", "sender", "url", "raw_status", "history"):
        assert incoming[key] == REDACTED, key
    raw = incoming["raw"]
    for key in ("trackingNumber", "sender", "statusText", "history", "npai"):
        assert raw[key] == REDACTED, key

    dumped = repr(result)
    for leaked in (ACTIVE_CODE, POSTCODE, "EXAMPLE SHOP", "being delivered", "regional", FAKE_NAME_FRAGMENT):
        assert leaked not in dumped, leaked
    # non-identifying fields survive, or the diagnostics would be useless
    assert incoming["status"] == "out_for_delivery"
    assert raw["lang"] == "en"


async def test_diagnostics_reports_suspended_polling(hass):
    """update_interval None (Section 2.1's full stop) must be visible, not just absent."""
    entry = MagicMock()
    entry.options = {"parcels": []}
    entry.runtime_data.coordinator.current_tier_minutes = None
    entry.runtime_data.coordinator.update_interval = None
    entry.runtime_data.coordinator.data = []
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["polling"] == {
        "tier_minutes": None,
        "update_interval_seconds": None,
        "suspended": True,
    }
