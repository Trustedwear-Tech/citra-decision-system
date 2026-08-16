"""Screen currency comes from the data's ontology, or from nothing at all.

Observed on acme-bank: every source declares domain.country "IN" (so the
catalogue carries currency INR), yet AppSpec.theme.currency was None, the
runtime fell back to en-US/USD, and a Rs 12,00,000 loan rendered as
"$1,200,000.00" in the queue while the agent reasoned in rupees beside it.

These pin the fill AND the two cases where filling would be worse than leaving
the hole: an authored currency, and sources that disagree.
"""
from __future__ import annotations

from types import SimpleNamespace

from locale_ontology import autowire_theme_locale


def _entry(country=None, currency=None):
    domain = {}
    if country:
        domain["country"] = country
    if currency:
        domain["currency"] = currency
    return {"domain": domain or None}


def _app(currency=None, locale=None):
    return SimpleNamespace(theme=SimpleNamespace(currency=currency, locale=locale))


def test_currency_and_locale_come_from_the_ontology():
    app = _app()
    assert autowire_theme_locale(app, {"a": _entry("IN", "INR")}) == "INR"
    assert app.theme.currency == "INR"
    assert app.theme.locale == "en-IN"


def test_currency_is_derived_from_country_when_the_entry_omits_it():
    """An MCP image older than the fill-at-validation behaviour carries a
    country but no currency. The country still settles it."""
    app = _app()
    assert autowire_theme_locale(app, {"a": _entry("IN")}) == "INR"
    assert app.theme.currency == "INR"


def test_an_authored_currency_is_never_overridden():
    """A currency on the spec is a decision. This fills holes, it does not
    correct authors."""
    app = _app(currency="USD")
    assert autowire_theme_locale(app, {"a": _entry("IN", "INR")}) is None
    assert app.theme.currency == "USD"


def test_disagreeing_sources_stamp_nothing():
    """Formatting every monetary column in one of two currencies would silently
    misstate half of them — and the reader cannot tell which half."""
    app = _app()
    cat = {"a": _entry("IN", "INR"), "b": _entry("US", "USD")}
    assert autowire_theme_locale(app, cat) is None
    assert app.theme.currency is None


def test_silent_ontology_leaves_the_runtime_default_alone():
    app = _app()
    assert autowire_theme_locale(app, {"a": {"domain": None}}) is None
    assert app.theme.currency is None


def test_locale_left_unset_when_countries_differ_but_currency_agrees():
    """Two countries sharing a currency settle the currency but not the number
    format — stamp what is known, leave what is not."""
    app = _app()
    cat = {"a": _entry("IN", "XCD"), "b": _entry("US", "XCD")}
    assert autowire_theme_locale(app, cat) == "XCD"
    assert app.theme.currency == "XCD"
    assert app.theme.locale is None


def test_dict_shaped_theme_is_handled():
    """Specs round-tripped through Mongo arrive as plain dicts."""
    app = {"theme": {"currency": None, "locale": None}}
    assert autowire_theme_locale(SimpleNamespace(theme=app["theme"]),
                                 {"a": _entry("IN", "INR")}) == "INR"
    assert app["theme"]["currency"] == "INR"


def test_no_theme_and_no_app_are_both_no_ops():
    assert autowire_theme_locale(None, {"a": _entry("IN", "INR")}) is None
    assert autowire_theme_locale(SimpleNamespace(theme=None),
                                 {"a": _entry("IN", "INR")}) is None
