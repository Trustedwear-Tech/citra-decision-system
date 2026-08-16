"""POST /publish must never store a spec that AppSpec refuses to load.

THE INVARIANT
-------------
Publish validates its payload, then MUTATES it before writing — model_copy for
app_id/version/ownership, and `maybe_inject_chart_panel` to add a chart the
builder may have omitted. Every mutation happens AFTER validation, so nothing
re-checks the result. If a mutation produces something the model rejects, the
document lands in Mongo and *every subsequent read* raises ValidationError:
the smoke gate, the runtime, My Apps. The app is bricked and publish reported
200.

This is not theoretical. `maybe_inject_chart_panel` appended `trigger_chart` to
an embed page — the one panel type AppSpec forbids there — and the resulting
`loan-credit-decision` document 500'd on every read. The builder, seeing only a
bodiless 500, misdiagnosed it and handed the BA a URL for a dead app.

So these tests assert the round trip, not the response code: publish it, read
the STORED document back, and validate it with the same model the readers use.
A 200 from /publish proves nothing on its own — that was exactly the failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests._test_helpers import _mint, client  # type: ignore  # noqa: E402,F401

from models import AppSpec  # noqa: E402

TENANT = "acme-bank"


def _spec(*, slug, pages):
    return {
        "spec_version": "v0",
        "slug": slug,
        "title": slug.replace("-", " ").title(),
        "agent_id": "agent_x",
        "tenant_id": TENANT,
        "data_sources": [{"id": "ds_main", "type": "static", "ref": "inline:x"}],
        "pages": pages,
    }


# A queue with a time column AND a measure — the shape the chart injector acts
# on. If the injector is going to misfire anywhere, it is on this page.
_CHARTABLE_QUEUE = {
    "id": "trigger",
    "type": "queue",
    "data_source": "ds_main",
    "columns": ["application_id", "amount", "created_at"],
    # An embed page must carry a trigger to validate at all. The queue holds it
    # here because a queue is the only panel the chart injector acts on, which
    # is what these tests are about.
    "actions": [{"label": "Review", "agent_action": "review_application"}],
}


def _minimal_agent_spec(slug):
    return {
        "spec_version": "v0",
        "agent_id": "agent_x",
        "name": f"agent for {slug}",
        "description": "publish round-trip",
        "model_tier": "tier_b",
        "system_prompt": "You are a test agent.",
        "input_schema": {"type": "object"},
        "tools": [],
        "actions": [{"name": "noop"}],
    }


def _publish(c, spec):
    return c.post(
        "/publish",
        # session_id is required by PublishRequest. A human dev-publish has no
        # build session, so this resolves to nothing and ownership falls back to
        # the publisher's own identity — which is all this test needs.
        json={"session_id": "bs_roundtrip_test", "app_spec": spec,
              "agent_spec": _minimal_agent_spec(spec["slug"])},
        headers={"Authorization": "Bearer " + _mint("ba@acme.test", TENANT)},
    )


def _stored(c, slug):
    docs = [d for d in c._cols["apps"].docs if d.get("slug") == slug]
    assert docs, f"nothing stored for {slug!r} — publish did not persist"
    return docs[-1]


@pytest.mark.parametrize("kind,panels", [
    # The regression case: an embed page whose queue is chart-worthy.
    ("embed", [_CHARTABLE_QUEUE]),
    # A standard page SHOULD get a chart — proving the guard is targeted, not a
    # blanket disable that would silently drop a real feature.
    ("standard", [_CHARTABLE_QUEUE]),
])
def test_published_spec_reads_back(client, kind, panels):  # noqa: F811
    slug = f"roundtrip-{kind}"
    r = _publish(client, _spec(slug=slug, pages=[
        {"id": "p1", "kind": kind, "title": "P", "panels": panels},
    ]))
    assert r.status_code == 200, r.text

    doc = _stored(client, slug)
    # THE ASSERTION THAT MATTERS: the readers' model must load what publish
    # wrote. Without this, a 200 above is meaningless.
    AppSpec.model_validate(doc["app_spec"])


def test_embed_page_is_stored_without_a_chart(client):  # noqa: F811
    """Concrete form of the bug: no chart may reach an embed page."""
    r = _publish(client, _spec(slug="roundtrip-embed-panels", pages=[
        {"id": "card", "kind": "embed", "title": "Card", "panels": [_CHARTABLE_QUEUE]},
    ]))
    assert r.status_code == 200, r.text

    page = _stored(client, "roundtrip-embed-panels")["app_spec"]["pages"][0]
    types = [p["type"] for p in page["panels"]]
    assert "chart" not in types, f"publish injected a chart into an embed page: {types}"


def test_mixed_app_charts_the_standard_page_only(client):  # noqa: F811
    """The guard must skip the embed page without suppressing injection for the
    rest of the app — otherwise fixing the brick would quietly cost the feature.
    """
    r = _publish(client, _spec(slug="roundtrip-mixed", pages=[
        {"id": "card", "kind": "embed", "title": "Card", "panels": [_CHARTABLE_QUEUE]},
        {"id": "ops", "kind": "standard", "title": "Ops", "panels": [
            dict(_CHARTABLE_QUEUE, id="all_apps")]},
    ]))
    assert r.status_code == 200, r.text

    doc = _stored(client, "roundtrip-mixed")
    AppSpec.model_validate(doc["app_spec"])
    pages = {p["id"]: [q["type"] for q in p["panels"]] for p in doc["app_spec"]["pages"]}
    assert "chart" not in pages["card"], pages["card"]
    assert "chart" in pages["ops"], pages["ops"]


# ── every build kind, not just the embed one ────────────────────────────────
#
# The embed work touched SHARED code: the chart injector, `_load_app_spec` at 23
# call sites, DetailPanel gaining `actions`, and the smoke gate's trigger lookup.
# None of that is embed-only, so "the embed tests pass" says nothing about the
# other three surfaces the builder can produce. These publish one of each and
# assert the stored spec still loads — the same round trip, applied across the
# build kinds rather than across one.


def _dashboard_page():
    """A dashboard page: KPI + chart, and the chart MUST survive. The embed fix
    made the injector skip embed pages; if that guard were too broad it would
    silently stop charting dashboards, which is where charts belong."""
    return {
        "id": "overview", "kind": "dashboard", "title": "Overview",
        "panels": [
            {"id": "kpis", "type": "dashboard",
             "metrics": [{"name": "Open", "agg": "count", "data_source": "ds_main"}]},
            {"id": "trend", "type": "chart", "chart_type": "line",
             "data_source": "ds_main", "x": "created_at", "y": "amount"},
        ],
    }


def _standard_page_with_detail():
    """The classic app shape: a queue the officer clicks and a detail bound with
    linked_to. DetailPanel gained `actions`; this asserts a detail panel WITHOUT
    them still publishes exactly as before."""
    return {
        "id": "work", "kind": "standard", "title": "Work",
        "panels": [
            _CHARTABLE_QUEUE,
            {"id": "det", "type": "detail", "linked_to": "trigger",
             "sections": [{"type": "fields"}]},
        ],
    }


def test_dashboard_app_still_publishes_and_reads_back(client):  # noqa: F811
    r = _publish(client, _spec(slug="rt-dashboard", pages=[_dashboard_page()]))
    assert r.status_code == 200, r.text
    doc = _stored(client, "rt-dashboard")
    AppSpec.model_validate(doc["app_spec"])
    types = [p["type"] for p in doc["app_spec"]["pages"][0]["panels"]]
    assert "chart" in types, f"the dashboard lost its chart: {types}"


def test_standard_app_with_linked_detail_still_publishes(client):  # noqa: F811
    r = _publish(client, _spec(slug="rt-standard", pages=[_standard_page_with_detail()]))
    assert r.status_code == 200, r.text
    doc = _stored(client, "rt-standard")
    AppSpec.model_validate(doc["app_spec"])
    det = [p for p in doc["app_spec"]["pages"][0]["panels"] if p["type"] == "detail"][0]
    # No actions authored ⇒ none invented. The new field is additive.
    assert not det.get("actions"), det.get("actions")
    assert det.get("linked_to") == "trigger"


def test_headless_api_app_still_publishes(client):  # noqa: F811
    """The decision-API build: no pages at all. `is_external_surface` treats it
    as external alongside embeds, so it shares code with the embed path — but
    the embed page rules must not reach it, because it has no pages to check."""
    spec = _spec(slug="rt-headless", pages=[])
    spec.pop("pages")
    spec["headless"] = True
    r = _publish(client, spec)
    assert r.status_code == 200, r.text
    doc = _stored(client, "rt-headless")
    AppSpec.model_validate(doc["app_spec"])
    assert doc["app_spec"].get("headless") is True


# ── each mode still DELIVERS its artefact ───────────────────────────────────
#
# Publishing and reading back is necessary but not sufficient: each surface
# hands the customer a different thing, and that handoff is what the embed work
# could have broken without any test noticing. A dashboard that publishes but
# renders no chart, or an API app whose decision-contract 404s, is a mode that
# "works" by the round-trip test and is useless in practice.
#
# One assertion per mode, on the artefact the BA is actually given.


# A test publish is forced to audience="owner", so the caller can only read the
# app back if it IS the owning Work SA. `_mint` carries no SA claims, so a token
# from it publishes an app it then cannot see — a 404 that looks like a broken
# endpoint and is actually the audience gate working. Mint the owner explicitly.
_WORK_SA = f"svc:work-ba-acme-test@{TENANT}.citra.ai"


def _owner_hdr():
    import time as _t

    import jwt as _jwt

    from tests._test_helpers import JWT_SECRET  # type: ignore

    tok = _jwt.encode({
        "sub": "ba@acme.test", "user_id": "ba@acme.test", "email": "ba@acme.test",
        "tenant_id": TENANT, "org_id": TENANT,
        "work_sa_id": _WORK_SA, "service_account_admin_of": [_WORK_SA],
        "iat": int(_t.time()), "exp": int(_t.time()) + 600, "iss": "Citra-AI",
    }, JWT_SECRET, algorithm="HS256")
    return {"Authorization": "Bearer " + tok}


def _agent_with_action(slug):
    a = _minimal_agent_spec(slug)
    a["actions"] = [{"name": "review_application"}]
    return a


def _publish_full(c, spec):
    return c.post(
        "/publish",
        json={"session_id": "bs_delivery", "app_spec": spec,
              "agent_spec": _agent_with_action(spec["slug"])},
        headers=_owner_hdr(),
    )


def _hdr():
    return _owner_hdr()


def test_embed_mode_delivers_a_snippet_and_a_resolvable_key(client):  # noqa: F811
    """What the BA hands their developer: Export on the My Apps card."""
    spec = _spec(slug="deliver-embed", pages=[{
        "id": "card", "kind": "embed", "title": "Card",
        "panels": [{"id": "d", "type": "detail", "data_source": "ds_main",
                    "id_field": "application_id",
                    "actions": [{"label": "Review",
                                 "agent_action": "review_application"}],
                    "sections": [{"type": "fields"}]}],
    }])
    assert _publish_full(client, spec).status_code == 200

    r = client.get("/apps/deliver-embed/embed/snippet", headers=_hdr())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["embed_key"].startswith("emb_"), body["embed_key"]
    assert "citra.js" in body["snippet"]
    # The developer must be told WHICH id to pass — an empty card with no
    # explanation is the failure this replaced.
    assert body["record_contract"]["key_field"] == "application_id"
    assert "application_id" in body["snippet"]

    # And the key the snippet carries must actually resolve to the spec.
    spec_resp = client.get(f"/embed/{body['embed_key']}/spec", headers=_hdr())
    assert spec_resp.status_code == 200, spec_resp.text


def test_api_mode_delivers_a_decision_contract(client):  # noqa: F811
    """What the BA hands their developer for a headless build: /run + /approve
    and the input schema. Headless shares the is_external_surface path with
    embeds, so it is the mode most exposed to the embed changes."""
    spec = _spec(slug="deliver-api", pages=[])
    spec.pop("pages")
    spec["headless"] = True
    assert _publish_full(client, spec).status_code == 200

    r = client.get("/apps/deliver-api/decision-contract", headers=_hdr())
    assert r.status_code == 200, r.text
    assert r.json(), "empty decision contract"


def test_app_and_dashboard_modes_deliver_their_pages(client):  # noqa: F811
    """A UI build's artefact is the rendered app. Assert the stored pages are
    what the runtime needs: the dashboard keeps its chart, the app keeps its
    queue+detail, and neither gained an embed-only constraint."""
    spec = _spec(slug="deliver-ui", pages=[
        _dashboard_page(), _standard_page_with_detail(),
    ])
    assert _publish_full(client, spec).status_code == 200

    doc = _stored(client, "deliver-ui")
    AppSpec.model_validate(doc["app_spec"])
    pages = {p["id"]: [q["type"] for q in p["panels"]]
             for p in doc["app_spec"]["pages"]}
    assert "chart" in pages["overview"], pages["overview"]
    assert "queue" in pages["work"] and "detail" in pages["work"], pages["work"]

    # A UI app must NOT be treated as an external surface — no embed key minted,
    # and Export must refuse rather than hand over a snippet for a card that
    # does not exist.
    assert not doc.get("embed_key"), doc.get("embed_key")
    r = client.get("/apps/deliver-ui/embed/snippet", headers=_hdr())
    assert r.status_code == 409, r.status_code
