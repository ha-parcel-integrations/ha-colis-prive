"""Services for the Colis Privé parcel tracker integration.

`colis_prive.track_parcel` / `colis_prive.untrack_parcel` let you add or remove a
tracked parcel without opening the integration options — so a Lovelace button
can start tracking a parcel straight from a dashboard.
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .config_flow import (
    normalize_postcode,
    normalize_tracking_code,
    valid_tracking_code,
)
from .const import (
    CONF_COUNTRY,
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DOMAIN,
)

SERVICE_TRACK_PARCEL = "track_parcel"
SERVICE_UNTRACK_PARCEL = "untrack_parcel"

_HUB_FIELDS = {
    vol.Optional(CONF_COUNTRY): cv.string,
    vol.Optional(CONF_POSTAL_CODE): cv.string,
}
_TRACK_SCHEMA = vol.Schema({vol.Required(CONF_TRACKING_CODE): cv.string, **_HUB_FIELDS})
_UNTRACK_SCHEMA = vol.Schema(
    {vol.Required(CONF_TRACKING_CODE): cv.string, **_HUB_FIELDS}
)


def _filter_hubs(entries, country: str | None, postal_code: str | None):
    """Narrow ``entries`` to the hubs matching whichever fields were given."""
    if country:
        entries = [e for e in entries if e.options.get(CONF_COUNTRY) == country.upper()]
    if postal_code:
        target = normalize_postcode(postal_code)
        entries = [e for e in entries if e.options.get(CONF_POSTAL_CODE) == target]
    return entries


def _require_hub_fields(country: str | None, postal_code: str | None) -> None:
    """Demand both hub fields: BE and LU postcodes collide, so neither is enough."""
    for field, value in ((CONF_COUNTRY, country), (CONF_POSTAL_CODE, postal_code)):
        if not value:
            raise ServiceValidationError(
                f"Multiple Colis Privé hubs are set up — pass {field}"
            )


def _resolve_entry(
    hass: HomeAssistant, country: str | None = None, postal_code: str | None = None
):
    """Return the selected hub, requiring country and postcode when ambiguous."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Colis Privé is not set up")
    if len(entries) > 1:
        _require_hub_fields(country, postal_code)
    matches = _filter_hubs(entries, country, postal_code)
    if not matches:
        raise ServiceValidationError(
            "No Colis Privé hub for this country and postal code"
        )
    return matches[0]


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Colis Privé services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_TRACK_PARCEL):
        return

    async def _track(call: ServiceCall) -> None:
        tracking_code = normalize_tracking_code(call.data[CONF_TRACKING_CODE])
        if not valid_tracking_code(tracking_code):
            raise ServiceValidationError(
                f"'{tracking_code}' is not a valid Colis Privé tracking code"
            )
        entry = _resolve_entry(
            hass, call.data.get(CONF_COUNTRY), call.data.get(CONF_POSTAL_CODE)
        )

        parcels = [dict(p) for p in entry.options.get(CONF_PARCELS, [])]
        if any(p[CONF_TRACKING_CODE] == tracking_code for p in parcels):
            return  # already tracked — no-op
        parcels.append({CONF_TRACKING_CODE: tracking_code})
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_PARCELS: parcels}
        )

    async def _untrack(call: ServiceCall) -> None:
        tracking_code = normalize_tracking_code(call.data[CONF_TRACKING_CODE])
        country = call.data.get(CONF_COUNTRY)
        postal_code = call.data.get(CONF_POSTAL_CODE)
        holders = [
            entry
            for entry in _filter_hubs(
                hass.config_entries.async_entries(DOMAIN), country, postal_code
            )
            if any(
                p[CONF_TRACKING_CODE] == tracking_code
                for p in entry.options.get(CONF_PARCELS, [])
            )
        ]
        if len(holders) > 1:
            _require_hub_fields(country, postal_code)
        for entry in holders:
            kept = [
                p
                for p in entry.options.get(CONF_PARCELS, [])
                if p[CONF_TRACKING_CODE] != tracking_code
            ]
            hass.config_entries.async_update_entry(
                entry, options={**entry.options, CONF_PARCELS: kept}
            )

    hass.services.async_register(
        DOMAIN, SERVICE_TRACK_PARCEL, _track, schema=_TRACK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UNTRACK_PARCEL, _untrack, schema=_UNTRACK_SCHEMA
    )


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the Colis Privé services once the last hub unloads."""
    if any(
        entry.state is ConfigEntryState.LOADED
        for entry in hass.config_entries.async_entries(DOMAIN)
    ):
        return
    for service in (SERVICE_TRACK_PARCEL, SERVICE_UNTRACK_PARCEL):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
