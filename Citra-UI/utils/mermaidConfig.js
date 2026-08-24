// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Shared Mermaid configuration
 * Single source of truth for Mermaid CDN URL, version, theme config, and loader.
 */

export const MERMAID_VERSION = '11.4.1';
export const MERMAID_CDN_URL = `https://cdn.jsdelivr.net/npm/mermaid@${MERMAID_VERSION}/dist/mermaid.min.js`;

/**
 * Returns Mermaid theme configuration for dark or light mode.
 * Uses Mermaid v11 default dark theme with curated variables for consistent rendering.
 */
export const getMermaidInitConfig = (isDark = true) => ({
  startOnLoad: false,
  theme: isDark ? 'dark' : 'default',
  themeVariables: isDark
    ? {
        primaryColor: '#3b82f6',
        primaryTextColor: '#f4f4f5',
        primaryBorderColor: '#60a5fa',
        lineColor: '#60a5fa',
        secondaryColor: '#1e3a5f',
        tertiaryColor: '#1a1a2e',
        background: '#1a1a2e',
        mainBkg: '#1e3a5f',
        nodeBorder: '#60a5fa',
        clusterBkg: '#16213e',
        titleColor: '#f4f4f5',
        edgeLabelBackground: '#1a1a2e',
        textColor: '#f4f4f5',
        noteBkgColor: '#1e3a5f',
        noteTextColor: '#f4f4f5',
        noteBorderColor: '#60a5fa',
        fontFamily: 'system-ui, -apple-system, sans-serif',
        fontSize: '16px',
      }
    : {
        primaryColor: '#3b82f6',
        primaryTextColor: '#1f2937',
        primaryBorderColor: '#93c5fd',
        lineColor: '#6b7280',
        secondaryColor: '#eff6ff',
        tertiaryColor: '#ffffff',
        background: '#ffffff',
        mainBkg: '#f0f9ff',
        nodeBorder: '#93c5fd',
        clusterBkg: '#f8fafc',
        titleColor: '#1f2937',
        edgeLabelBackground: '#ffffff',
        textColor: '#1f2937',
        noteBkgColor: '#eff6ff',
        noteTextColor: '#1f2937',
        noteBorderColor: '#93c5fd',
        fontFamily: 'system-ui, -apple-system, sans-serif',
        fontSize: '16px',
      },
  securityLevel: 'loose',
  fontFamily: 'system-ui, -apple-system, sans-serif',
  flowchart: {
    useMaxWidth: true,
    htmlLabels: true,
    curve: 'basis',
    padding: 20,
  },
  sequence: {
    actorMargin: 50,
    boxMargin: 10,
    boxTextMargin: 5,
    noteMargin: 10,
    messageMargin: 35,
  },
  gantt: {
    titleTopMargin: 25,
    barHeight: 30,
    barGap: 4,
    topPadding: 50,
    sidePadding: 75,
  },
});

/**
 * Loads Mermaid from CDN if not already loaded.
 * Returns a promise that resolves when window.mermaid is available.
 */
export const loadMermaidFromCDN = () => {
  return new Promise((resolve, reject) => {
    if (typeof window === 'undefined') {
      reject(new Error('Not in browser environment'));
      return;
    }

    if (window.mermaid) {
      resolve(window.mermaid);
      return;
    }

    // Check if script is already being loaded
    const existingScript = document.querySelector(`script[src="${MERMAID_CDN_URL}"]`);
    if (existingScript) {
      existingScript.addEventListener('load', () => resolve(window.mermaid));
      existingScript.addEventListener('error', (err) => reject(new Error('Failed to load Mermaid CDN')));
      return;
    }

    const script = document.createElement('script');
    script.src = MERMAID_CDN_URL;
    script.async = true;
    script.onload = () => {
      console.log(`✅ Mermaid v${MERMAID_VERSION} loaded from CDN`);
      resolve(window.mermaid);
    };
    script.onerror = () => {
      reject(new Error('Failed to load Mermaid CDN'));
    };
    document.head.appendChild(script);
  });
};

/**
 * Sanitize Mermaid code to fix common LLM-generated issues.
 * This is a shared sanitization pipeline used across all Mermaid renderers.
 */
export const sanitizeMermaidCode = (rawCode) => {
  let code = rawCode.trim();

  // Strip %%{init:...}%% theme/config directives — they cause parse errors
  code = code.replace(/%%\{init:[\s\S]*?\}%%/g, '');

  // Strip classDef, style, linkStyle lines — colors are applied by the renderer
  code = code.replace(/^\s*(classDef|style|linkStyle)\s+.*$/gm, '');

  // Replace 'end' as a node ID with 'finish' (reserved keyword)
  // Match patterns like: end[...], end(...), end{...}, --> end, -- end
  code = code.replace(/\bend\b(?=\s*[\[({])/g, 'finish');
  code = code.replace(/(-->|---|-\.->|==>)\s*\bend\b/g, '$1 finish');

  // Remove <br/> and <br> tags
  code = code.replace(/<br\s*\/?>/gi, ' - ');

  // Remove other HTML tags
  code = code.replace(/<[^>]+>/g, ' ');
  code = code.replace(/[ \t]+/g, ' ');

  // Replace forward slashes in node labels
  code = code.replace(/\[([^\]]*)\]/g, (match, content) => {
    return content.includes('/') ? `[${content.replace(/\//g, ',')}]` : match;
  });
  code = code.replace(/\{([^}]*)\}/g, (match, content) => {
    return content.includes('/') ? `{${content.replace(/\//g, ',')}}` : match;
  });

  // Fix nested brackets iteratively
  const maxIterations = 10;
  let prevCode, iterations;

  // Nested square brackets → parentheses
  prevCode = '';
  iterations = 0;
  while (prevCode !== code && iterations < maxIterations) {
    prevCode = code;
    iterations++;
    code = code.replace(/(\w+)\[([^\n]*?)\]/g, (match, nodeId, label) => {
      if (label.includes('[') || label.includes(']')) {
        return `${nodeId}[${label.replace(/\[/g, '(').replace(/\]/g, ')')}]`;
      }
      return match;
    });
  }

  // Parentheses in square brackets
  prevCode = '';
  iterations = 0;
  while (prevCode !== code && iterations < maxIterations) {
    prevCode = code;
    iterations++;
    code = code.replace(/\[([^\]]*)\(([^)]*)\)([^\]]*)\]/g, (_, before, inside, after) => {
      return `[${`${before}${inside}${after}`.replace(/\s+/g, ' ').trim()}]`;
    });
  }

  // Parentheses in curly braces
  prevCode = '';
  iterations = 0;
  while (prevCode !== code && iterations < maxIterations) {
    prevCode = code;
    iterations++;
    code = code.replace(/\{([^}]*)\(([^)]*)\)([^}]*)\}/g, (_, before, inside, after) => {
      return `{${`${before}${inside}${after}`.replace(/\s+/g, ' ').trim()}}`;
    });
  }

  // Nested round brackets
  prevCode = '';
  iterations = 0;
  while (prevCode !== code && iterations < maxIterations) {
    prevCode = code;
    iterations++;
    code = code.replace(/\(([^)]*)\(([^)]*)\)([^)]*)\)/g, (_, before, inside, after) => {
      return `(${`${before}${inside}${after}`.replace(/\s+/g, ' ').trim()})`;
    });
  }

  // Final sweep: stray brackets in node labels
  code = code.replace(/(\w+)\[([^\]]*)\]/g, (match, nodeId, label) => {
    const cleaned = label.replace(/[\[\]]/g, ' ').replace(/\s+/g, ' ').trim();
    return `${nodeId}[${cleaned}]`;
  });

  // Break edge-adjacent bracket pairs
  code = code.replace(/\]\s*\[/g, ' - ');

  // Timeline-specific fixes
  if (code.trim().toLowerCase().startsWith('timeline')) {
    code = code.replace(/^Section /gm, 'section ');
    const lines = code.split('\n');
    code = lines.map(line => {
      if (!line.trim() || line.trim().startsWith('section') || line.trim().startsWith('title')) return line;
      const sepIdx = line.indexOf(' : ');
      if (sepIdx !== -1) {
        const period = line.substring(0, sepIdx);
        const event = line.substring(sepIdx);
        if (period.match(/\d:\d/)) {
          return period.replace(/(\d):(\d)/g, '$1.$2') + event;
        }
      }
      return line;
    }).join('\n');
  }

  // Mindmap-specific fixes: ensure all content lines are children of the root.
  // Mermaid mindmaps require exactly one root; disconnected top-level nodes
  // (e.g. "Scenarios" without indentation) cause "No parent could be found" errors.
  if (code.trim().toLowerCase().startsWith('mindmap')) {
    const lines = code.split('\n');
    let rootIndent = -1;
    let hasRoot = false;
    const fixedLines = [];
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const trimmed = line.trim();
      // Skip empty lines and the "mindmap" declaration
      if (!trimmed || trimmed.toLowerCase() === 'mindmap') {
        fixedLines.push(line);
        continue;
      }
      const indent = line.search(/\S/);
      if (!hasRoot) {
        // First non-empty, non-declaration line is the root
        hasRoot = true;
        rootIndent = indent;
        fixedLines.push(line);
      } else if (indent <= rootIndent) {
        // This line is at root level or above — would create a second root.
        // Indent it one level deeper than root to make it a child.
        fixedLines.push(' '.repeat(rootIndent + 2) + trimmed);
      } else {
        fixedLines.push(line);
      }
    }
    code = fixedLines.join('\n');
  }

  return code;
};
