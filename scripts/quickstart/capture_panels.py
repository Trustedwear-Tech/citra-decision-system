# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Capture every surface HomePanel.js offers, one screenshot each.

Companion to capture_screens.py, which walks the decision loop end to end.
This one is breadth: sign in once, then open each card on the home screen and
photograph what it actually shows on the seeded acme-bank demo.

The card list is taken from Citra-UI/components/HomePanel.js. Keep it in step
with that file -- a card added there and not here is simply never photographed,
which is the failure mode this script exists to avoid.

Same two React Native Web workarounds as capture_screens.py, for the same
reasons (pressables are role-less divs; TextInput ignores synthetic typing).
Each card is opened from a known-good home screen and closed again afterwards,
so one panel failing to open cannot cascade into every later shot being of the
wrong screen -- the run re-asserts home between cards and says so when it
cannot get back.
"""
import hashlib
import os
import sys
from collections import defaultdict
from pathlib import Path

from playwright.sync_api import sync_playwright

UI = os.getenv("UI", "http://localhost:8081")
EMAIL = os.getenv("ADMIN_EMAIL", "admin@citra-ai.com")
PW = os.getenv("ADMIN_PASSWORD", "")
OUT = Path(os.getenv("OUT", r"C:\Github\citra-decision-system\assets\screens\panels"))

HELPERS = """
window.__tapEl = (el) => {
  const r = el.getBoundingClientRect();
  const o = {bubbles:true, cancelable:true, clientX:r.x+r.width/2,
             clientY:r.y+r.height/2, button:0, pointerId:1, isPrimary:true};
  for (const t of ['pointerdown','mousedown','pointerup','mouseup','click'])
    el.dispatchEvent(t.startsWith('pointer') ? new PointerEvent(t,o) : new MouseEvent(t,o));
  return true;
};
// Count PRESSABLES, not text nodes: a label often appears twice (once as a
// heading, once as the tappable card) and only one of them has a handler.
window.__pressables = (txt) => {
  const out = [];
  for (const e of document.querySelectorAll('*')) {
    if ((e.textContent||'').trim() !== txt || e.children.length !== 0) continue;
    let p = e;
    while (p && !/r-1loqt21/.test((p.className||'').toString())) p = p.parentElement;
    if (p && !out.includes(p)) out.push(p);
  }
  return out;
};
window.__tap = (txt, nth) => {
  const p = window.__pressables(txt)[nth||0];
  if (!p) return false;
  window.__tapEl(p);
  return true;
};
window.__fillLogin = (email, pw) => {
  const ins = [...document.querySelectorAll('input')];
  if (ins.length < 2) return false;
  const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  const e = ins.find(i => i.type === 'email') || ins[0];
  const p = ins.find(i => i.type === 'password') || ins[1];
  set.call(e, email); e.dispatchEvent(new Event('input', {bubbles:true}));
  set.call(p, pw);    p.dispatchEvent(new Event('input', {bubbles:true}));
  return true;
};
// "Am I home?" is not "is the home card in the DOM?". React Native modals
// leave the home screen mounted UNDERNEATH them, so the card stays present and
// tappable-looking while a panel covers it -- taps then land on the hidden card,
// nothing visibly happens, and the next screenshot is silently a copy of the
// panel that never closed. Hit-test the card instead: it is home only if the
// card is what you would actually hit at its own centre.
window.__atHome = () => {
  const p = window.__pressables('My Decision Apps')[0];
  if (!p) return false;
  const r = p.getBoundingClientRect();
  if (!r.width || !r.height) return false;
  const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
  return !!(top && (p === top || p.contains(top) || top.contains(p)));
};
"""

# (filename, card label, what the shot is meant to show)
# Order follows HomePanel.js top to bottom.
# NB: only FeatureCard `title=` values are cards. A grep for title="..."
# also matches subtitle="..." -- that is how "Department documents" got in
# here once. It is the SOP Library card's subtitle; both entries call the
# same onOpenDeptLibrary, so it produced a byte-identical duplicate shot.
CARDS = [
    ("10-dashboards",      "My Dashboards",       "live KPI + chart views"),
    ("11-operations-chat", "Operations Chat",     "governed NL questions over operational data"),
    ("12-sop-library",     "SOP Library",         "the policy corpus recommendations cite"),
    ("14-manage-users",    "Manage Users",        "org membership and roles"),
    ("15-departures",      "Departures",          "deactivate & handoff"),
    ("16-resources",       "Managed Resources",   "connections and sources IT manages"),
    ("17-app-memory",      "App Memory",          "learned judgements + past decisions"),
    ("18-learning-batch",  "Learning Batch",      "officer feedback folded into memory"),
    ("19-success-rate",    "Success Rate",        "how often recommendations are accepted"),
    ("20-money-impact",    "Money Impact",        "value recovered & protected"),
    ("21-screening-health","Screening Health",    "fraud checks & false alarms"),
    ("22-automation",      "Automation Control",  "kill switches: halt runs & writes"),
    ("23-login-as-user",   "Login as User",       "audited impersonation, still available"),
]


# Panels that open on the wrong tab: shot name -> tab to select first.
THEN = {
    "17-app-memory": "Loan Application Triage",
    "19-success-rate": "Loan Application Triage",
}


def shot(page, name, note):
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f"{name}.png"))
    print(f"  captured {name:<22} {note}")


def login(page) -> bool:
    """Sign in from the landing page and wait for the home screen."""
    page.wait_for_timeout(2000)
    if not page.evaluate("window.__tap('Sign In')"):
        return False
    page.wait_for_timeout(2500)
    if not page.evaluate(f"window.__fillLogin({EMAIL!r}, {PW!r})"):
        return False
    page.evaluate("window.__tap('Sign In')")
    try:
        page.wait_for_function("() => window.__atHome && window.__atHome()",
                               timeout=60000)
    except Exception:
        return False
    page.wait_for_timeout(1500)
    return True


def go_home(page, timeout=20000) -> bool:
    """Return to a home screen we can open the next card from.

    Panels close in more than one way (Escape, an X, a back arrow) and some
    cards are full screens rather than modals, so this tries the cheap exits
    and then VERIFIES rather than assuming any worked -- an unverified close
    means every later shot is of the wrong screen while still being written to
    a confidently-named file.

    Reloading is the last resort and costs the session: the shell keeps auth in
    memory, so a bare goto lands on the sign-in page and __atHome would never
    come back. That is why this path signs in again.
    """
    closers = ["\u00d7", "Close", "Back", "Done"]
    for attempt in (0, 1, 2):
        if page.evaluate("window.__atHome && window.__atHome()"):
            return True
        if attempt == 0:
            page.keyboard.press("Escape")
        elif attempt == 1:
            page.evaluate("(cs) => cs.some(c => window.__tap(c))", closers)
        else:
            page.goto(UI, wait_until="networkidle", timeout=timeout)
            if login(page):
                return True
        page.wait_for_timeout(2000)
    return bool(page.evaluate("window.__atHome && window.__atHome()"))


def main() -> int:
    if not PW:
        print("  ADMIN_PASSWORD not set -- cannot sign in", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    captured, missing = [], []

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        page = br.new_page(viewport={"width": 1440, "height": 900},
                           device_scale_factor=2)
        page.add_init_script(HELPERS)

        page.goto(UI, wait_until="networkidle", timeout=60000)
        if not login(page):
            print("  [!!] could not sign in", file=sys.stderr)
            br.close()
            return 1

        # The home screen is taller than the viewport and the Admin section --
        # where memory, money impact and the kill switches live -- is below the
        # fold, so the single home shot misses most of what this page offers.
        shot(page, "09-home-admin", "the Admin section, full page")
        page.screenshot(path=str(OUT / "09-home-admin.png"), full_page=True)

        for name, label, note in CARDS:
            if not go_home(page):
                print(f"  [!!] lost the home screen before {label} -- stopping",
                      file=sys.stderr)
                break
            if not page.evaluate("([t]) => window.__tap(t)", [label]):
                print(f"  [--] no card labelled {label!r} on this build")
                missing.append(label)
                continue
            page.wait_for_timeout(4500)
            # Some panels open on a tab that is not the interesting one. App
            # Memory defaults to the first app alphabetically, which on this
            # demo is a dashboard with no judgements -- shooting it straight
            # away photographs an empty state and calls it "learned
            # judgements". THEN is the tab worth showing.
            then = THEN.get(name)
            if then:
                if page.evaluate("([t]) => window.__tap(t)", [then]):
                    page.wait_for_timeout(3000)
                else:
                    print(f"  [--] {name}: could not select {then!r}; "
                          f"shooting the default tab")
            shot(page, name, note)
            captured.append(label)

        br.close()

    print(f"\n  {len(captured)} captured, {len(missing)} not present")
    if missing:
        print("  not on this build: " + ", ".join(missing))

    # A panel that never opened is indistinguishable from one that did, in the
    # log above -- both print "captured". Compare the bytes instead: two
    # identical shots mean one file is a copy of the previous screen under a
    # confident name. Fail on it rather than shipping it.
    seen = defaultdict(list)
    for f in sorted(OUT.glob("*.png")):
        seen[hashlib.md5(f.read_bytes()).hexdigest()].append(f.name)
    dupes = [names for names in seen.values() if len(names) > 1]
    if dupes:
        print("", file=sys.stderr)
        print("  [!!] identical screenshots -- a panel did not open:", file=sys.stderr)
        for names in dupes:
            print("       " + " == ".join(names), file=sys.stderr)
        return 1
    print("  all shots distinct")
    return 0


if __name__ == "__main__":
    sys.exit(main())
