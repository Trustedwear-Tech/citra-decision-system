/**
 * StreamingService - Handles Server-Sent Events (SSE) for streaming chat responses
 * 
 * This service provides real-time streaming of LLM responses from the backend,
 * enabling a "typewriter" effect where users see the response as it's generated.
 * 
 * Event Types:
 * - context_ready: Context retrieval complete (shows how many docs were found)
 * - chunk: Text chunk from LLM (streamed incrementally)
 * - mermaid_block: Complete mermaid diagram (rendered after streaming)
 * - citations: Citation data (processed after response complete)
 * - usage: Token usage stats
 * - done: Stream complete
 * - error: Error occurred
 */

import CONFIG from '../config/config';
import { authService } from './authService';

// Event type constants matching backend
export const StreamEventType = {
  CONTEXT_READY: 'context_ready',
  CHUNK: 'chunk',
  MERMAID_BLOCK: 'mermaid_block',
  CITATIONS: 'citations',
  USAGE: 'usage',
  OUTPUT_FILES: 'output_files',
  ARTIFACT: 'artifact',
  TOOL_STATUS: 'tool_status',
  SUGGESTION_CHIPS: 'suggestion_chips',
  STAGE: 'stage',
  PLAN: 'plan',
  REASONING: 'reasoning',
  OPEN_BUILDER: 'open_builder',
  ERROR: 'error',
  DONE: 'done',
};

/**
 * StreamingService class for managing SSE connections
 */
class StreamingService {
  constructor() {
    this.baseUrl = CONFIG.CITRA_SERVICE_URL || CONFIG.urls?.citraService || 'http://localhost:8085/citra-ai';
    this.activeStreams = new Map(); // Track active streams for cleanup
  }

  /**
   * Get streaming endpoint URL
   */
  getStreamUrl() {
    return `${this.baseUrl}/query/stream`;
  }

  /**
   * Send a streaming query request
   * 
   * @param {Object} payload - Query payload (same as /query endpoint)
   * @param {Object} callbacks - Event handlers
   * @param {Function} callbacks.onContextReady - Called when context is retrieved
   * @param {Function} callbacks.onChunk - Called for each text chunk
   * @param {Function} callbacks.onMermaidBlock - Called for complete mermaid diagrams
   * @param {Function} callbacks.onCitations - Called with citation data
   * @param {Function} callbacks.onUsage - Called with token usage stats
   * @param {Function} callbacks.onComplete - Called when stream is complete
   * @param {Function} callbacks.onError - Called on error
   * @returns {Object} - Stream controller with abort() method
   */
  async streamQuery(payload, callbacks = {}) {
    const streamId = `stream_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

    console.log(`🚀 [StreamingService] Starting stream ${streamId}`);
    console.log(`📝 [StreamingService] Query: ${payload.query?.substring(0, 100)}...`);

    // Get auth token
    let token;
    try {
      token = await authService.getToken();
    } catch (error) {
      console.error('❌ [StreamingService] Failed to get auth token:', error);
      callbacks.onError?.({ message: 'Authentication failed' });
      return { abort: () => { } };
    }

    // Create AbortController for cancellation
    const abortController = new AbortController();

    // Store stream reference for cleanup
    this.activeStreams.set(streamId, abortController);

    // Track accumulated response
    let fullResponse = '';
    let mermaidBlocks = [];
    let chunkCount = 0; // Count chunks per stream for debug

    try {
      const response = await fetch(this.getStreamUrl(), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
          'Accept': 'text/event-stream',
        },
        body: JSON.stringify(payload),
        signal: abortController.signal,
      });

      if (!response.ok) {
        const errorText = await response.text();
        console.error(`❌ [StreamingService] HTTP error ${response.status}:`, errorText);

        // Handle specific error codes
          if (response.status === 401) {
        } else {
          callbacks.onError?.({
            message: `Server error: ${response.status}`,
            status_code: response.status
          });
        }
        return { abort: () => abortController.abort() };
      }

      // Process SSE stream using ReadableStream
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          console.log(`✅ [StreamingService] Stream ${streamId} complete`);
          break;
        }

        // Decode chunk and add to buffer
        buffer += decoder.decode(value, { stream: true });

        // Process complete SSE events from buffer
        const events = this._parseSSEEvents(buffer);
        buffer = events.remaining;

        for (const event of events.parsed) {
          this._handleEvent(event, callbacks, {
            fullResponse: () => fullResponse,
            setFullResponse: (val) => { fullResponse = val; },
            mermaidBlocks,
            getChunkCount: () => chunkCount,
            incrementChunkCount: () => { chunkCount += 1; },
          });
        }
      }

      // Call complete callback with final data
      callbacks.onComplete?.({
        fullResponse,
        mermaidBlocks,
        streamId,
      });

    } catch (error) {
      if (error.name === 'AbortError') {
        console.log(`🛑 [StreamingService] Stream ${streamId} aborted`);
        callbacks.onAbort?.();
      } else {
        console.error(`❌ [StreamingService] Stream error:`, error);
        callbacks.onError?.({ message: error.message || 'Stream failed' });
      }
    } finally {
      // Cleanup
      this.activeStreams.delete(streamId);
    }

    return {
      abort: () => {
        console.log(`🛑 [StreamingService] Aborting stream ${streamId}`);
        abortController.abort();
      },
      streamId,
    };
  }

  /**
   * Parse SSE events from buffer
   * @private
   */
  _parseSSEEvents(buffer) {
    const parsed = [];
    const lines = buffer.split('\n');
    let currentEvent = { type: null, data: null };
    let remaining = '';

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];

      // Check if we're at the last line and it might be incomplete
      if (i === lines.length - 1 && line !== '') {
        remaining = line;
        continue;
      }

      if (line.startsWith('event: ')) {
        currentEvent.type = line.substring(7).trim();
      } else if (line.startsWith('data: ')) {
        currentEvent.data = line.substring(6);
      } else if (line === '' && currentEvent.type && currentEvent.data) {
        // Empty line = end of event
        try {
          parsed.push({
            type: currentEvent.type,
            data: JSON.parse(currentEvent.data),
          });
        } catch (e) {
          console.warn('⚠️ [StreamingService] Failed to parse event data:', currentEvent.data);
        }
        currentEvent = { type: null, data: null };
      }
    }

    return { parsed, remaining };
  }

  /**
   * Handle individual SSE event
   * @private
   */
  _handleEvent(event, callbacks, state) {
    const { type, data } = event;
    
    // Debug: Log ALL events being handled (skip the high-frequency CHUNK and
    // REASONING deltas — they fire per-token and would flood the console).
    if (type !== StreamEventType.CHUNK && type !== StreamEventType.REASONING) {
      console.log(`🔔 [StreamingService] Event received: ${type}`);
    }

    switch (type) {
      case StreamEventType.CONTEXT_READY:
        console.log(`📚 [StreamingService] Context ready: ${data.context_count} chunks`);
        callbacks.onContextReady?.(data);
        break;

      case StreamEventType.CHUNK:
        const chunkText = data.text || '';
        state.incrementChunkCount?.();
        state.setFullResponse(state.fullResponse() + chunkText);
        callbacks.onChunk?.(chunkText, state.fullResponse());
        break;

      case StreamEventType.MERMAID_BLOCK:
        console.log(`📊 [StreamingService] Mermaid block received`);
        state.mermaidBlocks.push(data);
        callbacks.onMermaidBlock?.(data);
        break;

      case StreamEventType.CITATIONS:
        console.log(`📖 [StreamingService] Citations received`);
        callbacks.onCitations?.(data.citations);
        break;

      case StreamEventType.USAGE:
        console.log(`📈 [StreamingService] Usage:`, data);
        callbacks.onUsage?.(data);
        break;

      case StreamEventType.OUTPUT_FILES:
        console.log(`📎 [StreamingService] Output files:`, data.files?.length || 0);
        callbacks.onOutputFiles?.(data.files || []);
        break;

      case StreamEventType.ARTIFACT:
        console.log(`🗂️ [StreamingService] Artifact:`, data.kind, data.title);
        callbacks.onArtifact?.(data);
        break;

      case StreamEventType.TOOL_STATUS:
        console.log(`🛠️ [StreamingService] Tool status:`, data.tool, data.status);
        callbacks.onToolStatus?.(data);
        break;

      case StreamEventType.SUGGESTION_CHIPS:
        console.log(`🧩 [StreamingService] Suggestion chips:`, data.options?.length || 0, 'options');
        callbacks.onSuggestionChips?.(data);
        break;

      case StreamEventType.STAGE:
        console.log(`🎬 [StreamingService] Stage:`, data.stage, data.label);
        callbacks.onStage?.(data);
        break;

      case StreamEventType.PLAN:
        console.log(`📋 [StreamingService] Plan received (round ${data.round})`);
        callbacks.onPlan?.(data);
        break;

      case StreamEventType.REASONING:
        // Incremental thinking tokens — accumulated into a collapsible
        // Reasoning card. Logged sparingly to avoid console spam.
        callbacks.onReasoning?.(data);
        break;

      case StreamEventType.OPEN_BUILDER:
        console.log(`🏗️ [StreamingService] open_builder:`, data?.builder, data?.goal);
        callbacks.onOpenBuilder?.(data);
        break;

      case StreamEventType.ERROR:
        console.error(`❌ [StreamingService] Error event:`, data);
        callbacks.onError?.(data);
        break;

      case StreamEventType.DONE:
        console.log(
          `✅ [StreamingService] Done event:`,
          {
            ...data,
            totalChunks: state.getChunkCount?.(),
            finalLength: state.fullResponse?.().length,
          }
        );
        callbacks.onDone?.(data);
        break;

      default:
        console.warn(`⚠️ [StreamingService] Unknown event type: ${type}`);
    }
  }

  /**
   * Abort all active streams
   */
  abortAll() {
    console.log(`🛑 [StreamingService] Aborting ${this.activeStreams.size} active streams`);
    for (const [streamId, controller] of this.activeStreams) {
      console.log(`   - Aborting ${streamId}`);
      controller.abort();
    }
    this.activeStreams.clear();
  }

  /**
   * Check if streaming is supported
   */
  isStreamingSupported() {
    return typeof fetch !== 'undefined' &&
      typeof ReadableStream !== 'undefined' &&
      typeof TextDecoder !== 'undefined';
  }
}

// Export singleton instance
export const streamingService = new StreamingService();
export default streamingService;
