# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Preview the README locally, the way GitHub will show it.

    python scripts/preview_readme.py        # then open http://localhost:8800

Renders on every request, so editing README.md and refreshing is the whole
loop -- no push, no commit, no GitHub round trip. Assets are served from the
repo root, so the relative paths in the README (assets/story/..., docs/...)
resolve exactly as they will on github.com.

The two things worth getting right in a preview of THIS README:

  * `<picture>` with prefers-color-scheme. The diagrams ship a light and a dark
    copy and GitHub swaps them by the viewer's theme. A preview that ignores
    that shows one of them and hides a whole class of mistake -- unreadable
    dark text on a dark card, for instance. Toggle your OS/browser theme
    against this page and both get looked at.

  * GitHub's heading anchors. It slugs `## The short version` to
    `#the-short-version`; if the preview does not, every in-page link looks
    broken here and works there, or worse the other way round.

Not pixel-identical to github.com -- it is a local approximation with GitHub's
colour tokens and spacing. Good enough to catch layout, contrast, broken images
and dead links, which is what a preview is for.
"""
from __future__ import annotations

import http.server
import pathlib
import re
import socketserver
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8800

CSS = """
:root {
  --fg:#1f2328; --bg:#ffffff; --muted:#59636e; --border:#d1d9e0;
  --link:#0969da; --code-bg:#f6f8fa; --quote:#59636e;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fg:#f0f6fc; --bg:#0d1117; --muted:#9198a1; --border:#3d444d;
    --link:#4493f8; --code-bg:#151b23; --quote:#9198a1;
  }
}
* { box-sizing:border-box; }
body {
  margin:0; padding:32px 16px 96px; background:var(--bg); color:var(--fg);
  font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Noto Sans,Helvetica,Arial,sans-serif;
}
main { max-width:1012px; margin:0 auto; }
h1,h2,h3,h4 { line-height:1.25; margin:24px 0 16px; font-weight:600; }
h1 { font-size:2em; padding-bottom:.3em; border-bottom:1px solid var(--border); }
h2 { font-size:1.5em; padding-bottom:.3em; border-bottom:1px solid var(--border); }
h3 { font-size:1.25em; }
p, ul, ol, table, pre, blockquote { margin:0 0 16px; }
a { color:var(--link); text-decoration:none; }
a:hover { text-decoration:underline; }
img { max-width:100%; }
code { background:var(--code-bg); padding:.2em .4em; border-radius:6px; font-size:85%;
       font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace; }
pre { background:var(--code-bg); padding:16px; border-radius:6px; overflow:auto; }
pre code { background:none; padding:0; }
blockquote { border-left:.25em solid var(--border); padding:0 1em; color:var(--quote); }
table { border-collapse:collapse; display:block; overflow:auto; }
th,td { border:1px solid var(--border); padding:6px 13px; }
tr:nth-child(2n) td { background:var(--code-bg); }
hr { height:.25em; background:var(--border); border:0; margin:24px 0; }
.bar { max-width:1012px; margin:0 auto 24px; padding:10px 14px; border:1px solid var(--border);
       border-radius:6px; color:var(--muted); font-size:13px; }
"""


def slug(text: str) -> str:
    """GitHub's heading slug: lowercase, strip punctuation, spaces to dashes."""
    s = re.sub(r"<[^>]+>", "", text).strip().lower()
    s = re.sub(r"[^\w\- ]+", "", s, flags=re.U)
    return s.replace(" ", "-")


def render() -> str:
    import markdown

    md = (ROOT / "README.md").read_text(encoding="utf-8")
    html = markdown.markdown(
        md,
        extensions=["extra", "sane_lists", "nl2br"],
        output_format="html5",
    )

    # GitHub gives every heading an id so in-page links work; python-markdown's
    # toc extension slugs differently, so do it the GitHub way explicitly.
    def add_id(m):
        lvl, body = m.group(1), m.group(2)
        return f'<h{lvl} id="{slug(body)}">{body}</h{lvl}>'

    html = re.sub(r"<h([1-6])>(.*?)</h\1>", add_id, html, flags=re.S)

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>README — {ROOT.name}</title><style>{CSS}</style></head><body>"
        "<div class='bar'>Local preview · rendered from README.md on each refresh · "
        "switch your OS theme to check the dark diagrams</div>"
        f"<main>{html}</main></body></html>"
    )


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def do_GET(self):  # noqa: N802
        if self.path in ("/", "/index.html", "/README.md"):
            body = render().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *a):  # quiet
        pass


def main() -> int:
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as srv:
        print(f"  README preview: http://localhost:{PORT}")
        print("  edit README.md and refresh; Ctrl+C to stop")
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
