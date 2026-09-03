# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""An animated GIF of the build path, companion to the learning loop.

The loop animation answers "what does it do with a decision". This one answers
the question that comes first: how does anything exist to decide with?

    your database -> the ontology describes it -> you describe the operation
    -> the builder drafts it against the catalogue -> app, API, embedded card

Two claims it is careful NOT to make:

  * That the product creates your database. It does not. The first beat says
    "one you run, or a new one you stand up", which is the true version and
    the one the README makes.

  * That a dashboard is a fourth surface. There are three. models.py is
    explicit that build_kind='dashboard' is retired and a dashboard is an app
    whose primary_page_kind is 'dashboard'.

    python scripts/make_build_animation.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from make_story_svgs import THEMES, head, txt, box, arrow  # noqa: E402
from anim_util import build_gif  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "assets" / "story"
W, H = 900, 330   # content ends ~284; keeps the caption close, not floating

# (label, sub, tone) — the four beats before anything is published
BEATS = [
    ("Your database",       "one you run, or a new one",     "panel"),
    ("make ontology",       "interviews it, writes the rules", "brand"),
    ("Describe it",         "in plain English",              "brand"),
    ("The builder drafts",  "and asks what it cannot infer", "warn"),
]
SURFACES = ["Decision App", "API", "Embedded card"]
QUOTE = "“Build me an app for claims processing.”"


def _tone(t, name):
    return {
        "panel": (t["panel"], t["panelEdge"], t["ink"], t["mute"]),
        "brand": (t["brandSoft"], t["brand"], t["brandDeep"], t["brandDeep"]),
        "warn":  (t["warnSoft"], t["warn"], t["warn"], t["warn"]),
        "good":  (t["goodSoft"], t["good"], t["good"], t["good"]),
    }[name]


def frame(t: dict, step: int) -> str:
    s = head(W, H, "The build path: your database, described by the ontology; "
                   "you describe the operation in plain English; the builder "
                   "drafts it and publishes one spec as an app, an API or an "
                   "embedded card", t)

    s += txt(W / 2, 42, "Describe it, and it builds", t, size=21,
             fill=t["ink"], weight="700", anchor="middle")

    bw, bh, gap = 186, 70, 22
    n = len(BEATS)
    x0 = (W - (n * bw + (n - 1) * gap)) / 2
    y = 88

    for i, (label, sub, tone) in enumerate(BEATS):
        x = x0 + i * (bw + gap)
        shown = i <= step
        fill, edge, ink, subink = _tone(t, tone if shown else "panel")
        # unreached beats stay faint rather than absent, so the layout
        # never jumps between frames
        op = "" if shown else ' opacity="0.28"'
        s += f'<g{op}>'
        s += box(x, y, bw, bh, t, fill=fill, stroke=edge)
        s += txt(x + bw / 2, y + 30, label, t, size=13.5, fill=ink,
                 weight="700", anchor="middle")
        s += txt(x + bw / 2, y + 49, sub, t, size=10.5, fill=subink, anchor="middle")
        s += "</g>"
        if i < n - 1:
            op = "" if i < step else ' opacity="0.28"'
            s += (f'<g{op}>' + arrow(x + bw + 2, y + bh / 2,
                                     x + bw + gap - 2, y + bh / 2, t) + "</g>")

    # the sentence the user actually types, revealed with the "Describe it" beat
    if step >= 2:
        s += box(x0, y + bh + 22, W - 2 * x0, 34, t, fill=t["panel"],
                 stroke=t["panelEdge"])
        s += txt(W / 2, y + bh + 44, QUOTE, t, size=13.5, fill=t["ink"],
                 anchor="middle")

    # published: one spec fans to three surfaces
    if step >= len(BEATS):
        cy = y + bh + 92
        cw, cgap = 176, 26
        cx0 = (W - (len(SURFACES) * cw + (len(SURFACES) - 1) * cgap)) / 2
        centres = [cx0 + i * (cw + cgap) + cw / 2 for i in range(len(SURFACES))]
        bus_y = cy - 20
        s += (f'<path d="M{W/2} {y+bh+56} L{W/2} {bus_y}" stroke="{t["good"]}" '
              f'stroke-width="2" fill="none"/>')
        s += (f'<path d="M{centres[0]} {bus_y} L{centres[-1]} {bus_y}" '
              f'stroke="{t["good"]}" stroke-width="2" fill="none"/>')
        for c in centres:
            s += (f'<path d="M{c} {bus_y} L{c} {cy-3}" stroke="{t["good"]}" '
                  f'stroke-width="2" fill="none" marker-end="url(#ag)"/>')
        for i, label in enumerate(SURFACES):
            x = cx0 + i * (cw + cgap)
            s += box(x, cy, cw, 34, t, fill=t["goodSoft"], stroke=t["good"], r=17)
            s += txt(x + cw / 2, cy + 22, label, t, size=13, fill=t["good"],
                     weight="700", anchor="middle")

    s += txt(W / 2, H - 20,
             "One published spec. Over your data, in your infrastructure.", t,
             size=12.5, fill=t["faint"], anchor="middle")
    return s + "</svg>\n"


def main() -> int:
    build_gif(frame, n_frames=len(BEATS) + 1, name="build",
              width=W, height=H, out=OUT, themes=THEMES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
