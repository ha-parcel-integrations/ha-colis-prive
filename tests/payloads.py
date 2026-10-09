"""Synthetic Colis Privé result pages and parsed payloads shared by the tests.

Everything here is invented: the numbers, the sender, the dates and the
recipient block. The structure mirrors the result panel the parser relies on.
"""
from __future__ import annotations

ACTIVE_CODE = "TEST00000001"
DELIVERED_CODE = "TEST00000002"
OTHER_CODE = "TEST00000003"
POSTCODE = "1000"

# Lives in the recipient block of every fixture page; no output may carry it.
FAKE_ADDRESS = "Fiktiva Testperson, Rue Imaginaire 99, 9999 Faketown"
FAKE_NAME_FRAGMENT = "Fiktiva"

# stage -> sentence, per page language.
SENTENCES = {
    "fr": {
        "out_for_delivery": "Votre colis est en cours de distribution par le livreur",
        "arrived": "Votre colis est arrivé sur notre agence régionale de distribution.",
        "taken_over": (
            "Votre colis est pris en charge par Colis Privé. Il va être expédié "
            "vers notre agence régionale de distribution."
        ),
        "registered": (
            "Votre colis est en cours de préparation par l'expéditeur. "
            "Il nous sera confié prochainement."
        ),
    },
    "nl": {
        "out_for_delivery": "Jouw pakket is onderweg naar jou.",
        "arrived": "Jouw pakket is toegekomen bij onze regionale bezorgpartner.",
        "taken_over": (
            "Jouw pakket is aangekomen op het nationaal platform van Colis Privé "
            "en wordt doorgestuurd naar onze regionale bezorgpartner."
        ),
        "registered": (
            "Jouw pakket wordt klaargemaakt door de webshop en wordt binnenkort "
            "overgeleverd aan Colis Privé."
        ),
    },
    "en": {
        "out_for_delivery": "Your parcel is being delivered by the driver.",
        "arrived": "Your parcel has arrived at our regional distribution office.",
        "taken_over": (
            "Your parcel is well received by a Colis Privé branch. "
            "It will be sent to its final destination."
        ),
        "registered": (
            "Your package has been prepared by your webmerchant. "
            "It will soon be delivered to Colis Privé."
        ),
    },
}
STAGES_OLDEST_FIRST = ["registered", "taken_over", "arrived", "out_for_delivery"]
EXPECTED = {
    "registered": "registered",
    "taken_over": "in_transit",
    "arrived": "in_transit",
    "out_for_delivery": "out_for_delivery",
}

# Not a real carrier sentence: tests add a keyword for it to exercise the
# delivered branch, which the real tables cannot reach yet.
DELIVERED_TEXT = "Test sentence: parcel handed over to recipient"
DELIVERED_KEYWORD = "handed over to recipient"

DATES = ["02/03/2026", "03/03/2026", "03/03/2026", "04/03/2026"]


def rows_newest_first(lang: str = "en", stages=STAGES_OLDEST_FIRST, dates=DATES):
    """History rows as the page lists them: newest first."""
    pairs = list(zip(dates, stages, strict=True))
    return [{"date": date, "text": SENTENCES[lang][stage]} for date, stage in reversed(pairs)]


def result_html(
    *,
    sender: str = "EXAMPLE SHOP",
    status: str | None = None,
    rows: list[dict] | None = None,
    lang: str = "en",
    npai: str | None = "0",
    address: str = FAKE_ADDRESS,
    spacer: bool = True,
    header: bool = True,
    panel: bool = True,
) -> str:
    """Render a synthetic result page."""
    rows = rows if rows is not None else rows_newest_first(lang)
    status = status if status is not None else rows[0]["text"]
    body = "".join(
        f'<tr class="bandeauText"><td headers="th-date">{row["date"]}</td>'
        f'<td headers="th-statut">{row["text"]}</td></tr>'
        for row in rows
    )
    head = (
        '<tr class="bandeauTitre"><th id="th-date">Date</th>'
        '<th id="th-statut">Status</th></tr>'
        if header
        else ""
    )
    spacer_row = '<tr><td colspan="2">&nbsp;</td></tr>' if spacer else ""
    npai_input = (
        f'<input type="hidden" id="ctl00_CDC_CtlBandeauInfoColis_hfNPAI" value="{npai}" />'
        if npai is not None
        else ""
    )
    inner = f"""
    <h1>Parcel sent by <span>{sender}</span></h1>
    {npai_input}
    <div class="divColis"><table><tr><td class="tdTitre">Parcel</td>
      <td class="tdText">123 456 789 012</td></tr></table></div>
    <div class="divStatut"><table><tr><td class="tdTitre">Status</td>
      <td class="tdText">{status}</td></tr></table></div>
    <div class="divDesti"><table><tr><td class="tdTitre">To</td>
      <td class="tdText">{address}<br/>second line</td></tr></table></div>
    <table class="tableHistoriqueColis">{spacer_row}{head}{body}</table>
    <p>Rescheduling is not available for this parcel.</p>
    """
    if panel:
        inner = f'<div id="ctl00_CDC_pnResultatColis">{inner}</div>'
    return f"<html><body><form>{inner}</form></body></html>"


def parsed(
    code: str = ACTIVE_CODE,
    *,
    lang: str = "en",
    stages=STAGES_OLDEST_FIRST,
    dates=DATES,
    status: str | None = None,
) -> dict:
    """What ``api.async_get_parcel`` returns for a page."""
    rows = rows_newest_first(lang, stages, dates)
    return {
        "sender": "EXAMPLE SHOP",
        "statusText": status if status is not None else rows[0]["text"],
        "history": rows,
        "npai": "0",
        "trackingNumber": code,
        "lang": lang,
    }


def active_sample(code: str = ACTIVE_CODE, lang: str = "en") -> dict:
    """An out-for-delivery parcel."""
    return parsed(code, lang=lang)


def in_transit_sample(code: str = ACTIVE_CODE, lang: str = "en") -> dict:
    """A parcel that reached the regional office."""
    return parsed(code, lang=lang, stages=STAGES_OLDEST_FIRST[:3], dates=DATES[:3])


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A delivered parcel; needs the ``delivered_keyword`` fixture to map."""
    sample = parsed(code, lang="en")
    sample["history"] = [{"date": "05/03/2026", "text": DELIVERED_TEXT}, *sample["history"]]
    sample["statusText"] = DELIVERED_TEXT
    return sample
