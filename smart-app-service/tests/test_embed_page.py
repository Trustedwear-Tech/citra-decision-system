# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Publish validation for the EMBED page kind and the standalone detail panel.

An embed page is a decision card rendered inside a CUSTOMER's application by
the citra.js bundle (docs/embeddable-decision-ui-plan.md). Two contract changes
make it possible, and both are load-bearing enough to guard:

  1. `DetailPanel` can bind its record via `data_source` instead of `linked_to`.
     An embed has no queue to click — the host application already knows which
     record the officer has open and passes the id in. Before this, a detail
     panel without a linked queue resolved NO record and rendered an empty card.

  2. `page.kind="embed"` rejects chart/map panels at publish. They cannot render
     (the bundle aliases echarts and leaflet away), so the failure has to land
     in front of the builder rather than a customer's officer.

The pre-existing `linked_to` path must keep working untouched — every app in
production uses it.
"""

from __future__ import annotations

import pytest

from validators import validate_app_spec, PydanticValidationError


def _spec(panels=None, pages=None):
    spec = {
        "spec_version": "v0",
        "slug": "embed-demo",
        "title": "Embed Demo",
        "agent_id": "demo_agent",
        "data_sources": [{"id": "ds", "type": "mcp", "ref": "s.t"}],
    }
    if pages is not None:
        spec["pages"] = pages
    else:
        spec["panels"] = panels or []
    return spec


# ── DetailPanel record binding ──────────────────────────────────────────────

def test_detail_via_linked_queue_still_publishes():
    """The classic binding — unchanged, and the one every live app uses."""
    validate_app_spec(_spec([
        {"id": "board", "type": "queue", "data_source": "ds", "columns": ["id"]},
        {"id": "det", "type": "detail", "linked_to": "board",
         "sections": [{"type": "fields"}]},
    ]))


def test_detail_via_direct_data_source_publishes():
    """The embed binding: read the record by id, with no queue in the spec."""
    validate_app_spec(_spec([
        {"id": "det", "type": "detail", "data_source": "ds",
         "id_field": "application_id",
         "sections": [{"type": "fields"}, {"type": "approval"}]},
    ]))


def test_detail_with_neither_binding_is_rejected():
    """Neither set was the original failure mode — the panel renders its chrome,
    resolves no record, and shows an empty card with no error anywhere."""
    with pytest.raises(PydanticValidationError) as exc:
        validate_app_spec(_spec([
            {"id": "det", "type": "detail", "sections": [{"type": "fields"}]},
        ]))
    assert "linked_to" in str(exc.value) and "data_source" in str(exc.value)


def test_detail_with_both_bindings_is_rejected():
    """Ambiguous: the resolver would silently prefer one and the author would
    never learn which."""
    with pytest.raises(PydanticValidationError) as exc:
        validate_app_spec(_spec([
            {"id": "board", "type": "queue", "data_source": "ds",
             "columns": ["id"]},
            {"id": "det", "type": "detail", "linked_to": "board",
             "data_source": "ds", "sections": [{"type": "fields"}]},
        ]))
    assert "BOTH" in str(exc.value)


# ── The embed page kind ─────────────────────────────────────────────────────

def _embed_page(panels):
    return [{"id": "card", "kind": "embed", "title": "Decision", "panels": panels}]


def test_embed_page_with_decision_card_publishes():
    """The shape the citra-embed-spec skill tells the builder to author: ONE
    detail panel that carries both the evidence and the trigger.

    The action on the detail panel is what makes the single-panel card possible.
    Before DetailAction the trigger could only hang off a queue, so this page
    also needed a one-row queue whose only job was to hold the button — which
    then rendered a search box, a view switcher and a row counter over a source
    pinned to one id, and repeated every field shown below it.
    """
    validate_app_spec(_spec(pages=_embed_page([
        {"id": "card", "type": "detail", "data_source": "ds",
         "id_field": "application_id",
         "actions": [{"label": "Review", "agent_action": "review_application"}],
         "sections": [
             {"type": "fields"},
             {"type": "documents", "data_source": "ds"},
             {"type": "agent_timeline"},
         ]},
    ])))


@pytest.mark.parametrize("bad_type,extra", [
    ("chart", {"chart_type": "bar", "data_source": "ds", "x": "a", "y": "b"}),
    ("map", {"data_source": "ds", "lat_field": "lat", "lng_field": "lng",
             "label_field": "site"}),
])
def test_embed_page_rejects_unrenderable_panels(bad_type, extra):
    """chart/map cannot render in an embed — the bundle excludes echarts and
    leaflet. Publish is where that must surface."""
    with pytest.raises(PydanticValidationError) as exc:
        validate_app_spec(_spec(pages=_embed_page([
            {"id": "p", "type": bad_type, **extra},
        ])))
    assert "cannot render in an embedded card" in str(exc.value)


def test_embed_page_allows_the_rest():
    """Everything else is the builder's call, guided by the skill — the model
    does not second-guess it. queue included: some hosts genuinely want a short
    worklist beside the card."""
    validate_app_spec(_spec(pages=_embed_page([
        {"id": "note", "type": "notice", "tone": "warn", "content": "Check ID."},
        {"id": "q", "type": "queue", "data_source": "ds", "columns": ["id"],
         # Trigger on the QUEUE — still valid. Moving the trigger to detail is
         # the better shape for a single-record card, not a replacement of the
         # queue path, which every non-embed app relies on.
         "actions": [{"label": "Review", "agent_action": "review_application"}]},
        {"id": "det", "type": "detail", "linked_to": "q",
         "sections": [{"type": "fields"}]},
    ])))


# ── the trigger rule ────────────────────────────────────────────────────────

def test_embed_page_needs_a_trigger():
    """An embed card with no agent_action anywhere renders perfectly and is
    pointless: it shows a record the host application already has, produces no
    recommendation, and captures no reason — so it can never learn. Silent
    failures are the ones worth spending a publish rule on."""
    with pytest.raises(PydanticValidationError) as exc:
        validate_app_spec(_spec(pages=_embed_page([
            {"id": "card", "type": "detail", "data_source": "ds",
             "id_field": "application_id", "sections": [{"type": "fields"}]},
        ])))
    assert "no agent_action" in str(exc.value)


def test_detail_action_alone_satisfies_the_trigger_rule():
    """No queue at all — the point of the change."""
    validate_app_spec(_spec(pages=_embed_page([
        {"id": "card", "type": "detail", "data_source": "ds",
         "id_field": "application_id",
         "actions": [{"label": "Review", "agent_action": "review_application"}],
         "sections": [{"type": "fields"}]},
    ])))


def test_detail_action_requires_an_agent_action():
    """A label-only action is a button that does nothing. Unlike a queue action
    there is no navigate alternative to fall back on."""
    with pytest.raises(PydanticValidationError):
        validate_app_spec(_spec(pages=_embed_page([
            {"id": "card", "type": "detail", "data_source": "ds",
             "id_field": "application_id",
             "actions": [{"label": "Review"}],
             "sections": [{"type": "fields"}]},
        ])))


def test_detail_action_rejects_is_row_click():
    """A detail panel has no rows; accepting the flag would let a builder author
    something that reads as configured and can never fire."""
    with pytest.raises(PydanticValidationError):
        validate_app_spec(_spec(pages=_embed_page([
            {"id": "card", "type": "detail", "data_source": "ds",
             "id_field": "application_id",
             "actions": [{"label": "Review", "agent_action": "a",
                          "is_row_click": True}],
             "sections": [{"type": "fields"}]},
        ])))


def test_chart_still_allowed_on_a_standard_page():
    """The embed restriction must not leak into ordinary pages."""
    validate_app_spec(_spec(pages=[
        {"id": "main", "kind": "standard", "title": "Main", "panels": [
            {"id": "c", "type": "chart", "chart_type": "bar",
             "data_source": "ds", "x": "a", "y": "b"},
        ]},
    ]))
