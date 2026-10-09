"""Config flow for the Colis Privé parcel tracker integration."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_COUNTRY,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_INCLUDE_HISTORY,
    DOMAIN,
    POSTCODE_LENGTHS,
    SUPPORTED_COUNTRIES,
)

_LOGGER = logging.getLogger(__name__)

_COUNTRY_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[country.lower() for country in SUPPORTED_COUNTRIES],
        translation_key=CONF_COUNTRY,
        mode=selector.SelectSelectorMode.DROPDOWN,
        sort=True,
    )
)


def _hub_schema(country: str) -> vol.Schema:
    """Build the hub setup schema, retaining the selected country on errors."""
    return vol.Schema(
        {
            vol.Required(CONF_COUNTRY, default=country.lower()): _COUNTRY_SELECTOR,
            vol.Required(CONF_POSTAL_CODE): str,
        }
    )


def normalize_tracking_code(value: str) -> str:
    """Return the tracking code upper-cased with separators stripped.

    The page shows the number grouped by three, so pasted codes carry spaces.
    """
    return re.sub(r"[^A-Z0-9]+", "", (value or "").upper())


def valid_tracking_code(value: str) -> bool:
    """Accept every non-empty code.

    Carriers' real tracking-number formats vary too much, and often aren't
    fully confirmed, to gate on a guessed shape — a false negative from a
    too-strict regex is far more annoying than a bad code that simply comes
    back "not found" on the next poll. Do not add a format regex here; this
    is a suite-wide convention, not a per-carrier TODO.
    """
    return bool(value)


def normalize_postcode(value: str) -> str:
    """Return a postcode without spaces or a leading ``L-`` / ``B-``."""
    cleaned = re.sub(r"\s+", "", (value or "").upper())
    return re.sub(r"^[LB]-", "", cleaned)


def valid_postcode(value: str, country: str) -> bool:
    """Whether ``value`` has the digit count of ``country``'s postcodes."""
    length = POSTCODE_LENGTHS.get(country)
    return length is not None and re.fullmatch(rf"\d{{{length}}}", value or "") is not None


def _current_parcels(entry: ConfigEntry) -> list[dict[str, str]]:
    """Return a mutable copy of the tracked parcels list."""
    return [dict(item) for item in entry.options.get(CONF_PARCELS, [])]


def _base_options(entry: ConfigEntry) -> dict[str, Any]:
    """Return hub fields that every options-flow save must preserve."""
    return {
        CONF_COUNTRY: entry.options.get(CONF_COUNTRY),
        CONF_POSTAL_CODE: entry.options.get(CONF_POSTAL_CODE),
    }


def _clean_tracking_codes(values: list[str] | None) -> list[str]:
    """Normalise, drop blanks, and de-duplicate tracking codes."""
    codes: list[str] = []
    for value in values or []:
        code = normalize_tracking_code(value)
        if code and code not in codes:
            codes.append(code)
    return codes


class ColisPrivConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI-driven configuration flow for the Colis Privé integration."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> ColisPrivOptionsFlowHandler:
        """Return the options flow handler."""
        return ColisPrivOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create a country-and-postcode hub without a network call.

        Several hubs may coexist; the unique id is the only limit. Parcels are
        added afterwards via the options flow or the ``track_parcel`` service.
        """
        errors: dict[str, str] = {}
        country = self.hass.config.country
        country = country if country in SUPPORTED_COUNTRIES else "FR"
        if user_input is not None:
            country = user_input[CONF_COUNTRY].upper()
            postal_code = normalize_postcode(user_input[CONF_POSTAL_CODE])
            if not valid_postcode(postal_code, country):
                errors[CONF_POSTAL_CODE] = "invalid_postcode"
            else:
                await self.async_set_unique_id(f"{country}:{postal_code}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Colis Privé ({postal_code})",
                    data={},
                    options={
                        CONF_COUNTRY: country,
                        CONF_POSTAL_CODE: postal_code,
                        CONF_PARCELS: [],
                        CONF_DELIVERED_FILTER_TYPE: DEFAULT_DELIVERED_FILTER_TYPE,
                        CONF_DELIVERED_FILTER_AMOUNT: DEFAULT_DELIVERED_FILTER_AMOUNT,
                        CONF_INCLUDE_HISTORY: DEFAULT_INCLUDE_HISTORY,
                    },
                )
        return self.async_show_form(
            step_id="user", data_schema=_hub_schema(country), errors=errors
        )


class ColisPrivOptionsFlowHandler(OptionsFlow):
    """Manage tracked parcels separately from integration settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer parcel management separately from integration settings."""
        return self.async_show_menu(
            step_id="init", menu_options=["parcels", "settings"]
        )

    async def async_step_parcels(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle the complete tracked-code list."""
        errors: dict[str, str] = {}
        if user_input is not None:
            codes = _clean_tracking_codes(user_input.get("tracking_codes"))
            if any(not valid_tracking_code(code) for code in codes):
                errors["base"] = "invalid_tracking_code"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        **_base_options(self.config_entry),
                        CONF_PARCELS: [{CONF_TRACKING_CODE: code} for code in codes],
                        CONF_DELIVERED_FILTER_TYPE: self.config_entry.options.get(
                            CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
                        ),
                        CONF_DELIVERED_FILTER_AMOUNT: self.config_entry.options.get(
                            CONF_DELIVERED_FILTER_AMOUNT,
                            DEFAULT_DELIVERED_FILTER_AMOUNT,
                        ),
                        CONF_INCLUDE_HISTORY: self.config_entry.options.get(
                            CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
                        ),
                    },
                )
        current_codes = [
            p[CONF_TRACKING_CODE] for p in _current_parcels(self.config_entry)
        ]
        schema = vol.Schema(
            {
                vol.Optional("tracking_codes"): selector.TextSelector(
                    selector.TextSelectorConfig(multiple=True)
                )
            }
        )
        return self.async_show_form(
            step_id="parcels",
            data_schema=self.add_suggested_values_to_schema(
                schema, {"tracking_codes": current_codes}
            ),
            errors=errors,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle the non-parcel integration settings."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    **_base_options(self.config_entry),
                    CONF_PARCELS: _current_parcels(self.config_entry),
                    CONF_DELIVERED_FILTER_TYPE: user_input[CONF_DELIVERED_FILTER_TYPE],
                    CONF_DELIVERED_FILTER_AMOUNT: int(
                        user_input[CONF_DELIVERED_FILTER_AMOUNT]
                    ),
                    CONF_INCLUDE_HISTORY: bool(user_input[CONF_INCLUDE_HISTORY]),
                },
            )
        current = self.config_entry.options
        schema: dict[Any, Any] = {
            vol.Required(
                CONF_DELIVERED_FILTER_TYPE,
                default=current.get(
                    CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
                ),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["days", "parcels"],
                    translation_key=CONF_DELIVERED_FILTER_TYPE,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Required(
                CONF_DELIVERED_FILTER_AMOUNT,
                default=current.get(
                    CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=365, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_INCLUDE_HISTORY,
                default=current.get(CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY),
            ): selector.BooleanSelector(),
        }
        return self.async_show_form(step_id="settings", data_schema=vol.Schema(schema))
