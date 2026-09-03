# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Turn a frame-producing function into an animated GIF, per theme.

Extracted when the second animation was written rather than copied into it.
The palette is already shared through make_story_svgs for exactly this reason:
two files that each define how a frame becomes a GIF is how one animation ends
up at a different frame rate or colour depth from the other, and nobody
notices until they are side by side in the README.
"""
from __future__ import annotations

import pathlib
from typing import Callable, Dict


def build_gif(
    frame_fn: Callable[[Dict, int], str],
    *,
    n_frames: int,
    name: str,
    width: int,
    height: int,
    out: pathlib.Path,
    themes: Dict[str, Dict],
    hold_ms: int = 2600,
    step_ms: int = 900,
) -> None:
    """Render `n_frames` SVGs per theme and assemble `out/name-<theme>.gif`.

    The final frame is held for `hold_ms` so a looping GIF reads as a loop and
    not a stutter; every other frame gets `step_ms`.
    """
    from PIL import Image
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    tmp = out / f"_frames_{name}"
    tmp.mkdir(exist_ok=True)

    try:
        for theme, t in themes.items():
            svgs = []
            for step in range(n_frames):
                p = tmp / f"{theme}-{step}.svg"
                p.write_text(frame_fn(t, step), encoding="utf-8", newline="\n")
                svgs.append(p)

            pngs = []
            with sync_playwright() as pw:
                b = pw.chromium.launch()
                pg = b.new_page(viewport={"width": width, "height": height},
                                device_scale_factor=2)
                for p in svgs:
                    pg.goto(p.resolve().as_uri())
                    png = p.with_suffix(".png")
                    pg.screenshot(path=str(png))
                    pngs.append(png)
                b.close()

            frames = [Image.open(p).convert("P", palette=Image.ADAPTIVE, colors=128)
                      for p in pngs]
            durations = [step_ms] * (len(frames) - 1) + [hold_ms]
            gif = out / f"{name}-{theme}.gif"
            frames[0].save(gif, save_all=True, append_images=frames[1:],
                           duration=durations, loop=0, optimize=True)
            for f in frames:
                f.close()
            print(f"  {gif.relative_to(out.parent.parent)}  "
                  f"({gif.stat().st_size/1024:.0f} KB, {len(frames)} frames)")
    finally:
        # clean up even if a render fails, so a half-written frame set does not
        # get mistaken for output next time
        if tmp.exists():
            for f in tmp.iterdir():
                f.unlink()
            tmp.rmdir()
