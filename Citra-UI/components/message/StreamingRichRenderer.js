// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * StreamingRichRenderer - Streaming-aware rich message renderer
 * 
 * This component handles the transition between streaming and complete rendering:
 * - During streaming: Uses lightweight progressive markdown rendering
 * - On completion: Switches to full RichMessageRenderer for mermaid, tables, citations
 * 
 * Key features:
 * - Smooth 60fps updates during streaming via streamingRef
 * - Deferred mermaid diagram rendering (only after stream complete)
 * - Progressive markdown rendering during stream
 * - Full RichMessageRenderer after completion
 */

import React, { memo, useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { View, Text, Platform, ActivityIndicator } from 'react-native';
import RichMessageRenderer from './RichMessageRenderer';
import { WebHTMLRenderer } from './MessageComponents';

// Import markdown renderer for mobile only
let Markdown = null;
if (Platform.OS !== 'web') {
  try {
    Markdown = require('react-native-markdown-display').default;
  } catch (error) {
    console.warn('Failed to load react-native-markdown-display:', error.message);
  }
}

// Initialize markdown-it for web streaming rendering (already in package.json)
let mdStreaming = null;
try {
  const MarkdownIt = require('markdown-it');
  mdStreaming = new MarkdownIt({
    html: false,   // security: never render raw HTML from AI output
    breaks: true,  // single newline → <br>
    linkify: true, // auto-link URLs
  });
} catch (e) {
  console.warn('[StreamingRichRenderer] markdown-it not available:', e.message);
}

/**
 * Lightweight streaming text renderer for real-time updates
 * Uses requestAnimationFrame for smooth 60fps rendering
 * 
 * FIX: Uses shouldContinueRef set SYNCHRONOUSLY before loop starts
 * to avoid race condition where isStreamingRef was set in a separate
 * useEffect that ran AFTER the animation loop started.
 */
const StreamingTextRenderer = memo(({
  streamingRef,
  theme,
  style,
  isStreaming,
}) => {
  const [displayText, setDisplayText] = useState('');
  const animationFrameRef = useRef(null);
  const containerRef = useRef(null);
  const displayTextRef = useRef(''); // Track current displayText without stale closure issues
  const loopCountRef = useRef(0); // DEBUG: count loop iterations
  
  // KEY FIX: Use shouldContinueRef instead of isStreamingRef
  // This ref is set SYNCHRONOUSLY before the loop starts, avoiding race conditions
  const shouldContinueRef = useRef(false);
  
  // DEBUG: Log on mount
  useEffect(() => {
    console.log('🔷 [StreamingTextRenderer] MOUNTED', { isStreaming, hasStreamingRef: !!streamingRef, refValue: streamingRef?.current?.substring(0, 50) });
    return () => console.log('🔷 [StreamingTextRenderer] UNMOUNTED');
  }, []);

  // IMMEDIATE UPDATE LOOP - No typewriter effect, render text as it arrives
  const updateDisplayText = useCallback(() => {
    loopCountRef.current++;
    const currentRefText = streamingRef?.current || '';
    
    // DEBUG: Log every 100 iterations or on first 5
    if (loopCountRef.current <= 5 || loopCountRef.current % 100 === 0) {
      console.log(`🔄 [StreamingTextRenderer] Loop #${loopCountRef.current}`, {
        shouldContinue: shouldContinueRef.current,
        refLength: currentRefText.length,
        displayLength: displayTextRef.current.length,
        preview: currentRefText.substring(0, 30)
      });
    }
    
    // IMMEDIATE RENDER: Show full text as it arrives - no typewriter animation
    if (displayTextRef.current !== currentRefText) {
      displayTextRef.current = currentRefText;
      setDisplayText(currentRefText);
      
      // DEBUG: Log text updates
      if (loopCountRef.current <= 10 || loopCountRef.current % 50 === 0) {
        console.log(`✏️ [StreamingTextRenderer] Immediate update: ${currentRefText.length} chars`);
      }
    }

    // Continue loop only while streaming is active
    if (shouldContinueRef.current) {
      if (Platform.OS === 'web' && typeof requestAnimationFrame !== 'undefined') {
        animationFrameRef.current = requestAnimationFrame(updateDisplayText);
      } else {
        animationFrameRef.current = setTimeout(updateDisplayText, 16);
      }
    } else {
      // Final update when streaming stops - ensure we have all text
      const finalText = streamingRef?.current || '';
      if (displayTextRef.current !== finalText) {
        displayTextRef.current = finalText;
        setDisplayText(finalText);
      }
      console.log(`🛑 [StreamingTextRenderer] Loop STOPPED at iteration #${loopCountRef.current}, displayLength=${displayTextRef.current.length}`);
    }
  }, [streamingRef]);

  // Start/stop the animation loop based on isStreaming prop
  useEffect(() => {
    console.log(`🎬 [StreamingTextRenderer] isStreaming effect triggered: isStreaming=${isStreaming}`);
    
    if (isStreaming) {
      // Reset refs at start of streaming
      displayTextRef.current = '';
      loopCountRef.current = 0;
      
      // KEY FIX: Set shouldContinueRef BEFORE calling updateDisplayText
      // This ensures the loop check sees true on the first iteration
      shouldContinueRef.current = true;
      
      console.log('🚀 [StreamingTextRenderer] Starting update loop, shouldContinue=', shouldContinueRef.current);
      
      // Start the update loop
      updateDisplayText();
    } else {
      console.log('⏹️ [StreamingTextRenderer] Streaming stopped. Ref length:', streamingRef?.current?.length || 0);
      
      // Stop the loop immediately
      shouldContinueRef.current = false;
      
      // Immediately show final text - no animation delay
      const finalText = streamingRef?.current || '';
      if (finalText && displayTextRef.current !== finalText) {
        displayTextRef.current = finalText;
        setDisplayText(finalText);
      }
    }

    return () => {
      // Cleanup: stop the loop and cancel any pending frame
      shouldContinueRef.current = false;
      if (animationFrameRef.current) {
        if (Platform.OS === 'web' && typeof cancelAnimationFrame !== 'undefined') {
          cancelAnimationFrame(animationFrameRef.current);
        } else {
          clearTimeout(animationFrameRef.current);
        }
      }
    };
  }, [isStreaming, updateDisplayText, streamingRef]);

  // Web rendering with basic markdown support
  if (Platform.OS === 'web') {
    return (
      <div 
        ref={containerRef}
        className="streaming-content"
        style={{
          color: theme?.botMessageText || '#2d3748',
          fontSize: 16,
          lineHeight: 1.6,
          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
        }}
      >
        <StreamingMarkdownWeb content={displayText} theme={theme} />
      </div>
    );
  }

  // Mobile rendering — show thinking dots when no text yet
  if (!displayText && isStreaming) {
    return (
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, paddingVertical: 4 }}>
        <ActivityIndicator size="small" color={theme?.isDark ? '#9ca3af' : '#6b7280'} />
        <Text style={{ fontSize: 13, color: theme?.isDark ? '#9ca3af' : '#6b7280', marginLeft: 6 }}>
          Thinking...
        </Text>
      </View>
    );
  }

  // Mobile rendering
  if (Markdown) {
    return (
      <Markdown
        style={{
          body: {
            color: theme?.botMessageText || '#2d3748',
            fontSize: 16,
            fontFamily: 'System',
          },
          paragraph: { marginBottom: 8, marginTop: 0 },
          strong: { fontWeight: 'bold' },
          em: { fontStyle: 'italic' },
          code_inline: {
            backgroundColor: theme?.isDark ? '#2a2a2a' : '#f5f5f5',
            color: theme?.isDark ? '#ff6b6b' : '#d73a49',
            paddingHorizontal: 4,
            borderRadius: 4,
            fontFamily: 'monospace',
            fontSize: 14,
          },
        }}
      >
        {displayText}
      </Markdown>
    );
  }

  return <Text style={style}>{displayText}</Text>;
});

/**
 * Web-specific streaming markdown renderer using markdown-it
 * Provides proper markdown formatting (lists, headers, tables, code blocks)
 * during streaming, matching the quality of the post-stream RichMessageRenderer.
 */
const StreamingMarkdownWeb = memo(({ content, theme }) => {
  const isDark = theme?.isDark;
  const textColor = theme?.botMessageText || (isDark ? '#e2e8f0' : '#2d3748');
  const codeBg = isDark ? '#1e1e1e' : '#f6f8fa';
  const codeBorder = isDark ? '#444444' : '#e1e4e8';
  const inlineBg = isDark ? '#2d2d2d' : '#f0f0f0';
  const inlineColor = isDark ? '#ff79c6' : '#d73a49';
  const blockquoteBorder = isDark ? '#4a5568' : '#d1d5db';
  const thBg = isDark ? '#2d3748' : '#f6f8fa';
  const linkColor = isDark ? '#79c0ff' : '#3182ce';

  const processedHtml = useMemo(() => {
    if (!content) {
      const dotColor = isDark ? '#9ca3af' : '#6b7280';
      return `<div style="display:flex;align-items:center;gap:4px;padding:4px 0;">
        <span style="width:8px;height:8px;border-radius:50%;background:${dotColor};display:inline-block;animation:sdot 1.4s ease-in-out infinite;"></span>
        <span style="width:8px;height:8px;border-radius:50%;background:${dotColor};display:inline-block;animation:sdot 1.4s ease-in-out 0.2s infinite;"></span>
        <span style="width:8px;height:8px;border-radius:50%;background:${dotColor};display:inline-block;animation:sdot 1.4s ease-in-out 0.4s infinite;"></span>
        <style>@keyframes sdot{0%,80%,100%{opacity:.3;transform:scale(.8)}40%{opacity:1;transform:scale(1.1)}}</style>
      </div>`;
    }

    // Strip mermaid/chartjs/ascii-diagram blocks — rendered only after stream completes
    // to avoid flickering/misaligned diagrams while characters stream in.
    let text = content
      .replace(/```mermaid[\s\S]*?```/g, '')
      .replace(/```mermaid[\s\S]*$/g, '')
      .replace(/```chartjs[\s\S]*?```/g, '')
      .replace(/```chart\.js[\s\S]*?```/g, '')
      .replace(/```chartjs[\s\S]*$/g, '')
      .replace(/```chart\.js[\s\S]*$/g, '')
      .replace(/```(?:ascii|diagram|asciiflow|boxdraw|txt-diagram)[\s\S]*?```/gi, '')
      .replace(/```(?:ascii|diagram|asciiflow|boxdraw|txt-diagram)[\s\S]*$/gi, '');

    // Close any unclosed code fence (content cut off mid-block during streaming)
    const fenceCount = (text.match(/```/g) || []).length;
    if (fenceCount % 2 !== 0) {
      text += '\n```';
    }

    if (mdStreaming) {
      return mdStreaming.render(text);
    }

    // Fallback: safe plain-text render if markdown-it failed to load
    const escaped = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    return `<pre style="white-space:pre-wrap;word-break:break-word;font-family:inherit;margin:0;">${escaped}</pre>`;
  }, [content, isDark]);

  const scopedCss = `
    .smd p { margin: 8px 0; line-height: 1.6; }
    .smd h1 { font-size: 22px; font-weight: 700; margin: 20px 0 10px; line-height: 1.3; }
    .smd h2 { font-size: 19px; font-weight: 600; margin: 16px 0 8px; line-height: 1.3; }
    .smd h3 { font-size: 17px; font-weight: 600; margin: 14px 0 6px; line-height: 1.3; }
    .smd h4 { font-size: 15px; font-weight: 600; margin: 12px 0 4px; }
    .smd ul, .smd ol { margin: 6px 0; padding-left: 24px; }
    .smd li { margin: 3px 0; line-height: 1.6; }
    .smd li > p { margin: 2px 0; }
    .smd code { background: ${inlineBg}; color: ${inlineColor}; padding: 2px 6px; border-radius: 4px; font-family: 'Fira Code', 'Monaco', monospace; font-size: 13px; }
    .smd pre { background: ${codeBg}; border: 1px solid ${codeBorder}; border-radius: 6px; padding: 12px 16px; overflow-x: auto; margin: 10px 0; }
    .smd pre code { background: none; color: ${isDark ? '#e2e8f0' : '#24292e'}; padding: 0; font-size: 13px; line-height: 1.5; }
    .smd a { color: ${linkColor}; text-decoration: underline; }
    .smd a:hover { opacity: 0.8; }
    .smd blockquote { border-left: 3px solid ${blockquoteBorder}; margin: 10px 0; padding: 4px 12px; opacity: 0.85; }
    .smd strong { font-weight: 700; }
    .smd em { font-style: italic; }
    .smd hr { border: none; border-top: 1px solid ${codeBorder}; margin: 16px 0; }
    .smd table { border-collapse: collapse; width: 100%; margin: 10px 0; }
    .smd th, .smd td { border: 1px solid ${codeBorder}; padding: 6px 12px; text-align: left; line-height: 1.5; }
    .smd th { background: ${thBg}; font-weight: 600; }
  `;

  return (
    <div style={{ color: textColor, wordBreak: 'break-word' }}>
      <style>{scopedCss}</style>
      <div className="smd" dangerouslySetInnerHTML={{ __html: processedHtml }} />
    </div>
  );
});

/**
 * Main StreamingRichRenderer component
 * Switches between streaming renderer and full RichMessageRenderer
 * 
 * FIX: Directly derive render decision from isStreaming prop instead of using
 * separate showRich state. This avoids timing issues where useEffect fires
 * after render but state update doesn't trigger re-render reliably.
 */
const StreamingRichRenderer = memo(({
  content,
  theme,
  isStreaming,
  streamingRef,
  citations = [],
  mermaidBlocks = [],
  suppressSources = false,
  onOpenReader,
  style,
}) => {
  // DEBUG: Log which branch we're rendering
  console.log('🎨 [StreamingRichRenderer] Render:', {
    isStreaming,
    hasStreamingRef: !!streamingRef,
    streamingRefValue: streamingRef?.current?.substring(0, 50),
    contentLength: content?.length || 0
  });
  
  // CRITICAL FIX: Directly use isStreaming prop to decide which renderer to show
  // This ensures immediate switch when streaming ends, without relying on
  // separate state + useEffect which caused timing issues
  if (isStreaming) {
    console.log('🎨 [StreamingRichRenderer] -> Using StreamingTextRenderer (streaming in progress)');
    return (
      <View style={style}>
        <StreamingTextRenderer
          streamingRef={streamingRef}
          theme={theme}
          style={style}
          isStreaming={isStreaming}
        />
      </View>
    );
  }

  // Streaming complete - use full RichMessageRenderer to render diagrams, tables, citations
  console.log('🎨 [StreamingRichRenderer] -> Using RichMessageRenderer (streaming complete, content length:', content?.length || 0, ')');
  return (
    <RichMessageRenderer
      content={content}
      theme={theme}
      isUserMessage={false}
      citations={citations}
      suppressSources={suppressSources}
      onOpenReader={onOpenReader}
    />
  );
});

export default StreamingRichRenderer;
export { StreamingTextRenderer, StreamingMarkdownWeb };
