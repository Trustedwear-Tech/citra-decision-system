"""Capture the acme-bank Decision App flow for the README.

Runs on the HOST at 1440x900 @2x, so every URL is plain localhost -- the origin
the CORS allowlist permits and the runtime config injects.

Two things this has to work around, both found by driving the real UI:

1. The shell is React Native Web. Pressables render as <div> with no role and
   no <button>, so page.click('text=...') matches the text node, not the
   handler, and nothing fires. Every tap goes through __tap(), which walks up
   to the nearest element carrying RNW's cursor-pointer class (r-1loqt21) and
   dispatches the full pointer sequence.

2. RNW's TextInput ignores synthetic typing. Values are set through the native
   value setter plus an input event, which is what React listens for.

The app runtime itself (localhost:3100) is ordinary React with real buttons, so
inside a Decision App normal clicks work.

Each shot names the claim it exists to prove. A shot that cannot be taken says
so and the run continues -- a missing screenshot beats a misleading one.
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

UI = os.getenv("UI", "http://localhost:8081")
EMAIL = os.getenv("ADMIN_EMAIL", "admin@citra-ai.com")
PW = os.getenv("ADMIN_PASSWORD", "")
OUT = Path(os.getenv("OUT", r"C:\Github\citra-decision-system\assets\screens"))

# Injected once per page: the two RNW workarounds described above.
HELPERS = """
window.__tapEl = (el) => {
  const r = el.getBoundingClientRect();
  const o = {bubbles:true, cancelable:true, clientX:r.x+r.width/2,
             clientY:r.y+r.height/2, button:0, pointerId:1, isPrimary:true};
  for (const t of ['pointerdown','mousedown','pointerup','mouseup','click'])
    el.dispatchEvent(t.startsWith('pointer') ? new PointerEvent(t,o) : new MouseEvent(t,o));
  return true;
};
// Count PRESSABLES, not text nodes. "Sign In" appears twice on the login
// screen -- once as the card heading, once as the button label -- and the
// heading has no pressable ancestor. Indexing the text nodes silently picked
// the heading and tapped nothing.
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
"""


def tap(page, txt, nth=0, timeout=30000):
    """Tap `txt` once it exists. A fixed sleep before a tap is a guess; these
    screens hydrate at their own pace and the guess is what failed first."""
    page.wait_for_function(
        "([t, n]) => window.__pressables && window.__pressables(t).length > n",
        arg=[txt, nth], timeout=timeout,
    )
    return page.evaluate("([t,n]) => window.__tap(t,n)", [txt, nth])


def shot(page, name, note):
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f"{name}.png"))
    print(f"  captured {name:<26} {note}")


def main() -> int:
    if not PW:
        print("  ADMIN_PASSWORD not set -- cannot sign in", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        page = br.new_page(viewport={"width": 1440, "height": 900},
                           device_scale_factor=2)
        page.add_init_script(HELPERS)

        # ── landing ───────────────────────────────────────────────────────────
        page.goto(UI, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        shot(page, "00-landing", "self-hosted landing, Apache footer")

        # ── sign in ───────────────────────────────────────────────────────────
        if not tap(page, 'Sign In'):
            print("  [!!] no Sign In pressable", file=sys.stderr)
            br.close()
            return 1
        page.wait_for_timeout(2500)
        if not page.evaluate(f"window.__fillLogin({EMAIL!r}, {PW!r})"):
            print("  [!!] sign-in fields never appeared", file=sys.stderr)
            br.close()
            return 1
        if not tap(page, 'Sign In'):
            print("  [!!] sign-in submit did not fire", file=sys.stderr)
            br.close()
            return 1
        # Wait for the home screen itself, not a guess at how long auth takes.
        page.wait_for_function(
            "() => window.__pressables && window.__pressables('My Decision Apps').length > 0",
            timeout=60000,
        )
        page.wait_for_timeout(2500)
        shot(page, "01-home", "signed in -- the operations home screen")

        # ── the four Decision Apps ────────────────────────────────────────────
        if not tap(page, 'My Decision Apps'):
            print("  [!!] no My Decision Apps tile", file=sys.stderr)
            br.close()
            return 1
        page.wait_for_timeout(4000)
        shot(page, "02-decision-apps", "the seeded apps, visible without impersonation")

        # ── open Claim Triage ─────────────────────────────────────────────────
        # "Open" launches the app runtime (localhost:3100) in a NEW TAB, so the
        # shell page stays where it is -- capturing `page` after this tap gets
        # the app list again, not the app. The session rides the launch, which
        # is why navigating to the runtime URL directly answers "session
        # expired" instead.
        try:
            with page.expect_popup(timeout=30000) as popup:
                if not tap(page, 'Open', 0):
                    raise RuntimeError("no Open button on the first app card")
            app = popup.value
        except Exception as exc:
            print(f"  [!!] the app did not open ({exc})", file=sys.stderr)
            br.close()
            return 1

        app.set_viewport_size({"width": 1440, "height": 900})
        app.wait_for_load_state("networkidle", timeout=60000)
        # Wait for real rows rather than a fixed sleep -- the queue is fetched
        # from Postgres through the MCP and takes a moment on a cold start.
        try:
            app.wait_for_selector("text=Triage this claim", timeout=60000)
        except Exception:
            print("  [!!] claim queue never rendered", file=sys.stderr)
        app.wait_for_timeout(1500)
        shot(app, "03-claim-queue", "real claims from Postgres, via MCP")

        # ── run the agent ─────────────────────────────────────────────────────
        # The app runtime is ordinary React with real buttons, so a normal
        # click works here -- none of the RNW workarounds apply.
        try:
            app.click("text=Triage this claim", timeout=15000)
        except Exception as exc:
            print(f"  [!!] could not start a triage run ({exc.__class__.__name__})",
                  file=sys.stderr)
            br.close()
            return 1
        print("  agent running -- waiting for the recommendation (up to 4 min)")

        # Wait for the staged decision rather than a fixed sleep: the panel only
        # appears once the run finishes and the write is dry-run validated.
        got = True
        try:
            app.wait_for_selector("text=Proposed changes", timeout=240000)
        except Exception:
            got = False
            print("  [!!] no recommendation within 4 minutes -- capturing anyway",
                  file=sys.stderr)
        app.wait_for_timeout(2500)
        shot(app, "04-recommendation",
             "cited recommendation + staged write" if got else "INCOMPLETE - check before use")

        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
