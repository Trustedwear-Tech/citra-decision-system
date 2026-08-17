// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * streamingQueryHandler - Integration layer between StreamingService and App.js
 * 
 * This module provides helper functions to integrate the new SSE streaming
 * with the existing App.js chat flow. It handles:
 * - Streaming state management
 * - Message updates during streaming
 * - Citation processing
 * - Mermaid block handling
 */

import streamingService from './StreamingService';
import { StreamEventType } from './StreamingService';
import authService from './authService';

/**
 * Handle a streaming query with the new SSE-based streaming service
 * 
 * @param {Object} params - Handler parameters
 * @param {Object} params.requestData - API request data
 * @param {Object} params.newMessage - User's message object
 * @param {string} params.currentSessionId - Session ID
 * @param {Function} params.setMessages - React setState for messages
 * @param {Function} params.scheduleScrollToBottom - Scroll handler
 * @param {Function} params.setIsBotTyping - Typing state setter
 * @param {Function} params.setIsLoading - Loading state setter
 * @param {Function} params.setIsGenerating - Generating state setter
 * @param {Object} params.storageManager - Storage manager for persistence
 * @param {Object} params.browserChatManager - Browser chat manager
 * @returns {Promise<Object>} - Stream result
 */
export async function handleSSEStreamingResponse({
  requestData,
  newMessage,
  currentSessionId,
  setMessages,
  scheduleScrollToBottom,
  setIsBotTyping,
  setIsLoading,
  setIsGenerating,
  storageManager,
  browserChatManager,
  onOpenBuilder,
}) {
  console.log('🌊 [SSE_STREAMING] Starting SSE streaming query');

  // Helper function to generate unique bot message ID
  const getBotMessageId = (messagePairId, suffix = '') => {
    return `bot-${messagePairId}${suffix ? '-' + suffix : ''}-${Date.now()}-${Math.random().toString(36).substr(2, 5)}`;
  };

  // First show typing indicator (3-dot animation)
  const typingMessage = {
    id: 'typing',
    text: '',
    sender: 'bot',
    isTyping: true,
  };

  setMessages(prev => [...prev, typingMessage]);
  // NO scroll - let user stay at their reading position
  // scheduleScrollToBottom(1);
  setIsBotTyping(true);

  // State for tracking stream
  let streamedText = '';
  let mermaidBlocks = [];
  let citations = [];
  let usage = null;
  let contextInfo = null;
  let firstChunkReceived = false; // Track if we've received first chunk
  let stallTimer = null; // Detects tool execution pauses (no chunks for 3s)
  const STALL_THRESHOLD_MS = 3000; // Show indicator after 3s of silence

  // Create a SHARED ref object that persists across message updates
  // This ref is what connects the onChunk updates to the UI rendering
  const sharedStreamingRef = { current: '' };

  // Create streaming message placeholder
  const botMessageId = getBotMessageId(requestData.message_pair_id, 'sse-stream');
  const streamingBotMessage = {
    id: botMessageId,
    text: '',
    sender: 'bot',
    isStreaming: true,
    shouldAnimate: false,
    streamingRef: sharedStreamingRef, // Use the shared ref
    mermaidBlocks: [], // Will hold mermaid blocks
    citations: [], // Will hold citations
  };

  // IMMEDIATELY add streaming message - no delay!
  // The streaming component MUST be mounted before chunks arrive
  setMessages(prev => prev.filter(msg => msg.id !== 'typing').concat([streamingBotMessage]));
  console.log('🟢 [SSE_STREAMING] Streaming message mounted', { botMessageId, hasRef: !!sharedStreamingRef });

  // Prepare streaming payload (transform from App.js format to backend format)
  const streamPayload = {
    query: requestData.query,
    chat_session_id: requestData.chat_session_id,
    message_pair_id: requestData.message_pair_id,
    ai_model: requestData.ai_model || null,
    // Pass through all the flags
    use_personal_data: requestData.use_personal_data,
    enterprise_enabled: requestData.enterprise_enabled,
    // Must be forwarded: the backend forces enterprise sources ON for main chat
    // (they are the only sources left), so `enterprise_enabled: false` alone no
    // longer means "search nothing". General Query mode is honoured only via
    // this explicit flag — drop it here and the mode silently stops working.
    model_only: requestData.model_only,
    enterprise_entity_id: requestData.enterprise_entity_id,
    enterprise_entity_name: requestData.enterprise_entity_name,
    enterprise_entity_type: requestData.enterprise_entity_type,
    enterprise_entity_enabled: requestData.enterprise_entity_enabled,
    persona_data: requestData.persona_data,
    selected_folder_ids: requestData.selected_folder_ids,
    folder_search_enabled: requestData.folder_search_enabled,
    enable_internet_search: requestData.enable_internet_search,
    legal_filters: requestData.legal_filters,
    conversation_history: requestData.conversation_history,
    chats: requestData.chats,
    multimodal_attachments: requestData.multimodal_attachments,
    // Optional per-request output-token override. Backend clamps to its
    // configured ceiling (MAX_OUTPUT_TOKENS_CEILING). Omit/undefined ->
    // backend uses MAX_OUTPUT_TOKENS_DEFAULT.
    max_output_tokens: requestData.max_output_tokens,
  };

  return new Promise(async (resolve, reject) => {
    try {
      const streamController = await streamingService.streamQuery(streamPayload, {
        // Called when context retrieval is complete
        onContextReady: (data) => {
          console.log('📚 [SSE_STREAMING] Context ready:', data);
          contextInfo = data;
          
          // Update message to show context was retrieved
          setMessages(prev => prev.map(msg =>
            msg.id === botMessageId
              ? { ...msg, contextReady: true, contextCount: data.context_count }
              : msg
          ));
        },

        // Called for each text chunk - this is the key streaming update
        onChunk: (chunk, fullText) => {
          streamedText = fullText;
          
          // Update the SHARED streaming ref for smooth rendering
          // This ref persists even when setMessages creates new message objects
          sharedStreamingRef.current = fullText;
          
          // DEBUG: Log chunk updates (first 5 and every 20th)
          const chunkCount = fullText.length;
          if (chunkCount <= 50 || chunkCount % 200 === 0) {
            console.log(`📦 [SSE_STREAMING] Chunk received, total length: ${fullText.length}, ref updated`);
          }
          
          // Reset stall detection — chunks are flowing, clear any active tool indicator
          if (stallTimer) clearTimeout(stallTimer);
          // If we had a stall indicator, clear it now that chunks resumed
          setMessages(prev => {
            const msg = prev.find(m => m.id === botMessageId);
            if (msg && msg.toolExecuting) {
              return prev.map(m => m.id === botMessageId ? { ...m, toolExecuting: false, toolName: null } : m);
            }
            return prev;
          });
          // Start new stall timer — if no chunk arrives in 3s, show tool indicator
          stallTimer = setTimeout(() => {
            console.log('⏳ [SSE_STREAMING] Stream stalled — showing tool indicator');
            setMessages(prev => prev.map(msg =>
              msg.id === botMessageId && msg.isStreaming
                ? { ...msg, toolExecuting: true, toolName: msg.toolName || null }
                : msg
            ));
          }, STALL_THRESHOLD_MS);
          
          // CRITICAL: On first chunk, trigger re-render so StreamingTextRenderer mounts
          // Without this, TypingIndicator keeps showing because ref update doesn't cause re-render
          if (!firstChunkReceived) {
            firstChunkReceived = true;
            console.log('🎯 [SSE_STREAMING] First chunk! Triggering re-render to mount StreamingTextRenderer');
            setMessages(prev => prev.map(msg =>
              msg.id === botMessageId
                ? { ...msg, _firstChunk: true } // Force re-render, StreamingTextRenderer will read from ref
                : msg
            ));
          }
          
          // NO auto-scroll during streaming - let user read from top
          // User can manually scroll if they want to see more
        },

        // Called for complete mermaid blocks
        onMermaidBlock: (block) => {
          console.log('📊 [SSE_STREAMING] Received mermaid block');
          mermaidBlocks.push(block);
          
          // Update message with new mermaid block
          setMessages(prev => prev.map(msg =>
            msg.id === botMessageId
              ? { ...msg, mermaidBlocks: [...(msg.mermaidBlocks || []), block] }
              : msg
          ));
        },

        // Called with citation data at end of stream
        onCitations: (citationData) => {
          console.log('📖 [SSE_STREAMING] Received citations:', citationData?.length || 0);
          citations = citationData || [];
          
          // Update message with citations
          setMessages(prev => prev.map(msg =>
            msg.id === botMessageId
              ? { ...msg, citations: citationData }
              : msg
          ));
        },

        // Called with usage stats
        onUsage: (usageData) => {
          console.log('📈 [SSE_STREAMING] Usage stats:', usageData);
          usage = usageData;
        },

        // Called with output files from code execution
        onOutputFiles: (outputFiles) => {
          console.log('📎 [SSE_STREAMING] Output files:', outputFiles?.length || 0);

          // Attach output files to the message for download rendering
          setMessages(prev => prev.map(msg =>
            msg.id === botMessageId
              ? { ...msg, outputFiles: outputFiles }
              : msg
          ));
        },

        // Single structured download card — emitted by the action-chat
        // bridge in Citra-Service when this turn was redirected to Deep
        // Research (presentation / report / deep-research intent).
        // The payload shape mirrors ActionChatArtifactCard / DownloadArtifactCard
        // so the UI is identical across all chat surfaces.
        onArtifact: (artifact) => {
          if (!artifact || !artifact.url) return;
          console.log('🗂️ [SSE_STREAMING] Artifact:', artifact.kind, artifact.title);
          setMessages(prev => prev.map(msg =>
            msg.id === botMessageId
              ? { ...msg, artifacts: [...(msg.artifacts || []), artifact] }
              : msg
          ));
        },

        // Called when a tool starts or finishes executing (internet search, code execution)
        onToolStatus: (data) => {
          console.log(`🛠️ [SSE_STREAMING] Tool status: ${data.tool} → ${data.status}`);
          const isRunning = data.status === 'running';
          // When a tool starts, cancel stall timer (we already know what's happening)
          if (isRunning && stallTimer) clearTimeout(stallTimer);
          setMessages(prev => prev.map(msg =>
            msg.id === botMessageId
              ? { ...msg, toolExecuting: isRunning, toolName: isRunning ? data.tool : null }
              : msg
          ));
        },

        // High-level processing stage for the live timeline UI. Backend emits
        // these at retrieval, thinking, planning, calling_tool, and
        // generating_answer boundaries. We append every stage to a per-message
        // `stages` array and the timeline component renders it in order.
        onStage: (data) => {
          console.log('🎬 [SSE_STREAMING] Stage:', data?.stage, data?.label);
          if (stallTimer) clearTimeout(stallTimer);
          const entry = {
            stage: data?.stage,
            label: data?.label,
            detail: data?.detail,
            tool: data?.tool,
            ts: Date.now(),
          };
          setMessages(prev => prev.map(msg =>
            msg.id === botMessageId
              ? { ...msg, stages: [...(msg.stages || []), entry], currentStage: entry }
              : msg
          ));
        },

        // The model emitted a <plan>...</plan> block. Backend has already
        // stripped it from the visible answer; we render it in a dedicated
        // PlanCard above the streaming text.
        onPlan: (data) => {
          console.log('📋 [SSE_STREAMING] Plan received (round', data?.round, ')');
          setMessages(prev => prev.map(msg =>
            msg.id === botMessageId
              ? {
                  ...msg,
                  plans: [...(msg.plans || []), { text: data?.plan_text, round: data?.round, ts: Date.now() }],
                }
              : msg
          ));
        },

        // The model emitted reasoning / thinking tokens. We append each
        // delta to a per-message `reasoning` array; ReasoningCard coalesces
        // them into a collapsible panel (mirrors Action Chat's Reasoning
        // tab). Backend gates this behind LLM_EXPOSE_REASONING, so this
        // handler is a no-op when reasoning is hidden.
        onReasoning: (data) => {
          const text = data?.text || '';
          if (!text) return;
          setMessages(prev => prev.map(msg =>
            msg.id === botMessageId
              ? { ...msg, reasoning: [...(msg.reasoning || []), { text, round: data?.round, ts: Date.now() }] }
              : msg
          ));
        },

        // Backend handed this turn off to the presentation / report
        // composer (open_builder). Bubble it up so App.js can navigate to
        // the composer pre-loaded with the goal + research corpus.
        onOpenBuilder: (data) => {
          console.log('🏗️ [SSE_STREAMING] open_builder →', data?.builder, data?.goal);
          try {
            onOpenBuilder?.(data);
          } catch (e) {
            console.error('🏗️ [SSE_STREAMING] onOpenBuilder handler failed:', e);
          }
        },

        // Called when the LLM (or server-side detection) emits a clarify
        // block. Attach the chip payload to the bot message so SuggestionChips
        // can render it. The text inside <clarify>...</clarify> is already
        // stripped on the server, so we only need to remember the payload.
        onSuggestionChips: (data) => {
          console.log('🧩 [SSE_STREAMING] Suggestion chips received:', data?.options?.length || 0, 'options');
          setMessages(prev => prev.map(msg =>
            msg.id === botMessageId
              ? {
                  ...msg,
                  suggestions: [...(msg.suggestions || []), data],
                  suggestionsConsumed: false,
                }
              : msg
          ));
        },

        // Called when stream is complete
        onComplete: async (result) => {
          if (stallTimer) clearTimeout(stallTimer);
          console.log('✅ [SSE_STREAMING] Stream complete');
          console.log('📝 [SSE_STREAMING] Final text length:', streamedText.length);
          console.log('📊 [SSE_STREAMING] Mermaid blocks collected:', mermaidBlocks.length);
          
          // CRITICAL FIX: Inject mermaid blocks into streamedText before setting final message
          // The API sends mermaid_block events separately, while text only has [Diagram rendering...] placeholder
          // We need to replace placeholders with actual mermaid markdown code blocks
          let processedText = streamedText;
          if (mermaidBlocks.length > 0) {
            console.log(`📊 [SSE_STREAMING] Injecting ${mermaidBlocks.length} mermaid blocks into response`);
            
            mermaidBlocks.forEach((block, index) => {
              const mermaidCode = block.code || '';
              if (mermaidCode) {
                const mermaidMarkdown = `\n\`\`\`mermaid\n${mermaidCode}\n\`\`\`\n`;
                
                // Try to replace the placeholder
                // The placeholder could be: [Diagram rendering...] or similar variations
                const placeholderPatterns = [
                  /\[Diagram rendering\.{3,}\]/gi,
                  /\[Diagram rendering\.\.\.\]/gi,
                  /\n\[Diagram rendering[^\]]*\]\n/gi,
                ];
                
                let replaced = false;
                for (const pattern of placeholderPatterns) {
                  if (pattern.test(processedText)) {
                    processedText = processedText.replace(pattern, mermaidMarkdown);
                    replaced = true;
                    console.log(`✅ [SSE_STREAMING] Replaced placeholder with mermaid block ${index}`);
                    break;
                  }
                }
                
                // If no placeholder found, insert at a good location
                if (!replaced) {
                  // Find a good insertion point - after the intro text, before the explanation
                  const introEndMatch = processedText.match(/\n\n(?=This diagram|The diagram|Here's|Below)/i);
                  if (introEndMatch && introEndMatch.index !== undefined) {
                    processedText = processedText.slice(0, introEndMatch.index) + mermaidMarkdown + processedText.slice(introEndMatch.index);
                  } else {
                    // If nothing found, just append before the last paragraph or at end
                    processedText += mermaidMarkdown;
                  }
                  console.log(`⚠️ [SSE_STREAMING] No placeholder found, inserted mermaid block ${index}`);
                }
              }
            });
          }
          
          // Final message update - mark streaming as complete
          // IMPORTANT: shouldAnimate=false because text was already shown during streaming
          // Setting it to true would cause Message component to re-animate the full text
          setMessages(prev => prev.map(msg => {
            if (msg.id !== botMessageId) return msg;
            return {
              ...msg,
              text: processedText,
              isStreaming: false,
              shouldAnimate: false, // NO animation - text was already shown via streaming
              isUpdated: true, // Mark as updated so AnimatedText shows immediately
              toolExecuting: false, // Clear any active tool indicator
              toolName: null,
              mermaidBlocks,
              citations,
              outputFiles: msg.outputFiles || undefined, // Preserve output files from code execution
            };
          }));

          // Store messages
          const finalBotMessage = {
            id: botMessageId,
            text: processedText,
            sender: 'bot',
            citations,
            mermaidBlocks,
          };

          try {
            await storageManager.addMessagePair(currentSessionId, newMessage, finalBotMessage);
            
            // Also store in browser chat manager if MongoDB chat history is disabled
            if (browserChatManager && browserChatManager.isBrowserOnlyMode()) {
              browserChatManager.addMessage(currentSessionId, newMessage.text, finalBotMessage.text, newMessage.id);
            }
          } catch (storageError) {
            console.error('❌ [SSE_STREAMING] Storage error:', storageError);
          }

          // Reset loading states
          setIsBotTyping(false);
          setIsLoading(false);
          setIsGenerating(false);
          // NO scroll on completion - maintain user's reading position
          // They were reading from top, don't disturb them

          resolve({
            success: true,
            streamedText: processedText,
            mermaidBlocks,
            citations,
            usage,
            contextInfo,
          });
        },

        // Called on error
        onError: (error) => {
          if (stallTimer) clearTimeout(stallTimer);
          console.error('❌ [SSE_STREAMING] Error:', error);
          
          // Show error message
          const errorText = error.message || 'An error occurred while streaming the response.';
          
          // Handle specific error types
          let userMessage = errorText;
          if (error.error_type === 'auth_required') {
            userMessage = '⚠️ Authentication required. Please sign in again.';
          }

          // Update or add error message
          setMessages(prev => {
            const filtered = prev.filter(msg => msg.id !== botMessageId && msg.id !== 'typing');
            return [...filtered, {
              id: `error-${Date.now()}`,
              text: userMessage,
              sender: 'bot',
              type: 'error',
            }];
          });

          // Reset loading states
          setIsBotTyping(false);
          setIsLoading(false);
          setIsGenerating(false);

          reject(new Error(errorText));
        },

        // Called on abort
        onAbort: () => {
          console.log('🛑 [SSE_STREAMING] Stream aborted by user');
          
          // Keep whatever text we have so far
          setMessages(prev => prev.map(msg =>
            msg.id === botMessageId
              ? {
                  ...msg,
                  text: streamedText || '(Response cancelled)',
                  isStreaming: false,
                  shouldAnimate: false,
                  streamingRef: null,
                }
              : msg
          ));

          // Reset loading states
          setIsBotTyping(false);
          setIsLoading(false);
          setIsGenerating(false);

          resolve({
            success: false,
            aborted: true,
            streamedText,
          });
        },

        // Called on done event
        onDone: (data) => {
          console.log('✅ [SSE_STREAMING] Done event received:', data);
        },
      });

      // Return the stream controller for cancellation
      return streamController;

    } catch (error) {
      console.error('❌ [SSE_STREAMING] Failed to start stream:', error);
      
      // Remove typing/streaming indicators
      setMessages(prev => prev.filter(msg => msg.id !== 'typing' && msg.id !== botMessageId));
      
      // Reset loading states
      setIsBotTyping(false);
      setIsLoading(false);
      setIsGenerating(false);

      reject(error);
    }
  });
}

/**
 * Check if SSE streaming is available and should be used
 * 
 * Streaming is model-agnostic - the backend decides which model to use
 * based on query type and configuration.
 * UI always enables streaming; backend handles model selection.
 * 
 * @returns {boolean} - True if SSE streaming should be used
 */
export function shouldUseSSEStreaming() {
  // Streaming is always enabled for chat - backend decides model
  // Model selection happens in orchestrator.py (backend decides based on query type)
  return streamingService.isStreamingSupported();
}

export default {
  handleSSEStreamingResponse,
  shouldUseSSEStreaming,
};
