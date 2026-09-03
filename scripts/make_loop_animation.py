# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""An animated GIF of the learning loop.

NOT USED IN THE README, DELIBERATELY. The hero diagram at the top of the page
already carries this loop as its left panel, beat for beat and in the same
words, so putting the animation in the section below it showed a reader the
same picture twice, two screens apart. The static hero stays; this is kept for
places the hero cannot go — a Reddit or LinkedIn post, a slide, an email —
where a single self-contained animation does the job the two-panel hero does
on GitHub.

WHY A GIF AND NOT AN ANIMATED SVG
    An SVG with CSS or SMIL animation is smaller and sharper, and it does not
    reliably animate on github.com: README images are proxied and rendered as
    <img>, and whether declarative animation survives that has changed more
    than once. A GIF renders everywhere, including in the release notes, on
    npm-style mirrors, and in a Reddit or LinkedIn post. Correctness of
    delivery beats elegance of format for the one image people will actually
    see.

WHY FRAMES AND NOT A SCREEN RECORDING
    Recording the product means a 29-minute builder run (measured, see
    docs/demo-recording-plan.md) and a clause that only forms once three
    officers agree. Both are true and neither is watchable. This animates the
    MECHANISM, which is what a reader needs before they will care about a
    screen recording.

    It is deliberately not a fake screenshot. Nothing here imitates the product
    UI, because an animation that looks like a screen recording but is not one
    is a lie with extra steps.

    python scripts/make_loop_animation.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from make_story_svgs import THEMES, head, txt, box, arrow  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "assets" / "story"
W, H = 900, 320

# (label, sub, tone) for the five beats of the loop
BEATS = [
    ("A case arrives",       "assembled from your records",    "panel"),
    ("Citra recommends",     "with the SOP passage it cites",  "brand"),
    ("An officer overrides", "and it asks why",                "warn"),
    ("Three officers agree", "the bar for learning anything",  "warn"),
    ("It becomes a rule",    "named, attributed, reversible",  "good"),
]
CAPTION = "and the next case is decided with it"


def _tone(t, name):
    return {
        "panel": (t["panel"], t["panelEdge"], t["ink"], t["mute"]),
        "brand": (t["brandSoft"], t["brand"], t["brandDeep"], t["brandDeep"]),
        "warn":  (t["warnSoft"], t["warn"], t["warn"], t["warn"]),
        "good":  (t["goodSoft"], t["good"], t["good"], t["good"]),
    }[name]


def frame(t: dict, step: int) -> str:
    """One frame. `step` 0..len(BEATS) — the last shows the rule feeding back."""
    s = head(W, H, "The learning loop: a case is recommended, an officer "
                   "corrects it, three officers agreeing makes it a rule, and "
                   "the next case is decided with it", t)

    s += txt(W / 2, 44, "Judgement compounds", t, size=21, fill=t["ink"],
             weight="700", anchor="middle")

    bw, bh, gap = 150, 74, 26
    n = len(BEATS)
    total = n * bw + (n - 1) * gap
    x0 = (W - total) / 2
    y = 96

    for i, (label, sub, tone) in enumerate(BEATS):
        x = x0 + i * (bw + gap)
        shown = i <= step
        fill, edge, ink, subink = _tone(t, tone if shown else "panel")
        # unreached beats sit faint rather than absent, so the layout never
        # jumps between frames — a moving diagram is much harder to read
        op = "" if shown else ' opacity="0.28"'
        s += f'<g{op}>'
        s += box(x, y, bw, bh, t, fill=fill, stroke=edge)
        s += txt(x + bw / 2, y + 31, label, t, size=13.5, fill=ink,
                 weight="700", anchor="middle")
        s += txt(x + bw / 2, y + 51, sub, t, size=11, fill=subink, anchor="middle")
        s += "</g>"
        if i < n - 1:
            a_op = "" if i < step else ' opacity="0.28"'
            s += f'<g{a_op}>' + arrow(x + bw + 3, y + bh / 2, x + bw + gap - 3,
                                      y + bh / 2, t) + "</g>"

    # the feedback edge, only on the final frame
    if step >= len(BEATS):
        left_cx = x0 + bw / 2
        right_cx = x0 + (n - 1) * (bw + gap) + bw / 2
        yy = y + bh + 44
        s += (f'<path d="M{right_cx} {y+bh} L{right_cx} {yy} L{left_cx} {yy} '
              f'L{left_cx} {y+bh+6}" stroke="{t["good"]}" stroke-width="2" '
              f'fill="none" stroke-dasharray="6 4" marker-end="url(#ag)"/>')
        s += txt(W / 2, yy - 10, CAPTION, t, size=13, fill=t["good"],
                 weight="600", anchor="middle")

    s += txt(W / 2, H - 22, "Nothing is learned from a single opinion.", t,
             size=12.5, fill=t["faint"], anchor="middle")
    return s + "</svg>\n"


def main() -> int:
    from PIL import Image
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "_frames"
    tmp.mkdir(exist_ok=True)

    for theme, t in THEMES.items():
        paths = []
        for step in range(len(BEATS) + 1):
            p = tmp / f"{theme}-{step}.svg"
            p.write_text(frame(t, step), encoding="utf-8", newline="\n")
            paths.append(p)

        pngs = []
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page(viewport={"width": W, "height": H},
                            device_scale_factor=2)
            for p in paths:
                pg.goto(p.resolve().as_uri())
                png = p.with_suffix(".png")
                pg.screenshot(path=str(png))
                pngs.append(png)
            b.close()

        frames = [Image.open(p).convert("P", palette=Image.ADAPTIVE, colors=128)
                  for p in pngs]
        # hold the final frame, so the loop reads as a loop and not a stutter
        durations = [900] * (len(frames) - 1) + [2600]
        gif = OUT / f"loop-{theme}.gif"
        frames[0].save(gif, save_all=True, append_images=frames[1:],
                       duration=durations, loop=0, optimize=True)
        print(f"  {gif.relative_to(OUT.parent.parent)}  "
              f"({gif.stat().st_size/1024:.0f} KB, {len(frames)} frames)")

    for f in tmp.iterdir():
        f.unlink()
    tmp.rmdir()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
