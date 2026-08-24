"""Generate the four story diagrams at the top of the README.

One source, two themes. GitHub swaps them with <picture>, and writing the
geometry once means the light and dark copies cannot drift apart -- which is
exactly what happens when someone edits one SVG and forgets the other.

The four answer, in order, the questions a reader has before they will read
anything else:

  1. why is this needed at all, when we already have SOPs and data?
  2. what does it actually do, case by case?
  3. how do I stop it doing something stupid?
  4. how does it reach my people?

Deliberately few words. Each diagram carries one idea; the prose underneath
carries the rest.
"""
from __future__ import annotations

import pathlib

OUT = pathlib.Path(r"C:\Github\citra-decision-system\assets\story")

LICENCE = """<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->
"""

THEMES = {
    "light": dict(bg="#FFFFFF", ink="#15181A", mute="#5A6472", faint="#79838F",
                  line="#C9D2DC", panel="#F5F7FA", panelEdge="#DFE6EE",
                  brand="#2563EB", brandDeep="#1E3A8A", brandSoft="#E8F0FE",
                  warn="#B45309", warnSoft="#FEF3C7", good="#0F766E",
                  goodSoft="#D6F0EC"),
    "dark": dict(bg="#0B1220", ink="#E6EDF6", mute="#93A1B3", faint="#76839A",
                 line="#2A3648", panel="#111A2B", panelEdge="#243247",
                 brand="#5B8DEF", brandDeep="#2563EB", brandSoft="#16233A",
                 warn="#F0B429", warnSoft="#2A2413", good="#3FBFA9",
                 goodSoft="#122A29"),
}

FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Roboto,"
        "'Helvetica Neue',Arial,sans-serif")


def head(w: int, h: int, label: str, t: dict) -> str:
    return (f'{LICENCE}<svg xmlns="http://www.w3.org/2000/svg" width="{w}" '
            f'height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{label}">\n'
            f'  <defs>\n'
            f'    <marker id="a" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">\n'
            f'      <path d="M0,0 L10,5 L0,10 z" fill="{t["line"]}"/>\n'
            f'    </marker>\n'
            f'    <marker id="ab" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">\n'
            f'      <path d="M0,0 L10,5 L0,10 z" fill="{t["brand"]}"/>\n'
            f'    </marker>\n'
            f'  </defs>\n'
            f'  <rect width="{w}" height="{h}" fill="{t["bg"]}"/>\n')


def txt(x, y, s, t, size=15, fill=None, weight="400", anchor="start"):
    return (f'  <text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill or t["ink"]}" '
            f'text-anchor="{anchor}">{s}</text>\n')


def box(x, y, w, h, t, fill=None, stroke=None, r=10, sw=1.5):
    return (f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
            f'fill="{fill or t["panel"]}" stroke="{stroke or t["panelEdge"]}" '
            f'stroke-width="{sw}"/>\n')


def arrow(x1, y1, x2, y2, t, brand=False, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    col = t["brand"] if brand else t["line"]
    mk = "ab" if brand else "a"
    return (f'  <path d="M{x1},{y1} L{x2},{y2}" stroke="{col}" stroke-width="2"'
            f' fill="none" marker-end="url(#{mk})"{d}/>\n')


# ── 1. why it is needed ──────────────────────────────────────────────────────
def gap(t: dict) -> str:
    s = head(1200, 330, "Most cases follow the rules; the ones that matter need "
                        "judgement that is written down nowhere", t)
    s += txt(40, 46, "Every day, your team decides thousands of cases.", t, 19, weight="600")
    # the stream of cases
    s += box(40, 80, 250, 190, t)
    s += txt(60, 108, "CASES ARRIVE", t, 11, t["faint"], "700")
    for i in range(5):
        for j in range(4):
            s += (f'  <rect x="{62 + j*44}" y="{124 + i*26}" width="30" height="14" '
                  f'rx="3" fill="{t["line"]}" opacity=".55"/>\n')
    s += arrow(300, 175, 348, 175, t)
    # the rules
    s += box(358, 80, 250, 190, t)
    s += txt(378, 108, "YOUR SOP + YOUR DATA", t, 11, t["faint"], "700")
    s += txt(378, 142, "Answers most of them.", t, 15)
    s += txt(378, 168, "Written down, testable,", t, 14, t["mute"])
    s += txt(378, 190, "already automatable.", t, 14, t["mute"])
    s += txt(378, 232, "MOST CASES", t, 11, t["good"], "700")
    s += box(378, 240, 60, 8, t, t["good"], t["good"], 4, 0)
    s += arrow(618, 175, 666, 175, t, brand=True)
    # the hard slice
    s += box(676, 80, 484, 190, t, t["brandSoft"], t["brand"])
    s += txt(696, 108, "THE ONES THAT CARRY THE MONEY", t, 11, t["brand"], "700")
    s += txt(696, 142, "Resolved by a person who knows better than the rules.", t, 15)
    s += txt(696, 176, "That knowledge is not in your SOP.", t, 14, t["mute"])
    s += txt(696, 200, "Not in your recorded data. Not in the model's training.", t, 14, t["mute"])
    s += txt(696, 236, "It leaves when they do.", t, 15, t["ink"], "600")
    s += txt(40, 306, "A model that has read your policy still cannot make this call. "
                      "It has never seen the judgement — because nobody wrote it down.",
             t, 15, t["mute"])
    return s + "</svg>\n"


# ── 2. what it does ──────────────────────────────────────────────────────────
def loop(t: dict) -> str:
    s = head(1200, 350, "One case at a time: recommend with evidence, let the "
                        "officer decide, learn from the correction", t)
    s += txt(40, 46, "So it learns that judgement, one case at a time.", t, 19, weight="600")
    steps = [
        (40,  "A CASE", "Arrives from your\nsystem of record."),
        (275, "IT RECOMMENDS", "With the SOP passage\nand past cases cited."),
        (510, "A PERSON DECIDES", "Approves, or corrects it\nand says why."),
        (745, "IT LEARNS", "Three officers agreeing\nmakes it a rule."),
        (980, "NEXT CASE", "The rule applies —\nonly where it fits."),
    ]
    for i, (x, title, body) in enumerate(steps):
        hot = i in (2, 3)
        s += box(x, 80, 180, 150, t,
                 t["brandSoft"] if hot else t["panel"],
                 t["brand"] if hot else t["panelEdge"])
        s += txt(x + 18, 108, title, t, 11, t["brand"] if hot else t["faint"], "700")
        for k, line in enumerate(body.split("\n")):
            s += txt(x + 18, 140 + k * 22, line, t, 14, t["mute"])
        if i < 4:
            # Stop 8px short of the next box. Drawing to x+262 put the
            # arrowhead 27px INSIDE it, where the fill hid it -- the connectors
            # rendered as plain lines and the direction of the loop was lost.
            s += arrow(x + 190, 155, x + 227, 155, t, brand=hot)
    # feedback edge
    s += (f'  <path d="M1070,240 L1070,286 L130,286 L130,240" stroke="{t["brand"]}" '
          f'stroke-width="2" fill="none" stroke-dasharray="5 5" marker-end="url(#ab)"/>\n')
    s += txt(600, 278, "every case makes the next one better", t, 13, t["brand"], "600", "middle")
    s += txt(40, 326, "A judgement cites the corrections that formed it, applies only to "
                      "cases like those, and can be retired when it stops being true.",
             t, 15, t["mute"])
    return s + "</svg>\n"


# ── 3. governance ────────────────────────────────────────────────────────────
def governed(t: dict) -> str:
    s = head(1200, 330, "The agent proposes; a policy gate bounds it; a person "
                        "approves; everything is recorded", t)
    s += txt(40, 46, "And it is never allowed to just act.", t, 19, weight="600")
    s += box(40, 80, 215, 130, t)
    s += txt(60, 108, "THE AGENT", t, 11, t["faint"], "700")
    s += txt(60, 140, "Proposes one", t, 14, t["mute"])
    s += txt(60, 162, "specific action.", t, 14, t["mute"])
    s += arrow(265, 145, 313, 145, t)
    s += box(323, 80, 215, 130, t, t["warnSoft"], t["warn"])
    s += txt(343, 108, "POLICY GATE", t, 11, t["warn"], "700")
    s += txt(343, 140, "Only what you", t, 14, t["mute"])
    s += txt(343, 162, "allowed. Nothing more.", t, 14, t["mute"])
    s += arrow(548, 145, 596, 145, t)
    s += box(606, 80, 215, 130, t, t["brandSoft"], t["brand"])
    s += txt(626, 108, "A PERSON", t, 11, t["brand"], "700")
    s += txt(626, 140, "Sees the plan before", t, 14, t["mute"])
    s += txt(626, 162, "anything is written.", t, 14, t["mute"])
    s += arrow(831, 145, 879, 145, t)
    s += box(889, 80, 271, 130, t, t["goodSoft"], t["good"])
    s += txt(909, 108, "YOUR SYSTEM OF RECORD", t, 11, t["good"], "700")
    s += txt(909, 140, "Written only on approval,", t, 14, t["mute"])
    s += txt(909, 162, "and reversible.", t, 14, t["mute"])
    s += box(40, 232, 1120, 46, t)
    s += txt(60, 261, "AUDIT", t, 11, t["faint"], "700")
    s += txt(120, 261, "Who decided, what it read, which rule it applied, what changed "
                       "— for every case, kept.", t, 14, t["mute"])
    s += txt(40, 312, "One switch halts every run and every write, immediately.",
             t, 15, t["mute"])
    return s + "</svg>\n"


# ── 4. how it reaches people ─────────────────────────────────────────────────
def surfaces(t: dict) -> str:
    s = head(1200, 320, "Ships as an app, embedded in your own UI, or as an API; "
                        "runs on demand, ahead of time, or automatically", t)
    s += txt(40, 46, "It reaches your team however suits the work.", t, 19, weight="600")
    left = [("AS AN APP", "A screen your team opens."),
            ("EMBEDDED", "A panel inside your own system."),
            ("AS AN API", "No UI at all.")]
    for i, (title, body) in enumerate(left):
        y = 80 + i * 74
        s += box(40, y, 350, 62, t)
        s += txt(60, y + 26, title, t, 11, t["brand"], "700")
        s += txt(60, y + 48, body, t, 14, t["mute"])
    s += txt(430, 100, "×", t, 22, t["faint"], "400", "middle")
    right = [("ON DEMAND", "Someone asks; it answers."),
             ("PREPARED AHEAD", "Ready before the desk opens."),
             ("AUTOMATIC", "Within limits you set, case by case.")]
    for i, (title, body) in enumerate(right):
        y = 80 + i * 74
        s += box(470, y, 690, 62, t)
        s += txt(490, y + 26, title, t, 11, t["good"], "700")
        s += txt(490, y + 48, body, t, 14, t["mute"])
    s += txt(40, 302, "Self-hosted throughout — your infrastructure, your models, "
                      "your data. Nothing leaves your network.", t, 15, t["mute"])
    return s + "</svg>\n"


DIAGRAMS = {"1-why": gap, "2-loop": loop, "3-governed": governed, "4-surfaces": surfaces}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in DIAGRAMS.items():
        for theme, t in THEMES.items():
            p = OUT / f"{name}-{theme}.svg"
            p.write_text(fn(t), encoding="utf-8", newline="\n")
            print(f"  {p.relative_to(OUT.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
