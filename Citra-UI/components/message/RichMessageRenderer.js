// Enhanced Rich Message Renderer with comprehensive markdown support for web
import React, { memo, useState, useEffect, useCallback, useMemo, useRef } from 'react';
import ReactDOM from 'react-dom';
import { View, Text, ScrollView, Platform, Image, TouchableOpacity, Linking } from 'react-native';
import { styles } from '../../styles';
import { authService } from '../../services/authService';
import CONFIG from '../../config/config';
import MermaidDiagram from '../MermaidDiagram';
import ChartJsDiagram from '../ChartJsDiagram';
import AsciiDiagram from '../AsciiDiagram';

// Fence languages that should render as a plain-text ASCII/Unicode box diagram
// (lightweight alternative to Mermaid for quick conceptual diagrams).
const ASCII_DIAGRAM_LANGUAGES = new Set([
  'ascii', 'diagram', 'asciiflow', 'boxdraw', 'txt-diagram'
]);

// Heuristic: unlabeled code blocks containing several Unicode box-drawing
// codepoints (U+2500-U+257F) or block arrows are almost certainly diagrams
// rather than source code. Keep threshold conservative to avoid false positives
// on prose that happens to include a single arrow.
const isLikelyAsciiDiagram = (rawContent) => {
  if (!rawContent || typeof rawContent !== 'string') return false;
  const text = rawContent;
  // Must be multi-line — single-line arrows in prose shouldn't trigger.
  if (text.indexOf('\n') === -1) return false;
  let boxChars = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    // Box Drawing + Block Elements
    if (c >= 0x2500 && c <= 0x259F) { boxChars++; continue; }
    // Arrows block
    if (c >= 0x2190 && c <= 0x21FF) { boxChars++; continue; }
    if (boxChars >= 6) return true;
  }
  return boxChars >= 6;
};

// Helper function to get Citra AI service base URL
const getCitraServiceBaseUrl = () => {
  return CONFIG.CITRA_SERVICE_URL || CONFIG.urls?.citraService || 'http://localhost:8085/citra-ai';
};

// Helper function to check if citation debug mode is enabled
const isCitationDebugEnabled = () => {
  return CONFIG.features?.debugLogging || CONFIG.FEATURES?.debugLogging || false;
};

const getTextColor = (theme, isUserMessage) => {
  if (!theme) {
    return '#2d3748';
  }

  if (isUserMessage) {
    return theme.userMessageText || theme.text || '#2d3748';
  }

  return theme.botMessageText || theme.text || '#2d3748';
};

const escapeHtml = (value) => {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
};

const decodeHtmlEntities = (value) => {
  const text = String(value || '');
  // Use a temporary DOM element to decode HTML entities
  if (typeof document !== 'undefined') {
    const textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    return textarea.value;
  }
  // Fallback for non-browser environments (mobile)
  return text
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
};

const convertInlineSourceMarkdown = (inputText) => {
  if (!inputText || typeof inputText !== 'string') {
    return inputText;
  }

  return inputText.replace(/([^\s].*?)--(https?:\/\/[^\s)]+)(?=\s|$)/g, (match, label, url) => {
    const trimmedLabel = label.trim();
    if (!trimmedLabel) {
      return match;
    }

    if (/\[[^\]]+\]\([^\)]+\)$/.test(trimmedLabel)) {
      return match;
    }

    return `${label.replace(trimmedLabel, '')}[${trimmedLabel}](${url})`;
  });
};

const deduplicateSourceItems = (items) => {
  if (!Array.isArray(items)) {
    return [];
  }

  const seen = new Set();
  const result = [];

  items.forEach((rawLine) => {
    if (typeof rawLine !== 'string') {
      return;
    }

    const trimmedLine = rawLine.trim();
    if (!trimmedLine) {
      return;
    }

    const markerInfo = extractSourceMarkerInfo(trimmedLine);
    const key = markerInfo
      ? `${markerInfo.type || ''}::${(markerInfo.identifier || '').toLowerCase()}`
      : trimmedLine.replace(/\s+/g, ' ').toLowerCase();

    if (!seen.has(key)) {
      seen.add(key);
      result.push(trimmedLine);
    }
  });

  return result;
};

const STRUCTURED_JSON_BLOCK_REGEX = /```json\s*([\s\S]*?)```/gi;

const extractStructuredAppendix = (text) => {
  if (!text || typeof text !== 'string') {
    return { cleanedText: text, structuredPayload: null };
  }

  let structuredPayload = null;
  let workingText = text;

  // First, extract and remove ---WEB_SOURCES--- or ---LLM_CITATIONS--- section
  const webSourcesMatch = workingText.match(/\n\s*---(?:WEB_SOURCES|LLM_CITATIONS)---\s*\n([\s\S]*?)\n\s*---END_(?:WEB_SOURCES|LLM_CITATIONS)---/i);
  if (webSourcesMatch) {
    try {
      const webSourcesJson = JSON.parse(webSourcesMatch[1].trim());
      if (webSourcesJson && webSourcesJson.web && Array.isArray(webSourcesJson.web)) {
        // Initialize structured payload if not exists
        if (!structuredPayload) {
          structuredPayload = { citations: {} };
        }
        // Add web citations to the payload
        structuredPayload.citations.web = webSourcesJson.web;
        console.log(`✅ Extracted ${webSourcesJson.web.length} web citations from WEB_SOURCES section`);
      }
    } catch (error) {
      console.warn('⚠️ Failed to parse WEB_SOURCES JSON:', error);
    }
    // Remove the entire WEB_SOURCES section from text
    workingText = workingText.replace(/\n\s*---(?:WEB_SOURCES|LLM_CITATIONS)---\s*\n[\s\S]*?\n\s*---END_(?:WEB_SOURCES|LLM_CITATIONS)---/i, '');
  }

  // Process text for LLM citations (JSON appendix from LLM response)
  // First, try to extract JSON wrapped in ```json or ``` code blocks
  let match;
  let lastMatch = null;
  // Match both ```json and ``` (without language identifier) followed by JSON content
  const jsonBlockRegex = /```(?:json)?\s*(\{[\s\S]*?"citations"\s*:\s*\{[\s\S]*?\}[\s\S]*?\})\s*```/gi;
  while ((match = jsonBlockRegex.exec(workingText)) !== null) {
    lastMatch = match;
  }

  if (lastMatch && lastMatch[1]) {
    try {
      const parsed = JSON.parse(lastMatch[1].trim());
      const hasMermaid = parsed && Array.isArray(parsed.mermaid_blocks);
      const hasCharts = parsed && Array.isArray(parsed.chart_blocks);
      const hasCitations = parsed && parsed.citations && typeof parsed.citations === 'object';
      if (!hasMermaid && !hasCharts && !hasCitations) {
        return { cleanedText: workingText.trim(), structuredPayload };
      }

      // Merge with existing structured payload
      if (structuredPayload) {
        const existingCitations = structuredPayload.citations || {};
        structuredPayload = { ...structuredPayload, ...parsed };
        if (parsed.citations || existingCitations) {
          // Preserve any existing citation arrays (like web) before overwriting
          structuredPayload.citations = {
            ...(parsed.citations || {}),
            ...existingCitations,
          };
        }
        console.log('🔄 [MERGE_CODEBLOCK] After merging JSON block, citations keys:', Object.keys(structuredPayload.citations || {}));
        console.log('🔄 [MERGE_CODEBLOCK] Web citations count:', structuredPayload.citations?.web?.length || 0);
      } else {
        structuredPayload = parsed;
      }

      const prefix = workingText.slice(0, lastMatch.index);
      const suffix = workingText.slice(lastMatch.index + lastMatch[0].length);
      const cleanedText = `${prefix}${suffix}`.trim();
      return { cleanedText, structuredPayload };
    } catch (error) {
      console.warn('⚠️ Failed to parse structured JSON code block:', error);
    }
  }

  // If no code block found, try to extract raw JSON at the end of text
  // Look for a JSON object starting with { and ending with } at the end OR before SOURCES section
  let rawJsonMatch = workingText.match(/\n\s*(\{[\s\S]*"citations"\s*:\s*\{[\s\S]*\})\s*$/);

  // If not found at end, try to find JSON before SOURCES section
  if (!rawJsonMatch) {
    rawJsonMatch = workingText.match(/\n\s*(\{[\s\S]*"citations"\s*:\s*\{[\s\S]*\})\s*\n+\s*SOURCES:/);
  }

  if (rawJsonMatch && rawJsonMatch[1]) {
    try {
      const parsed = JSON.parse(rawJsonMatch[1].trim());
      const hasMermaid = parsed && Array.isArray(parsed.mermaid_blocks);
      const hasCharts = parsed && Array.isArray(parsed.chart_blocks);
      const hasCitations = parsed && parsed.citations && typeof parsed.citations === 'object';
      if (!hasMermaid && !hasCharts && !hasCitations) {
        return { cleanedText: workingText.trim(), structuredPayload };
      }

      // Merge with existing structured payload
      if (structuredPayload) {
        const existingCitations = structuredPayload.citations || {};
        structuredPayload = { ...structuredPayload, ...parsed };
        if (parsed.citations || existingCitations) {
          structuredPayload.citations = {
            ...(parsed.citations || {}),
            ...existingCitations,
          };
        }
        console.log('🔄 [MERGE_RAWJSON] After merging raw JSON, citations keys:', Object.keys(structuredPayload.citations || {}));
        console.log('🔄 [MERGE_RAWJSON] Web citations count:', structuredPayload.citations?.web?.length || 0);
      } else {
        structuredPayload = parsed;
      }

      // Remove the JSON from the text (but keep the SOURCES section if it exists)
      const beforeJson = workingText.slice(0, rawJsonMatch.index);
      const afterJson = workingText.slice(rawJsonMatch.index + rawJsonMatch[0].length);
      const cleanedText = (beforeJson + afterJson).trim();
      console.log('✅ Extracted raw JSON citations from message');
      return { cleanedText, structuredPayload };
    } catch (error) {
      console.warn('⚠️ Failed to parse raw JSON appendix:', error);
    }
  }

  // Final return - ensure we return any web citations that were extracted
  return { cleanedText: workingText.trim(), structuredPayload };
};

const normalizeSourceLabel = (value, fallback = 'Source') => {
  const base = typeof value === 'string' && value.trim() ? value.trim() : fallback;
  if (!base) {
    return '';
  }
  return base
    .replace(/\r?\n+/g, ' ')
    .replace(/\s+/g, ' ')
    .replace(/--/g, '—')
    .trim();
};

const buildMetadataString = (segments) => {
  return (segments || [])
    .filter((segment) => typeof segment === 'string' && segment.trim())
    .map((segment) => segment.trim())
    .join(' | ');
};

const buildSourceLinesFromStructuredCitations = (citations) => {
  if (!citations || typeof citations !== 'object') {
    return { header: null, items: [] };
  }

  const lines = [];

  // Flatten nested arrays (LLM sometimes returns [[{...}]] instead of [{...}])
  const flattenArray = (arr) => {
    if (!Array.isArray(arr)) return [];
    return arr.flatMap(item => Array.isArray(item) ? item : [item]);
  };

  if (Array.isArray(citations.documents)) {
    const flatDocuments = flattenArray(citations.documents);
    flatDocuments.forEach((doc, docIndex) => {
      const documentId = typeof doc?.document_id === 'string' ? doc.document_id.trim() : '';
      const displayName = normalizeSourceLabel(doc.display_name || doc.name || 'Document');

      // Debug logging for citation parsing
      if (isCitationDebugEnabled()) {
        console.log(`📄 [DOC_CITATION_${docIndex}] Parsing document:`, {
          has_document_id: !!documentId,
          document_id: documentId ? documentId.substring(0, 50) : 'MISSING',
          display_name: displayName.substring(0, 50),
          raw_doc_keys: Object.keys(doc)
        });
      }

      // Handle personal documents (with document_id)
      if (documentId) {
        // Personal document from Milvus/orchestrator
        const base = `DOC::${displayName}--${documentId}`;
        const metadata = buildMetadataString([
          doc.description ? `Description: ${doc.description}` : null,
          doc.date ? `Date: ${doc.date}` : null,
          doc.relevance ? `Relevance: ${doc.relevance}` : null,
          doc.notes ? `Note: ${doc.notes}` : null
        ]);
        const citationLine = metadata ? `${base} ${metadata}` : base;
        if (isCitationDebugEnabled()) {
          console.log(`✅ [DOC_CITATION_${docIndex}] Created line:`, citationLine.substring(0, 100));
        }
        lines.push(citationLine);
      } else if (doc.display_name || doc.description || doc.metadata) {

        const metadataFields = doc.metadata && typeof doc.metadata === 'object' ? [
          doc.metadata.case_id ? `Case ID: ${doc.metadata.case_id}` : null,
          doc.metadata.case_number ? `Case No: ${doc.metadata.case_number}` : null,
          doc.metadata.case_title ? `Title: ${doc.metadata.case_title}` : null,
          doc.metadata.citation ? `Citation: ${doc.metadata.citation}` : null,
          doc.metadata.air_citation ? `AIR: ${doc.metadata.air_citation}` : null,
          doc.metadata.scc_citation ? `SCC: ${doc.metadata.scc_citation}` : null,
          doc.metadata.cnr ? `CNR: ${doc.metadata.cnr}` : null,
          doc.metadata.cnr_number ? `CNR: ${doc.metadata.cnr_number}` : null,
          doc.metadata.year ? `Year: ${doc.metadata.year}` : null,
          doc.metadata.judgment_date ? `Date: ${doc.metadata.judgment_date}` : null,
          doc.metadata.filing_date ? `Filed: ${doc.metadata.filing_date}` : null,
          doc.metadata.court ? `Court: ${doc.metadata.court}` : null,
          doc.metadata.bench ? `Bench: ${doc.metadata.bench}` : null,
          doc.metadata.judges ? `Judges: ${doc.metadata.judges}` : null,
          doc.metadata.appellant ? `Appellant: ${doc.metadata.appellant}` : null,
          doc.metadata.respondent ? `Respondent: ${doc.metadata.respondent}` : null,
          doc.metadata.petitioner ? `Petitioner: ${doc.metadata.petitioner}` : null,
          doc.metadata.act ? `Act: ${doc.metadata.act}` : null,
          doc.metadata.section ? `Section: ${doc.metadata.section}` : null,
          doc.metadata.provision ? `Provision: ${doc.metadata.provision}` : null,
          doc.metadata.document_type ? `Type: ${doc.metadata.document_type}` : null,
          doc.metadata.category ? `Category: ${doc.metadata.category}` : null,
          // Add any other metadata fields dynamically
          ...Object.keys(doc.metadata).filter(key => ![
            'case_id', 'case_number', 'case_title', 'citation', 'air_citation', 'scc_citation',
            'cnr', 'cnr_number', 'year', 'judgment_date', 'filing_date', 'court', 'bench', 'judges',
            'appellant', 'respondent', 'petitioner', 'act', 'section', 'provision', 'document_type', 'category'
          ].includes(key)).map(key => `${key}: ${doc.metadata[key]}`)
        ] : [];

        const details = buildMetadataString([
          doc.description ? `Description: ${doc.description}` : null,
          ...metadataFields
        ]);
        lines.push(details ? `Document: ${displayName} — ${details}` : `Document: ${displayName}`);
      }
    });
  }

  // Handle web citations (internet sources)
  if (Array.isArray(citations.web)) {
    const flatWeb = flattenArray(citations.web);
    console.log(`📊 [WEB_CITATIONS] Processing ${flatWeb.length} web citations`);
    flatWeb.forEach((webItem) => {
      const url = typeof webItem?.url === 'string' ? webItem.url.trim() : '';
      if (!url) return;

      // Extract domain from URL for friendly display (used as fallback title)
      let domainName = 'Web Source';
      try {
        const urlObj = new URL(url);
        domainName = urlObj.hostname.replace(/^www\./, '');
      } catch (e) {
        // If URL parsing fails, use a generic label
        domainName = 'Web Source';
      }

      // Use title if available, otherwise use domain name
      const title = webItem.display_name || webItem.title || domainName;
      const label = normalizeSourceLabel(title);

      // IMPORTANT: Show full URL, not just domain name
      // Format: WEB::Title--URL (the URL will be displayed in the UI)
      const base = `WEB::${label}--${url}`;
      const metadata = buildMetadataString([
        webItem.description ? `Description: ${webItem.description}` : null,
        webItem.date ? `Date: ${webItem.date}` : null,
        webItem.source ? `Source: ${webItem.source}` : null
      ]);
      const finalLine = metadata ? `${base} ${metadata}` : base;
      console.log(`🌐 [WEB_CITATION_LINE] Created: ${finalLine.substring(0, 100)}...`);
      lines.push(finalLine);
    });
  }

  if (Array.isArray(citations.chunks)) {
    const flatChunks = flattenArray(citations.chunks);

    // Group chunks by document_id to create hierarchical structure
    const chunksByDocument = new Map();
    const standaloneChunks = [];

    flatChunks.forEach((chunk, chunkIndex) => {
      const vectorId = typeof chunk?.vector_id === 'string' ? chunk.vector_id.trim() : '';
      if (!vectorId) {
        if (isCitationDebugEnabled()) {
          console.warn(`⚠️ [CHUNK_CITATION_${chunkIndex}] Skipping chunk - no vector_id:`, chunk);
        }
        return;
      }

      const documentId = typeof chunk?.document_id === 'string' ? chunk.document_id.trim() : '';

      if (documentId) {
        // Group under parent document
        if (!chunksByDocument.has(documentId)) {
          chunksByDocument.set(documentId, {
            documentId,
            displayName: chunk.display_name || chunk.topic || 'Document',
            chunks: []
          });
        }
        chunksByDocument.get(documentId).chunks.push(chunk);
      } else {
        // Standalone chunk without document_id
        standaloneChunks.push(chunk);
      }
    });

    // Emit document entries with their chunks
    chunksByDocument.forEach((docGroup, documentId) => {
      // Create parent document entry
      const docLabel = normalizeSourceLabel(docGroup.displayName);
      const docLine = `DOC::${docLabel}--${documentId}`;
      if (isCitationDebugEnabled()) {
        console.log(`📄 [DOC_FROM_CHUNKS] Created parent document: ${docLine.substring(0, 100)}`);
      }
      lines.push(docLine);

      // Add all chunks under this document
      docGroup.chunks.forEach((chunk, chunkIndex) => {
        const vectorId = chunk.vector_id.trim();
        const label = normalizeSourceLabel(chunk.display_name || chunk.topic || 'Chunk');
        const base = `CHUNK::${label}--${vectorId}`;

        if (isCitationDebugEnabled()) {
          console.log(`📝 [CHUNK_CITATION_${chunkIndex}] Parsing chunk under ${documentId.substring(0, 30)}:`, {
            vector_id: vectorId.substring(0, 80),
            display_name: label.substring(0, 50)
          });
        }

        const metadata = buildMetadataString([
          chunk.description ? `Description: ${chunk.description}` : null,
          chunk.relevance ? `Relevance: ${chunk.relevance}` : null,
          chunk.notes ? `Note: ${chunk.notes}` : null
        ]);
        const citationLine = metadata ? `${base} ${metadata}` : base;
        if (isCitationDebugEnabled()) {
          console.log(`✅ [CHUNK_CITATION_${chunkIndex}] Created line:`, citationLine.substring(0, 120));
        }
        lines.push(citationLine);
      });
    });

    // Add standalone chunks at the end
    standaloneChunks.forEach((chunk, chunkIndex) => {
      const vectorId = chunk.vector_id.trim();
      const label = normalizeSourceLabel(chunk.display_name || chunk.topic || 'Chunk');
      const base = `CHUNK::${label}--${vectorId}`;

      if (isCitationDebugEnabled()) {
        console.log(`📝 [STANDALONE_CHUNK_${chunkIndex}] Parsing standalone chunk:`, {
          vector_id: vectorId.substring(0, 80),
          display_name: label.substring(0, 50)
        });
      }

      const metadata = buildMetadataString([
        chunk.description ? `Description: ${chunk.description}` : null,
        chunk.parent_document_id ? `Document: ${chunk.parent_document_id}` : null,
        chunk.relevance ? `Relevance: ${chunk.relevance}` : null,
        chunk.notes ? `Note: ${chunk.notes}` : null
      ]);
      const citationLine = metadata ? `${base} ${metadata}` : base;
      if (isCitationDebugEnabled()) {
        console.log(`✅ [STANDALONE_CHUNK_${chunkIndex}] Created line:`, citationLine.substring(0, 120));
      }
      lines.push(citationLine);
    });
  }

  if (Array.isArray(citations.statutes)) {
    citations.statutes.forEach((statute) => {
      // Handle both string format (from LLM) and object format
      if (typeof statute === 'string') {
        lines.push(`Statute: ${statute.trim()}`);
      } else {
        const title = normalizeSourceLabel(statute.name || statute.title || statute.display_name, 'Statute');
        const details = buildMetadataString([
          statute.sections ? `Sections: ${statute.sections}` : (statute.section ? `Section: ${statute.section}` : null),
          statute.description ? `Description: ${statute.description}` : null,
          statute.citation ? `Citation: ${statute.citation}` : null,
          statute.status ? `Status: ${statute.status}` : null,
          statute.notes ? `Notes: ${statute.notes}` : null
        ]);
        lines.push(details ? `Statute: ${title} — ${details}` : `Statute: ${title}`);
      }
    });
  }

  if (Array.isArray(citations.case_law)) {
    citations.case_law.forEach((caseItem) => {
      // Handle both string format (from LLM) and object format
      if (typeof caseItem === 'string') {
        lines.push(`Case Law: ${caseItem.trim()}`);
      } else {
        const title = normalizeSourceLabel(caseItem.display_name || caseItem.title || caseItem.name || 'Case Law');
        const details = buildMetadataString([
          caseItem.citation ? `Citation: ${caseItem.citation}` : null,
          caseItem.court ? `Court: ${caseItem.court}` : null,
          caseItem.date ? `Date: ${caseItem.date}` : null,
          caseItem.bench ? `Bench: ${caseItem.bench}` : null,
          caseItem.holding ? `Holding: ${caseItem.holding}` : null,
          caseItem.status ? `Status: ${caseItem.status}` : null,
          caseItem.description ? `Description: ${caseItem.description}` : null
        ]);
        lines.push(details ? `Case Law: ${title} — ${details}` : `Case Law: ${title}`);
      }
    });
  }

  if (Array.isArray(citations.regulations)) {
    citations.regulations.forEach((regulation) => {
      // Handle both string format (from LLM) and object format
      if (typeof regulation === 'string') {
        lines.push(`Regulation: ${regulation.trim()}`);
      } else {
        const title = normalizeSourceLabel(regulation.name || regulation.title || regulation.display_name, 'Regulation');
        const details = buildMetadataString([
          regulation.sections ? `Sections: ${regulation.sections}` : (regulation.section ? `Section: ${regulation.section}` : null),
          regulation.description ? `Description: ${regulation.description}` : null,
          regulation.citation ? `Citation: ${regulation.citation}` : null,
          regulation.status ? `Status: ${regulation.status}` : null,
          regulation.notes ? `Notes: ${regulation.notes}` : null
        ]);
        lines.push(details ? `Regulation: ${title} — ${details}` : `Regulation: ${title}`);
      }
    });
  }

  if (Array.isArray(citations.standards)) {
    citations.standards.forEach((standard) => {
      // Handle both string format (from LLM) and object format
      if (typeof standard === 'string') {
        lines.push(`Standard: ${standard.trim()}`);
      } else {
        const title = normalizeSourceLabel(standard.name || standard.code || standard.display_name, 'Standard');
        const details = buildMetadataString([
          standard.version ? `Version: ${standard.version}` : null,
          standard.year ? `Year: ${standard.year}` : null,
          standard.clause ? `Clause: ${standard.clause}` : null,
          standard.description ? `Description: ${standard.description}` : null,
          standard.status ? `Status: ${standard.status}` : null,
          standard.notes ? `Notes: ${standard.notes}` : null
        ]);
        lines.push(details ? `Standard: ${title} — ${details}` : `Standard: ${title}`);
      }
    });
  }

  if (Array.isArray(citations.other_sources)) {
    citations.other_sources.forEach((item) => {
      const category = normalizeSourceLabel(item.category || 'Source');
      const title = normalizeSourceLabel(item.title || item.reference || item.name || category);
      const details = buildMetadataString([
        item.author ? `Author: ${item.author}` : null,
        item.publisher ? `Publisher: ${item.publisher}` : null,
        item.year ? `Year: ${item.year}` : null,
        item.pages ? `Pages: ${item.pages}` : null,
        item.reference ? `Reference: ${item.reference}` : null,
        item.notes ? `Notes: ${item.notes}` : null
      ]);
      lines.push(details ? `${category}: ${title} — ${details}` : `${category}: ${title}`);
    });
  }

  if (citations.verification && typeof citations.verification === 'string' && citations.verification.trim()) {
    lines.push(`Verification: ${citations.verification.trim()}`);
  }

  const header = normalizeSourceLabel(citations.heading || citations.title || 'SOURCES & LEGAL REFERENCES', 'SOURCES & LEGAL REFERENCES');

  console.log(`📋 [BUILD_SOURCE_LINES] Returning ${lines.length} source lines, header: "${header}"`);
  if (lines.length > 0) {
    console.log(`📋 [BUILD_SOURCE_LINES] First 3 lines:`, lines.slice(0, 3));
    console.log(`📋 [BUILD_SOURCE_LINES] Last 3 lines:`, lines.slice(-3));
  }
  console.log(`📋 [BUILD_SOURCE_LINES] Citations object keys:`, Object.keys(citations));
  console.log(`📋 [BUILD_SOURCE_LINES] citations.web exists:`, Array.isArray(citations.web), citations.web?.length);

  return {
    header,
    items: lines
  };
};

const appendStructuredAppendixSections = (sections, structuredPayload) => {
  if (!structuredPayload || typeof structuredPayload !== 'object') {
    return sections;
  }

  const mermaidBlocks = Array.isArray(structuredPayload.mermaid_blocks)
    ? structuredPayload.mermaid_blocks
    : [];

  mermaidBlocks.forEach((block) => {
    const code = typeof block?.code === 'string' ? block.code.trim() : '';
    if (!code) {
      return;
    }
    const title = normalizeSourceLabel(block.title || block.heading || '', '');
    const description = typeof block?.description === 'string' ? block.description.trim() : '';
    if (title) {
      sections.push({ type: 'header', level: 3, content: title });
    }
    if (description) {
      sections.push({ type: 'text', content: description });
    }
    sections.push({ type: 'code', language: 'mermaid', content: code });
  });

  // Append Chart.js blocks from structured payload
  const chartBlocks = Array.isArray(structuredPayload.chart_blocks)
    ? structuredPayload.chart_blocks
    : [];

  chartBlocks.forEach((block) => {
    const config = block?.chart_config || block?.config || block;
    if (!config || !config.type || !config.data) return;
    const title = block.title || block.heading || '';
    const description = typeof block?.description === 'string' ? block.description.trim() : '';
    if (title) {
      sections.push({ type: 'header', level: 3, content: title });
    }
    if (description) {
      sections.push({ type: 'text', content: description });
    }
    sections.push({ type: 'code', language: 'chartjs', content: JSON.stringify(config) });
  });

  console.log(`📊 [APPEND_STRUCTURED] structuredPayload.citations:`, structuredPayload.citations);
  console.log(`📊 [APPEND_STRUCTURED] Has web citations:`, Array.isArray(structuredPayload.citations?.web), structuredPayload.citations?.web?.length);

  const { header, items } = buildSourceLinesFromStructuredCitations(structuredPayload.citations);
  if (!items.length) {
    return sections;
  }

  const listIndex = sections.findIndex((section) => section.type === 'sources_list');
  const headerIndex = sections.findIndex((section) => section.type === 'sources_header');

  if (listIndex !== -1) {
    const merged = sections[listIndex].items || [];
    sections[listIndex] = {
      ...sections[listIndex],
      items: [...merged, ...items]
    };
    if (headerIndex !== -1 && header) {
      sections[headerIndex] = { ...sections[headerIndex], content: header };
    }
  } else {
    if (headerIndex !== -1) {
      sections[headerIndex] = {
        ...sections[headerIndex],
        content: sections[headerIndex].content || header || 'SOURCES & LEGAL REFERENCES'
      };
    } else if (items.length > 0) {
      sections.push({ type: 'sources_header', content: header || 'SOURCES & LEGAL REFERENCES' });
    }

    if (items.length > 0) {
      sections.push({ type: 'sources_list', items });
    }
  }

  return sections;
};

// Citations are now fully handled by LLM in JSON response
// The buildSourceLinesFromStructuredCitations function processes all citation types

const stripDocumentIdPrefix = (value, documentId = '') => {
  const text = typeof value === 'string' ? value.trim() : '';
  if (!text) {
    return '';
  }

  let normalized = text;
  const escapeRegExp = (input) => input.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const cleanedId = typeof documentId === 'string' ? documentId.trim() : '';

  if (cleanedId) {
    const idParts = cleanedId.split('--');
    const idForPrefix = idParts[0] ? escapeRegExp(idParts[0]) : '';
    if (idForPrefix) {
      const pattern = new RegExp(`^${idForPrefix}[-_\\s:]+`, 'i');
      const removed = normalized.replace(pattern, '');
      if (removed !== normalized) {
        normalized = removed.replace(/^[-_\s:]+/, '') || normalized;
      }
    }
  }

  const genericPatterns = [
    /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}[-_\s:]+/i,
    /^[a-z0-9]{16,}[-_\s:]+/i
  ];

  for (const pattern of genericPatterns) {
    const removed = normalized.replace(pattern, '');
    if (removed !== normalized) {
      normalized = removed.replace(/^[-_\s:]+/, '') || normalized;
    }
  }

  if (normalized && normalized.includes('_') && !normalized.includes('://')) {
    const withSpaces = normalized.replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
    if (withSpaces) {
      normalized = withSpaces;
    }
  }

  return normalized;
};

const deriveCitationUrl = (rawLabel, rawIdentifier) => {
  const candidates = [];

  const pushCandidate = (value) => {
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (trimmed) {
        candidates.push(trimmed);
      }
    }
  };

  pushCandidate(rawIdentifier);
  if (typeof rawIdentifier === 'string' && rawIdentifier.includes('--')) {
    rawIdentifier.split('--').forEach(pushCandidate);
  }
  pushCandidate(rawLabel);

  for (const candidate of candidates) {
    const markdownMatch = candidate.match(/\((https?:\/\/[^\s)]+)\)/i);
    if (markdownMatch) {
      return markdownMatch[1];
    }

    const urlMatch = candidate.match(/https?:\/\/[^\s)]+/i);
    if (urlMatch) {
      return urlMatch[0];
    }
  }

  return null;
};

const HierarchicalSourcesRenderer = ({ documentGroups, orderedItems, theme, onCollectionGroupClick, onOpenReader }) => {
  // Debug: Log props received
  console.log('🔷 HIERARCHICAL_RENDERER: Received props', {
    hasDocumentGroups: !!documentGroups,
    documentGroupsSize: documentGroups?.size,
    orderedItemsCount: orderedItems?.length,
    hasOnOpenReader: !!onOpenReader,
    onOpenReaderType: typeof onOpenReader
  });

  if (!theme) {
    theme = { isDark: false, text: '#2d3748' };
  }

  const defaultTextColor = theme.text || (theme.isDark ? '#e2e8f0' : '#2d3748');
  const metadataTextColor = theme.isDark ? '#9ca3af' : '#6b7280';
  const markdownLinkPattern = /^\[([^\]]+)\]\(([^)]+)\)$/;

  const getIndentedWebMargin = (indentLevel) => `${1 + indentLevel * 0.75}em`;
  const getIndentedMobileMargin = (indentLevel) => 16 + indentLevel * 8;

  const renderStandaloneText = (content, key, options = {}) => {
    const { isMetadata = false, indentLevel = 0 } = options;

    if (Platform.OS === 'web') {
      const marginLeft = indentLevel === 0 ? '1em' : getIndentedWebMargin(indentLevel);
      return (
        <div
          key={key}
          style={{
            marginLeft,
            marginBottom: isMetadata ? '0.2em' : '0.4em',
            color: isMetadata ? metadataTextColor : defaultTextColor,
            fontStyle: isMetadata ? 'italic' : 'normal',
            fontSize: isMetadata ? '13px' : undefined,
            lineHeight: '1.4'
          }}
          dangerouslySetInnerHTML={{ __html: `• ${inlineMarkdownToHtml(content, theme)}` }}
        />
      );
    }

    const marginLeft = getIndentedMobileMargin(indentLevel);
    const markdownMatch = content ? content.match(markdownLinkPattern) : null;

    const handleLinkPress = async (url) => {
      try {
        await Linking.openURL(url);
      } catch (error) {
        console.warn('Failed to open source link', error);
      }
    };

    return (
      <View
        key={key}
        style={{
          flexDirection: 'row',
          alignItems: 'flex-start',
          marginLeft,
          marginBottom: isMetadata ? 4 : 6
        }}
      >
        <Text style={{ color: defaultTextColor, marginRight: 8 }}>•</Text>
        {markdownMatch ? (
          <Text
            style={{
              color: '#10b981',
              textDecorationLine: 'underline',
              flex: 1,
              fontStyle: isMetadata ? 'italic' : 'normal'
            }}
            onPress={() => handleLinkPress(markdownMatch[2])}
          >
            🌐 {markdownMatch[1]}
          </Text>
        ) : (
          <Text
            style={{
              color: isMetadata ? metadataTextColor : defaultTextColor,
              flex: 1,
              fontStyle: isMetadata ? 'italic' : 'normal'
            }}
          >
            {content}
          </Text>
        )}
      </View>
    );
  };

  const renderStandaloneChunk = (item, key, indentLevel = 0) => {
    const chunkDescriptors = Array.isArray(item.descriptors) ? item.descriptors : [];

    if (Platform.OS === 'web') {
      const marginLeft = indentLevel === 0 ? '1em' : getIndentedWebMargin(indentLevel);
      return (
        <div key={key} style={{ marginLeft, marginBottom: chunkDescriptors.length ? '0.45em' : '0.3em' }}>
          <div style={{ display: 'flex', alignItems: 'center' }}>
            <span style={{ marginRight: '0.4em' }}>•</span>
            <ChunkCitationLink vectorId={item.vectorId} topic={item.topic} theme={theme} onOpenReader={onOpenReader} documentId={item.parentDocumentId}>
              {item.friendlyName}
            </ChunkCitationLink>
          </div>
          {chunkDescriptors.map((descriptor, descriptorIndex) => (
            <div
              key={`${key}-descriptor-${descriptorIndex}`}
              style={{
                marginLeft: '1.5em',
                marginTop: '0.15em',
                fontSize: descriptor.type === 'metadata' ? '0.85em' : '0.95em',
                fontStyle: descriptor.type === 'metadata' ? 'italic' : 'normal',
                color: descriptor.type === 'metadata' ? metadataTextColor : defaultTextColor
              }}
              dangerouslySetInnerHTML={{ __html: `• ${inlineMarkdownToHtml(descriptor.content, theme)}` }}
            />
          ))}
        </div>
      );
    }

    return (
      <View key={key} style={{ marginLeft: getIndentedMobileMargin(indentLevel), marginBottom: chunkDescriptors.length ? 6 : 4 }}>
        <View style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
          <Text style={{ color: defaultTextColor, marginRight: 8 }}>•</Text>
          <ChunkCitationLink vectorId={item.vectorId} topic={item.topic} theme={theme} onOpenReader={onOpenReader} documentId={item.parentDocumentId}>
            {item.friendlyName}
          </ChunkCitationLink>
        </View>
        {chunkDescriptors.map((descriptor, descriptorIndex) =>
          renderStandaloneText(
            descriptor.content,
            `${key}-descriptor-${descriptorIndex}`,
            { isMetadata: descriptor.type === 'metadata', indentLevel: indentLevel + 1 }
          )
        )}
      </View>
    );
  };

  const getDocFriendlyName = (group, docId) => {
    if (!group) {
      return docId || 'Document';
    }
    if (group.doc && group.doc.friendlyName) {
      return group.doc.friendlyName;
    }
    if (group.fallbackFriendlyName) {
      return group.fallbackFriendlyName;
    }
    if (group.chunks && group.chunks.length > 0) {
      return group.chunks[0].friendlyName;
    }
    return docId || 'Document';
  };

  const getCitationDocumentId = (group, docId) => {
    if (group && group.doc && group.doc.documentId) {
      return group.doc.documentId;
    }
    return docId;
  };

  const sortChunks = (chunks = []) => {
    return [...chunks].sort((a, b) => {
      const aIndex = typeof a.chunkIndex === 'number' ? a.chunkIndex : Number.MAX_SAFE_INTEGER;
      const bIndex = typeof b.chunkIndex === 'number' ? b.chunkIndex : Number.MAX_SAFE_INTEGER;
      if (aIndex === bIndex) {
        return (a.friendlyName || '').localeCompare(b.friendlyName || '');
      }
      return aIndex - bIndex;
    });
  };

  const renderWebCitation = (item, key, indentLevel = 0) => {
    const webDescriptors = Array.isArray(item.descriptors) ? item.descriptors : [];
    const url = item.url || '';
    // friendlyName already contains the extracted domain/path from appendCitations
    const displayName = item.friendlyName || item.topic || 'Internet Source';

    const handleLinkPress = async () => {
      if (onOpenReader) {
        console.log('🌐 Opening web citation in Reader:', url);
        onOpenReader(url, 'citation');
        return;
      }

      if (Platform.OS === 'web') {
        window.open(url, '_blank');
      } else {
        try {
          await Linking.openURL(url);
        } catch (error) {
          console.warn('Failed to open web citation', error);
        }
      }
    };

    if (Platform.OS === 'web') {
      const marginLeft = indentLevel === 0 ? '1em' : getIndentedWebMargin(indentLevel);
      return (
        <div key={key} style={{ marginLeft, marginBottom: webDescriptors.length ? '0.45em' : '0.3em' }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center' }}>
              <span style={{ marginRight: '0.4em' }}>🌐</span>
              <a
                href="#"
                onClick={(e) => {
                  e.preventDefault();
                  if (onOpenReader) {
                    onOpenReader(url, 'citation');
                  } else {
                    window.open(url, '_blank');
                  }
                }}
                style={{
                  color: theme.isDark ? '#60a5fa' : '#2563eb',
                  textDecoration: 'underline',
                  cursor: 'pointer',
                  fontWeight: '500'
                }}
              >
                {displayName}
              </a>
            </div>
            {/* Display full URL below the website name */}
            {url && (
              <div style={{ marginLeft: '1.5em', marginTop: '0.15em' }}>
                <a
                  href="#"
                  onClick={(e) => {
                    e.preventDefault();
                    if (onOpenReader) {
                      onOpenReader(url, 'citation');
                    } else {
                      window.open(url, '_blank');
                    }
                  }}
                  style={{
                    color: theme.isDark ? '#9ca3af' : '#6b7280',
                    fontSize: '0.85em',
                    textDecoration: 'none',
                    wordBreak: 'break-all'
                  }}
                >
                  {url}
                </a>
              </div>
            )}
          </div>
          {webDescriptors.map((descriptor, descriptorIndex) => (
            <div
              key={`${key}-descriptor-${descriptorIndex}`}
              style={{
                marginLeft: '1.5em',
                marginTop: '0.15em',
                fontSize: descriptor.type === 'metadata' ? '0.85em' : '0.95em',
                fontStyle: descriptor.type === 'metadata' ? 'italic' : 'normal',
                color: descriptor.type === 'metadata' ? metadataTextColor : defaultTextColor
              }}
              dangerouslySetInnerHTML={{ __html: `• ${inlineMarkdownToHtml(descriptor.content, theme)}` }}
            />
          ))}
        </div>
      );
    }

    return (
      <View key={key} style={{ marginLeft: getIndentedMobileMargin(indentLevel), marginBottom: webDescriptors.length ? 6 : 4 }}>
        <View style={{ flexDirection: 'column' }}>
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <Text style={{ color: defaultTextColor, marginRight: 8 }}>🌐</Text>
            <Text
              style={{
                color: theme.isDark ? '#60a5fa' : '#2563eb',
                textDecorationLine: 'underline',
                fontWeight: '500',
                flex: 1
              }}
              onPress={handleLinkPress}
            >
              {displayName}
            </Text>
          </View>
          {/* Display full URL below the website name */}
          {url ? (
            <Text
              style={{
                color: theme.isDark ? '#9ca3af' : '#6b7280',
                fontSize: 12,
                marginLeft: 26, // Align with text above (approx icon + margin)
                marginTop: 2
              }}
              onPress={handleLinkPress}
              numberOfLines={1}
              ellipsizeMode="middle"
            >
              {url}
            </Text>
          ) : null}
        </View>
        {webDescriptors.map((descriptor, descriptorIndex) =>
          renderStandaloneText(
            descriptor.content,
            `${key}-descriptor-${descriptorIndex}`,
            { isMetadata: descriptor.type === 'metadata', indentLevel: indentLevel + 1 }
          )
        )}
      </View>
    );
  };

  const renderCollectionCitation = (item, key, indentLevel = 0) => {
    const collectionDescriptors = Array.isArray(item.descriptors) ? item.descriptors : [];
    const uri = item.uri || '';
    const displayName = item.friendlyName || item.topic || 'Collection Document';

    if (Platform.OS === 'web') {
      const marginLeft = indentLevel === 0 ? '1em' : getIndentedWebMargin(indentLevel);
      return (
        <div key={key} style={{ marginLeft, marginBottom: collectionDescriptors.length ? '0.45em' : '0.3em' }}>
          <div style={{ display: 'flex', alignItems: 'center' }}>
            <span style={{ marginRight: '0.4em' }}>📚</span>
            <span
              style={{
                color: theme.isDark ? '#a78bfa' : '#7c3aed',
                fontWeight: '500'
              }}
            >
              {displayName}
            </span>
          </div>
          {collectionDescriptors.map((descriptor, descriptorIndex) => (
            <div
              key={`${key}-descriptor-${descriptorIndex}`}
              style={{
                marginLeft: '1.5em',
                marginTop: '0.15em',
                fontSize: descriptor.type === 'metadata' ? '0.85em' : '0.95em',
                fontStyle: descriptor.type === 'metadata' ? 'italic' : 'normal',
                color: descriptor.type === 'metadata' ? metadataTextColor : defaultTextColor
              }}
              dangerouslySetInnerHTML={{ __html: `• ${inlineMarkdownToHtml(descriptor.content, theme)}` }}
            />
          ))}
        </div>
      );
    }

    return (
      <View key={key} style={{ marginLeft: getIndentedMobileMargin(indentLevel), marginBottom: collectionDescriptors.length ? 6 : 4 }}>
        <View style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
          <Text style={{ color: defaultTextColor, marginRight: 8 }}>📚</Text>
          <Text
            style={{
              color: theme.isDark ? '#a78bfa' : '#7c3aed',
              fontWeight: '500',
              flex: 1
            }}
          >
            {displayName}
          </Text>
        </View>
        {collectionDescriptors.map((descriptor, descriptorIndex) =>
          renderStandaloneText(
            descriptor.content,
            `${key}-descriptor-${descriptorIndex}`,
            { isMetadata: descriptor.type === 'metadata', indentLevel: indentLevel + 1 }
          )
        )}
      </View>
    );
  };

  const renderCollectionReference = (item, key, indentLevel = 0) => {
    const referenceColor = theme.isDark ? '#fbbf24' : '#f59e0b';
    const name = item.name || 'Collection Reference';
    const details = item.details;

    if (Platform.OS === 'web') {
      const marginLeft = indentLevel === 0 ? '1em' : getIndentedWebMargin(indentLevel);
      return (
        <div key={key} style={{ marginLeft, marginBottom: '0.3em', lineHeight: '1.5' }}>
          <span style={{ marginRight: '0.5em' }}>⚖️</span>
          <span style={{ color: referenceColor, fontWeight: '500' }}>{name}</span>
          {details && (
            <span style={{ color: metadataTextColor, fontSize: '0.9em', marginLeft: '0.5em' }}>
              — <span dangerouslySetInnerHTML={{ __html: inlineMarkdownToHtml(details, theme) }} />
            </span>
          )}
        </div>
      );
    }

    return (
      <View key={key} style={{ marginLeft: getIndentedMobileMargin(indentLevel), marginBottom: 6 }}>
        <View style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
          <Text style={{ marginRight: 8 }}>⚖️</Text>
          <Text style={{ color: referenceColor, fontWeight: '500', flex: 1 }}>{name}</Text>
        </View>
        {details && (
          <Text style={{ color: metadataTextColor, fontSize: 13, marginLeft: 32, marginTop: 2 }}>
            — {details}
          </Text>
        )}
      </View>
    );
  };

  const renderStatuteCitation = (item, key, indentLevel = 0) => {
    const statuteName = item.name || 'Statute';
    const statuteDetails = item.details || '';
    const statuteColor = theme.isDark ? '#a78bfa' : '#7c3aed'; // Purple for statutes

    if (Platform.OS === 'web') {
      const marginLeft = indentLevel === 0 ? '1em' : getIndentedWebMargin(indentLevel);
      return (
        <div key={key} style={{ marginLeft, marginBottom: '0.3em', lineHeight: '1.5' }}>
          <span style={{ marginRight: '0.5em' }}>⚖️</span>
          <span style={{ color: statuteColor, fontWeight: '500' }}>{statuteName}</span>
          {statuteDetails && (
            <span style={{ color: metadataTextColor, fontSize: '0.9em', marginLeft: '0.5em' }}>
              — {statuteDetails}
            </span>
          )}
        </div>
      );
    }

    return (
      <View key={key} style={{ marginLeft: getIndentedMobileMargin(indentLevel), marginBottom: 6 }}>
        <View style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
          <Text style={{ marginRight: 8 }}>⚖️</Text>
          <Text style={{ color: statuteColor, fontWeight: '500', flex: 1 }}>
            {statuteName}
          </Text>
        </View>
        {statuteDetails && (
          <Text style={{ color: metadataTextColor, fontSize: 13, marginLeft: 32, marginTop: 2 }}>
            — {statuteDetails}
          </Text>
        )}
      </View>
    );
  };

  const renderCaseLawCitation = (item, key, indentLevel = 0) => {
    const caseName = item.name || 'Case Law';
    const caseDetails = item.details || '';
    const caseColor = theme.isDark ? '#f472b6' : '#ec4899'; // Pink for case law

    if (Platform.OS === 'web') {
      const marginLeft = indentLevel === 0 ? '1em' : getIndentedWebMargin(indentLevel);
      return (
        <div key={key} style={{ marginLeft, marginBottom: '0.3em', lineHeight: '1.5' }}>
          <span style={{ marginRight: '0.5em' }}>⚖️</span>
          <span style={{ color: caseColor, fontWeight: '500' }}>{caseName}</span>
          {caseDetails && (
            <span style={{ color: metadataTextColor, fontSize: '0.9em', marginLeft: '0.5em' }}>
              — {caseDetails}
            </span>
          )}
        </div>
      );
    }

    return (
      <View key={key} style={{ marginLeft: getIndentedMobileMargin(indentLevel), marginBottom: 6 }}>
        <View style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
          <Text style={{ marginRight: 8 }}>⚖️</Text>
          <Text style={{ color: caseColor, fontWeight: '500', flex: 1 }}>
            {caseName}
          </Text>
        </View>
        {caseDetails && (
          <Text style={{ color: metadataTextColor, fontSize: 13, marginLeft: 32, marginTop: 2 }}>
            — {caseDetails}
          </Text>
        )}
      </View>
    );
  };

  const renderCollectionGroup = (item, key, indentLevel = 0) => {
    const displayName = item.friendlyName || item.topic || 'Collection Citations';
    const citations = Array.isArray(item.citations) ? item.citations : [];

    const handleCollectionClick = () => {
      if (onCollectionGroupClick) {
        onCollectionGroupClick(citations);
      }
    };

    if (Platform.OS === 'web') {
      const marginLeft = indentLevel === 0 ? '1em' : getIndentedWebMargin(indentLevel);
      return (
        <div key={key} style={{ marginLeft, marginBottom: '0.3em' }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              cursor: 'pointer',
              padding: '4px 8px',
              borderRadius: '6px',
              transition: 'background-color 0.2s'
            }}
            onClick={handleCollectionClick}
            onMouseEnter={(e) => e.currentTarget.style.backgroundColor = theme.isDark ? 'rgba(167, 139, 250, 0.1)' : 'rgba(124, 58, 237, 0.05)'}
            onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'transparent'}
          >
            <span style={{ marginRight: '0.4em' }}>📚</span>
            <span
              style={{
                color: theme.isDark ? '#a78bfa' : '#7c3aed',
                fontWeight: '500',
                textDecoration: 'underline'
              }}
            >
              {displayName}
            </span>
            <span style={{
              marginLeft: '0.5em',
              fontSize: '0.85em',
              color: metadataTextColor
            }}>
              ({citations.length} document{citations.length !== 1 ? 's' : ''})
            </span>
          </div>
        </div>
      );
    }

    return (
      <View key={key} style={{ marginLeft: getIndentedMobileMargin(indentLevel), marginBottom: 4 }}>
        <TouchableOpacity
          onPress={handleCollectionClick}
          style={{
            flexDirection: 'row',
            alignItems: 'center',
            padding: 6,
            borderRadius: 6
          }}
        >
          <Text style={{ color: defaultTextColor, marginRight: 8 }}>📚</Text>
          <Text
            style={{
              color: theme.isDark ? '#a78bfa' : '#7c3aed',
              fontWeight: '500',
              textDecorationLine: 'underline',
              flex: 1
            }}
          >
            {displayName}
          </Text>
          <Text style={{
            fontSize: 12,
            color: metadataTextColor,
            marginLeft: 8
          }}>
            ({citations.length})
          </Text>
        </TouchableOpacity>
      </View>
    );
  };

  const renderDocBlockMobile = (group, docId, index) => {
    if (!group) {
      return null;
    }

    const friendlyName = getDocFriendlyName(group, docId);
    const citationDocumentId = getCitationDocumentId(group, docId);
    const chunkCount = group.chunks?.length || 0;
    const descriptors = group.descriptors || [];
    const sortedChunks = sortChunks(group.chunks);
    let fallbackCounter = 0;

    return (
      <View key={`doc-${docId}-${index}`} style={{ marginBottom: 12 }}>
        <View
          style={{
            flexDirection: 'row',
            alignItems: 'flex-start',
            marginBottom: chunkCount || descriptors.length ? 4 : 0
          }}
        >
          <Text style={{ color: defaultTextColor, marginRight: 8 }}>📄</Text>
          <CitationLink onOpenReader={onOpenReader} documentId={citationDocumentId} theme={theme}>
            {friendlyName}
          </CitationLink>
          {chunkCount > 0 && (
            <Text style={{ color: metadataTextColor, marginLeft: 8, fontSize: 12 }}>
              ({chunkCount} paragraph{chunkCount !== 1 ? 's' : ''})
            </Text>
          )}
        </View>
        {descriptors.map((descriptor, descriptorIndex) =>
          renderStandaloneText(
            descriptor.content,
            `doc-${docId}-descriptor-${descriptorIndex}`,
            { isMetadata: descriptor.type === 'metadata', indentLevel: 1 }
          )
        )}
        {sortedChunks.map((chunk) => {
          const displayIndex = typeof chunk.chunkIndex === 'number' ? chunk.chunkIndex + 1 : (++fallbackCounter);
          const chunkDescriptors = Array.isArray(chunk.descriptors) ? chunk.descriptors : [];
          return (
            <View key={`chunk-${chunk.vectorId}`} style={{ marginLeft: 24, marginBottom: chunkDescriptors.length ? 6 : 4 }}>
              <View style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
                <Text style={{ color: defaultTextColor, marginRight: 8, fontSize: 12 }}>└</Text>
                <ChunkCitationLink vectorId={chunk.vectorId} topic={chunk.topic} theme={theme} onOpenReader={onOpenReader} documentId={docId}>
                  Paragraph {displayIndex}
                </ChunkCitationLink>
              </View>
              {chunkDescriptors.map((descriptor, descriptorIndex) =>
                renderStandaloneText(
                  descriptor.content,
                  `chunk-${chunk.vectorId}-descriptor-${descriptorIndex}`,
                  { isMetadata: descriptor.type === 'metadata', indentLevel: 2 }
                )
              )}
            </View>
          );
        })}
      </View>
    );
  };

  if (Platform.OS !== 'web') {
    const items = [];

    orderedItems.forEach((item, idx) => {
      if (item.type === 'doc') {
        items.push(renderDocBlockMobile(documentGroups.get(item.documentId), item.documentId, idx));
      } else if (item.type === 'chunk') {
        items.push(renderStandaloneChunk(item, `chunk-${idx}`));
      } else if (item.type === 'web') {
        items.push(renderWebCitation(item, `web-${idx}`));
      } else if (item.type === 'collection') {
        items.push(renderCollectionCitation(item, `collection-${idx}`));
      } else if (item.type === 'collection_reference') {
        items.push(renderCollectionReference(item, `collection-ref-${idx}`));
      } else if (item.type === 'collection_group') {
        items.push(renderCollectionGroup(item, `collection-group-${idx}`));
      } else if (item.type === 'statute') {
        items.push(renderStatuteCitation(item, `statute-${idx}`));
      } else if (item.type === 'case_law') {
        items.push(renderCaseLawCitation(item, `case-law-${idx}`));
      } else {
        items.push(
          renderStandaloneText(item.content, `text-${idx}`, {
            isMetadata: item.type === 'metadata'
          })
        );
      }
    });

    return <>{items}</>;
  }

  const [expandedDocs, setExpandedDocs] = React.useState(new Set());

  const toggleDocument = (docId) => {
    setExpandedDocs((prev) => {
      const next = new Set(prev);
      if (next.has(docId)) {
        next.delete(docId);
      } else {
        next.add(docId);
      }
      return next;
    });
  };

  const renderDocBlockWeb = (group, docId) => {
    if (!group) {
      return null;
    }

    const friendlyName = getDocFriendlyName(group, docId);
    const citationDocumentId = getCitationDocumentId(group, docId);
    const chunkCount = group.chunks?.length || 0;
    const descriptors = group.descriptors || [];
    const hasChunks = chunkCount > 0;
    const hasDescriptors = descriptors.length > 0;
    const isExpanded = hasChunks ? expandedDocs.has(docId) : true;
    const sortedChunks = sortChunks(group.chunks);
    let fallbackCounter = 0;

    return (
      <div key={`doc-${docId}`} style={{ marginBottom: '12px', marginLeft: '1em' }}>
        <div
          style={{
            marginBottom: hasChunks || hasDescriptors ? '4px' : '0',
            cursor: hasChunks ? 'pointer' : 'default',
            display: 'flex',
            alignItems: 'center'
          }}
          onClick={() => hasChunks && toggleDocument(docId)}
        >
          {hasChunks && (
            <span
              style={{
                marginRight: '6px',
                fontSize: '0.85em',
                color: metadataTextColor,
                userSelect: 'none',
                transition: 'transform 0.2s ease'
              }}
            >
              {isExpanded ? '▼' : '▶'}
            </span>
          )}
          <span style={{ marginRight: '8px' }}>📄</span>
          <CitationLink onOpenReader={onOpenReader} documentId={citationDocumentId} theme={theme}>
            {friendlyName}
          </CitationLink>
          {chunkCount > 0 && (
            <span
              style={{
                marginLeft: '8px',
                fontSize: '0.85em',
                color: theme.isDark ? '#6b7280' : '#9ca3af',
                userSelect: 'none'
              }}
            >
              ({chunkCount} paragraph{chunkCount !== 1 ? 's' : ''})
            </span>
          )}
        </div>
        {(hasDescriptors && (isExpanded || !hasChunks)) && (
          <div style={{ marginLeft: '24px', paddingLeft: '12px' }}>
            {descriptors.map((descriptor, descriptorIndex) => (
              <div
                key={`doc-${docId}-descriptor-${descriptorIndex}`}
                style={{
                  marginBottom: '0.3em',
                  fontSize: descriptor.type === 'metadata' ? '13px' : '0.95em',
                  fontStyle: descriptor.type === 'metadata' ? 'italic' : 'normal',
                  color: descriptor.type === 'metadata' ? metadataTextColor : defaultTextColor
                }}
                dangerouslySetInnerHTML={{ __html: `• ${inlineMarkdownToHtml(descriptor.content, theme)}` }}
              />
            ))}
          </div>
        )}
        {isExpanded && hasChunks && (
          <div style={{ marginLeft: '24px', borderLeft: `2px solid ${theme.isDark ? '#374151' : '#e5e7eb'}`, paddingLeft: '12px' }}>
            {sortedChunks.map((chunk) => {
              const displayIndex = typeof chunk.chunkIndex === 'number' ? chunk.chunkIndex + 1 : (++fallbackCounter);
              const chunkDescriptors = Array.isArray(chunk.descriptors) ? chunk.descriptors : [];
              return (
                <div key={`chunk-${chunk.vectorId}`} style={{ marginBottom: chunkDescriptors.length ? '0.5em' : '0.35em' }}>
                  <div style={{ display: 'flex', alignItems: 'center', fontSize: '0.95em' }}>
                    <span style={{ marginRight: '6px', color: metadataTextColor }}>└</span>
                    <ChunkCitationLink vectorId={chunk.vectorId} topic={chunk.topic} theme={theme} onOpenReader={onOpenReader} documentId={docId}>
                      Paragraph {displayIndex}
                    </ChunkCitationLink>
                  </div>
                  {chunkDescriptors.map((descriptor, descriptorIndex) => (
                    <div
                      key={`chunk-${chunk.vectorId}-descriptor-${descriptorIndex}`}
                      style={{
                        marginLeft: '1.6em',
                        marginTop: '0.15em',
                        fontSize: descriptor.type === 'metadata' ? '0.85em' : '0.95em',
                        fontStyle: descriptor.type === 'metadata' ? 'italic' : 'normal',
                        color: descriptor.type === 'metadata' ? metadataTextColor : defaultTextColor
                      }}
                      dangerouslySetInnerHTML={{ __html: `• ${inlineMarkdownToHtml(descriptor.content, theme)}` }}
                    />
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  const items = [];

  orderedItems.forEach((item, idx) => {
    if (item.type === 'doc') {
      items.push(renderDocBlockWeb(documentGroups.get(item.documentId), item.documentId));
    } else if (item.type === 'chunk') {
      items.push(renderStandaloneChunk(item, `chunk-${idx}`));
    } else if (item.type === 'web') {
      items.push(renderWebCitation(item, `web-${idx}`));
    } else if (item.type === 'collection') {
      items.push(renderCollectionCitation(item, `collection-${idx}`));
    } else if (item.type === 'collection_reference') {
      items.push(renderCollectionReference(item, `collection-ref-${idx}`));
    } else if (item.type === 'collection_group') {
      items.push(renderCollectionGroup(item, `collection-group-${idx}`));
    } else if (item.type === 'statute') {
      items.push(renderStatuteCitation(item, `statute-${idx}`));
    } else if (item.type === 'case_law') {
      items.push(renderCaseLawCitation(item, `case-law-${idx}`));
    } else {
      items.push(
        renderStandaloneText(item.content, `text-${idx}`, {
          isMetadata: item.type === 'metadata'
        })
      );
    }
  });

  return <>{items}</>;
};
const CitationLink = memo(({ documentId, children, theme, onOpenReader }) => {
  if (!theme) {
    theme = { isDark: false };
  }

  // Debug: Log when CitationLink receives onOpenReader
  useEffect(() => {
    console.log('🔗 CITATION_LINK_MOUNT: CitationLink mounted/updated', {
      documentId: documentId?.substring(0, 30),
      hasOnOpenReader: !!onOpenReader,
      onOpenReaderType: typeof onOpenReader
    });
  }, [documentId, onOpenReader]);

  const [isDownloading, setIsDownloading] = useState(false);

  const trimmedDocumentId = (documentId || '').trim();
  const childAsString = typeof children === 'string' ? children : '';
  const sanitizedChildString = childAsString
    ? stripDocumentIdPrefix(childAsString, trimmedDocumentId) || childAsString
    : '';
  const labelText = sanitizedChildString || childAsString || trimmedDocumentId || 'Document';
  const derivedCitationUrl = deriveCitationUrl(childAsString, trimmedDocumentId);
  const isInternetCitation = Boolean(
    derivedCitationUrl ||
    (trimmedDocumentId && (trimmedDocumentId.startsWith('internet-') || trimmedDocumentId.startsWith('http')))
  );

  const handleCitationClick = useCallback(async (e) => {
    if (e && typeof e.preventDefault === 'function') {
      e.preventDefault();
    }

    if (isDownloading) {
      return;
    }

    // Debug: Log callback state at click time
    console.log('🖱️ CITATION_CLICK_DEBUG: Click handler invoked', {
      hasOnOpenReader: !!onOpenReader,
      onOpenReaderType: typeof onOpenReader,
      documentId: trimmedDocumentId?.substring(0, 30)
    });

    const citationUrl = deriveCitationUrl(childAsString, trimmedDocumentId);

    if (!trimmedDocumentId) {
      const message = `Citation detected: "${labelText}"\n\nThis appears to be a document reference, but no document ID was found. This might be:\n• A filename mentioned in the content\n• A reference that needs proper citation formatting\n• Content that requires additional context`;
      if (typeof alert !== 'undefined') {
        alert(message);
      }
      return;
    }

    let actualDocumentId = trimmedDocumentId;
    if (trimmedDocumentId.includes('--')) {
      const parts = trimmedDocumentId.split('--');
      actualDocumentId = parts[parts.length - 1];
      console.log('🔧 CITATION_PARSE: Extracted identifier from citation:', {
        original: trimmedDocumentId,
        extracted: actualDocumentId,
        allParts: parts,
        topicName: parts[0]
      });
    } else {
      console.log('🔧 CITATION_PARSE: Using full citation ID (no -- separator):', {
        documentId: actualDocumentId
      });
    }

    if (citationUrl) {
      try {
        if (typeof window !== 'undefined' && typeof window.open === 'function') {
          window.open(citationUrl, '_blank');
        } else {
          throw new Error('Window API not available');
        }
      } catch (error) {
        const message = `Internet Citation: "${labelText}"\n\nURL: ${citationUrl}\n\nUnable to open the link. Please copy the URL manually.`;
        if (typeof alert !== 'undefined') {
          alert(message);
        }
        console.error('Failed to open URL from topic:', error);
      }
      return;
    }

    if (actualDocumentId && actualDocumentId.startsWith('http')) {
      try {
        if (typeof window !== 'undefined' && typeof window.open === 'function') {
          window.open(actualDocumentId, '_blank');
        } else {
          throw new Error('Window API not available');
        }
      } catch (error) {
        const message = `Internet Citation: "${labelText}"\n\nURL: ${actualDocumentId}\n\nUnable to open the link. Please copy the URL manually.`;
        if (typeof alert !== 'undefined') {
          alert(message);
        }
        console.error('Failed to open URL:', error);
      }
      return;
    }

    if (actualDocumentId && actualDocumentId.startsWith('internet-')) {
      const message = `Internet Citation: "${labelText}"\n\nThis is a citation from internet search results. The original web page is not directly accessible through this citation link.\n\nInternet citations are generated from web search results and may not have permanent URLs. For the most current information, try searching for this topic directly on the web.`;
      if (typeof alert !== 'undefined') {
        alert(message);
      }
      return;
    }

    setIsDownloading(true);

    try {
      console.log(`🔗 CITATION_DOWNLOAD: Citation clicked`, {
        labelText,
        trimmedDocumentId,
        actualDocumentId,
        extractedFromFormat: trimmedDocumentId !== actualDocumentId
      });

      // Document citations should open in Reader panel (not new tab)
      if (onOpenReader && typeof onOpenReader === 'function') {
        console.log('📖 CITATION_CLICK: Opening Reader with document_id:', actualDocumentId);
        onOpenReader(actualDocumentId);
        setIsDownloading(false);
        return;
      }

      // Fallback: if no onOpenReader callback, use old behavior (open in new tab)
      console.warn('⚠️ CITATION_CLICK: No onOpenReader callback, falling back to direct download');

      const serviceBaseUrl = getCitraServiceBaseUrl();

      // Check if this is an xAI collection file (file_ prefix)
      const isXAIFile = actualDocumentId.startsWith('file_');

      let downloadUrl;
      let downloadName;

      if (isXAIFile) {
        // xAI files: Use streaming endpoint (no signed URL support)
        downloadUrl = `${serviceBaseUrl}/api/xai-files/download/${actualDocumentId}`;
        downloadName = labelText || trimmedDocumentId;

        console.log('🔮 CITATION_DOWNLOAD: xAI collection file detected, using streaming endpoint:', downloadUrl);
      } else {
        // Personal documents: Use S3 signed URL (existing behavior)
        const downloadEndpoint = `${serviceBaseUrl}/api/documents/${actualDocumentId}/download`;

        console.log('🌐 CITATION_DOWNLOAD: Personal document, requesting presigned URL from:', downloadEndpoint);

        const response = await authService.authenticatedFetchJson(downloadEndpoint);

        if (!response || !response.download_url) {
          throw new Error('Download URL not available from service');
        }

        downloadUrl = response.download_url;
        downloadName = response.filename || labelText || trimmedDocumentId;

        console.log('✅ CITATION_DOWNLOAD: Received presigned URL', {
          filename: downloadName,
          document_id: response.document_id
        });
      }

      // Open document URL in new tab - browser will handle download/display
      if (typeof window !== 'undefined' && typeof window.open === 'function') {
        window.open(downloadUrl, '_blank', 'noopener,noreferrer');
        console.log(`✅ CITATION_DOWNLOAD: Opened ${isXAIFile ? 'xAI file' : 'document'} in new tab`);
      } else {
        // Fallback for environments without window.open
        const fallbackLink = document.createElement('a');
        fallbackLink.href = downloadUrl;
        fallbackLink.target = '_blank';
        fallbackLink.rel = 'noopener noreferrer';
        fallbackLink.download = downloadName;
        document.body.appendChild(fallbackLink);
        fallbackLink.click();
        document.body.removeChild(fallbackLink);
        console.log(`✅ CITATION_DOWNLOAD: Triggered download via fallback link (${isXAIFile ? 'xAI file' : 'document'})`);
      }

      console.log(`✅ Download completed for: ${downloadName}`);
    } catch (downloadError) {
      console.error(`❌ Download failed for ${labelText}:`, downloadError);

      const errorMsg = downloadError.message && downloadError.message.includes('not found')
        ? 'Document not found or no longer available'
        : 'Failed to download document. Please try again.';

      if (typeof alert !== 'undefined') {
        alert(errorMsg);
      } else {
        console.error('Download Error:', errorMsg);
      }
    } finally {
      setIsDownloading(false);
    }
  }, [childAsString, isDownloading, labelText, trimmedDocumentId, onOpenReader]);

  if (Platform.OS !== 'web') {
    return (
      <TouchableOpacity onPress={handleCitationClick} disabled={isDownloading}>
        <Text
          style={{
            color: isInternetCitation
              ? (theme.isDark ? '#10b981' : '#059669')
              : (theme.isDark ? '#79c0ff' : '#0066cc'),
            textDecorationLine: 'underline',
            opacity: isDownloading ? 0.7 : 1,
            fontWeight: 500
          }}
        >
          {isDownloading ? 'Downloading...' : (isInternetCitation ? `🌐 ${labelText}` : labelText)}
        </Text>
      </TouchableOpacity>
    );
  }

  return (
    <a
      href="#"
      onClick={handleCitationClick}
      className="citation-link"
      style={{
        color: isInternetCitation
          ? (theme.isDark ? '#10b981' : '#059669')
          : (theme.isDark ? '#79c0ff' : '#0066cc'),
        textDecoration: 'underline',
        cursor: isDownloading ? 'not-allowed' : 'pointer',
        fontWeight: 500,
        opacity: isDownloading ? 0.7 : 1,
        pointerEvents: isDownloading ? 'none' : 'auto',
        background: isInternetCitation
          ? (theme.isDark
            ? 'linear-gradient(120deg, #064e3b 0%, #065f46 100%)'
            : 'linear-gradient(120deg, #ecfdf5 0%, #d1fae5 100%)'
          )
          : (theme.isDark
            ? 'linear-gradient(120deg, #1a365d 0%, #2d3748 100%)'
            : 'linear-gradient(120deg, #f7fafc 0%, #edf2f7 100%)'
          ),
        padding: '2px 6px',
        borderRadius: '4px',
        border: `1px solid ${isInternetCitation
          ? (theme.isDark ? '#065f46' : '#10b981')
          : (theme.isDark ? '#4a5568' : '#cbd5e0')
          }`,
        display: 'inline-block',
        margin: '1px'
      }}
      aria-label={isInternetCitation ? `Internet source: ${labelText}` : `Download ${labelText}`}
      role="link"
      title={isInternetCitation ? 'Internet search result citation' : 'Click to download document'}
    >
      {isDownloading ? 'Downloading...' : (isInternetCitation ? `🌐 ${labelText}` : labelText)}
    </a>
  );
});

// Chunk Citation Link Component with Popup Display
const ChunkCitationLink = memo(({ vectorId, topic, children, theme, onOpenReader, documentId }) => {
  // Safety check for theme
  if (!theme) {
    theme = { isDark: false }; // Default to light theme
  }

  const [isLoading, setIsLoading] = useState(false);
  const [showPopup, setShowPopup] = useState(false);
  const [chunkData, setChunkData] = useState(null);

  const labelText = typeof children === 'string' ? children : topic || vectorId || 'Chunk';

  // Resolve a usable document ID from props or by parsing the vectorId
  const resolvedDocumentId = documentId || extractDocumentIdFromVectorId(vectorId);
  const isUnknownChunk = !vectorId || vectorId === 'unknown';

  // Lock body scroll when modal is open
  useEffect(() => {
    if (showPopup && typeof document !== 'undefined') {
      const originalOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';

      return () => {
        document.body.style.overflow = originalOverflow;
      };
    }
  }, [showPopup]);

  const handleChunkClick = useCallback(async (e) => {
    if (e && typeof e.preventDefault === 'function') {
      e.preventDefault();
    }

    if (isLoading) {
      return;
    }

    // If vectorId is "unknown" (e.g. DuckDB/structured data citation), open reader directly
    if (isUnknownChunk) {
      if (resolvedDocumentId && onOpenReader && typeof onOpenReader === 'function') {
        console.log(`📖 Chunk citation "unknown" — opening reader for document: ${resolvedDocumentId}`);
        onOpenReader(resolvedDocumentId);
      } else {
        console.warn(`⚠️ Chunk citation has unknown vectorId and no documentId to open reader`);
      }
      return;
    }

    setIsLoading(true);

    try {
      console.log(`📄 Chunk citation clicked: ${labelText} (Vector ID: ${vectorId})`);

      const serviceBaseUrl = getCitraServiceBaseUrl();
      const chunkEndpoint = `${serviceBaseUrl}/api/v2/documents/chunked/chunk/${vectorId}`;

      const response = await authService.authenticatedFetchJson(chunkEndpoint);

      if (!response) {
        throw new Error('Chunk data not available from service');
      }

      setChunkData(response);
      setShowPopup(true);
      console.log('✅ Chunk data retrieved successfully');
      console.log('🔍 [DEBUG] State updated:', { showPopup: true, hasChunkData: !!response, vectorId });
    } catch (error) {
      console.error(`❌ Failed to fetch chunk ${vectorId}:`, error);

      // Fallback: if chunk fetch fails (404), try opening the reader with the document ID
      const fallbackDocId = resolvedDocumentId || (chunkData && chunkData.document_id);
      if (fallbackDocId && onOpenReader && typeof onOpenReader === 'function') {
        console.log(`📖 Chunk fetch failed — falling back to reader for document: ${fallbackDocId}`);
        onOpenReader(fallbackDocId);
      } else {
        const errorMsg = error.message && error.message.includes('not found')
          ? 'Chunk not found or no longer available'
          : 'Failed to load chunk. Please try again.';

        if (typeof alert !== 'undefined') {
          alert(errorMsg);
        }
      }
    } finally {
      setIsLoading(false);
    }
  }, [vectorId, isLoading, labelText, isUnknownChunk, resolvedDocumentId, onOpenReader]);

  const handleCopyChunk = useCallback(() => {
    if (!chunkData?.text) return;

    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      navigator.clipboard.writeText(chunkData.text).then(() => {
        console.log('✅ Chunk text copied to clipboard');
        if (typeof alert !== 'undefined') {
          alert('Chunk text copied to clipboard!');
        }
      }).catch(err => {
        console.error('❌ Failed to copy:', err);
      });
    }
  }, [chunkData]);

  const handleClosePopup = useCallback(() => {
    setShowPopup(false);
    setChunkData(null);
  }, []);

  if (Platform.OS !== 'web') {
    return (
      <TouchableOpacity onPress={handleChunkClick} disabled={isLoading}>
        <Text
          style={{
            color: theme.isDark ? '#fbbf24' : '#f59e0b',
            textDecorationLine: 'underline',
            opacity: isLoading ? 0.7 : 1,
            fontWeight: 500
          }}
        >
          {isLoading ? 'Loading...' : `📝 ${labelText}`}
        </Text>
      </TouchableOpacity>
    );
  }

  return (
    <>
      <a
        href="#"
        onClick={handleChunkClick}
        className="chunk-citation-link"
        style={{
          color: theme.isDark ? '#fbbf24' : '#f59e0b',
          textDecoration: 'underline',
          cursor: isLoading ? 'not-allowed' : 'pointer',
          fontWeight: 500,
          opacity: isLoading ? 0.7 : 1,
          pointerEvents: isLoading ? 'none' : 'auto',
          background: theme.isDark
            ? 'linear-gradient(120deg, #78350f 0%, #92400e 100%)'
            : 'linear-gradient(120deg, #fef3c7 0%, #fde68a 100%)',
          padding: '2px 6px',
          borderRadius: '4px',
          border: `1px solid ${theme.isDark ? '#92400e' : '#fbbf24'}`,
          display: 'inline-block',
          margin: '1px'
        }}
        aria-label={`View chunk: ${labelText}`}
        role="link"
        title="Click to view chunk content"
      >
        {isLoading ? 'Loading...' : `📝 ${labelText}`}
      </a>

      {/* Use React Portal to render modal outside the chat component */}
      {showPopup && chunkData && typeof document !== 'undefined' && ReactDOM.createPortal(
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.7)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 99999,
            padding: '20px'
          }}
          onClick={handleClosePopup}
        >
          <div
            style={{
              backgroundColor: theme.isDark ? '#1e293b' : '#ffffff',
              color: theme.isDark ? '#e2e8f0' : '#2d3748',
              borderRadius: '12px',
              padding: '24px',
              maxWidth: '700px',
              width: '100%',
              maxHeight: '80vh',
              overflow: 'auto',
              boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.3), 0 10px 10px -5px rgba(0, 0, 0, 0.2)',
              border: `1px solid ${theme.isDark ? '#334155' : '#e2e8f0'}`,
              position: 'relative'
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ marginBottom: '16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h3 style={{
                margin: 0,
                fontSize: '18px',
                fontWeight: 600,
                color: theme.isDark ? '#fbbf24' : '#f59e0b'
              }}>
                📝 Chunk Content
              </h3>
              <button
                onClick={handleClosePopup}
                style={{
                  background: 'transparent',
                  border: 'none',
                  fontSize: '24px',
                  cursor: 'pointer',
                  color: theme.isDark ? '#94a3b8' : '#64748b',
                  padding: '0 8px'
                }}
                aria-label="Close"
              >
                ×
              </button>
            </div>

            <div style={{
              marginBottom: '12px',
              fontSize: '13px',
              color: theme.isDark ? '#94a3b8' : '#64748b',
              borderBottom: `1px solid ${theme.isDark ? '#334155' : '#e2e8f0'}`,
              paddingBottom: '12px'
            }}>
              {chunkData.topic_or_filename && (
                <div><strong>File:</strong> {chunkData.topic_or_filename}</div>
              )}
              {chunkData.file_type && (
                <div><strong>Type:</strong> {chunkData.file_type}</div>
              )}
            </div>

            <div
              style={{
                backgroundColor: theme.isDark ? '#0f172a' : '#f8fafc',
                padding: '16px',
                borderRadius: '8px',
                marginBottom: '16px',
                maxHeight: '400px',
                overflow: 'auto',
                fontSize: '14px',
                lineHeight: '1.6',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                border: `1px solid ${theme.isDark ? '#1e293b' : '#e2e8f0'}`
              }}
            >
              {chunkData.text}
            </div>

            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
              <button
                onClick={handleCopyChunk}
                style={{
                  backgroundColor: theme.isDark ? '#3b82f6' : '#2563eb',
                  color: '#ffffff',
                  border: 'none',
                  padding: '8px 16px',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  fontSize: '14px',
                  fontWeight: 500
                }}
              >
                📋 Copy
              </button>
              <button
                onClick={handleClosePopup}
                style={{
                  backgroundColor: theme.isDark ? '#475569' : '#cbd5e0',
                  color: theme.isDark ? '#e2e8f0' : '#2d3748',
                  border: 'none',
                  padding: '8px 16px',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  fontSize: '14px',
                  fontWeight: 500
                }}
              >
                Close
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </>
  );
});

// Helper function to extract document ID from chunk vector ID
const extractDocumentIdFromVectorId = (vectorId) => {
  if (!vectorId || typeof vectorId !== 'string') return null;

  // Vector ID format: {document_id}_chunk_{index} (new) or {document_id}chunk{index} (legacy)
  // Document ID can be:
  // 1. UUID: "c7622b79-6543-4fb4-8ece-a850cbf56834_chunk_0000"
  // 2. Filename: "TW-presentation-new (1) (5) (1) (2) (1) (4)_chunk_0000"

  // Try to match UUID format first
  const uuidMatch = vectorId.match(/^([a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})(?:_chunk_|chunk)/);
  if (uuidMatch) {
    return uuidMatch[1];
  }

  // Try to match filename format: everything before _chunk_ or chunk
  const filenameMatch = vectorId.match(/^(.+?)(?:_chunk_|chunk)\d+$/);
  if (filenameMatch) {
    return filenameMatch[1];
  }

  return null;
};

// Helper function to extract chunk index from vector ID
const extractChunkIndexFromVectorId = (vectorId) => {
  if (!vectorId || typeof vectorId !== 'string') return null;

  // Extract chunk number from vector ID (supports both new and legacy formats)
  // New format: {document_id}_chunk_{index}
  // Legacy format: {document_id}chunk{index}
  const match = vectorId.match(/(?:_chunk_|chunk)(\d+)$/);
  return match ? parseInt(match[1], 10) : null;
};

// Updated regex to capture IDs with spaces, parentheses, and special characters
// Pattern: TYPE::topic--identifier [optional trailing metadata starting with Description:|Date:|etc]
// The identifier group (.*?) captures everything including spaces until we hit optional metadata
const SOURCE_MARKER_REGEX = /^(DOC|CHUNK|WEB|COLLECTION|COLLECTION_GROUP)::(.+?)--(.*?)(?:\s+(Description:|Date:|Relevance:|Source:|Note:|Retrieved from:|Document:|Paragraph:).+)?$/is;

const toTrimmedString = (value) => (typeof value === 'string' ? value.trim() : '');

const extractSourceMarkerInfo = (rawLine) => {
  if (!rawLine || typeof rawLine !== 'string') {
    return null;
  }

  const trimmed = rawLine.trim();
  if (!trimmed) {
    return null;
  }

  const withoutBullets = trimmed.replace(/^[-•*]\s*/, '');
  const numberMatch = withoutBullets.match(/^(\d+)[\s\.)\-:]+/);
  const numberPrefix = numberMatch ? numberMatch[1] : null;
  const withoutNumbering = withoutBullets.replace(/^(\d+)[\s\.)\-:]+/, '').trim();
  const effectiveLine = withoutNumbering || withoutBullets.trim();

  const match = effectiveLine.match(SOURCE_MARKER_REGEX);
  if (!match) {
    return null;
  }

  // Extract capture groups: [fullMatch, type, topic, identifier, metadataPrefix, ...]
  const [, type, topic, rawIdentifier, metadataPrefix] = match;
  const trimmedIdentifier = toTrimmedString(rawIdentifier);
  const looksLikeStructuredData = trimmedIdentifier.startsWith('[') || trimmedIdentifier.startsWith('{');
  const identifier = looksLikeStructuredData
    ? trimmedIdentifier
    : trimmedIdentifier.replace(/[\]\)\.,;:]+$/, '').trim();

  // Extract trailing text (metadata) - if metadataPrefix exists, reconstruct from the original line
  let trailingText = '';
  if (metadataPrefix) {
    // Find where metadata starts in the original line
    const metadataStart = effectiveLine.indexOf(metadataPrefix);
    if (metadataStart !== -1) {
      trailingText = effectiveLine.substring(metadataStart).trim();
    }
  }

  if (!identifier) {
    return null;
  }

  // Debug log for citation parsing
  if (isCitationDebugEnabled()) {
    console.log(`🔍 [CITATION_PARSE] Parsed ${type}:`, {
      type,
      topic: topic.substring(0, 50),
      identifier: identifier.substring(0, 100),
      identifierLength: identifier.length,
      hasSpaces: identifier.includes(' '),
      trailingText: trailingText ? trailingText.substring(0, 50) : 'none'
    });
  }

  return {
    type: type.toUpperCase(),
    topic: toTrimmedString(topic),
    identifier,
    effectiveLine,
    numberPrefix,
    trailingText
  };
};

// Function to parse sources and group chunks under their documents
const parseSourcesHierarchical = (sourceLines, theme) => {
  if (!sourceLines || sourceLines.length === 0) {
    return {
      documentGroups: new Map(),
      orderedItems: []
    };
  }

  // Data structure: Map<documentId, { doc: docInfo, chunks: chunkInfo[], descriptors: descriptorInfo[] }>
  const documentGroups = new Map();
  const orderedItems = [];
  const orderedDocIds = new Set();
  let lastDocumentId = null;

  const pushDocToOrder = (documentId) => {
    if (!documentId) {
      return;
    }
    lastDocumentId = documentId;
    if (orderedDocIds.has(documentId)) {
      return;
    }
    orderedDocIds.add(documentId);
    orderedItems.push({ type: 'doc', documentId });
  };

  const metadataPattern = /^(Description|Date|Relevance|Retrieved from|Source|Note|Court|Citation|Holding|Status|Key Provision|Reference|Chapter|Pages):/i;

  const createDescriptor = (rawValue) => {
    if (!rawValue || typeof rawValue !== 'string') {
      return null;
    }

    const trimmed = rawValue.trim();
    if (!trimmed) {
      return null;
    }

    const withoutLeadingPunctuation = trimmed.replace(/^[-–—\s]+/, '').trim() || trimmed;
    if (!withoutLeadingPunctuation) {
      return null;
    }

    return {
      type: metadataPattern.test(withoutLeadingPunctuation) ? 'metadata' : 'text',
      content: withoutLeadingPunctuation
    };
  };

  sourceLines.forEach((line) => {
    const trimmedLine = typeof line === 'string' ? line.trim() : '';
    if (!trimmedLine) {
      return;
    }

    const normalizedLine = trimmedLine.replace(/^[-•*]\s*/, '');
    const collectionReferenceMatch = normalizedLine.match(/^(?:\d+[\s\.)\-:]+)?COLLECTION::(.+?)(?:::(.+))?$/i);
    if (collectionReferenceMatch) {
      const [, referenceName, referenceDetails] = collectionReferenceMatch;
      console.log(`🟠 [PARSE_HIERARCHICAL] Collection reference detected:`, normalizedLine.substring(0, 120));
      orderedItems.push({
        type: 'collection_reference',
        name: toTrimmedString(referenceName) || 'Collection Reference',
        details: referenceDetails ? referenceDetails.trim() : null
      });
      lastDocumentId = null;
      return;
    }

    const markerInfo = extractSourceMarkerInfo(trimmedLine);

    if (markerInfo) {
      const { type, topic, identifier, trailingText } = markerInfo;
      const friendlyName = stripDocumentIdPrefix(topic, identifier);

      if (type === 'DOC') {
        const documentId = identifier;
        if (!documentGroups.has(documentId)) {
          documentGroups.set(documentId, {
            doc: { documentId, topic, friendlyName: friendlyName || topic.trim() },
            chunks: [],
            descriptors: [],
            fallbackFriendlyName: friendlyName || topic.trim()
          });
        } else {
          const existing = documentGroups.get(documentId);
          if (!existing.doc) {
            existing.doc = { documentId, topic, friendlyName: friendlyName || topic.trim() };
          }
          if (!existing.fallbackFriendlyName && (friendlyName || topic)) {
            existing.fallbackFriendlyName = friendlyName || topic.trim();
          }
          if (!Array.isArray(existing.descriptors)) {
            existing.descriptors = [];
          }
        }
        pushDocToOrder(documentId);

        const descriptor = createDescriptor(trailingText);
        if (descriptor) {
          const group = documentGroups.get(documentId);
          if (!Array.isArray(group.descriptors)) {
            group.descriptors = [];
          }
          group.descriptors.push(descriptor);
        }
      } else if (type === 'CHUNK') {
        const vectorId = identifier;
        const documentId = extractDocumentIdFromVectorId(vectorId) || lastDocumentId;
        const chunkIndex = extractChunkIndexFromVectorId(vectorId);

        console.log(`📌 [PARSE_CHUNK] Detected CHUNK type:`, {
          vectorId: vectorId.substring(0, 80),
          extractedDocId: documentId,
          usedLastDocId: !extractDocumentIdFromVectorId(vectorId) && !!lastDocumentId,
          topic: topic.substring(0, 50),
          friendlyName: (friendlyName || topic.trim()).substring(0, 50)
        });

        if (documentId) {
          if (!documentGroups.has(documentId)) {
            console.log(`📌 [PARSE_CHUNK] Creating new document group for:`, documentId);
            documentGroups.set(documentId, {
              doc: null,
              chunks: [],
              descriptors: [],
              fallbackFriendlyName: friendlyName || topic.trim()
            });
          }
          const group = documentGroups.get(documentId);
          const descriptor = createDescriptor(trailingText);
          const chunkEntry = {
            vectorId,
            topic,
            friendlyName: friendlyName || topic.trim(),
            chunkIndex
          };

          if (descriptor) {
            chunkEntry.descriptors = [descriptor];
          }

          group.chunks.push(chunkEntry);
          if (!group.fallbackFriendlyName && (friendlyName || topic)) {
            group.fallbackFriendlyName = friendlyName || topic.trim();
          }
          pushDocToOrder(documentId);
        } else {
          const descriptor = createDescriptor(trailingText);
          const chunkItem = {
            type: 'chunk',
            vectorId,
            topic,
            friendlyName: friendlyName || topic.trim(),
            parentDocumentId: null
          };

          if (descriptor) {
            chunkItem.descriptors = [descriptor];
          }

          orderedItems.push(chunkItem);
          lastDocumentId = null;
        }
      } else if (type === 'WEB') {
        // Handle internet/web citations from LLM
        // Identifier format: "Title--URL" or just "URL" (backward compatibility)
        let url = identifier;
        let displayName = friendlyName || topic.trim();

        // Check if identifier follows the Title--URL format we defined
        if (identifier.includes('--')) {
          const parts = identifier.split('--');
          // Last part is the URL, everything before is the title
          const potentialUrl = parts.pop().trim();
          const potentialTitle = parts.join('--').trim();

          if (potentialUrl && (potentialUrl.startsWith('http') || potentialUrl.startsWith('www'))) {
            url = potentialUrl;
            displayName = potentialTitle;
          }
        }

        const descriptor = createDescriptor(trailingText);
        const webItem = {
          type: 'web',
          url,
          topic: topic || `Internet Source`,
          friendlyName: displayName || 'Internet Source'
        };

        if (descriptor) {
          webItem.descriptors = [descriptor];
        }

        console.log(`🌐 [PARSE_HIERARCHICAL] Created web item:`, webItem);
        orderedItems.push(webItem);
        lastDocumentId = null;
      }
    } else {
      const cleaned = line && typeof line === 'string'
        ? line.trim().replace(/^[-•*]\s*/, '').replace(/^\d+[\s\.)\-:]+/, '').trim()
        : '';
      if (cleaned) {
        // Check for Statute citations (from collection)
        const statuteMatch = cleaned.match(/^Statute:\s*(.+?)(?:\s+[-–—]\s+(.+))?$/i);
        if (statuteMatch) {
          const [, statuteName, statuteDetails] = statuteMatch;
          orderedItems.push({
            type: 'statute',
            name: statuteName.trim(),
            details: statuteDetails ? statuteDetails.trim() : null,
            content: cleaned
          });
          lastDocumentId = null;
          return;
        }

        // Check for Case Law citations (from collection)
        const caseLawMatch = cleaned.match(/^Case Law:\s*(.+?)(?:\s+[-–—]\s+(.+))?$/i);
        if (caseLawMatch) {
          const [, caseName, caseDetails] = caseLawMatch;
          orderedItems.push({
            type: 'case_law',
            name: caseName.trim(),
            details: caseDetails ? caseDetails.trim() : null,
            content: cleaned
          });
          lastDocumentId = null;
          return;
        }

        if (metadataPattern.test(cleaned) && lastDocumentId && documentGroups.has(lastDocumentId)) {
          const group = documentGroups.get(lastDocumentId);
          if (!Array.isArray(group.descriptors)) {
            group.descriptors = [];
          }
          group.descriptors.push({ type: 'metadata', content: cleaned });
        } else {
          orderedItems.push({
            type: metadataPattern.test(cleaned) ? 'metadata' : 'text',
            content: cleaned
          });
          if (!metadataPattern.test(cleaned)) {
            lastDocumentId = null;
          }
        }
      }
    }
  });

  return { documentGroups, orderedItems };
};

// Function to parse a single source line and return appropriate component for both web and mobile
const parseSourceLine = (line, theme, index, isUserMessage = false, onOpenReader = null) => {
  const trimmedLine = line.trim();
  if (!trimmedLine) return null;
  const metadataPattern = /^(Description|Date|Relevance|Retrieved from|Source|Note|Court|Citation|Holding|Status|Key Provision|Reference|Chapter|Pages):/i;
  const metadataColor = theme.isDark ? '#9ca3af' : '#6b7280';
  const normalizeDescriptor = (value) => {
    if (!value || typeof value !== 'string') {
      return '';
    }
    const trimmed = value.trim();
    if (!trimmed) {
      return '';
    }
    return trimmed.replace(/^[-–—\s]+/, '').trim() || trimmed;
  };

  // Check for section headers (e.g., **USER'S PERSONAL DOCUMENTS (FROM VECTOR DB/RAG CONTEXT):**)
  if (trimmedLine.match(/^\*\*[A-Z\s'()\/]+:\*\*$/)) {
    const headerText = trimmedLine.replace(/^\*\*|\*\*$/g, '');
    if (Platform.OS === 'web') {
      return (
        <div key={index} style={{
          marginTop: '1.5em',
          marginBottom: '0.5em',
          fontWeight: '600',
          fontSize: '14px',
          color: theme.isDark ? '#fbbf24' : '#f59e0b',
          textTransform: 'uppercase',
          letterSpacing: '0.5px'
        }}>
          {headerText}
        </div>
      );
    } else {
      return (
        <View key={index} style={{ marginTop: 16, marginBottom: 8 }}>
          <Text style={{
            fontWeight: '600',
            fontSize: 14,
            color: theme.isDark ? '#fbbf24' : '#f59e0b',
            textTransform: 'uppercase',
            letterSpacing: 0.5
          }}>
            {headerText}
          </Text>
        </View>
      );
    }
  }

  const markerInfo = extractSourceMarkerInfo(trimmedLine);
  if (markerInfo) {
    const { type, topic, identifier, numberPrefix, trailingText } = markerInfo;
    const friendlyName = stripDocumentIdPrefix(topic, identifier);
    const prefixText = numberPrefix ? `${numberPrefix}.` : '•';
    const descriptorContent = normalizeDescriptor(trailingText);
    const descriptorIsMetadata = descriptorContent ? metadataPattern.test(descriptorContent) : false;

    if (type === 'CHUNK') {
      if (Platform.OS === 'web') {
        return (
          <div key={index} style={{ marginLeft: '1em', marginBottom: descriptorContent ? '0.45em' : numberPrefix ? '0.3em' : '0.2em' }}>
            <div style={{ display: 'flex', alignItems: 'center' }}>
              <span style={{ marginRight: '0.5em' }}>{prefixText}</span>
              <ChunkCitationLink vectorId={identifier} topic={topic} theme={theme} onOpenReader={onOpenReader}>
                {friendlyName || topic.trim()}
              </ChunkCitationLink>
            </div>
            {descriptorContent && (
              <div
                style={{
                  marginLeft: '1.6em',
                  marginTop: '0.15em',
                  fontSize: descriptorIsMetadata ? '0.85em' : '0.95em',
                  fontStyle: descriptorIsMetadata ? 'italic' : 'normal',
                  color: descriptorIsMetadata ? metadataColor : theme.text
                }}
                dangerouslySetInnerHTML={{ __html: `• ${inlineMarkdownToHtml(descriptorContent, theme)}` }}
              />
            )}
          </div>
        );
      }
      return (
        <View key={index} style={{ marginLeft: 16, marginBottom: descriptorContent ? 6 : numberPrefix ? 6 : 4 }}>
          <View style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
            <Text style={{ color: theme.text, marginRight: 8 }}>{prefixText}</Text>
            <ChunkCitationLink vectorId={identifier} topic={topic} theme={theme} onOpenReader={onOpenReader}>
              {friendlyName || topic.trim()}
            </ChunkCitationLink>
          </View>
          {descriptorContent && (
            <View style={{ flexDirection: 'row', alignItems: 'flex-start', marginLeft: 24, marginTop: 2 }}>
              <Text style={{ color: theme.text, marginRight: 8 }}>•</Text>
              <Text
                style={{
                  color: descriptorIsMetadata ? metadataColor : theme.text,
                  fontStyle: descriptorIsMetadata ? 'italic' : 'normal',
                  flex: 1
                }}
              >
                {descriptorContent}
              </Text>
            </View>
          )}
        </View>
      );
    }

    if (type === 'DOC') {
      if (Platform.OS === 'web') {
        return (
          <div key={index} style={{ marginLeft: '1em', marginBottom: descriptorContent ? '0.5em' : numberPrefix ? '0.3em' : '0.2em' }}>
            <div style={{ display: 'flex', alignItems: 'center' }}>
              <span style={{ marginRight: '0.5em' }}>{prefixText}</span>
              <CitationLink onOpenReader={onOpenReader} documentId={identifier} theme={theme}>
                {friendlyName || topic.trim()}
              </CitationLink>
            </div>
            {descriptorContent && (
              <div
                style={{
                  marginLeft: '1.6em',
                  marginTop: '0.15em',
                  fontSize: descriptorIsMetadata ? '0.85em' : '0.95em',
                  fontStyle: descriptorIsMetadata ? 'italic' : 'normal',
                  color: descriptorIsMetadata ? metadataColor : theme.text
                }}
                dangerouslySetInnerHTML={{ __html: `• ${inlineMarkdownToHtml(descriptorContent, theme)}` }}
              />
            )}
          </div>
        );
      }
      return (
        <View key={index} style={{ marginLeft: 16, marginBottom: descriptorContent ? 6 : numberPrefix ? 6 : 4 }}>
          <View style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
            <Text style={{ color: theme.text, marginRight: 8 }}>{prefixText}</Text>
            <CitationLink onOpenReader={onOpenReader} documentId={identifier} theme={theme}>
              {friendlyName || topic.trim()}
            </CitationLink>
          </View>
          {descriptorContent && (
            <View style={{ flexDirection: 'row', alignItems: 'flex-start', marginLeft: 24, marginTop: 2 }}>
              <Text style={{ color: theme.text, marginRight: 8 }}>•</Text>
              <Text
                style={{
                  color: descriptorIsMetadata ? metadataColor : theme.text,
                  fontStyle: descriptorIsMetadata ? 'italic' : 'normal',
                  flex: 1
                }}
              >
                {descriptorContent}
              </Text>
            </View>
          )}
        </View>
      );
    }
  }

  const cleanLine = trimmedLine
    .replace(/^[-•*]\s*/, '')
    .replace(/^(\d+)[\s\.)\-:]+/, '')
    .trim();

  // Check for metadata lines (Description, Date, Relevance, etc.)
  if (metadataPattern.test(cleanLine)) {
    if (Platform.OS === 'web') {
      return (
        <div key={index} style={{
          marginLeft: '2.5em',
          fontSize: '13px',
          color: metadataColor,
          fontStyle: 'italic',
          marginBottom: '0.2em',
          lineHeight: '1.4'
        }}
          dangerouslySetInnerHTML={{ __html: inlineMarkdownToHtml(cleanLine, theme) }}
        />
      );
    } else {
      return (
        <View key={index} style={{ marginLeft: 40, marginBottom: 4 }}>
          <Text style={{
            fontSize: 13,
            color: metadataColor,
            fontStyle: 'italic',
            lineHeight: 18
          }}>
            {cleanLine}
          </Text>
        </View>
      );
    }
  }

  // Check for statute citations (e.g., "Statute: Motor Vehicles Act, 1988 - S.2(30)")
  const statuteMatch = cleanLine.match(/^Statute:\s*(.+?)(?:\s+[-–—]\s+(.+))?$/i);
  if (statuteMatch) {
    const [, statuteName, statuteDetails] = statuteMatch;
    const statuteColor = theme.isDark ? '#a78bfa' : '#7c3aed'; // Purple for statutes

    if (Platform.OS === 'web') {
      return (
        <div key={index} style={{ marginLeft: '1em', marginBottom: '0.3em', lineHeight: '1.5' }}>
          <span style={{ marginRight: '0.5em' }}>⚖️</span>
          <span style={{ color: statuteColor, fontWeight: '500' }}>{statuteName.trim()}</span>
          {statuteDetails && (
            <span style={{ color: metadataColor, fontSize: '0.9em', marginLeft: '0.5em' }}>
              — <span dangerouslySetInnerHTML={{ __html: inlineMarkdownToHtml(statuteDetails.trim(), theme) }} />
            </span>
          )}
        </div>
      );
    } else {
      return (
        <View key={index} style={{ marginLeft: 16, marginBottom: 6 }}>
          <View style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
            <Text style={{ marginRight: 8 }}>⚖️</Text>
            <Text style={{ color: statuteColor, fontWeight: '500', flex: 1 }}>
              {statuteName.trim()}
            </Text>
          </View>
          {statuteDetails && (
            <Text style={{ color: metadataColor, fontSize: 13, marginLeft: 32, marginTop: 2 }}>
              — {statuteDetails.trim()}
            </Text>
          )}
        </View>
      );
    }
  }

  // Check for case law citations (e.g., "Case Law: Karikho Kri v. Nuney Tayang, [2024] 4 S.C.R. 394")
  const caseLawMatch = cleanLine.match(/^Case Law:\s*(.+?)(?:\s+[-–—]\s+(.+))?$/i);
  if (caseLawMatch) {
    const [, caseName, caseDetails] = caseLawMatch;
    const caseColor = theme.isDark ? '#f472b6' : '#ec4899'; // Pink for case law

    if (Platform.OS === 'web') {
      return (
        <div key={index} style={{ marginLeft: '1em', marginBottom: '0.3em', lineHeight: '1.5' }}>
          <span style={{ marginRight: '0.5em' }}>⚖️</span>
          <span style={{ color: caseColor, fontWeight: '500' }}>{caseName.trim()}</span>
          {caseDetails && (
            <span style={{ color: metadataColor, fontSize: '0.9em', marginLeft: '0.5em' }}>
              — <span dangerouslySetInnerHTML={{ __html: inlineMarkdownToHtml(caseDetails.trim(), theme) }} />
            </span>
          )}
        </div>
      );
    } else {
      return (
        <View key={index} style={{ marginLeft: 16, marginBottom: 6 }}>
          <View style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
            <Text style={{ marginRight: 8 }}>⚖️</Text>
            <Text style={{ color: caseColor, fontWeight: '500', flex: 1 }}>
              {caseName.trim()}
            </Text>
          </View>
          {caseDetails && (
            <Text style={{ color: metadataColor, fontSize: 13, marginLeft: 32, marginTop: 2 }}>
              — {caseDetails.trim()}
            </Text>
          )}
        </View>
      );
    }
  }

  // Check for numbered items without DOC::/CHUNK:: (e.g., statutes, cases, books)
  const numberedTextMatch = cleanLine.match(/^(\d+)\.\s+(.+)$/);
  if (numberedTextMatch) {
    const [, number, text] = numberedTextMatch;

    if (Platform.OS === 'web') {
      return (
        <div key={index} style={{ marginLeft: '1em', marginBottom: '0.3em', lineHeight: '1.5' }}>
          <span style={{ fontWeight: '500', marginRight: '0.5em' }}>{number}.</span>
          <span dangerouslySetInnerHTML={{ __html: inlineMarkdownToHtml(text, theme) }} />
        </div>
      );
    } else {
      return (
        <View key={index} style={{ flexDirection: 'row', alignItems: 'flex-start', marginLeft: 16, marginBottom: 6 }}>
          <Text style={{ color: theme.text, marginRight: 8, fontWeight: '500' }}>{number}.</Text>
          <Text style={{ color: theme.text, flex: 1, lineHeight: 20 }}>{text}</Text>
        </View>
      );
    }
  }

  // Check for new citation format with markers: DOC::topic--document_id or CHUNK::topic--vector_id
  const markerMatch = cleanLine.match(SOURCE_MARKER_REGEX);

  if (markerMatch) {
    const [, citationType, topic, identifier] = markerMatch;
    const friendlyName = stripDocumentIdPrefix(topic, identifier);

    if (citationType === 'CHUNK') {
      // Render chunk citation with ChunkCitationLink
      if (Platform.OS === 'web') {
        return (
          <div key={index} style={{ marginLeft: '1em', marginBottom: '0.2em' }}>
            • <ChunkCitationLink vectorId={identifier} topic={topic} theme={theme} onOpenReader={onOpenReader}>
              {friendlyName || topic.trim()}
            </ChunkCitationLink>
          </div>
        );
      } else {
        return (
          <View key={index} style={{ flexDirection: 'row', alignItems: 'flex-start', marginLeft: 16, marginBottom: 4 }}>
            <Text style={{ color: theme.text, marginRight: 8 }}>•</Text>
            <ChunkCitationLink vectorId={identifier} topic={topic} theme={theme} onOpenReader={onOpenReader}>
              {friendlyName || topic.trim()}
            </ChunkCitationLink>
          </View>
        );
      }
    } else if (citationType === 'DOC') {
      // Render document citation with CitationLink - use identifier only
      if (Platform.OS === 'web') {
        return (
          <div key={index} style={{ marginLeft: '1em', marginBottom: '0.2em' }}>
            • <CitationLink onOpenReader={onOpenReader} documentId={identifier} theme={theme}>
              {friendlyName || topic.trim()}
            </CitationLink>
          </div>
        );
      } else {
        return (
          <View key={index} style={{ flexDirection: 'row', alignItems: 'flex-start', marginLeft: 16, marginBottom: 4 }}>
            <Text style={{ color: theme.text, marginRight: 8 }}>•</Text>
            <CitationLink onOpenReader={onOpenReader} documentId={identifier} theme={theme}>
              {friendlyName || topic.trim()}
            </CitationLink>
          </View>
        );
      }
    } else if (citationType === 'WEB') {
      // Render web citation with clickable link
      const url = identifier;
      const displayName = friendlyName || topic.trim();

      if (Platform.OS === 'web') {
        return (
          <div key={index} style={{ marginLeft: '1em', marginBottom: '0.2em', display: 'flex', alignItems: 'center' }}>
            <span style={{ marginRight: '0.4em' }}>🌐</span>
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                color: theme.isDark ? '#60a5fa' : '#2563eb',
                textDecoration: 'underline',
                cursor: 'pointer'
              }}
            >
              {displayName}
            </a>
          </div>
        );
      } else {
        const handleLinkPress = async () => {
          try {
            await Linking.openURL(url);
          } catch (error) {
            console.warn('Failed to open web citation', error);
          }
        };

        return (
          <View key={index} style={{ flexDirection: 'row', alignItems: 'flex-start', marginLeft: 16, marginBottom: 4 }}>
            <Text style={{ color: theme.text, marginRight: 8 }}>🌐</Text>
            <Text
              style={{
                color: theme.isDark ? '#60a5fa' : '#2563eb',
                textDecorationLine: 'underline',
                flex: 1
              }}
              onPress={handleLinkPress}
            >
              {displayName}
            </Text>
          </View>
        );
      }
    }
  }

  // Check if this is JUST a document ID (number--uuid format) - LEGACY FORMAT
  // This handles SOURCES section entries like: 12345--f3ae9bbc-9c56-4454-8472-a83e48f8fc4b
  const justDocIdMatch = cleanLine.match(/^(\d+)--((?:[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})|(?:[a-fA-F0-9]{24})|(?:internet-\d{2}-[a-fA-F0-9]{8}))$/);

  if (justDocIdMatch) {
    const [, displayNumber, fullDocId] = justDocIdMatch;
    const documentId = `${displayNumber}--${fullDocId}`;

    if (Platform.OS === 'web') {
      return (
        <div key={index} style={{ marginLeft: '1em', marginBottom: '0.2em' }}>
          • <CitationLink onOpenReader={onOpenReader} documentId={documentId} theme={theme}>
            {displayNumber}
          </CitationLink>
        </div>
      );
    } else {
      // For mobile platforms
      return (
        <View key={index} style={{ flexDirection: 'row', alignItems: 'flex-start', marginLeft: 16, marginBottom: 4 }}>
          <Text style={{ color: theme.text, marginRight: 8 }}>•</Text>
          <CitationLink onOpenReader={onOpenReader} documentId={documentId} theme={theme}>
            {displayNumber}
          </CitationLink>
        </View>
      );
    }
  }

  // Check for DocumentName--DocumentID or DocumentName--URL pattern - LEGACY FORMAT
  // Supports MongoDB ObjectIds, UUIDs, internet citations, and URLs
  const documentMatch = cleanLine.match(/^(.+?)--((?:[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})|(?:[a-fA-F0-9]{24})|(?:internet-\d{2}-[a-fA-F0-9]{8})|(?:https?:\/\/[^\s]+))$/);

  if (documentMatch) {
    const [, documentName, documentId] = documentMatch;
    const friendlyName = stripDocumentIdPrefix(documentName, documentId);
    if (Platform.OS === 'web') {
      return (
        <div key={index} style={{ marginLeft: '1em', marginBottom: '0.2em' }}>
          • <CitationLink onOpenReader={onOpenReader} documentId={documentId} theme={theme}>
            {friendlyName || documentName.trim()}
          </CitationLink>
        </div>
      );
    } else {
      // For mobile platforms
      return (
        <View key={index} style={{ flexDirection: 'row', alignItems: 'flex-start', marginLeft: 16, marginBottom: 4 }}>
          <Text style={{ color: theme.text, marginRight: 8 }}>•</Text>
          <CitationLink onOpenReader={onOpenReader} documentId={documentId} theme={theme}>
            {friendlyName || documentName.trim()}
          </CitationLink>
        </View>
      );
    }
  } else {
    // Regular source without download capability - could be markdown links from LLM
    const friendlyLine = stripDocumentIdPrefix(cleanLine);

    if (Platform.OS === 'web') {
      // Parse markdown links and convert to HTML
      return (
        <div
          key={index}
          style={{ marginLeft: '1em', marginBottom: '0.2em', color: theme.text }}
          dangerouslySetInnerHTML={{ __html: `• ${inlineMarkdownToHtml(friendlyLine, theme)}` }}
        />
      );
    } else {
      return (
        <View key={index} style={{ flexDirection: 'row', alignItems: 'flex-start', marginLeft: 16, marginBottom: 4 }}>
          <Text style={{ color: theme.text, marginRight: 8 }}>•</Text>
          <Text style={{ color: theme.text, flex: 1 }}>
            {friendlyLine}
          </Text>
        </View>
      );
    }
  }
};

// Enhanced function to parse and render text with citations as React components
const parseTextWithCitations = (text, theme, onOpenReader) => {
  if (!text || typeof text !== 'string') {
    return <span />;
  }

  // Citation pattern: [cite: 123--docId, 456--anotherId] or [cite: 123--docId]
  // Where 123 is the topic name and docId is the document UUID
  const citationRegex = /\[cite:\s*([^\]]+)\]/gi;
  const parts = [];
  let lastIndex = 0;
  let match;

  while ((match = citationRegex.exec(text)) !== null) {
    // Add text before citation
    if (match.index > lastIndex) {
      const beforeText = text.substring(lastIndex, match.index);
      parts.push(
        <span
          key={`text-${lastIndex}`}
          dangerouslySetInnerHTML={{ __html: inlineMarkdownToHtml(beforeText, theme) }}
        />
      );
    }

    // Parse citation IDs (can be comma-separated)
    const citationContent = match[1].trim();
    const documentIds = citationContent.split(',').map(id => id.trim());

    // Add the "cite: " prefix
    parts.push(<span key={`cite-prefix-${match.index}`}>cite: </span>);

    // Create citation links - show topic name (part before --) as clickable link
    documentIds.forEach((docId, idx) => {
      // Split into topicName--documentUUID
      const [topicName, ...uuidParts] = docId.split('--');
      const fullDocumentId = docId; // Keep the full ID for the link
      const displayName = topicName; // Show only the topic name

      console.log('🔖 CITATION_PARSE: Parsing citation for rendering:', {
        fullDocId: docId,
        topicName,
        uuidParts,
        reconstructedUUID: uuidParts.join('--'),
        willPassToLink: fullDocumentId
      });

      parts.push(
        <CitationLink
          key={`cite-${match.index}-${idx}`}
          documentId={fullDocumentId}
          theme={theme}
          onOpenReader={onOpenReader}
        >
          {displayName}
        </CitationLink>
      );

      // Add comma separator between multiple citations
      if (idx < documentIds.length - 1) {
        parts.push(<span key={`comma-${match.index}-${idx}`}>, </span>);
      }
    });

    lastIndex = match.index + match[0].length;
  }

  // Add remaining text after last citation
  if (lastIndex < text.length) {
    const remainingText = text.substring(lastIndex);
    parts.push(
      <span
        key={`text-${lastIndex}`}
        dangerouslySetInnerHTML={{ __html: inlineMarkdownToHtml(remainingText, theme) }}
      />
    );
  }

  // If no citations found, just return the text with inline markdown
  if (parts.length === 0) {
    return <span dangerouslySetInnerHTML={{ __html: inlineMarkdownToHtml(text, theme) }} />;
  }

  return <span>{parts}</span>;
};

// Comprehensive inline markdown to HTML converter with advanced formatting
const inlineMarkdownToHtml = (text, theme) => {
  if (!text && text !== 0) return '';

  // Safety check for theme - provide fallback if undefined
  if (!theme) {
    theme = { isDark: false }; // Default to light theme
  }

  const normalizedText = convertInlineSourceMarkdown(String(text));

  // First decode any HTML entities that might be in the LLM response
  const decodedText = decodeHtmlEntities(normalizedText);

  // Temporarily protect safe HTML tags before escaping
  const protectedTags = [];
  const PROTECTED_TAG_PREFIX = '〔PROTECTEDTAG';
  const PROTECTED_TAG_SUFFIX = 'ENDPROTECTED〕';

  // Protect <br>, <br/>, and <br /> tags
  let textWithProtectedTags = decodedText.replace(/(<br\s*\/?>)/gi, (match) => {
    const placeholder = `${PROTECTED_TAG_PREFIX}${protectedTags.length}${PROTECTED_TAG_SUFFIX}`;
    protectedTags.push(match);
    return placeholder;
  });

  // Then escape HTML to prevent XSS while processing markdown
  let html = escapeHtml(textWithProtectedTags);

  // Restore protected tags after escaping
  protectedTags.forEach((tag, index) => {
    const placeholder = `${PROTECTED_TAG_PREFIX}${index}${PROTECTED_TAG_SUFFIX}`;
    html = html.replace(placeholder, tag);
  });

  // CRITICAL FIX: Store anchor tags temporarily to protect URLs from markdown processing
  // This prevents underscores and asterisks in URLs from being converted to <em> or <strong>
  const anchorPlaceholders = [];
  const ANCHOR_PLACEHOLDER_PREFIX = '〔ANCHORPLACEHOLDER';
  const ANCHOR_PLACEHOLDER_SUFFIX = 'ENDANCHOR〕';

  // Process in order to avoid conflicts

  // 1. Code spans (highest priority to avoid processing markdown inside code)
  html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

  // Define link colors based on theme for inline styles
  const linkColor = theme.isDark ? '#79c0ff' : '#0066cc';
  const linkHoverColor = theme.isDark ? '#b3d9ff' : '#004499';

  // 2. Links with titles
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\s+"([^"]+)"\)/g, (match, text, url, title) => {
    const placeholder = `${ANCHOR_PLACEHOLDER_PREFIX}${anchorPlaceholders.length}${ANCHOR_PLACEHOLDER_SUFFIX}`;
    anchorPlaceholders.push(`<a href="${url}" title="${title}" target="_blank" rel="noopener noreferrer" class="rich-link" style="color: ${linkColor}; text-decoration: underline; cursor: pointer;">${text}</a>`);
    return placeholder;
  });

  // 2b. Download links: <!-- download -->[text](url) → direct-download anchor (NOT a citation)
  // The HTML comment arrives escaped as &lt;!-- download --&gt; after the escapeHtml step above.
  html = html.replace(/(?:&lt;!--\s*download\s*--&gt;)\s*\[([^\]]+)\]\(([^)]+)\)/g, (match, text, url) => {
    const placeholder = `${ANCHOR_PLACEHOLDER_PREFIX}${anchorPlaceholders.length}${ANCHOR_PLACEHOLDER_SUFFIX}`;
    anchorPlaceholders.push(`<a href="${url}" target="_blank" rel="noopener noreferrer" class="rich-link download-link" download style="color: ${linkColor}; text-decoration: underline; cursor: pointer; font-weight: 600;">${text}</a>`);
    return placeholder;
  });

  // 3. Regular links — detect code-execution output URLs and render as download
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, text, url) => {
    const placeholder = `${ANCHOR_PLACEHOLDER_PREFIX}${anchorPlaceholders.length}${ANCHOR_PLACEHOLDER_SUFFIX}`;
    // S3 output paths (quick-chat/*/output/* or sandbox output) are download links, not citations
    const isOutputFile = /\/output\/[^?]+\.(xlsx|csv|xls|json|pdf|zip|docx|pptx|txt|png|jpg|html)/i.test(url);
    if (isOutputFile) {
      anchorPlaceholders.push(`<a href="${url}" target="_blank" rel="noopener noreferrer" class="rich-link download-link" download style="color: ${linkColor}; text-decoration: underline; cursor: pointer; font-weight: 600;">${text}</a>`);
    } else {
      anchorPlaceholders.push(`<a href="${url}" data-url="${url}" target="_blank" rel="noopener noreferrer" class="rich-link web-inline-citation" style="color: ${linkColor}; text-decoration: underline; cursor: pointer;">${text}</a>`);
    }
    return placeholder;
  });

  // 4a. Handle embedded WEB citations (WEB::Title--URL or WEB::URL)
  html = html.replace(/WEB::((?:https?:\/\/|www\.)[^\s]+)(?:--((?:https?:\/\/|www\.)[^\s]+))?/g, (match, first, second) => {
    const placeholder = `${ANCHOR_PLACEHOLDER_PREFIX}${anchorPlaceholders.length}${ANCHOR_PLACEHOLDER_SUFFIX}`;

    // Pattern could be WEB::URL or WEB::Title--URL
    // But regex above captures URL if it's the first part? 
    // Wait, the standard format is WEB::Title--URL. 
    // And user showed WEB::https://... which implies Title is the URL or missing.

    // Let's use a more flexible parsing for WEB::...
    let url = first;
    let title = first;

    // If strict WEB::url
    if (!second && (first.startsWith('http') || first.startsWith('www'))) {
      title = first;
      url = first;
    } else if (second) {
      // Maybe WEB::Title--URL
      // But the previous regex expects URL as first capture?
      // Let's rely on the simpler logic below
    }

    // Fallback: Just simple replacement for WEB::...
    return match; // Will be handled by logic below if we don't return placeholder
  });

  // Re-implementing WEB:: pattern with simpler logic
  html = html.replace(/WEB::([^\s]+)/g, (match, content) => {
    // Check if it's a URL-like string
    if (content.startsWith('http') || content.startsWith('www')) {
      const placeholder = `${ANCHOR_PLACEHOLDER_PREFIX}${anchorPlaceholders.length}${ANCHOR_PLACEHOLDER_SUFFIX}`;
      anchorPlaceholders.push(`<a href="${content}" data-url="${content}" target="_blank" rel="noopener noreferrer" class="rich-link web-inline-citation" style="color: ${linkColor}; text-decoration: underline; cursor: pointer;">${content}</a>`);
      return placeholder;
    }
    return match;
  });

  // 4. Handle embedded DOC/CHUNK citations (for inline collection references)
  // Pattern: DOC::{docId} CHUNK::{chunkId} (or just DOC::{docId})
  // Example: DOC::file_c9362b36... CHUNK::019af7cf...
  html = html.replace(/DOC::([a-zA-Z0-9_\-]+)(?:\s+CHUNK::([a-zA-Z0-9_\-]+))?/g, (match, docId, chunkId) => {
    const placeholder = `${ANCHOR_PLACEHOLDER_PREFIX}${anchorPlaceholders.length}${ANCHOR_PLACEHOLDER_SUFFIX}`;

    // Create a special link that global handler can intercept
    // We use data attributes to store the IDs
    // For xAI collection documents (starting with file_), ignore the chunk ID
    // We want to link to the main document, not a specific chunk (which we can't load individually)
    const isCollectionDoc = docId && docId.startsWith('file_');
    const effectiveChunkId = isCollectionDoc ? null : chunkId;

    // User requested "Citra AI Database Source" label
    const displayText = '[Citra AI Database Source]';

    anchorPlaceholders.push(`<a href="#" class="collection-inline-citation" data-doc-id="${docId}" data-chunk-id="${effectiveChunkId || ''}" style="color: ${linkColor}; text-decoration: underline; cursor: pointer; font-weight: 500;">${displayText}</a>`);
    return placeholder;
  });

  // 4. Auto links - FIXED: Use negative lookbehind to avoid matching URLs already inside href attributes
  // This prevents double-wrapping of URLs that were already converted from markdown format
  html = html.replace(/(?<!href=")(?<!href=&quot;)(?<!data-url=")(https?:\/\/[^\s<>"]+)(?![^<]*<\/a>)/g, (match, url) => {
    const placeholder = `${ANCHOR_PLACEHOLDER_PREFIX}${anchorPlaceholders.length}${ANCHOR_PLACEHOLDER_SUFFIX}`;
    // S3 output paths are download links, not citations
    const isOutputFile = /\/output\/[^?]+\.(xlsx|csv|xls|json|pdf|zip|docx|pptx|txt|png|jpg|html)/i.test(url);
    if (isOutputFile) {
      anchorPlaceholders.push(`<a href="${url}" target="_blank" rel="noopener noreferrer" class="rich-link download-link" download style="color: ${linkColor}; text-decoration: underline; cursor: pointer; font-weight: 600;">${url}</a>`);
    } else {
      anchorPlaceholders.push(`<a href="${url}" data-url="${url}" target="_blank" rel="noopener noreferrer" class="rich-link web-inline-citation" style="color: ${linkColor}; text-decoration: underline; cursor: pointer;">${url}</a>`);
    }
    return placeholder;
  });

  // 5. Email links
  html = html.replace(/([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})/g, (match, email) => {
    const placeholder = `${ANCHOR_PLACEHOLDER_PREFIX}${anchorPlaceholders.length}${ANCHOR_PLACEHOLDER_SUFFIX}`;
    anchorPlaceholders.push(`<a href="mailto:${email}" class="rich-link" style="color: ${linkColor}; text-decoration: underline; cursor: pointer;">${email}</a>`);
    return placeholder;
  });

  // NOW apply markdown formatting - anchors are protected by placeholders

  // 6. Strikethrough
  html = html.replace(/~~([^~]+)~~/g, '<del>$1</del>');

  // 7. Bold (** and __)
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');

  // 8. Italic (* and _)
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  html = html.replace(/_([^_]+)_/g, '<em>$1</em>');

  // 9. Highlight/Mark
  html = html.replace(/==([^=]+)==/g, '<mark>$1</mark>');

  // 10. Superscript and Subscript
  html = html.replace(/\^([^\s^]+)/g, '<sup>$1</sup>');
  html = html.replace(/~([^\s~]+)/g, '<sub>$1</sub>');

  // 11. Keyboard keys
  html = html.replace(/\[\[([^\]]+)\]\]/g, '<kbd>$1</kbd>');

  // 12. Mathematical expressions (LaTeX style)
  html = html.replace(/\$([^$]+)\$/g, '<span class="math-inline">$1</span>');

  // FINAL STEP: Restore all anchor tags from placeholders
  anchorPlaceholders.forEach((anchor, index) => {
    const placeholder = `${ANCHOR_PLACEHOLDER_PREFIX}${index}${ANCHOR_PLACEHOLDER_SUFFIX}`;
    html = html.replace(placeholder, anchor);
  });

  return html;
};

// Advanced CSS styles for web rendering
const getAdvancedCSS = (theme) => `
<style>
  .rich-content {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell', sans-serif;
    line-height: 1.6;
    color: ${theme.text};
    max-width: 100%;
    word-wrap: break-word;
    white-space: pre-wrap;
    overflow-wrap: break-word;
  }
  
  .rich-content h1, .rich-content h2, .rich-content h3, .rich-content h4, .rich-content h5, .rich-content h6 {
    margin-top: 1.5em;
    margin-bottom: 0.5em;
    font-weight: 600;
    line-height: 1.25;
    border-bottom: ${theme.isDark ? '1px solid #4a5568' : '1px solid #e2e8f0'};
    padding-bottom: 0.3em;
    font-size: 15px;
  }
  
  .rich-content p {
    margin: 0.8em 0;
    line-height: 1.6;
    white-space: pre-wrap !important;
    word-wrap: break-word;
    overflow-wrap: break-word;
    font-family: inherit;
  }
  
  .rich-content strong {
    font-weight: 600;
    color: ${theme.text};
  }
  
  .rich-content em {
    font-style: italic;
  }
  
  .rich-content del {
    text-decoration: line-through;
    opacity: 0.7;
  }
  
  .rich-content mark {
    background-color: ${theme.isDark ? '#ffd60a4d' : '#fff3cd'};
    padding: 0.1em 0.2em;
    border-radius: 2px;
  }
  
  .rich-content sup {
    vertical-align: super;
    font-size: 15px;
  }
  
  .rich-content sub {
    vertical-align: sub;
    font-size: 15px;
  }
  
  .rich-content kbd {
    background: ${theme.isDark ? '#4a5568' : '#f1f3f4'};
    border: 1px solid ${theme.isDark ? '#718096' : '#dadce0'};
    border-radius: 4px;
    box-shadow: 0 1px 1px rgba(0,0,0,.15), 0 3px 0 ${theme.isDark ? '#2d3748' : '#b4b4b4'}, inset 0 -1px 0 #ffffff1f;
    color: ${theme.text};
    display: inline-block;
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 15px;
    font-weight: 500;
    line-height: 1;
    padding: 2px 6px;
    white-space: nowrap;
  }
  
  .rich-content .inline-code {
    background: ${theme.isDark ? '#2d3748' : '#f6f8fa'};
    border: 1px solid ${theme.isDark ? '#4a5568' : '#e1e4e8'};
    border-radius: 3px;
    color: ${theme.isDark ? '#f7fafc' : '#24292e'};
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 15px;
    padding: 0.2em 0.4em;
    white-space: pre-wrap;
  }
  
  .rich-content .math-inline {
    font-family: "Latin Modern Math", "STIX Two Math", "Times New Roman", serif;
    font-style: italic;
    background: ${theme.isDark ? '#2d3748' : '#f8f9fa'};
    padding: 2px 4px;
    border-radius: 3px;
    border: 1px solid ${theme.isDark ? '#4a5568' : '#e9ecef'};
  }
  
  .rich-content a {
    color: ${theme.isDark ? '#79c0ff' : '#0066cc'};
    text-decoration: none;
    border-bottom: 1px dotted;
    transition: all 0.2s ease;
  }
  
  .rich-content a:hover {
    color: ${theme.isDark ? '#b3d9ff' : '#004499'};
    border-bottom: 1px solid;
  }
  
  .rich-content blockquote {
    border-left: 4px solid ${theme.isDark ? '#4a9eff' : '#007acc'};
    background: ${theme.isDark ? '#1a2332' : '#f8f9fa'};
    margin: 1em 0;
    padding: 1em 1.5em;
    border-radius: 0 4px 4px 0;
    font-style: italic;
    position: relative;
  }
  
  .rich-content blockquote:before {
    content: '"';
    font-size: 24px;
    color: ${theme.isDark ? '#4a9eff66' : '#007acc66'};
    position: absolute;
    left: 10px;
    top: -10px;
    font-family: serif;
  }
  
  .rich-content hr {
    border: none;
    height: 2px;
    background: linear-gradient(90deg, transparent, ${theme.isDark ? '#4a5568' : '#e2e8f0'}, transparent);
    margin: 2em 0;
  }
  
  .rich-content ul, .rich-content ol {
    margin: 1em 0;
    padding-left: 2em;
  }
  
  .rich-content li {
    margin: 0.5em 0;
    line-height: 1.6;
  }
  
  .rich-content ul li {
    list-style-type: disc;
  }
  
  .rich-content ul ul li {
    list-style-type: circle;
  }
  
  .rich-content ul ul ul li {
    list-style-type: square;
  }
  
  .rich-content ol {
    counter-reset: list-counter;
  }
  
  .rich-content ol li {
    counter-increment: list-counter;
    list-style: none;
    position: relative;
  }
  
  .rich-content ol li:before {
    content: counter(list-counter) ".";
    font-weight: bold;
    position: absolute;
    left: -2em;
    width: 1.5em;
    text-align: right;
    color: ${theme.isDark ? '#a0aec0' : '#718096'};
  }
  
  .rich-content .task-list-item {
    list-style: none;
    position: relative;
    padding-left: 1.5em;
  }
  
  .rich-content .task-list-item input[type="checkbox"] {
    position: absolute;
    left: 0;
    top: 0.3em;
    margin: 0;
  }
  
  .rich-content .highlight {
    background: ${theme.isDark ? '#2d3748' : '#f8f9fa'};
    border: 1px solid ${theme.isDark ? '#4a5568' : '#e9ecef'};
    border-radius: 4px;
    padding: 0.1em 0.3em;
    font-family: monospace;
    font-size: 15px;
  }
  
  .rich-content .mermaid {
    text-align: center;
    margin: 1em 0;
  }
  
  .rich-content .footnote {
    font-size: 15px;
    vertical-align: super;
    color: ${theme.isDark ? '#79c0ff' : '#0066cc'};
    text-decoration: none;
  }
  
  .rich-content .footnote-content {
    font-size: 15px;
    border-top: 1px solid ${theme.isDark ? '#4a5568' : '#e2e8f0'};
    margin-top: 2em;
    padding-top: 1em;
  }
  
  .rich-content details {
    border: 1px solid ${theme.isDark ? '#4a5568' : '#e1e4e8'};
    border-radius: 6px;
    margin: 1em 0;
    padding: 0.5em;
  }
  
  .rich-content summary {
    cursor: pointer;
    font-weight: 600;
    padding: 0.5em;
    background: ${theme.isDark ? '#2d3748' : '#f6f8fa'};
    border-radius: 4px;
    outline: none;
  }
  
  .rich-content summary:hover {
    background: ${theme.isDark ? '#4a5568' : '#e1e4e8'};
  }
  
  .rich-content .definition {
    border-left: 3px solid ${theme.isDark ? '#68d391' : '#38a169'};
    background: ${theme.isDark ? '#1a2e1a' : '#f0fff4'};
    padding: 1em;
    margin: 1em 0;
    border-radius: 0 4px 4px 0;
  }
  
  .rich-content .warning {
    border-left: 3px solid ${theme.isDark ? '#f6e05e' : '#d69e2e'};
    background: ${theme.isDark ? '#2e2813' : '#fffdf0'};
    padding: 1em;
    margin: 1em 0;
    border-radius: 0 4px 4px 0;
  }
  
  .rich-content .danger {
    border-left: 3px solid ${theme.isDark ? '#fc8181' : '#e53e3e'};
    background: ${theme.isDark ? '#2d1b1b' : '#fff5f5'};
    padding: 1em;
    margin: 1em 0;
    border-radius: 0 4px 4px 0;
  }
  
  /* Table Styling */
  .rich-content table {
    border-collapse: collapse;
    width: 100%;
    margin: 1em 0;
    background: ${theme.isDark ? '#1a202c' : '#ffffff'};
    border: 1px solid ${theme.isDark ? '#4a5568' : '#e2e8f0'};
    border-radius: 6px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  }
  
  .rich-content th, .rich-content td {
    border: 1px solid ${theme.isDark ? '#4a5568' : '#e2e8f0'};
    padding: 0.75em;
    text-align: left;
    vertical-align: top;
  }
  
  .rich-content th {
    background: ${theme.isDark ? '#2d3748' : '#f7fafc'};
    font-weight: 600;
    font-size: 15px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: ${theme.isDark ? '#e2e8f0' : '#4a5568'};
  }
  
  .rich-content tr:nth-child(even) {
    background: ${theme.isDark ? '#2d3748' : '#f8f9fa'};
  }
  
  .rich-content tr:hover {
    background: ${theme.isDark ? '#4a5568' : '#e2e8f0'};
  }
  
  .rich-content .table-wrapper {
    overflow-x: auto;
    margin: 1em 0;
    border-radius: 6px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  }
  
  /* Code Block Styling */
  .rich-content pre {
    background: ${theme.isDark ? '#1a202c' : '#f8f9fa'};
    border: 1px solid ${theme.isDark ? '#4a5568' : '#e1e4e8'};
    border-radius: 6px;
    color: ${theme.isDark ? '#e2e8f0' : '#24292e'};
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 15px;
    line-height: 1.45;
    margin: 1em 0;
    overflow: auto;
    padding: 1em;
    position: relative;
  }
  
  .rich-content .code-header {
    background: ${theme.isDark ? '#2d3748' : '#f1f3f4'};
    border-bottom: 1px solid ${theme.isDark ? '#4a5568' : '#e1e4e8'};
    color: ${theme.isDark ? '#a0aec0' : '#6a737d'};
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 15px;
    font-weight: 600;
    margin: -1em -1em 1em -1em;
    padding: 0.5em 1em;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  
  .rich-content .copy-button {
    position: absolute;
    top: 0.5em;
    right: 0.5em;
    background: ${theme.isDark ? '#4a5568' : '#f1f3f4'};
    border: 1px solid ${theme.isDark ? '#718096' : '#d1d5da'};
    border-radius: 4px;
    color: ${theme.text};
    cursor: pointer;
    font-size: 15px;
    opacity: 0.7;
    padding: 0.25em 0.5em;
    transition: opacity 0.2s;
  }
  
  .rich-content .copy-button:hover {
    opacity: 1;
  }
  
  /* Responsive Design */
  @media (max-width: 768px) {
    .rich-content {
      font-size: 15px;
    }
    
    .rich-content table {
      font-size: 15px;
    }
    
    .rich-content .table-wrapper {
      margin-left: -1em;
      margin-right: -1em;
    }
  }
</style>
`;

// Table Row Component
const TableRow = memo(({ cells, isHeader, theme, rowIndex }) => {
  return (
    <View style={[
      styles.tableRow,
      {
        backgroundColor: isHeader
          ? (theme.isDark ? '#2d3748' : '#f7fafc')
          : (rowIndex % 2 === 0
            ? (theme.isDark ? '#1a202c' : '#ffffff')
            : (theme.isDark ? '#2d3748' : '#f8f9fa')
          ),
        borderBottomWidth: 1,
        borderBottomColor: theme.isDark ? '#4a5568' : '#e2e8f0',
      }
    ]}>
      {cells.map((cell, index) => (
        <View
          key={index}
          style={[
            styles.tableCell,
            {
              flex: 1,
              padding: 12,
              borderRightWidth: index < cells.length - 1 ? 1 : 0,
              borderRightColor: theme.isDark ? '#4a5568' : '#e2e8f0',
              minHeight: 44,
              justifyContent: 'center',
            }
          ]}
        >
          <Text style={[
            styles.tableCellText,
            {
              color: theme.text,
              fontSize: isHeader ? 14 : 13,
              fontWeight: isHeader ? 'bold' : 'normal',
              textAlign: 'left',
              lineHeight: 18,
            }
          ]}>
            {(cell || '').trim()}
          </Text>
        </View>
      ))}
    </View>
  );
});

// Enhanced Table Component with advanced features
const MarkdownTable = memo(({ content, theme }) => {
  const lines = content.split('\n').filter(line => line.trim());

  if (lines.length < 2) return null;

  // Find header and separator
  let headerIndex = -1;
  let separatorIndex = -1;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (line.includes('|')) {
      if (headerIndex === -1) {
        headerIndex = i;
      } else if (separatorIndex === -1 && line.match(/^\s*\|[\s\-\|:]+\|\s*$/)) {
        separatorIndex = i;
        break;
      }
    }
  }

  if (headerIndex === -1) return null;

  // Parse header
  const headerLine = lines[headerIndex].trim();
  const headerCells = headerLine.split('|')
    .map(cell => cell.trim())
    .filter(cell => cell.length > 0);

  if (headerCells.length === 0) return null;

  // Parse alignment from separator if exists
  let alignments = [];
  if (separatorIndex > -1) {
    const separatorLine = lines[separatorIndex].trim();
    const separatorCells = separatorLine.split('|')
      .map(cell => cell.trim())
      .filter(cell => cell.length > 0);

    alignments = separatorCells.map(cell => {
      if (cell.startsWith(':') && cell.endsWith(':')) return 'center';
      if (cell.endsWith(':')) return 'right';
      return 'left';
    });
  }

  // Parse data rows
  const dataRows = [];
  const startIndex = separatorIndex > -1 ? separatorIndex + 1 : headerIndex + 1;

  for (let i = startIndex; i < lines.length; i++) {
    const line = lines[i].trim();
    if (line.includes('|')) {
      const cells = line.split('|')
        .map(cell => cell.trim())
        .filter(cell => cell.length > 0);

      if (cells.length > 0) {
        // Pad cells to match header length
        while (cells.length < headerCells.length) {
          cells.push('');
        }
        dataRows.push(cells.slice(0, headerCells.length));
      }
    }
  }

  // For web platform, render as enhanced HTML table
  if (Platform.OS === 'web') {
    const tableHTML = `
      <div class="table-wrapper" style="overflow-x: auto; max-width: 100%;">
        <table class="markdown-table" style="width: 100%; table-layout: auto;">
          <thead>
            <tr>
              ${headerCells.map((cell, index) => {
      // Escape HTML in cell content
      const escapedCell = escapeHtml(cell);
      return `
                  <th style="text-align: ${alignments[index] || 'left'}; word-break: break-word;">
                    ${inlineMarkdownToHtml(escapedCell, theme)}
                  </th>
                `;
    }).join('')}
            </tr>
          </thead>
          <tbody>
            ${dataRows.map((row, rowIndex) => `
              <tr>
                ${row.map((cell, cellIndex) => {
      // Escape HTML in cell content
      const escapedCell = escapeHtml(cell);
      return `
                    <td style="text-align: ${alignments[cellIndex] || 'left'}; word-break: break-word;">
                      ${inlineMarkdownToHtml(escapedCell, theme)}
                    </td>
                  `;
    }).join('')}
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;

    return (
      <div
        className="rich-table-container"
        style={{ margin: '1em 0' }}
        dangerouslySetInnerHTML={{ __html: tableHTML }}
      />
    );
  }

  // For mobile platforms, use the existing View-based approach
  return (
    <View style={[
      styles.tableContainer,
      {
        marginVertical: 16,
        borderWidth: 1,
        borderColor: theme.isDark ? '#4a5568' : '#e2e8f0',
        borderRadius: 8,
        overflow: 'hidden',
        backgroundColor: theme.isDark ? '#1a202c' : '#ffffff',
      }
    ]}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <View style={{ minWidth: Math.max(300, headerCells.length * 120) }}>
          {/* Header */}
          <TableRow
            cells={headerCells}
            isHeader={true}
            theme={theme}
            rowIndex={0}
          />

          {/* Data Rows */}
          {dataRows.map((row, index) => (
            <TableRow
              key={index}
              cells={row}
              isHeader={false}
              theme={theme}
              rowIndex={index + 1}
            />
          ))}
        </View>
      </ScrollView>
    </View>
  );
});

// List Item Component
const ListItem = memo(({ content, ordered, index, theme, level = 0, isUserMessage = false, onOpenReader }) => {
  const textColor = getTextColor(theme, isUserMessage);
  const bullet = ordered ? `${index + 1}.` : '•';
  const leftMargin = level * 20;

  return (
    Platform.OS === 'web' ? (
      <div style={{ display: 'flex', flexDirection: 'row', marginVertical: 2, marginLeft: leftMargin }}>
        <div style={{ minWidth: 20, marginRight: 8, color: textColor, fontSize: 15, fontWeight: ordered ? 'bold' : 'normal' }}>{bullet}</div>
        <div style={{ color: textColor, fontSize: 15, lineHeight: '20px', flex: 1 }}>
          {parseTextWithCitations(content, theme, onOpenReader)}
        </div>
      </div>
    ) : (
      <View style={[
        styles.listItem,
        {
          flexDirection: 'row',
          marginVertical: 2,
          marginLeft: leftMargin,
        }
      ]}>
        <Text style={[
          styles.listBullet,
          {
            color: textColor,
            fontSize: 15,
            fontWeight: ordered ? 'bold' : 'normal',
            minWidth: 20,
            marginRight: 8,
          }
        ]}>
          {bullet}
        </Text>
        <Text style={[
          styles.listContent,
          {
            color: textColor,
            fontSize: 15,
            lineHeight: 20,
            flex: 1,
          }
        ]}>
          {content.trim()}
        </Text>
      </View>
    )
  );
});

// Image Component
const ImageRenderer = memo(({ src, alt, caption, theme }) => {
  return (
    <View style={{ marginVertical: 16, alignItems: 'center' }}>
      <Image
        source={{ uri: src }}
        style={{
          maxWidth: '100%',
          width: 400,
          height: 300,
          resizeMode: 'contain',
          borderRadius: 8,
          ...(Platform.OS === 'web' && {
            objectFit: 'contain',
            border: `1px solid ${theme.isDark ? '#4a5568' : '#e0e0e0'}`,
            boxShadow: theme.isDark ? '0 2px 8px rgba(0,0,0,0.3)' : '0 2px 8px rgba(0,0,0,0.1)'
          })
        }}
        onLoad={() => { }}
        onError={(e) => console.error('🖼️ [RICH_RENDERER] Image load error:', e)}
      />
      {(alt || caption) && (
        <Text style={{
          fontSize: 15,
          color: theme.text,
          opacity: 0.7,
          textAlign: 'center',
          marginTop: 8,
          fontStyle: 'italic',
          paddingHorizontal: 16
        }}>
          {caption || alt}
        </Text>
      )}
    </View>
  );
});

// Sources Header Component - Special styling for the SOURCES section
const SourcesHeader = memo(({ content, theme, isUserMessage = false }) => {
  const textColor = getTextColor(theme, isUserMessage);

  if (Platform.OS === 'web') {
    const html = `
      <div class="sources-header" style="
        margin-top: 2em;
        margin-bottom: 0.5em;
        padding-bottom: 0.5em;
        border-bottom: 2px solid ${theme.isDark ? '#4a5568' : '#e2e8f0'};
      ">
        <h4 style="
          font-size: 15px;
          font-weight: 600;
          color: ${escapeHtml(textColor)};
          margin: 0;
          text-transform: uppercase;
          letter-spacing: 0.5px;
        ">📚 ${escapeHtml(content || 'SOURCES')}</h4>
      </div>
    `;
    return <div dangerouslySetInnerHTML={{ __html: html }} />;
  }

  return (
    <View style={{
      marginTop: 24,
      marginBottom: 8,
      paddingBottom: 8,
      borderBottomWidth: 2,
      borderBottomColor: theme.isDark ? '#4a5568' : '#e2e8f0',
    }}>
      <Text style={{
        fontSize: 15,
        fontWeight: '600',
        color: textColor,
        textTransform: 'uppercase',
        letterSpacing: 0.5,
      }}>
        📚 {content || 'SOURCES'}
      </Text>
    </View>
  );
});

// Enhanced Section Header Component with proper text color and all 6 levels
const SectionHeader = memo(({ content, level, theme, isUserMessage = false }) => {
  const textColor = getTextColor(theme, isUserMessage);

  // Define sizes for h1-h6 (matching markdown standards)
  const headerStyles = {
    1: { fontSize: 24, marginTop: 28, marginBottom: 16, fontWeight: 'bold' },
    2: { fontSize: 20, marginTop: 24, marginBottom: 14, fontWeight: 'bold' },
    3: { fontSize: 18, marginTop: 20, marginBottom: 12, fontWeight: 'bold' },
    4: { fontSize: 16, marginTop: 18, marginBottom: 10, fontWeight: '600' },
    5: { fontSize: 14, marginTop: 16, marginBottom: 8, fontWeight: '600' },
    6: { fontSize: 13, marginTop: 14, marginBottom: 6, fontWeight: '600' }
  };

  const style = headerStyles[level] || headerStyles[3]; // Default to h3 if invalid level

  if (Platform.OS === 'web') {
    const html = `<h${level} style="font-size:${style.fontSize}px;font-weight:${style.fontWeight};color:${escapeHtml(textColor)};margin-top:${style.marginTop}px;margin-bottom:${style.marginBottom}px;line-height:${Math.round(style.fontSize * 1.3)}px;word-break:break-word;overflow-wrap:break-word;word-wrap:break-word;white-space:normal;display:block;max-width:100%;">${escapeHtml(content)}</h${level}>`;
    return <div dangerouslySetInnerHTML={{ __html: html }} />;
  }

  return (
    <Text style={[
      styles.sectionHeader,
      {
        fontSize: style.fontSize,
        fontWeight: style.fontWeight,
        color: textColor,
        marginTop: style.marginTop,
        marginBottom: style.marginBottom,
        lineHeight: style.fontSize * 1.3,
      }
    ]}>
      {content.trim()}
    </Text>
  );
});

// Enhanced Code Block Component with comprehensive features
const CodeBlock = memo(({ content, language, theme }) => {
  const code = content?.trim() || '';

  if (!code) return null;

  // For web platform, use enhanced HTML with syntax highlighting
  if (Platform.OS === 'web') {
    const codeHTML = `
      <div class="code-block-wrapper">
        <div class="code-block-header">
          <span class="code-language">${language || 'code'}</span>
          <button class="copy-button" onclick="
            navigator.clipboard.writeText(this.closest('.code-block-wrapper').querySelector('code').textContent);
            this.textContent = 'Copied!';
            setTimeout(() => this.textContent = 'Copy', 2000);
          ">Copy</button>
        </div>
        <pre class="code-block"><code class="language-${language || 'text'}">${escapeHtml(code)}</code></pre>
      </div>
    `;

    return (
      <div
        className="rich-code-container"
        style={{ margin: '1em 0' }}
        dangerouslySetInnerHTML={{ __html: codeHTML }}
      />
    );
  }

  // For mobile platforms, enhanced View-based approach
  return (
    <View style={[
      styles.richCodeBlock,
      {
        backgroundColor: theme.isDark ? '#1a1a1a' : '#f6f8fa',
        borderRadius: 8,
        marginVertical: 12,
        borderWidth: 1,
        borderColor: theme.isDark ? '#30363d' : '#d0d7de',
        overflow: 'hidden',
      }
    ]}>
      {/* Enhanced header with language and copy functionality */}
      <View style={[
        styles.richCodeHeader,
        {
          backgroundColor: theme.isDark ? '#262626' : '#f1f3f4',
          paddingHorizontal: 12,
          paddingVertical: 8,
          borderBottomWidth: 1,
          borderBottomColor: theme.isDark ? '#30363d' : '#d0d7de',
          flexDirection: 'row',
          justifyContent: 'space-between',
          alignItems: 'center',
        }
      ]}>
        <Text style={[
          styles.richCodeLanguage,
          {
            color: theme.isDark ? '#7d8590' : '#656d76',
            fontSize: 15,
            fontWeight: '600',
            textTransform: 'uppercase',
          }
        ]}>
          {language || 'Code'}
        </Text>

        <TouchableOpacity
          style={{
            paddingHorizontal: 8,
            paddingVertical: 4,
            borderRadius: 4,
            backgroundColor: theme.isDark ? '#21262d' : '#ffffff',
            borderWidth: 1,
            borderColor: theme.isDark ? '#30363d' : '#d0d7de',
          }}
          onPress={() => {
            if (Clipboard && Clipboard.setString) {
              Clipboard.setString(code);
            }
          }}
        >
          <Text style={{
            fontSize: 15,
            color: theme.isDark ? '#7d8590' : '#656d76',
            fontWeight: '500',
          }}>
            Copy
          </Text>
        </TouchableOpacity>
      </View>

      {/* Enhanced code content */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <Text style={[
          styles.richCodeContent,
          {
            color: theme.isDark ? '#e6edf3' : '#24292f',
            fontSize: 15,
            fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
            padding: 12,
            lineHeight: 20,
            minHeight: 44,
          }
        ]}>
          {code}
        </Text>
      </ScrollView>
    </View>
  );
});

// Horizontal Rule Component
const HorizontalRule = memo(({ theme }) => {
  return (
    <View style={{
      height: 2,
      backgroundColor: theme.isDark ? '#4a5568' : '#e2e8f0',
      marginVertical: 24,
      width: '100%',
      borderRadius: 1,
    }} />
  );
});

// Quote Block Component
const QuoteBlock = memo(({ content, theme, isUserMessage = false }) => {
  const textColor = getTextColor(theme, isUserMessage);

  return (
    <View style={[
      styles.quoteBlock,
      {
        borderLeftWidth: 4,
        borderLeftColor: theme.isDark ? '#4a9eff' : '#007acc',
        backgroundColor: theme.isDark ? '#2d3748' : '#f7fafc',
        marginVertical: 12,
        paddingVertical: 12,
        paddingHorizontal: 16,
        borderRadius: 4,
      }
    ]}>
      <Text style={[
        styles.quoteContent,
        {
          color: textColor,
          fontSize: 15,
          fontStyle: 'italic',
          lineHeight: 20,
        }
      ]}>
        {content.trim()}
      </Text>
    </View>
  );
});

const normalizeInlineCodeFences = (input) => {
  if (!input || typeof input !== 'string') {
    return input;
  }

  return input.replace(/```([a-zA-Z0-9_-]+)([^`\n]*?)```/g, (match, language, inlineContent) => {
    const sanitizedLanguage = (language || '').trim() || 'text';
    const trimmedContent = (inlineContent || '').trim();
    const lines = ['```' + sanitizedLanguage];
    if (trimmedContent.length > 0) {
      lines.push(trimmedContent);
    }
    lines.push('```');
    return '\n' + lines.join('\n') + '\n';
  });
};

const isLikelyTableLine = (line) => {
  if (!line || typeof line !== 'string') {
    return false;
  }

  const trimmed = line.trim();
  if (!trimmed.includes('|')) {
    return false;
  }

  const pipeMatches = trimmed.match(/\|/g) || [];
  if (pipeMatches.length < 2) {
    return false;
  }

  const startsOrEndsWithPipe = trimmed.startsWith('|') || trimmed.endsWith('|');
  const hasSpacedDelimiter = /\s\|\s/.test(trimmed);
  const looksLikeSeparator = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/.test(trimmed);

  return startsOrEndsWithPipe || hasSpacedDelimiter || looksLikeSeparator;
};

const MERMAID_HEADER_REGEX = /^(graph|flowchart|timeline|gantt|sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|pie|mindmap|quadrantChart|gitGraph|sankeyDiagram|xychart-beta|requirementDiagram|blockDiagram|zenuml)\b/i;

/**
 * Convert Mermaid pie chart syntax to Chart.js JSON config.
 * Handles both "Label" : value and "Label" value (missing colon) formats.
 * Returns Chart.js JSON string if conversion succeeds, null otherwise.
 */
const convertMermaidPieToChartJs = (content) => {
  const lines = content.trim().split('\n');
  const labels = [];
  const data = [];
  let title = '';

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('%%')) continue;

    if (/^pie\b/i.test(trimmed)) {
      const titleMatch = trimmed.match(/^pie\s+title\s+(.+)$/i);
      if (titleMatch) {
        title = titleMatch[1].trim();
      }
      continue;
    }

    // Parse "Label" : value  OR  "Label" value (missing colon)
    const entryMatch = trimmed.match(/^"([^"]+)"\s*:?\s*(\d+(?:\.\d+)?)/);
    if (entryMatch) {
      labels.push(entryMatch[1]);
      data.push(parseFloat(entryMatch[2]));
    }
  }

  if (labels.length === 0) return null;

  return JSON.stringify({
    type: 'pie',
    data: {
      labels,
      datasets: [{ data }],
    },
    options: {
      responsive: true,
      plugins: {
        title: title ? { display: true, text: title } : { display: false },
      },
    },
  });
};

const isLikelyMermaidDiagram = (rawContent) => {
  if (!rawContent || typeof rawContent !== 'string') {
    return false;
  }

  const trimmed = rawContent.trim();
  if (!trimmed) {
    return false;
  }

  const firstUsableLine = trimmed
    .split('\n')
    .map((segment) => segment.trim())
    .find((segment) => segment && !segment.startsWith('%%'));

  if (!firstUsableLine) {
    return false;
  }

  if (MERMAID_HEADER_REGEX.test(firstUsableLine)) {
    return true;
  }

  return /^(graph|flowchart)\s+[A-Za-z]+\b/i.test(firstUsableLine);
};

// Strips [vault:<id>], [internet:<host>], [structured:<table>], and
// [general-knowledge] inline tokens that the backend prompt instructs the LLM
// to emit after factual sentences. Returns the cleaned text plus an ordered,
// deduped list of refs used to backfill the Sources appendix when the
// CITATIONS SSE payload is missing or partial. Skips fenced code blocks so
// legitimate examples in code are preserved.
const INLINE_CITATION_RE = /\s?\[(vault|internet|structured|enterprise):([^\]\n]+?)\]|\s?\[general-knowledge\]/g;

// Vault citation ids come either as a bare document UUID or as a
// chunk-suffixed UUID (e.g. "<uuid>_4" for chunk 4). The Sources appendix
// links to whole documents in the reader, so strip the trailing "_<digits>"
// to collapse all chunks of the same document to one entry. Conservative:
// only strips when the prefix is a UUID and the suffix is purely numeric;
// any other id (filename-style, non-vault kinds) is returned unchanged.
const VAULT_UUID_CHUNK_RE = /^([a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})_\d+$/;
const stripVaultChunkSuffix = (id) => {
  if (!id || typeof id !== 'string') return id;
  const m = id.match(VAULT_UUID_CHUNK_RE);
  return m ? m[1] : id;
};

const stripInlineCitationTokens = (text) => {
  if (typeof text !== 'string' || text.length === 0) {
    return { cleanedText: text || '', inlineRefs: [] };
  }
  const parts = text.split(/(```[\s\S]*?```)/g);
  const refs = [];
  const seen = new Set();
  const cleaned = parts
    .map((part) => {
      if (part.startsWith('```')) return part;
      return part.replace(INLINE_CITATION_RE, (_match, kind, id) => {
        const refKind = kind || 'general-knowledge';
        let refId = (id || '').trim();
        if (refKind === 'vault') refId = stripVaultChunkSuffix(refId);
        const key = `${refKind}:${refId}`;
        if (refKind !== 'general-knowledge' && refId && !seen.has(key)) {
          seen.add(key);
          refs.push({ kind: refKind, id: refId });
        }
        return '';
      });
    })
    .join('');
  return { cleanedText: cleaned, inlineRefs: refs };
};

// Rich Content Parser
const parseRichContent = (text) => {
  if (!text || typeof text !== 'string') return [];

  const normalizedText = normalizeInlineCodeFences(text);

  const { cleanedText, structuredPayload } = extractStructuredAppendix(normalizedText);
  let textToParse = typeof cleanedText === 'string' ? cleanedText : normalizedText;

  // Only collapse truly excessive empty lines (5+)
  textToParse = textToParse.replace(/\n{5,}/g, '\n\n\n');

  // Pre-process to ensure headers have proper line breaks
  textToParse = textToParse.replace(/([^\n])(#{1,6}\s+[^\n#]+)/g, '$1\n$2');

  const sections = [];
  const lines = (textToParse || '').split('\n');
  let currentSection = null;
  let currentType = 'text';
  let currentContent = [];
  let inCodeBlock = false;
  let codeLanguage = '';
  let listItems = [];
  let currentListType = null;

  const flushCurrentSection = () => {
    if (currentSection || currentContent.length > 0 || listItems.length > 0) {
      if (listItems.length > 0) {
        const type = currentListType === 'sources' ? 'sources_list' : 'list';
        sections.push({
          type: type,
          ordered: currentListType === 'ordered',
          items: [...listItems]
        });
        listItems = [];
        currentListType = null;
      } else if (currentContent.length > 0) {
        const content = currentContent.join('\n');

        // Auto-detect Mermaid diagrams in plain code blocks (no language specified)
        if (currentType === 'code' && (!codeLanguage || codeLanguage === 'text')) {
          const trimmedContent = content.trim();
          // ASCII/Unicode box diagrams take precedence over Mermaid header detection
          // because header regex won't match them, but we check first for clarity.
          if (isLikelyAsciiDiagram(trimmedContent)) {
            codeLanguage = 'ascii';
          } else if (isLikelyMermaidDiagram(trimmedContent)) {
            // If it starts with 'pie', convert to Chart.js instead of mermaid
            if (/^pie\b/i.test(trimmedContent.split('\n').find(l => l.trim() && !l.trim().startsWith('%%'))?.trim() || '')) {
              const chartJson = convertMermaidPieToChartJs(trimmedContent);
              if (chartJson) {
                codeLanguage = 'chartjs';
                currentContent = [chartJson];
              } else {
                codeLanguage = 'mermaid';
              }
            } else {
              codeLanguage = 'mermaid';
            }
          } else {
            // Auto-detect Chart.js configs in unlabeled JSON code blocks
            try {
              const parsed = JSON.parse(trimmedContent);
              if (parsed && typeof parsed === 'object' && parsed.type && parsed.data) {
                codeLanguage = 'chartjs';
              }
            } catch (_) {
              // Not JSON, skip
            }
          }
        }

        // CRITICAL FIX: Validate and sanitize Mermaid content before adding section
        if (currentType === 'code' && codeLanguage && codeLanguage.toLowerCase() === 'mermaid') {
          let trimmedContent = content.trim();

          // Convert mermaid pie charts to Chart.js — Chart.js renders them better
          if (/^pie\b/i.test(trimmedContent.split('\n').find(l => l.trim() && !l.trim().startsWith('%%'))?.trim() || '')) {
            const chartJson = convertMermaidPieToChartJs(trimmedContent);
            if (chartJson) {
              sections.push({
                type: currentType,
                content: chartJson,
                language: 'chartjs',
              });
              currentContent = [];
              currentSection = null;
              codeLanguage = '';
              return;
            }
          }

          if (!isLikelyMermaidDiagram(trimmedContent)) {
            currentContent = [];
            currentSection = null;
            codeLanguage = '';
            return;
          }

          // PRE-SANITIZE Mermaid content to fix common LLM issues before passing to MermaidDiagram component
          // This provides an extra layer of sanitization in addition to MermaidDiagram.js
          // Remove HTML <br/> and <br> tags - they break Mermaid parsing
          trimmedContent = trimmedContent.replace(/<br\s*\/?>/gi, ' - ');
          // Remove any other HTML-like tags
          trimmedContent = trimmedContent.replace(/<[^>]+>/g, ' ');
          // Collapse multiple spaces/tabs on each line (but PRESERVE newlines - critical for Mermaid)
          trimmedContent = trimmedContent.replace(/[ \t]+/g, ' ');

          sections.push({
            type: currentType,
            content: trimmedContent,
            language: codeLanguage,
          });
          currentContent = [];
          currentSection = null;
          codeLanguage = '';
          return;
        }

        sections.push({
          type: currentType,
          content: content,
          language: codeLanguage,
          // Ensure 'list' types always have an items array for safety
          ...(currentType === 'list' && { items: content.split('\n') }),
          ...(currentType === 'sources_list' && { items: content.split('\n') }),
        });
      }
      currentContent = [];
      currentSection = null;
      codeLanguage = '';
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmedLine = line.trim();

    // Handle code blocks
    if (trimmedLine.startsWith('```')) {
      if (!inCodeBlock) {
        // Flush any pending content before starting code block
        flushCurrentSection();
        inCodeBlock = true;
        const rawLanguageSpec = trimmedLine.slice(3).trim();
        if (!rawLanguageSpec) {
          codeLanguage = 'text';
        } else {
          const firstSpaceIdx = rawLanguageSpec.search(/\s/);
          if (firstSpaceIdx === -1) {
            codeLanguage = rawLanguageSpec;
          } else {
            codeLanguage = rawLanguageSpec.slice(0, firstSpaceIdx);
            const inlineFirstLine = rawLanguageSpec.slice(firstSpaceIdx).trim();
            if (inlineFirstLine.length > 0) {
              currentContent.push(inlineFirstLine);
            }
          }
        }
        currentType = 'code';
      } else {
        // Ending code block
        inCodeBlock = false;
        flushCurrentSection();
        currentType = 'text';
      }
      continue;
    }

    if (inCodeBlock) {
      // Pass code content unchanged to preserve syntax
      // For Mermaid diagrams, sanitization is handled by the MermaidDiagram component
      currentContent.push(line);
      continue;
    }

    // Handle horizontal rules (before other parsing)
    if (trimmedLine.match(/^---+\s*$/)) {
      flushCurrentSection();
      sections.push({
        type: 'hr'
      });
      continue;
    }

    // Handle images - check for markdown image syntax
    const imageMatch = trimmedLine.match(/^!\[([^\]]*)\]\s*\(([^)]+)\)(.*)$/);
    if (imageMatch) {
      flushCurrentSection();
      sections.push({
        type: 'image',
        alt: imageMatch[1] || '',
        src: imageMatch[2],
        caption: imageMatch[3]?.trim() || ''
      });
      continue;
    }

    // Handle tables
    if (isLikelyTableLine(line)) {
      if (currentType !== 'table') {
        flushCurrentSection();
        currentType = 'table';
      }
      currentContent.push(line);
      continue;
    }

    // Handle headers (support up to 6 levels as per markdown spec)
    if (trimmedLine.startsWith('#')) {
      flushCurrentSection();
      const level = (trimmedLine.match(/^#+/) || [''])[0].length;
      const content = trimmedLine.replace(/^#+\s*/, '').trim();

      // Skip headers with empty content (e.g., just "#" or "##")
      if (content.length > 0) {
        sections.push({
          type: 'header',
          level: Math.min(Math.max(level, 1), 6), // Support h1-h6
          content
        });
      }
      continue;
    }

    // Handle SOURCES section specifically (strip markdown formatting first)
    const strippedLine = trimmedLine.replace(/^\*\*|\*\*$/g, '').replace(/^📚\s*/, ''); // Remove bold markdown and emoji

    // Check for new format: ═══════════════ SOURCES & LEGAL REFERENCES ═══════════════
    if (trimmedLine.match(/^═+\s*SOURCES\s*[&\s]*LEGAL\s*REFERENCES\s*═+$/i) ||
      strippedLine.toUpperCase().startsWith('SOURCES:') ||
      strippedLine.toUpperCase().startsWith('SOURCES &') ||
      strippedLine.toUpperCase().startsWith('SOURCES AND')) {
      flushCurrentSection();
      sections.push({ type: 'sources_header', content: 'SOURCES' });

      // All subsequent lines are part of the sources list until we hit a new section or end
      currentType = 'sources_list';
      currentContent = [];
      continue;
    }

    if (currentType === 'sources_list') {
      if (trimmedLine.length > 0) {
        currentContent.push(line);
      } else {
        // Allow empty lines within sources section - only end if we've collected items
        // and then hit an empty line (indicating end of section)
        if (currentContent.length > 0) {
          // Check if the next non-empty line looks like a source or new section
          // For now, just continue collecting (will flush at end or new section)
        }
      }
      continue;
    }

    // Handle quotes
    if (trimmedLine.startsWith('>')) {
      if (currentType !== 'quote') {
        flushCurrentSection();
        currentType = 'quote';
      }
      currentContent.push(trimmedLine.slice(1).trim());
      continue;
    }

    // Handle ordered lists
    if (trimmedLine.match(/^\d+\.\s/)) {
      if (currentListType !== 'ordered') {
        flushCurrentSection();
        currentListType = 'ordered';
      }
      listItems.push(trimmedLine.replace(/^\d+\.\s*/, ''));
      continue;
    }

    // Handle unordered lists (and source lists)
    if (trimmedLine.match(/^[\-\*\+]\s/)) {
      const listType = currentType === 'sources_list' ? 'sources' : 'unordered';
      if (currentListType !== listType) {
        flushCurrentSection();
        currentListType = listType;
      }
      listItems.push(trimmedLine.replace(/^[\-\*\+]\s*/, ''));
      continue;
    }

    // Handle regular text
    if (trimmedLine.length > 0) {
      if (currentType !== 'text' || listItems.length > 0) {
        flushCurrentSection();
        currentType = 'text';
      }
      currentContent.push(line);
    } else {
      // Empty line behavior: preserve within text sections for legal documents
      // Only flush if we're not in a text section (for lists, quotes, etc.)
      if (currentType === 'text') {
        // Keep the empty line as part of the text section to preserve paragraph spacing
        currentContent.push(line);
      } else {
        // For non-text sections (lists, quotes), flush immediately
        flushCurrentSection();
      }
    }
  }

  flushCurrentSection();
  return appendStructuredAppendixSections(sections, structuredPayload);
};

// Format inline text (bold, italic, code, etc.)
const formatInlineText = (text, theme, isUserMessage = false) => {
  if (!text || typeof text !== 'string') return text;

  // Process text directly without citation cleaning, preserve line breaks
  const cleanedText = text;

  const textColor = getTextColor(theme, isUserMessage);
  const parts = [];
  let lastIndex = 0;

  // Split by line breaks first to preserve paragraph structure
  const lines = cleanedText.split('\n');
  const processedLines = [];

  lines.forEach((line, lineIndex) => {
    if (line.trim() === '' && lineIndex > 0 && lineIndex < lines.length - 1) {
      // Preserve empty lines as line breaks
      processedLines.push(
        <Text key={`linebreak-${lineIndex}`} style={{ color: textColor }}>
          {'\n'}
        </Text>
      );
      return;
    }

    // Process inline formatting for each line
    const lineParts = [];
    let lineLastIndex = 0;

    // Regex for inline formatting
    const inlineRegex = /(\*\*([^*]+)\*\*)|(\*([^*]+)\*)|(`([^`]+)`)|(__([^_]+)__)|(_([^_]+)_)|(~~([^~]+)~~)/g;

    let match;
    while ((match = inlineRegex.exec(line)) !== null) {
      // Add text before match
      if (match.index > lineLastIndex) {
        lineParts.push(
          <Text key={`text-${lineIndex}-${lineLastIndex}`} style={{ color: textColor }}>
            {line.slice(lineLastIndex, match.index)}
          </Text>
        );
      }

      if (match[1]) {
        // **bold**
        lineParts.push(
          <Text key={`bold-${lineIndex}-${match.index}`} style={{ color: textColor, fontWeight: 'bold' }}>
            {match[2]}
          </Text>
        );
      } else if (match[3]) {
        // *italic*
        lineParts.push(
          <Text key={`italic-${lineIndex}-${match.index}`} style={{ color: textColor, fontStyle: 'italic' }}>
            {match[4]}
          </Text>
        );
      } else if (match[5]) {
        // `code`
        lineParts.push(
          <Text key={`code-${lineIndex}-${match.index}`} style={{
            backgroundColor: theme.isDark ? '#4a5568' : '#e2e8f0',
            color: theme.isDark ? '#e2e8f0' : '#2d3748',
            paddingHorizontal: 4,
            paddingVertical: 2,
            borderRadius: 3,
            fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
            fontSize: 15,
          }}>
            {match[6]}
          </Text>
        );
      } else if (match[7]) {
        // __bold__
        lineParts.push(
          <Text key={`bold2-${lineIndex}-${match.index}`} style={{ color: textColor, fontWeight: 'bold' }}>
            {match[8]}
          </Text>
        );
      } else if (match[9]) {
        // _italic_
        lineParts.push(
          <Text key={`italic2-${lineIndex}-${match.index}`} style={{ color: textColor, fontStyle: 'italic' }}>
            {match[10]}
          </Text>
        );
      } else if (match[11]) {
        // ~~strikethrough~~
        lineParts.push(
          <Text key={`strike-${lineIndex}-${match.index}`} style={{
            color: textColor,
            textDecorationLine: 'line-through'
          }}>
            {match[12]}
          </Text>
        );
      }

      lineLastIndex = match.index + match[0].length;
    }

    // Add remaining text in line
    if (lineLastIndex < line.length) {
      lineParts.push(
        <Text key={`text-${lineIndex}-${lineLastIndex}`} style={{ color: textColor }}>
          {line.slice(lineLastIndex)}
        </Text>
      );
    }

    // If no formatting found, add the whole line
    if (lineParts.length === 0 && line.length > 0) {
      lineParts.push(
        <Text key={`line-${lineIndex}`} style={{ color: textColor }}>
          {line}
        </Text>
      );
    }

    processedLines.push(...lineParts);

    // Add line break between lines (except for the last line)
    if (lineIndex < lines.length - 1) {
      processedLines.push(
        <Text key={`newline-${lineIndex}`} style={{ color: textColor }}>
          {'\n'}
        </Text>
      );
    }
  });

  return processedLines.length > 0 ? processedLines : text;
};

// Enhanced Main Rich Message Renderer Component
const RichMessageRenderer = memo(({ content, theme, isUserMessage = false, citations = [], suppressSources = false, onOpenReader, disableMermaid = false }) => {
  // Handle clicks on inline web citations injected by inlineMarkdownToHtml
  useEffect(() => {
    if (Platform.OS === 'web' && typeof document !== 'undefined') {
      const handleCitationClick = (e) => {
        // Find closest anchor with our class (but NOT download links)
        const target = e.target.closest('.web-inline-citation');
        if (target && !target.classList.contains('download-link')) {
          e.preventDefault();
          e.stopPropagation();

          const webUrl = target.getAttribute('data-url');

          if (webUrl) {
            console.log(`🔗 Inline web citation clicked: ${webUrl}`);
            if (onOpenReader) {
              onOpenReader(webUrl, 'citation');
            } else {
              // Fallback if no reader handler
              window.open(webUrl, '_blank');
            }
          }
        }
      };

      document.addEventListener('click', handleCitationClick);
      return () => {
        document.removeEventListener('click', handleCitationClick);
      };
    }
  }, [onOpenReader]);

  // Safety check for theme
  if (!theme) {
    theme = { isDark: false, text: '#2d3748', botMessageText: '#2d3748' }; // Default to light theme
  }

  const containerRef = useRef(null);

  // Strip inline [vault:<id>] / [internet:<host>] / [structured:<table>] /
  // [general-knowledge] tokens before parsing, and remember which refs the body
  // contained so we can backfill the Sources appendix when the CITATIONS SSE
  // payload is missing entries.
  const { cleanedContent, inlineCitationRefs } = useMemo(() => {
    const { cleanedText, inlineRefs } = stripInlineCitationTokens(content ?? '');
    return { cleanedContent: cleanedText, inlineCitationRefs: inlineRefs };
  }, [content]);

  const memoizedSections = useMemo(() => parseRichContent(cleanedContent), [cleanedContent]);

  // Augment parsed sections with sources derived from the structured `citations`
  // prop (object form: { documents, web, chunks, ... }) and from inlineRefs as
  // a fallback for ids the LLM didn't include in its trailing JSON block. Legacy
  // callers passing an array `citations` continue to use the existing
  // shouldRenderCitationsAppendix path below.
  const augmentedSections = useMemo(() => {
    if (suppressSources) return memoizedSections;
    const isStructured = citations && typeof citations === 'object' && !Array.isArray(citations);
    if (!isStructured && inlineCitationRefs.length === 0) {
      return memoizedSections;
    }
    const structured = isStructured ? citations : null;
    const augmented = {
      documents: Array.isArray(structured?.documents) ? [...structured.documents] : [],
      web: Array.isArray(structured?.web) ? [...structured.web] : [],
      chunks: Array.isArray(structured?.chunks) ? [...structured.chunks] : [],
    };
    if (structured) {
      Object.keys(structured).forEach((k) => {
        if (!(k in augmented)) augmented[k] = structured[k];
      });
    }
    const docIds = new Set(
      augmented.documents
        .map((d) => (d && typeof d.document_id === 'string' ? d.document_id.trim() : ''))
        .filter(Boolean)
    );
    const webHosts = new Set(
      augmented.web
        .map((w) => {
          try { return w?.url ? new URL(w.url).hostname.replace(/^www\./, '') : ''; } catch (_e) { return ''; }
        })
        .filter(Boolean)
    );
    inlineCitationRefs.forEach((ref) => {
      if (ref.kind === 'vault' && !docIds.has(ref.id)) {
        augmented.documents.push({ document_id: ref.id, display_name: 'Document' });
        docIds.add(ref.id);
      } else if (ref.kind === 'internet') {
        const host = ref.id.replace(/^www\./, '');
        if (host && !webHosts.has(host)) {
          augmented.web.push({ url: `https://${host}`, display_name: host });
          webHosts.add(host);
        }
      }
    });
    const { header, items } = buildSourceLinesFromStructuredCitations(augmented);
    if (!items.length) return memoizedSections;
    const sectionsCopy = [...memoizedSections];
    const existingListIdx = sectionsCopy.findIndex((s) => s.type === 'sources_list');
    const existingHeaderIdx = sectionsCopy.findIndex((s) => s.type === 'sources_header');
    if (existingListIdx !== -1) {
      const existingItems = sectionsCopy[existingListIdx].items || [];
      sectionsCopy[existingListIdx] = {
        ...sectionsCopy[existingListIdx],
        items: [...existingItems, ...items],
      };
      if (existingHeaderIdx !== -1 && header) {
        sectionsCopy[existingHeaderIdx] = { ...sectionsCopy[existingHeaderIdx], content: header };
      }
    } else {
      if (existingHeaderIdx === -1) {
        sectionsCopy.push({ type: 'sources_header', content: header || 'SOURCES' });
      }
      sectionsCopy.push({ type: 'sources_list', items });
    }
    return sectionsCopy;
  }, [memoizedSections, citations, inlineCitationRefs, suppressSources]);

  const { sourceSections, nonSourceSections, hasSourcesInContent } = useMemo(() => {
    const sources = augmentedSections.filter((s) => s.type === 'sources_header' || s.type === 'sources_list');
    const nonSources = augmentedSections.filter((s) => s.type !== 'sources_header' && s.type !== 'sources_list');
    return {
      sourceSections: sources,
      nonSourceSections: nonSources,
      hasSourcesInContent: sources.length > 0,
    };
  }, [augmentedSections]);

  const orderedSections = useMemo(() => (
    suppressSources ? nonSourceSections : [...nonSourceSections, ...sourceSections]
  ), [nonSourceSections, sourceSections, suppressSources]);

  // Only render appended citations when sources are not already present and not suppressed
  const shouldRenderCitationsAppendix = useMemo(() => (
    !suppressSources && !hasSourcesInContent && Array.isArray(citations) && citations.length > 0
  ), [suppressSources, hasSourcesInContent, citations]);

  const baseTextColor = useMemo(() => getTextColor(theme, isUserMessage), [theme, isUserMessage]);
  const isDarkTheme = !!theme?.isDark;

  const handleCopy = useCallback((event) => {
    if (Platform.OS !== 'web') {
      return;
    }

    if (!containerRef.current) {
      return;
    }

    if (typeof window === 'undefined' || typeof document === 'undefined') {
      return;
    }

    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) {
      return;
    }

    const range = selection.getRangeAt(0);
    if (!range || !containerRef.current.contains(range.commonAncestorContainer)) {
      return;
    }

    if (!event.clipboardData) {
      return;
    }

    const fragment = range.cloneContents();
    if (!fragment) {
      return;
    }

    const wrapper = document.createElement('div');
    wrapper.appendChild(fragment);

    // Remove interactive elements that do not make sense in Word
    wrapper.querySelectorAll('button.copy-button, button').forEach((node) => node.remove());
    wrapper.querySelectorAll('[data-skip-copy="true"]').forEach((node) => node.remove());

    // Remove stray markdown markers that might leak into the DOM (e.g., isolated "#" lines)
    if (typeof document !== 'undefined' && document.createTreeWalker && typeof NodeFilter !== 'undefined') {
      const walker = document.createTreeWalker(wrapper, NodeFilter.SHOW_TEXT);
      const orphanNodes = [];
      let currentNode = walker.nextNode();
      while (currentNode) {
        if (currentNode.textContent && currentNode.textContent.trim() === '#') {
          orphanNodes.push(currentNode);
        }
        currentNode = walker.nextNode();
      }
      orphanNodes.forEach((node) => {
        if (node.parentNode) {
          node.parentNode.removeChild(node);
        }
      });
    }

    const html = wrapper.innerHTML.trim();
    if (!html) {
      return;
    }

    const plainText = wrapper.textContent || selection.toString() || '';

    const palette = {
      baseText: baseTextColor,
      border: isDarkTheme ? '#475569' : '#cbd5e1',
      inlineCodeBg: isDarkTheme ? '#1e293b' : '#f3f4f6',
      codeBg: isDarkTheme ? '#0f172a' : '#f8fafc',
      link: isDarkTheme ? '#79c0ff' : '#1d4ed8'
    };

    const htmlFragment = `<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <style>
      body { font-family: "Times New Roman", "Segoe UI", sans-serif; font-size: 15px; line-height: 1.6; color: ${palette.baseText}; }
      h1, h2, h3, h4, h5, h6 { font-weight: 600; margin-top: 1.2em; margin-bottom: 0.5em; color: ${palette.baseText}; }
      p { margin: 0 0 0.8em 0; }
      div { margin: 0 0 0.8em 0; }
      ul, ol { margin: 0 0 0.8em 1.8em; }
      li { margin-bottom: 0.4em; }
      blockquote { margin: 0 0 0.8em 0; padding-left: 12px; border-left: 4px solid ${palette.border}; background: ${palette.inlineCodeBg}; font-style: italic; }
      code, kbd { font-family: "Courier New", monospace; background: ${palette.inlineCodeBg}; padding: 2px 4px; border-radius: 3px; }
      pre { font-family: "Courier New", monospace; background: ${palette.codeBg}; padding: 12px; border-radius: 6px; border: 1px solid ${palette.border}; overflow-x: auto; }
      table { border-collapse: collapse; width: 100%; margin: 0 0 0.8em 0; }
      th, td { border: 1px solid ${palette.border}; padding: 6px 8px; text-align: left; }
      a { color: ${palette.link}; text-decoration: underline; }
    </style>
  </head>
  <body>
    <!--StartFragment-->${html}<!--EndFragment-->
  </body>
</html>`;

    event.preventDefault();
    event.clipboardData.setData('text/html', htmlFragment);
    if (plainText) {
      event.clipboardData.setData('text/plain', plainText);
    }
  }, [baseTextColor, isDarkTheme]);

  const renderedWebSections = useMemo(() => {
    if (Platform.OS !== 'web') {
      return null;
    }

    return orderedSections.map((section, index) => {
      try {  // Add error boundary for each section
        switch (section.type) {
          case 'text': {
            const content = section.content;

            // Simple line-by-line rendering - respect LLM formatting
            const lines = content.split('\n');

            return (
              <div key={`text-${index}`} style={{
                fontFamily: 'Times New Roman, serif',
                fontSize: '15px',
                lineHeight: '1.8',
                color: baseTextColor
              }}>
                {lines.map((line, lineIdx) => {
                  const trimmed = line.trim();

                  // Empty line = spacer
                  if (!trimmed) {
                    return <div key={`line-${lineIdx}`} style={{ height: '12px' }} />;
                  }

                  // Render line with markdown support
                  return (
                    <div key={`line-${lineIdx}`} style={{ marginBottom: '4px' }}>
                      <span dangerouslySetInnerHTML={{ __html: inlineMarkdownToHtml(line, theme) }} />
                    </div>
                  );
                })}
              </div>
            );
          }

          case 'header': {
            const level = Math.min(Math.max(section.level || 1, 1), 6);
            const HeaderTag = `h${level}`;

            const headerStyles = {
              1: { fontSize: '26px', marginTop: '24px', marginBottom: '16px', fontWeight: 'bold' },
              2: { fontSize: '22px', marginTop: '20px', marginBottom: '14px', fontWeight: 'bold' },
              3: { fontSize: '19px', marginTop: '18px', marginBottom: '12px', fontWeight: 'bold' },
              4: { fontSize: '17px', marginTop: '16px', marginBottom: '10px', fontWeight: '600' },
              5: { fontSize: '15px', marginTop: '14px', marginBottom: '8px', fontWeight: '600' },
              6: { fontSize: '14px', marginTop: '12px', marginBottom: '6px', fontWeight: '600' }
            };

            const style = headerStyles[level] || headerStyles[3];

            return React.createElement(HeaderTag, {
              key: `header-${index}`,
              style: {
                color: baseTextColor,
                margin: `${style.marginTop} 0 ${style.marginBottom} 0`,
                fontWeight: style.fontWeight,
                fontSize: style.fontSize,
                lineHeight: '1.4',
                wordWrap: 'break-word',
                overflowWrap: 'break-word',
                wordBreak: 'break-word',
                maxWidth: '100%'
              }
            }, parseTextWithCitations(section.content, theme, onOpenReader));
          }

          case 'sources_list': {
            const dedupedSources = deduplicateSourceItems(section.items);

            // Parse sources hierarchically (group chunks under documents)
            const { documentGroups, orderedItems } = parseSourcesHierarchical(dedupedSources, theme);

            // Debug: Log before calling HierarchicalSourcesRenderer
            console.log('🔶 SOURCES_LIST_RENDER: About to render HierarchicalSourcesRenderer', {
              hasOnOpenReader: !!onOpenReader,
              onOpenReaderType: typeof onOpenReader,
              documentGroupsSize: documentGroups?.size,
              orderedItemsCount: orderedItems?.length
            });

            return (
              <div key={`sources-list-${index}`} style={{ marginTop: '0.5em' }}>
                <HierarchicalSourcesRenderer
                  documentGroups={documentGroups}
                  orderedItems={orderedItems}
                  theme={theme}
                  onCollectionGroupClick={(citations) => {
                    setCollectionModalVisible(true);
                    setSelectedCollectionCitations(citations);
                  }}
                  onOpenReader={onOpenReader}
                />
              </div>
            );
          }

          case 'list': {
            const ListTag = section.ordered ? 'ol' : 'ul';
            return React.createElement(ListTag, {
              key: `list-${index}`,
              style: {
                margin: '1em 0',
                paddingLeft: '2em',
                color: baseTextColor,
                listStyleType: section.ordered ? 'decimal' : 'disc'
              }
            }, section.items.map((item, itemIndex) => {
              const itemContent = parseTextWithCitations(item, theme, onOpenReader);
              return React.createElement('li', {
                key: itemIndex,
                style: {
                  margin: '0.5em 0',
                  lineHeight: '1.6',
                  color: baseTextColor
                }
              }, itemContent);
            }));
          }

          case 'code':
            // Check if this is an ASCII/Unicode box diagram (lightweight alternative to Mermaid)
            if (section.language && ASCII_DIAGRAM_LANGUAGES.has(section.language.toLowerCase())) {
              try {
                return (
                  <AsciiDiagram
                    key={`ascii-${index}`}
                    content={section.content}
                    theme={theme}
                  />
                );
              } catch (asciiError) {
                console.error('AsciiDiagram rendering error:', asciiError);
                return (
                  <CodeBlock
                    key={`code-${index}`}
                    content={section.content}
                    language={section.language}
                    theme={theme}
                    isUserMessage={isUserMessage}
                  />
                );
              }
            }

            // Check if this is a Mermaid diagram
            // disableMermaid=true (Action Chat) renders as a plain code
            // block instead — Action Chat is ASCII-only for diagrams.
            if (section.language === 'mermaid') {
              if (disableMermaid) {
                return (
                  <CodeBlock
                    key={`code-${index}`}
                    content={section.content}
                    language="mermaid"
                    theme={theme}
                    isUserMessage={isUserMessage}
                  />
                );
              }
              // Wrap Mermaid in error boundary
              try {
                return (
                  <MermaidDiagram
                    key={`mermaid-${index}`}
                    diagramCode={section.content}
                    theme={theme}
                  />
                );
              } catch (mermaidError) {
                console.error('Mermaid rendering error:', mermaidError);
                // Fallback to code block if Mermaid fails
                return (
                  <CodeBlock
                    key={`code-${index}`}
                    content={section.content}
                    language="mermaid"
                    theme={theme}
                    isUserMessage={isUserMessage}
                  />
                );
              }
            }

            // Check if this is a Chart.js chart
            if (section.language === 'chartjs' || section.language === 'chart.js') {
              try {
                return (
                  <ChartJsDiagram
                    key={`chartjs-${index}`}
                    chartConfig={section.content}
                    theme={theme}
                  />
                );
              } catch (chartError) {
                console.error('Chart.js rendering error:', chartError);
                return (
                  <CodeBlock
                    key={`code-${index}`}
                    content={section.content}
                    language="json"
                    theme={theme}
                    isUserMessage={isUserMessage}
                  />
                );
              }
            }

            return (
              <CodeBlock
                key={`code-${index}`}
                content={section.content}
                language={section.language}
                theme={theme}
                isUserMessage={isUserMessage}
              />
            );

          case 'table':
            return (
              <MarkdownTable
                key={`table-${index}`}
                content={section.content}
                theme={theme}
                isUserMessage={isUserMessage}
              />
            );

          case 'quote': {
            const quoteContent = parseTextWithCitations(section.content, theme, onOpenReader);
            return (
              <blockquote
                key={`quote-${index}`}
                style={{
                  borderLeft: `4px solid ${theme.isDark ? '#4a9eff' : '#007acc'}`,
                  backgroundColor: theme.isDark ? '#2d3748' : '#f7fafc',
                  margin: '1em 0',
                  padding: '1em',
                  borderRadius: '0 8px 8px 0',
                  fontStyle: 'italic',
                  color: baseTextColor
                }}
              >
                {quoteContent}
              </blockquote>
            );
          }

          case 'hr':
            return (
              <hr
                key={`hr-${index}`}
                style={{
                  border: 'none',
                  height: '2px',
                  backgroundColor: theme.isDark ? '#4a5568' : '#e2e8f0',
                  margin: '1.5em 0',
                  borderRadius: '1px'
                }}
              />
            );

          case 'sources_header':
            return (
              <div
                key={`sources-header-${index}`}
                style={{
                  marginTop: '2em',
                  marginBottom: '0.5em',
                  paddingBottom: '0.5em',
                  borderBottom: `2px solid ${theme.isDark ? '#4a5568' : '#e2e8f0'}`
                }}
              >
                <h4 style={{
                  fontSize: '16px',
                  fontWeight: '600',
                  color: baseTextColor,
                  margin: 0,
                  textTransform: 'uppercase',
                  letterSpacing: '0.5px'
                }}>
                  📚 {section.content || 'SOURCES'}
                </h4>
              </div>
            );

          case 'image':
            return (
              <ImageRenderer
                key={`image-${index}`}
                src={section.src}
                alt={section.alt}
                caption={section.caption}
                theme={theme}
                isUserMessage={isUserMessage}
              />
            );

          default:
            return null;
        }
      } catch (sectionError) {
        console.error(`Error rendering section ${index} of type ${section.type}:`, sectionError);
        // Return a fallback element instead of crashing
        return (
          <div key={`error-${index}`} style={{
            color: 'red',
            padding: '10px',
            border: '1px solid red',
            margin: '10px 0',
            borderRadius: '4px'
          }}>
            Error rendering content. Please refresh the page.
          </div>
        );
      }
    });
  }, [orderedSections, theme, isUserMessage, baseTextColor, cleanedContent, onOpenReader]);

  const renderedWebCitations = useMemo(() => {
    if (!shouldRenderCitationsAppendix || Platform.OS !== 'web') {
      return null;
    }

    return (
      <div style={{ marginTop: '1em' }}>
        <div
          style={{
            marginTop: '2em',
            marginBottom: '0.5em',
            paddingBottom: '0.5em',
            borderBottom: `2px solid ${theme.isDark ? '#4a5568' : '#e2e8f0'}`
          }}
        >
          <h4 style={{
            fontSize: '16px',
            fontWeight: 600,
            color: getTextColor(theme, false),
            margin: 0,
            textTransform: 'uppercase',
            letterSpacing: '0.5px'
          }}>
            📚 SOURCES:
          </h4>
        </div>

        <div style={{ marginTop: '0.5em' }}>
          {citations.map((c, i) => {
            const topic = (c && (c.topic_or_filename || c.topic)) || '';
            const parts = String(topic).split('--');
            const urlCandidate = parts.length >= 2 ? parts[parts.length - 1] : '';
            const hasUrl = typeof urlCandidate === 'string' && (urlCandidate.startsWith('http://') || urlCandidate.startsWith('https://'));
            const rawName = hasUrl ? parts.slice(0, parts.length - 1).join('--').trim() || topic : (topic || 'Unknown Source');
            const linkId = hasUrl ? urlCandidate : (c && c.document_id) || '';
            const friendlyName = stripDocumentIdPrefix(rawName, hasUrl ? '' : linkId);
            const label = friendlyName || rawName;
            return (
              <div key={`cit-${i}`} style={{ marginLeft: '1em', marginBottom: '0.2em' }}>
                • <CitationLink onOpenReader={onOpenReader} documentId={linkId} theme={theme}>
                  {label}
                </CitationLink>
              </div>
            );
          })}
        </div>
      </div>
    );
  }, [shouldRenderCitationsAppendix, citations, theme, onOpenReader]);

  const renderedNativeSections = useMemo(() => {
    if (Platform.OS === 'web') {
      return null;
    }

    return orderedSections.map((section, index) => {
      switch (section.type) {
        case 'image':
          return (
            <ImageRenderer
              key={`image-${index}`}
              src={section.src}
              alt={section.alt}
              caption={section.caption}
              theme={theme}
              isUserMessage={isUserMessage}
            />
          );

        case 'table':
          return (
            <MarkdownTable
              key={`table-${index}`}
              content={section.content}
              theme={theme}
              isUserMessage={isUserMessage}
            />
          );

        case 'hr':
          return (
            <HorizontalRule
              key={`hr-${index}`}
              theme={theme}
              isUserMessage={isUserMessage}
            />
          );

        case 'header':
          return (
            <SectionHeader
              key={`header-${index}`}
              content={section.content}
              level={section.level}
              theme={theme}
              isUserMessage={isUserMessage}
            />
          );

        case 'sources_header':
          return (
            <SourcesHeader
              key={`sources-header-${index}`}
              content={section.content}
              theme={theme}
              isUserMessage={isUserMessage}
            />
          );

        case 'sources_list': {
          const dedupedSources = deduplicateSourceItems(section.items);
          const { documentGroups, orderedItems } = parseSourcesHierarchical(dedupedSources, theme);

          return (
            <View key={`sources-list-${index}`} style={{ marginVertical: 8 }}>
              <HierarchicalSourcesRenderer
                documentGroups={documentGroups}
                orderedItems={orderedItems}
                theme={theme}
                onCollectionGroupClick={(citations) => {
                  setCollectionModalVisible(true);
                  setSelectedCollectionCitations(citations);
                }}
                onOpenReader={onOpenReader}
              />
            </View>
          );
        }

        case 'list':
          return (
            <View key={`list-${index}`} style={{ marginVertical: 8 }}>
              {section.items.map((item, itemIndex) => (
                <ListItem
                  key={itemIndex}
                  content={item}
                  ordered={section.ordered}
                  index={itemIndex}
                  theme={theme}
                  isUserMessage={isUserMessage}
                  onOpenReader={onOpenReader}
                />
              ))}
            </View>
          );

        case 'code':
          // Check if this is a Mermaid diagram
          // disableMermaid=true (Action Chat) renders as a plain code
          // block instead — Action Chat is ASCII-only for diagrams.
          if (section.language === 'mermaid') {
            if (disableMermaid) {
              return (
                <CodeBlock
                  key={`code-${index}`}
                  content={section.content}
                  language="mermaid"
                  theme={theme}
                  isUserMessage={isUserMessage}
                />
              );
            }
            console.log(`📊 [RENDER_NATIVE_DEBUG] Rendering Mermaid diagram, content length: ${section.content?.length || 0}`);
            return (
              <MermaidDiagram
                key={`mermaid-${index}`}
                diagramCode={section.content}
                theme={theme}
              />
            );
          }

          // Check if this is a Chart.js chart
          if (section.language === 'chartjs' || section.language === 'chart.js') {
            return (
              <ChartJsDiagram
                key={`chartjs-${index}`}
                chartConfig={section.content}
                theme={theme}
              />
            );
          }

          return (
            <CodeBlock
              key={`code-${index}`}
              content={section.content}
              language={section.language}
              theme={theme}
              isUserMessage={isUserMessage}
            />
          );

        case 'quote':
          return (
            <QuoteBlock
              key={`quote-${index}`}
              content={section.content}
              theme={theme}
              isUserMessage={isUserMessage}
            />
          );

        case 'text':
        default: {
          const formattedText = formatInlineText(section.content, theme, isUserMessage);
          return (
            <Text
              key={`text-${index}`}
              style={[
                styles.messageText,
                {
                  color: baseTextColor,
                  fontSize: 15,
                  lineHeight: 20,
                  marginVertical: 4,
                }
              ]}
            >
              {formattedText}
            </Text>
          );
        }
      }
    });
  }, [orderedSections, theme, isUserMessage, baseTextColor, onOpenReader]);

  const renderedNativeCitations = useMemo(() => {
    if (!shouldRenderCitationsAppendix || Platform.OS === 'web') {
      return null;
    }

    return (
      <View style={{ marginTop: 16 }}>
        <View style={{
          marginTop: 24,
          marginBottom: 8,
          paddingBottom: 8,
          borderBottomWidth: 2,
          borderBottomColor: theme.isDark ? '#4a5568' : '#e2e8f0',
        }}>
          <Text style={{
            fontSize: 15,
            fontWeight: '600',
            color: getTextColor(theme, false),
            textTransform: 'uppercase',
            letterSpacing: 0.5,
          }}>
            📚 SOURCES:
          </Text>
        </View>
        <View style={{ marginVertical: 8 }}>
          {citations.map((c, i) => {
            const topic = (c && (c.topic_or_filename || c.topic)) || '';
            const parts = String(topic).split('--');
            const urlCandidate = parts.length >= 2 ? parts[parts.length - 1] : '';
            const hasUrl = typeof urlCandidate === 'string' && (urlCandidate.startsWith('http://') || urlCandidate.startsWith('https://'));
            const rawName = hasUrl ? parts.slice(0, parts.length - 1).join('--').trim() || topic : (topic || 'Unknown Source');
            const linkId = hasUrl ? urlCandidate : (c && c.document_id) || '';
            const friendlyName = stripDocumentIdPrefix(rawName, hasUrl ? '' : linkId);
            const label = friendlyName || rawName;
            return (
              <View key={`cit-${i}`} style={{ flexDirection: 'row', alignItems: 'flex-start', marginLeft: 16, marginBottom: 4 }}>
                <Text style={{ color: theme.text, marginRight: 8 }}>•</Text>
                <CitationLink onOpenReader={onOpenReader} documentId={linkId} theme={theme}>
                  {label}
                </CitationLink>
              </View>
            );
          })}
        </View>
      </View>
    );
  }, [shouldRenderCitationsAppendix, citations, theme, onOpenReader]);

  const hasRenderableSections = orderedSections.length > 0;



  if (!hasRenderableSections && !shouldRenderCitationsAppendix && cleanedContent.length === 0) {
    // Reduced logging - was too verbose
    // console.log('🔧 [RICH_RENDERER] No content to render, returning null');
    return null;
  }

  // For web platform, use portal to render outside React Native View hierarchy

  if (Platform.OS === 'web') {
    try {
      return (
        <>
          <div
            ref={containerRef}
            onCopy={handleCopy}
            style={{
              fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
              lineHeight: '1.6',
              wordWrap: 'break-word',
              overflowWrap: 'break-word',
              maxWidth: '100%',  // Prevent container overflow
              position: 'relative',  // Ensure proper positioning context
            }}
          >
            {renderedWebSections}
            {renderedWebCitations}
          </div>
        </>
      );
    } catch (renderError) {
      console.error('Fatal render error in RichMessageRenderer:', renderError);
      // Return minimal fallback UI
      return (
        <div style={{
          color: theme.isDark ? '#e2e8f0' : '#2d3748',
          padding: '10px',
          fontFamily: 'sans-serif'
        }}>
          <p>Error displaying message content. Raw content preserved below:</p>
          <pre style={{
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            backgroundColor: theme.isDark ? '#2d3748' : '#f7fafc',
            padding: '10px',
            borderRadius: '4px',
            fontSize: '14px',
            overflow: 'auto'
          }}>
            {cleanedContent}
          </pre>
        </div>
      );
    }
  }

  // For mobile platforms or fallback, use original View-based approach
  return (
    <View style={styles.richContentContainer}>
      {renderedNativeSections}
      {renderedNativeCitations}
    </View>
  );
});

// Enhanced CSS Styles for Web Platform
if (Platform.OS === 'web') {
  const webStyles = `
    <style>
      /* Enhanced Table Styles */
      .table-wrapper {
        margin: 1.5rem 0;
        overflow-x: auto;
        border-radius: 8px;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
      }
      
      .markdown-table {
        width: 100%;
        border-collapse: collapse;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background-color: var(--table-bg, #ffffff);
        border: 1px solid var(--table-border, #e2e8f0);
      }
      
      .markdown-table th {
        background-color: var(--table-header-bg, #f8f9fa);
        color: var(--table-header-text, #2d3748);
        font-weight: 600;
        padding: 0.75rem;
        text-align: left;
        border-bottom: 2px solid var(--table-border, #e2e8f0);
        border-right: 1px solid var(--table-border, #e2e8f0);
      }
      
      .markdown-table td {
        padding: 0.75rem;
        border-bottom: 1px solid var(--table-border, #e2e8f0);
        border-right: 1px solid var(--table-border, #e2e8f0);
        color: var(--table-text, #2d3748);
        line-height: 1.5;
      }
      
      .markdown-table tr:hover {
        background-color: var(--table-hover-bg, #f1f5f9);
      }
      
      .markdown-table th:last-child,
      .markdown-table td:last-child {
        border-right: none;
      }
      
      /* Enhanced Code Block Styles */
      .code-block-wrapper {
        margin: 1.5rem 0;
        border-radius: 8px;
        overflow: hidden;
        border: 1px solid var(--code-border, #e1e5e9);
        background-color: var(--code-bg, #f6f8fa);
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
      }
      
      .code-block-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 0.5rem 1rem;
        background-color: var(--code-header-bg, #f1f3f4);
        border-bottom: 1px solid var(--code-border, #e1e5e9);
      }
      
      .code-language {
        font-size: 0.75rem;
        font-weight: 600;
        color: var(--code-language-text, #586069);
        text-transform: uppercase;
        letter-spacing: 0.5px;
      }
      
      .copy-button {
        background: var(--button-bg, #ffffff);
        border: 1px solid var(--code-border, #e1e5e9);
        color: var(--code-language-text, #586069);
        padding: 0.25rem 0.5rem;
        border-radius: 4px;
        font-size: 0.75rem;
        cursor: pointer;
        transition: all 0.2s ease;
      }
      
      .copy-button:hover {
        background-color: var(--button-hover-bg, #f3f4f6);
        border-color: var(--code-language-text, #586069);
      }
      
      .code-block {
        margin: 0;
        padding: 1rem;
        font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, 'Courier New', monospace;
        font-size: 0.875rem;
        line-height: 1.5;
        color: var(--code-text, #24292e);
        background-color: var(--code-bg, #f6f8fa);
        overflow-x: auto;
        white-space: pre;
        tab-size: 2;
      }
      
      .code-block code {
        background: none;
        padding: 0;
        font-size: inherit;
        color: inherit;
      }
      
      /* Enhanced Inline Code Styles */
      .inline-code {
        background-color: var(--inline-code-bg, #f3f4f6);
        color: var(--inline-code-text, #e53e3e);
        padding: 0.125rem 0.25rem;
        border-radius: 3px;
        font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, 'Courier New', monospace;
        font-size: 15px;
        border: 1px solid var(--inline-code-border, #e2e8f0);
      }
      
      /* Enhanced Math Expression Styles */
      .math-expression {
        background-color: var(--math-bg, #f7fafc);
        border: 1px solid var(--math-border, #cbd5e0);
        border-radius: 4px;
        padding: 0.125rem 0.25rem;
        font-family: 'Latin Modern Math', 'STIX Two Math', 'Times New Roman', serif;
        color: var(--math-text, #2d3748);
      }
      
      /* Enhanced Keyboard Key Styles */
      .keyboard-key {
        display: inline-block;
        background: linear-gradient(to bottom, #f9f9f9, #e9e9e9);
        border: 1px solid #aaa;
        border-radius: 4px;
        box-shadow: 0 1px 0 rgba(0, 0, 0, 0.2), 0 0 0 2px #fff inset;
        color: #333;
        font-family: Arial, sans-serif;
        font-size: 0.75em;
        font-weight: bold;
        line-height: 1;
        padding: 0.125rem 0.25rem;
        text-align: center;
        text-shadow: 0 1px 0 #fff;
        min-width: 1.5em;
      }
      
      /* Enhanced Link Styles */
      .rich-link {
        color: var(--link-color, #79c0ff) !important;
        text-decoration: none !important;
        border-bottom: 1px solid transparent !important;
        transition: all 0.2s ease !important;
      }
      
      .rich-link:hover {
        color: var(--link-hover-color, #b3d9ff) !important;
        border-bottom-color: var(--link-hover-color, #b3d9ff) !important;
        text-decoration: none !important;
      }
      
      .rich-link:visited {
        color: var(--link-visited-color, #bc8cff) !important;
      }
      
      /* Citation Link Styles for Downloadable Documents */
      .citation-link {
        color: var(--link-color, #0366d6);
        text-decoration: none;
        border-bottom: 1px solid transparent;
        transition: all 0.2s ease;
        cursor: pointer;
        font-weight: 500;
        background: linear-gradient(120deg, #f7fafc 0%, #edf2f7 100%);
        padding: 2px 6px;
        border-radius: 4px;
        border: 1px solid var(--citation-border, #cbd5e0);
      }
      
      .citation-link:hover {
        color: var(--link-hover-color, #0256cc);
        background: linear-gradient(120deg, #e2e8f0 0%, #cbd5e0 100%);
        border-color: var(--link-hover-color, #0256cc);
        text-decoration: none;
        transform: translateY(-1px);
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
      }
      
      .citation-link:active {
        transform: translateY(0);
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
      }
      
      /* Enhanced Text Formatting */
      .rich-bold {
        font-weight: 600;
        color: var(--bold-text, inherit);
      }
      
      .rich-italic {
        font-style: italic;
      }
      
      .rich-strikethrough {
        text-decoration: line-through;
        opacity: 0.7;
      }
      
      .rich-highlight {
        background-color: var(--highlight-bg, #fff3cd);
        color: var(--highlight-text, #856404);
        padding: 0.125rem 0.25rem;
        border-radius: 3px;
      }
      
      /* Enhanced paragraph spacing and line preservation */
      .content-text {
        white-space: pre-wrap !important;
        word-wrap: break-word !important;
        overflow-wrap: break-word !important;
      }
      
      .rich-message-content {
        white-space: pre-wrap;
        word-wrap: break-word;
        overflow-wrap: break-word;
      }
      
      /* Dark Theme Support */
      @media (prefers-color-scheme: dark) {
        :root {
          --table-bg: #1a202c;
          --table-border: #4a5568;
          --table-header-bg: #2d3748;
          --table-header-text: #e2e8f0;
          --table-text: #e2e8f0;
          --table-hover-bg: #2d3748;
          
          --code-bg: #1a1a1a;
          --code-border: #30363d;
          --code-header-bg: #262626;
          --code-text: #e6edf3;
          --code-language-text: #7d8590;
          --button-bg: #21262d;
          --button-hover-bg: #30363d;
          
          --inline-code-bg: #262626;
          --inline-code-text: #ff7b72;
          --inline-code-border: #30363d;
          
          --math-bg: #2d3748;
          --math-border: #4a5568;
          --math-text: #e2e8f0;
          
          --link-color: #79c0ff;
          --link-hover-color: #b3d9ff;
          --link-visited-color: #bc8cff;
          
          --highlight-bg: #594214;
          --highlight-text: #f1c40f;
        }
      }
      
      /* Responsive Design */
      @media (max-width: 768px) {
        .table-wrapper {
          margin: 1rem 0;
        }
        
        .markdown-table {
          font-size: 0.875rem;
        }
        
        .markdown-table th,
        .markdown-table td {
          padding: 0.5rem;
        }
        
        .code-block {
          font-size: 15px;
          padding: 0.75rem;
        }
        
        .code-block-header {
          padding: 0.375rem 0.75rem;
        }
      }
      
      /* Accessibility Enhancements */
      .copy-button:focus {
        outline: 2px solid var(--link-color, #0366d6);
        outline-offset: 2px;
      }
      
      .rich-link:focus {
        outline: 2px solid var(--link-color, #0366d6);
        outline-offset: 2px;
        border-radius: 2px;
      }
      
      /* Print Styles */
      @media print {
        .code-block-wrapper,
        .table-wrapper {
          break-inside: avoid;
        }
        
        .copy-button {
          display: none;
        }
        
        .rich-link {
          color: #000;
          text-decoration: underline;
        }
      }
    </style>
  `;

  // Inject styles into document head if not already present
  if (typeof document !== 'undefined' && !document.getElementById('rich-message-styles')) {
    const styleElement = document.createElement('style');
    styleElement.id = 'rich-message-styles';
    styleElement.textContent = webStyles.replace(/<\/?style>/g, ''); // Remove style tags from the content
    document.head.appendChild(styleElement);

  }
}

export default RichMessageRenderer;

// Debug helper: Expose a test function to the browser console to validate citation parsing quickly
if (Platform.OS === 'web') {
  try {
    const installHook = () => {
      if (isCitationDebugEnabled()) {
        // Provide a simple theme stub for parsing in the console
        const themeStub = { isDark: false, text: '#2d3748' };
        // Expose a test function: call window.__testCitations("text with citations") to inspect parsed output
        window.__testCitations = (text) => {
          return parseTextWithCitations(String(text || ''), themeStub);
        };
      }
    };
    // Install immediately and also after any hot reloads
    installHook();
    if (module && module.hot && typeof module.hot.addStatusHandler === 'function') {
      module.hot.addStatusHandler((status) => {
        if (status === 'apply') {
          setTimeout(installHook, 0);
        }
      });
    }
  } catch (e) {
    // no-op if module.hot not available
  }
}
