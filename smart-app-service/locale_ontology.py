# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Screen locale/currency, taken from the data's own ontology.

The runtime formats every column declared ``format: "currency"`` through
``Intl.NumberFormat`` using ``AppSpec.theme.currency``, and falls back to
en-US/USD when the theme is silent (citra-app-runtime/src/lib/executiveTheme.ts
``setAppLocale``). Nothing ever filled that field, so acme-bank's retail-lending
app rendered a Rs 12,00,000 loan as ``$1,200,000.00`` in the queue while the
agent reasoned about the same record in rupees two panels away.

The ontology already knows the answer. Every source declares ``domain.country``,
and the MCP fills ``domain.currency`` from ``COUNTRY_DEFAULTS`` at validation
time (source-mcp-template/registry_models.py) so the catalogue entry always
carries it explicitly. This lifts it onto the theme during publish — in the same
pass, off the same already-fetched catalogue, as the fraud auto-wiring.

Two rules, both deliberate:

  * An author's explicit ``theme.currency`` ALWAYS wins. This only fills a hole.
  * When the ontology is silent, or two sources disagree, it stamps NOTHING and
    logs why. A confidently wrong currency on a credit file is worse than an
    unformatted number — the reader cannot tell it is wrong.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: Display locale per ISO-3166 country. The domain went GLOBAL (2026-08-10), so
#: any ISO country may arrive here. The platform's language is ENGLISH
#: everywhere, so a locale is always ``en-<CC>`` and is DERIVED rather than
#: tabulated — the old two-entry table would have silently dropped every market
#: outside IN/US the moment the enum opened.
def locale_for_country(country: str) -> str:
    """``en-<CC>`` for any ISO country; '' for a multinational deployment."""
    c = (country or "").strip().upper()
    if not c or c == "MULTINATIONAL":
        return ""
    return f"en-{c}"


#: Kept for callers that read the mapping directly; the derivation above is the
#: source of truth. Seeded with the markets that have shipped templates.
LOCALE_BY_COUNTRY: Dict[str, str] = {c: f"en-{c}" for c in ("IN", "US", "GB", "DE", "AE", "SG", "AU")}

#: Mirror of the MCP's COUNTRY_DEFAULTS currency column. Only consulted when a
#: catalogue entry carries ``domain.country`` but no ``domain.currency`` (an
#: older MCP image that predates the fill-at-validation behaviour). Anything not
#: listed falls back to USD, matching the MCP's DEFAULT_CURRENCY — a deployment
#: is never blocked for living in a country we have not tabulated.
DEFAULT_CURRENCY = "USD"
CURRENCY_BY_COUNTRY: Dict[str, str] = {
    "IN": "INR", "US": "USD", "GB": "GBP", "AE": "AED", "SG": "SGD",
    "AU": "AUD", "CA": "CAD", "JP": "JPY", "CN": "CNY", "ZA": "ZAR",
    "BR": "BRL", "MX": "MXN", "NG": "NGN", "KE": "KES", "ID": "IDR",
    "PH": "PHP", "MY": "MYR", "SA": "SAR", "CH": "CHF", "SE": "SEK",
    "MULTINATIONAL": "USD",
}
for _c in ("DE FR IT ES NL BE AT IE PT FI GR SK SI LT LV EE LU CY MT HR").split():
    CURRENCY_BY_COUNTRY[_c] = "EUR"


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field off a dict or a pydantic model indifferently — specs arrive
    as either depending on whether they have been round-tripped through Mongo."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _domain_locale(entry: Any) -> Tuple[Optional[str], Optional[str]]:
    """``(country, currency)`` from one catalogue entry's ontology domain.

    ``currency`` is whatever the ontology says; it is only derived from the
    country when the entry itself does not carry one."""
    domain = _get(entry, "domain")
    if not domain:
        return None, None
    country = _get(domain, "country")
    country = str(country).strip().upper() if country else None
    currency = _get(domain, "currency")
    currency = str(currency).strip().upper() if currency else None
    if not currency and country:
        currency = CURRENCY_BY_COUNTRY.get(country)
    return country, currency


def autowire_theme_locale(app_spec: Any, catalogue: Dict[Any, Any]) -> Optional[str]:
    """Fill ``theme.locale`` / ``theme.currency`` from the bound datasets'
    ontology. Mutates ``app_spec.theme`` in place. Returns the currency stamped,
    or None when nothing was (already set, ontology silent, or sources disagree).
    """
    if app_spec is None:
        return None
    theme = _get(app_spec, "theme")
    if theme is None:
        return None

    # An explicit authored currency is a decision, not a hole to fill.
    if _get(theme, "currency"):
        return None

    countries: set[str] = set()
    currencies: set[str] = set()
    for entry in (catalogue or {}).values():
        if not entry:
            continue
        country, currency = _domain_locale(entry)
        if country:
            countries.add(country)
        if currency:
            currencies.add(currency)

    if not currencies:
        # Not an error: an app may bind only datasets whose sources declare no
        # domain. Debug, not warning — the runtime default stands and always did.
        logger.debug("[locale-autowire] no ontology domain on any bound dataset "
                     "— theme.currency left unset (runtime default applies)")
        return None

    if len(currencies) > 1:
        # Genuinely ambiguous. Formatting every column in one of two currencies
        # would silently misstate half of them.
        logger.warning(
            "[locale-autowire] bound datasets disagree on currency (%s) — "
            "theme.currency left UNSET so nothing is formatted wrongly. Set "
            "theme.currency explicitly on the AppSpec to resolve it.",
            ", ".join(sorted(currencies)))
        return None

    currency = next(iter(currencies))
    locale = None
    if len(countries) == 1:
        country = next(iter(countries))
        locale = LOCALE_BY_COUNTRY.get(country)
        if not locale:
            logger.warning(
                "[locale-autowire] ontology country %r has no display locale "
                "mapping — currency %s stamped, locale left to the runtime "
                "default. Add it to LOCALE_BY_COUNTRY.", country, currency)

    if isinstance(theme, dict):
        theme["currency"] = currency
        if locale and not theme.get("locale"):
            theme["locale"] = locale
    else:
        theme.currency = currency
        if locale and not getattr(theme, "locale", None):
            theme.locale = locale

    logger.info("[locale-autowire] theme.currency=%s theme.locale=%s from the "
                "dataset ontology", currency, locale or "(unset)")
    return currency
