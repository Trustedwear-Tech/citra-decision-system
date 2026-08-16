"use client";

import { type ReactNode } from "react";

/**
 * Minimal, dependency-free Markdown renderer.
 *
 * Covers the subset smart-app agents and report panels actually emit:
 * headings, bold / italic, inline + fenced code, ordered / unordered lists,
 * blockquotes, tables, horizontal rules and links. Output is built as React
 * nodes (never dangerouslySetInnerHTML) so untrusted agent text can't inject
 * markup. Good enough for chat replies and `markdown` panels — not a spec-
 * complete CommonMark implementation.
 */

// --- inline -----------------------------------------------------------------

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  // Underscore-italic (`_x_`) only matches at WORD BOUNDARIES — intra-word
  // underscores in snake_case identifiers (theft_cases, tamper_events) must
  // render literally, never get paired into an italic span that swallows the
  // underscores and any **bold** caught between them. Asterisk-italic keeps
  // the original behaviour; `**bold**` and `` `code` `` are unchanged.
  const re =
    /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\n]+\*|(?<![A-Za-z0-9])_[^_\n]+_(?![A-Za-z0-9]))|(\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    const k = `${keyPrefix}-${i++}`;
    if (m[1]) {
      nodes.push(
        <code key={k} className="md-code">
          {tok.slice(1, -1)}
        </code>,
      );
    } else if (m[2]) {
      nodes.push(<strong key={k}>{tok.slice(2, -2)}</strong>);
    } else if (m[3]) {
      nodes.push(<em key={k}>{tok.slice(1, -1)}</em>);
    } else if (m[4]) {
      const mm = /\[([^\]]+)\]\(([^)\s]+)\)/.exec(tok);
      if (mm) {
        nodes.push(
          <a key={k} href={mm[2]} target="_blank" rel="noreferrer noopener">
            {mm[1]}
          </a>,
        );
      } else {
        nodes.push(tok);
      }
    }
    last = m.index + tok.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

// --- blocks -----------------------------------------------------------------

function splitRow(line: string): string[] {
  return line
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((c) => c.trim());
}

export default function Markdown({
  content,
  className,
}: {
  content: string;
  className?: string;
}) {
  const lines = (content ?? "").replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];

    // blank
    if (!line.trim()) {
      i++;
      continue;
    }

    // fenced code
    if (/^```/.test(line.trim())) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i].trim())) {
        buf.push(lines[i]);
        i++;
      }
      i++; // closing fence
      blocks.push(
        <pre key={key++} className="md-pre">
          <code>{buf.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    // heading
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      const level = h[1].length;
      const Tag = (`h${Math.min(level + 1, 6)}`) as keyof JSX.IntrinsicElements;
      blocks.push(
        <Tag key={key++} className={`md-h md-h${level}`}>
          {renderInline(h[2], `h${key}`)}
        </Tag>,
      );
      i++;
      continue;
    }

    // horizontal rule
    if (/^(\s*[-*_]){3,}\s*$/.test(line)) {
      blocks.push(<hr key={key++} className="md-hr" />);
      i++;
      continue;
    }

    // table
    if (
      line.includes("|") &&
      i + 1 < lines.length &&
      /^\s*\|?[\s:-]+\|[\s:|-]*$/.test(lines[i + 1])
    ) {
      const header = splitRow(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        rows.push(splitRow(lines[i]));
        i++;
      }
      blocks.push(
        <div key={key++} className="md-table-wrap">
          <table className="md-table">
            <thead>
              <tr>
                {header.map((c, ci) => (
                  <th key={ci}>{renderInline(c, `th${key}-${ci}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>
                  {r.map((c, ci) => (
                    <td key={ci}>{renderInline(c, `td${key}-${ri}-${ci}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    // blockquote
    if (/^\s*>/.test(line)) {
      const buf: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        buf.push(lines[i].replace(/^\s*>\s?/, ""));
        i++;
      }
      blocks.push(
        <blockquote key={key++} className="md-quote">
          {renderInline(buf.join(" "), `bq${key}`)}
        </blockquote>,
      );
      continue;
    }

    // unordered list
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ""));
        i++;
      }
      blocks.push(
        <ul key={key++} className="md-list">
          {items.map((it, ii) => (
            <li key={ii}>{renderInline(it, `ul${key}-${ii}`)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    // ordered list
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ""));
        i++;
      }
      blocks.push(
        <ol key={key++} className="md-list">
          {items.map((it, ii) => (
            <li key={ii}>{renderInline(it, `ol${key}-${ii}`)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    // paragraph — collect until blank or a block-starting line
    const buf: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^```|^(#{1,4})\s|^\s*>|^\s*[-*+]\s|^\s*\d+[.)]\s/.test(lines[i]) &&
      !/^(\s*[-*_]){3,}\s*$/.test(lines[i])
    ) {
      buf.push(lines[i]);
      i++;
    }
    blocks.push(
      <p key={key++} className="md-p">
        {renderInline(buf.join(" "), `p${key}`)}
      </p>,
    );
  }

  return <div className={`md${className ? ` ${className}` : ""}`}>{blocks}</div>;
}
