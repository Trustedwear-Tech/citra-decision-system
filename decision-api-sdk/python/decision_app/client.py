# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Decision API client (Python).

A thin, sync wrapper over the smart-app-service Decision API — the headless
"brain" of a Citra Decision App. Use it from desktop apps, automations, backend
services, or notebooks. No UI assumptions: you get data + decisions; you render.

Governance boundary: NEVER write the system of record directly. Commit only
through ``recommend()`` -> ``approve()`` (AI-recommended) or ``decide_direct()``
(a no-AI human decision). Both run the governed path: policy gate ->
schema-validated idempotent SoR write -> audit -> DecisionRecord -> learning loop.

Depends on ``requests`` (the de-facto HTTP client). ``pip install requests``.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Union
from urllib.parse import quote, urlencode

import requests

TokenProvider = Union[str, Callable[[], str]]


class DecisionApiError(RuntimeError):
    def __init__(self, status: int, detail: Any, path: str):
        self.status = status
        self.detail = detail
        self.path = path
        msg = detail if isinstance(detail, str) else str(detail)[:300]
        super().__init__(f"Decision API {status} on {path}: {msg}")


class DecisionAppClient:
    """Client for one deployment's Decision API.

    Args:
        base_url: smart-app-service base, e.g. "https://apps.citra-ai.com/api".
        token: the end-user's JWT (or a callable returning it, to refresh on
            expiry). Sent as ``Authorization: Bearer <jwt>`` so per-user authz +
            audit apply to every decision.
        timeout: per-request seconds (default 60).
    """

    def __init__(self, base_url: str, token: TokenProvider, *, timeout: float = 60.0,
                 session: Optional[requests.Session] = None,
                 headers: Optional[Dict[str, str]] = None):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout
        self._session = session or requests.Session()
        self._extra_headers = headers or {}

    # ── plumbing ──────────────────────────────────────────────────────────────
    def _auth(self) -> str:
        t = self._token() if callable(self._token) else self._token
        return f"Bearer {t}"

    def _request(self, method: str, path: str, *, query: Optional[Dict[str, Any]] = None,
                 body: Any = None) -> Any:
        url = self.base_url + path
        if query:
            q = {k: v for k, v in query.items() if v is not None}
            if q:
                url += ("&" if "?" in url else "?") + urlencode(q)
        headers = {"Authorization": self._auth(), **self._extra_headers}
        if body is not None:
            headers["Content-Type"] = "application/json"
        resp = self._session.request(method, url, headers=headers, json=body, timeout=self.timeout)
        if not resp.ok:
            try:
                detail = resp.json().get("detail")
            except Exception:  # noqa: BLE001 — non-JSON error body
                detail = resp.text[:300]
            raise DecisionApiError(resp.status_code, detail, path)
        if resp.status_code == 204 or not resp.content:
            return None
        ct = resp.headers.get("content-type", "")
        return resp.json() if "application/json" in ct else resp.content

    # ── discovery ─────────────────────────────────────────────────────────────
    def list_apps(self) -> Dict[str, Any]:
        return self._request("GET", "/apps")

    def get_app(self, slug: str) -> Dict[str, Any]:
        return self._request("GET", f"/apps/{quote(slug)}")

    def get_contract(self, slug: str) -> Dict[str, Any]:
        """Self-describing headless contract: actions, request schema, endpoints,
        governance. Read once; drive the integration from it."""
        return self._request("GET", f"/apps/{quote(slug)}/decision-contract")

    # ── governed decision loop ────────────────────────────────────────────────
    def recommend(self, slug: str, action: str, inputs: Optional[Dict[str, Any]] = None,
                  *, correlation_id: Optional[str] = None) -> Dict[str, Any]:
        """Reason + recommend (no write yet). Result carries ``status`` +
        ``planned_writes``/``decision``/``reasoning``; then ``approve()``."""
        # NB: no ``surface`` field — the server's RunRequest never had one (the
        # real field is ``mode``, default "queue_action"); sending it was dead weight.
        body: Dict[str, Any] = {"action": action, "inputs": inputs or {}}
        if correlation_id:
            body["correlation_id"] = correlation_id
        return self._request("POST", f"/apps/{quote(slug)}/run", body=body)

    def approve(self, slug: str, correlation_id: str, decision: str = "approve",
                *, overrides: Optional[List[Dict[str, Any]]] = None,
                expected_plan_hash: Optional[str] = None,
                decision_reason: Optional[str] = None,
                note: Optional[str] = None) -> Dict[str, Any]:
        """Commit: decision = approve | reject | cancel. ``overrides`` change
        editable fields before the write (governed override), aligned to
        planned_writes[i].

        Pass ``expected_plan_hash`` (the run response's ``plan_hash`` you showed
        the approver) so a plan that changed since display is rejected with 409
        instead of committed — the display==commit integrity guard. With
        ``item_review_gate="hard"``, every non-fraud item finding must be
        dispositioned via ``submit_feedback`` first (server returns 409 otherwise).
        """
        body: Dict[str, Any] = {"decision": decision}
        if overrides is not None:
            body["overrides"] = overrides
        if expected_plan_hash is not None:
            body["expected_plan_hash"] = expected_plan_hash
        if decision_reason is not None:
            body["decision_reason"] = decision_reason
        if note is not None:
            body["note"] = note
        return self._request("POST", f"/apps/{quote(slug)}/run/{quote(correlation_id)}/approve", body=body)

    def decide_direct(self, slug: str, tool_name: str, arguments: Dict[str, Any],
                      *, panel_id: Optional[str] = None) -> Dict[str, Any]:
        """A human decision WITHOUT the AI. Runs the same governed write path."""
        body: Dict[str, Any] = {"arguments": arguments}
        if panel_id:
            body["panel_id"] = panel_id
        return self._request("POST", f"/apps/{quote(slug)}/tool/{quote(tool_name)}", body=body)

    def chat(self, slug: str, message: str, **extra: Any) -> Dict[str, Any]:
        return self._request("POST", f"/apps/{quote(slug)}/chat", body={"message": message, **extra})

    # ── data (render your own UI) ─────────────────────────────────────────────
    def get_panel_data(self, slug: str, panel_id: str, **params: Any) -> Dict[str, Any]:
        return self._request("GET", f"/apps/{quote(slug)}/data/{quote(panel_id)}", query=params)

    def get_detail(self, slug: str, panel_id: str, record_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/apps/{quote(slug)}/detail/{quote(panel_id)}", query={"id": record_id})

    def get_field_options(self, slug: str, body: Dict[str, Any]) -> Any:
        return self._request("POST", f"/apps/{quote(slug)}/field-options", body=body)

    def get_notifications(self, slug: str, panel_id: str) -> Any:
        return self._request("GET", f"/apps/{quote(slug)}/notifications/{quote(panel_id)}")

    def media_url(self, slug: str, data_source_id: str, *, key_field: str, key: str, col: str) -> str:
        qs = urlencode({"key_field": key_field, "key": key, "col": col})
        return f"{self.base_url}/apps/{quote(slug)}/media/{quote(data_source_id)}?{qs}"

    def fetch_media(self, slug: str, data_source_id: str, *, key_field: str, key: str, col: str) -> bytes:
        """Record media (photo/PDF) bytes, streamed through the MCP (never a
        storage URL)."""
        return self._request("GET", f"/apps/{quote(slug)}/media/{quote(data_source_id)}",
                             query={"key_field": key_field, "key": key, "col": col})

    # ── governance / audit / learning ─────────────────────────────────────────
    def list_runs(self, slug: str, *, limit: Optional[int] = None, offset: Optional[int] = None) -> Dict[str, Any]:
        return self._request("GET", f"/apps/{quote(slug)}/runs", query={"limit": limit, "offset": offset})

    def get_audit(self, slug: str, correlation_id: str) -> Any:
        return self._request("GET", f"/apps/{quote(slug)}/runs/{quote(correlation_id)}/audit")

    def get_loop_metrics(self, slug: str) -> Any:
        return self._request("GET", f"/apps/{quote(slug)}/loop-metrics")

    def get_self_learning(self, slug: str) -> Any:
        return self._request("GET", f"/apps/{quote(slug)}/self-learning")

    def set_self_learning(self, slug: str, body: Dict[str, Any]) -> Any:
        return self._request("POST", f"/apps/{quote(slug)}/self-learning", body=body)

    def submit_feedback(self, slug: str, item_id: str, body: Dict[str, Any]) -> Any:
        """Disposition one item review card (an entry of ``run.item_findings``).

        ``body`` = ``{"modality": "image"|"document"|"api"|"case", "task_type": str,
        "decision": "accept"|"reject"|"cancel", "reason"?: str, "subject"?: str}``.
        ``accept`` confirms the finding, ``reject`` dismisses it, ``cancel`` skips.
        For a fraud ``case`` item this records the officer's confirm/dismiss for
        learning — it does NOT reject the overall decision (use ``approve(...,
        "reject")`` for that). Raises 409 ``fraud_not_enabled`` if you disposition
        a ``case`` item on an app whose ontology has fraud detection off.
        """
        return self._request("POST", f"/apps/{quote(slug)}/items/{quote(item_id)}/feedback", body=body)

    def calibrate_fraud(self, slug: str, body: Optional[Dict[str, Any]] = None) -> Any:
        """Fraud calibration report (read-only).

        Replays past decisions and returns ``per_signal_hit_rate`` — how often each
        fraud signal coincided with an officer reject. Changes nothing automatically.
        Raises 409 ``fraud_not_enabled`` if the app's ontology does not enable fraud.
        """
        return self._request("POST", f"/apps/{quote(slug)}/fraud-calibration", body=body or {})

    # ── auth ──────────────────────────────────────────────────────────────────
    def mint_runtime_token(self, slug: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._request("POST", f"/apps/{quote(slug)}/runtime/token", body=body or {})
