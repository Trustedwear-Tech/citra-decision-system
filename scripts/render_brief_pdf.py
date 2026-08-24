# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Render the executive brief markdown to a polished A4 PDF (reportlab)."""
import re, sys
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Preformatted, HRFlowable)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

SRC = r"C:\Github\Citra-AI\docs\decision-apps-executive-brief.md"
OUT = r"C:\Github\Citra-AI\docs\decision-apps-executive-brief.pdf"

# ── fonts: prefer Arial/Consolas (have → – · and box glyphs); fall back ──
try:
    F = r"C:/Windows/Fonts/"
    pdfmetrics.registerFont(TTFont("Body", F + "arial.ttf"))
    pdfmetrics.registerFont(TTFont("Body-Bold", F + "arialbd.ttf"))
    pdfmetrics.registerFont(TTFont("Body-Italic", F + "ariali.ttf"))
    pdfmetrics.registerFont(TTFont("Body-BoldItalic", F + "arialbi.ttf"))
    registerFontFamily("Body", normal="Body", bold="Body-Bold",
                       italic="Body-Italic", boldItalic="Body-BoldItalic")
    pdfmetrics.registerFont(TTFont("Mono", F + "consola.ttf"))
    BODY, BODYB, BODYI, MONO = "Body", "Body-Bold", "Body-Italic", "Mono"
except Exception as e:
    print("font fallback:", e)
    BODY, BODYB, BODYI, MONO = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Courier"

BLUE = colors.HexColor("#1f3a93")
INK = colors.HexColor("#1a1a1a")
MUT = colors.HexColor("#555555")

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def inline(s):
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"`(.+?)`", r'<font name="%s" size="9">\1</font>' % MONO, s)
    return s

CODEMAP = {"─": "-", "│": "|", "▼": "v", "▲": "^", "▶": ">", "◀": "<",
           "←": "<-", "→": "->", "↺": " (repeat)", "•": "*"}
def asciicode(s):
    for k, v in CODEMAP.items():
        s = s.replace(k, v)
    return s

styles = getSampleStyleSheet()
title = ParagraphStyle("T", parent=styles["Title"], fontName=BODYB, fontSize=21,
                       leading=25, textColor=BLUE, spaceAfter=4, alignment=1)
subtitle = ParagraphStyle("ST", fontName=BODYI, fontSize=11.5, leading=15,
                          textColor=MUT, alignment=1, spaceAfter=10)
h2 = ParagraphStyle("H2", fontName=BODYB, fontSize=13.5, leading=17, textColor=BLUE,
                    spaceBefore=12, spaceAfter=5)
h3 = ParagraphStyle("H3", fontName=BODYB, fontSize=11.5, leading=15, textColor=INK,
                    spaceBefore=8, spaceAfter=4)
body = ParagraphStyle("B", fontName=BODY, fontSize=10, leading=14.5, textColor=INK,
                      spaceAfter=7, alignment=4)
meta = ParagraphStyle("M", fontName=BODY, fontSize=9.5, leading=14, textColor=INK, spaceAfter=1)
bullet = ParagraphStyle("BU", parent=body, leftIndent=16, bulletIndent=3, spaceAfter=4)
foot = ParagraphStyle("F", fontName=BODYI, fontSize=8, leading=11, textColor=MUT, alignment=1)
codest = ParagraphStyle("C", fontName=MONO, fontSize=8.3, leading=10.6, textColor=INK)

def build_flowables(lines):
    flow, i = [], 0
    in_meta_block = False
    while i < len(lines):
        ln = lines[i].rstrip("\n")
        s = ln.strip()
        # code fence
        if s.startswith("```"):
            buf = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(asciicode(lines[i].rstrip("\n")))
                i += 1
            i += 1
            tbl = Table([[Preformatted(esc("\n".join(buf)), codest)]], colWidths=[480])
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f6fb")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#c9d2e8")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
            flow += [Spacer(1, 4), tbl, Spacer(1, 6)]
            continue
        # table
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|?$", lines[i + 1].strip()):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            cells = []
            for r_i, r in enumerate(rows):
                if r_i == 1:
                    continue  # separator
                parts = [c.strip() for c in r.strip().strip("|").split("|")]
                cells.append(parts)
            ncol = max(len(r) for r in cells)
            widths = [120, 180, 180] if ncol == 3 else [480.0 / ncol] * ncol
            data = []
            for r_i, parts in enumerate(cells):
                st = ParagraphStyle("tc", parent=body, fontSize=9, leading=12, spaceAfter=0,
                                    textColor=colors.white if r_i == 0 else INK,
                                    fontName=BODYB if r_i == 0 else BODY, alignment=0)
                while len(parts) < ncol:
                    parts.append("")
                data.append([Paragraph(inline(c) if c else "&nbsp;", st) for c in parts])
            tbl = Table(data, colWidths=widths, repeatRows=1)
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), BLUE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef1f8")]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d2e8")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
            flow += [Spacer(1, 4), tbl, Spacer(1, 8)]
            continue
        if s == "---":
            flow.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#c9d2e8"),
                                   spaceBefore=6, spaceAfter=8))
            i += 1
            continue
        if s.startswith("# "):
            flow.append(Paragraph(inline(s[2:]), title)); i += 1; continue
        if s.startswith("## "):
            flow.append(Paragraph(inline(s[3:]), h2)); i += 1; continue
        if s.startswith("### "):
            # line 2 is the subtitle; later ### are sub-headings
            st = subtitle if not any(isinstance(f, Paragraph) and f.style.name in ("H2",) for f in flow) else h3
            flow.append(Paragraph(inline(s[4:]), st)); i += 1; continue
        m = re.match(r"^(\d+)\.\s+(.*)", s)
        if m:
            flow.append(Paragraph(inline(m.group(2)), bullet, bulletText=m.group(1) + "."))
            i += 1; continue
        if s.startswith("- "):
            flow.append(Paragraph(inline(s[2:]), bullet, bulletText="•")); i += 1; continue
        if s.startswith("*") and s.endswith("*") and s.count("*") == 2:
            flow.append(Paragraph(inline(s), foot)); i += 1; continue
        if not s:
            i += 1; continue
        # meta header lines (To/From/Contact/Date/Classification) → tight
        if re.match(r"^\*\*(To|From|Contact|Date|Classification):\*\*", s):
            flow.append(Paragraph(inline(s), meta)); i += 1; continue
        flow.append(Paragraph(inline(s), body)); i += 1
    return flow

def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(BODYI if False else "Helvetica-Oblique", 7.5)
    canvas.setFillColor(MUT)
    canvas.drawString(57, 28, "Confidential — Trustedwear Tech Private Limited (Citra AI)")
    canvas.drawRightString(A4[0] - 57, 28, "Page %d" % doc.page)
    canvas.restoreState()

with open(SRC, "r", encoding="utf-8") as f:
    lines = f.readlines()

doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=57, rightMargin=57,
                        topMargin=52, bottomMargin=46,
                        title="Self-Improving Decision Apps & Decision APIs",
                        author="Rohit Kumar Chandan, Trustedwear Tech Private Limited")
doc.build(build_flowables(lines), onFirstPage=footer, onLaterPages=footer)
print("WROTE", OUT)
