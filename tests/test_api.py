"""Tests for the Colis Privé page client."""
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.colis_prive.api import ColisPrivApiClient, ColisPrivApiError

from .payloads import ACTIVE_CODE, POSTCODE, result_html


def _session_returning(status: int, body: str = "", headers: dict | None = None):
    response = AsyncMock()
    response.status = status
    response.headers = headers or {}
    response.text = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_parses_the_result_panel():
    session = _session_returning(200, result_html())
    parcel = await ColisPrivApiClient(session).async_get_parcel(ACTIVE_CODE, POSTCODE, "en")

    assert parcel["trackingNumber"] == ACTIVE_CODE
    assert parcel["lang"] == "en"
    assert parcel["sender"] == "EXAMPLE SHOP"
    assert len(parcel["history"]) == 4


async def test_request_is_one_get_with_redirects_off():
    session = _session_returning(200, result_html())
    await ColisPrivApiClient(session).async_get_parcel(ACTIVE_CODE, POSTCODE, "nl")

    assert session.get.call_count == 1
    kwargs = session.get.call_args.kwargs
    assert kwargs["allow_redirects"] is False
    assert kwargs["params"] == {"numColis": f"{ACTIVE_CODE}{POSTCODE}", "lang": "nl"}


@pytest.mark.parametrize("location", ["/moncolis/Default.aspx", "/moncolis/default.aspx?x=1"])
async def test_redirect_to_search_form_is_not_found(location):
    session = _session_returning(302, headers={"Location": location})
    client = ColisPrivApiClient(session)
    assert await client.async_get_parcel(ACTIVE_CODE, POSTCODE, "en") is None


async def test_other_redirect_is_an_error():
    session = _session_returning(302, headers={"Location": "/elsewhere"})
    with pytest.raises(ColisPrivApiError) as err:
        await ColisPrivApiClient(session).async_get_parcel(ACTIVE_CODE, POSTCODE, "en")
    assert err.value.status_code == 302


async def test_200_without_the_panel_is_a_changed_template_not_not_found():
    session = _session_returning(200, result_html(panel=False))
    with pytest.raises(ColisPrivApiError) as err:
        await ColisPrivApiClient(session).async_get_parcel(ACTIVE_CODE, POSTCODE, "en")
    assert "layout" in err.value.detail


async def test_changed_layout_warns_only_once(caplog):
    client = ColisPrivApiClient(_session_returning(200, result_html(panel=False)))
    for _ in range(3):
        with pytest.raises(ColisPrivApiError):
            await client.async_get_parcel(ACTIVE_CODE, POSTCODE, "en")
    assert caplog.text.count("page layout not recognised") == 1


@pytest.mark.parametrize("status", [500, 503, 404])
async def test_server_errors_raise_with_status(status):
    session = _session_returning(status)
    with pytest.raises(ColisPrivApiError) as err:
        await ColisPrivApiClient(session).async_get_parcel(ACTIVE_CODE, POSTCODE, "en")
    assert err.value.status_code == status


@pytest.mark.parametrize(
    ("header", "expected"), [("120", 120.0), ("Wed, 21 Oct 2026 07:28:00 GMT", None), (None, None)]
)
async def test_429_carries_retry_after(header, expected):
    headers = {"Retry-After": header} if header else {}
    session = _session_returning(429, headers=headers)
    with pytest.raises(ColisPrivApiError) as err:
        await ColisPrivApiClient(session).async_get_parcel(ACTIVE_CODE, POSTCODE, "en")
    assert err.value.status_code == 429
    assert err.value.retry_after == expected


async def test_network_error_propagates():
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    with pytest.raises(aiohttp.ClientError):
        await ColisPrivApiClient(session).async_get_parcel(ACTIVE_CODE, POSTCODE, "en")
