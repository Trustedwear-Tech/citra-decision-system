// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

// Optimized streaming message component that handles smooth updates internally
import React, { useState, useEffect, useRef, memo, useCallback } from 'react';
import { View, Text, Image, Platform, TouchableOpacity, TextInput, ActivityIndicator } from 'react-native';
import { Ionicons, FontAwesome5 } from '@expo/vector-icons';
import { styles } from '../../styles';
import { TypingIndicator } from '../ui/TypingIndicator';
import { ToolStatusIndicator } from '../ui/ToolStatusIndicator';
import { PlanCard } from '../streaming/PlanCard';
import { ReasoningCard } from '../streaming/ReasoningCard';
import { 
  FormattedMessageContent, 
  MessageActions,
  WebHTMLRenderer 
} from './MessageComponents';
import { processLaTeXForMobile } from '../../utils/textProcessing';
import WebRenderFix from '../../WebRenderFix';
import StreamingRichRenderer from './StreamingRichRenderer';
import DownloadArtifactCard from './DownloadArtifactCard';
import SuggestionChips from './SuggestionChips';

// Import images

// Import markdown renderer for mobile only with error handling
let Markdown = null;
if (Platform.OS !== 'web') {
  try {
    Markdown = require('react-native-markdown-display').default;
  } catch (error) {
    console.warn('Failed to load react-native-markdown-display in StreamingMessage.js:', error.message);
  }
}

// Smooth streaming text component that updates itself
const SmoothStreamingText = memo(({ 
  streamingRef, 
  finalText, 
  isStreaming, 
  style, 
  theme 
}) => {
  const [displayText, setDisplayText] = useState('');
  const animationFrameRef = useRef();
  const lastUpdateRef = useRef(0);
  const displayTextRef = useRef(''); // Track displayText without causing re-renders

  // Update display text smoothly using platform-appropriate optimization
  const updateDisplayText = useCallback(() => {
    if (!streamingRef?.current) {
      // Schedule next check even if ref is empty (might be loading)
      if (isStreaming) {
        if (Platform.OS === 'web' && typeof requestAnimationFrame !== 'undefined') {
          animationFrameRef.current = requestAnimationFrame(updateDisplayText);
        } else {
          animationFrameRef.current = setTimeout(updateDisplayText, 16);
        }
      }
      return;
    }
    
    const currentText = streamingRef.current;
    
    // Check if text changed using ref (avoids stale closure issue)
    if (currentText !== displayTextRef.current) {
      // Use platform-specific optimization
      if (Platform.OS === 'web' && typeof requestAnimationFrame !== 'undefined') {
        // Web: throttle to ~60fps
        const now = performance.now();
        if (now - lastUpdateRef.current > 16) {
          displayTextRef.current = currentText;
          setDisplayText(currentText);
          lastUpdateRef.current = now;
        }
      } else {
        // Mobile: direct update for smooth native rendering
        displayTextRef.current = currentText;
        setDisplayText(currentText);
      }
    }
    
    // Always schedule next frame while streaming
    if (isStreaming) {
      if (Platform.OS === 'web' && typeof requestAnimationFrame !== 'undefined') {
        animationFrameRef.current = requestAnimationFrame(updateDisplayText);
      } else {
        // Mobile: use setTimeout as fallback
        animationFrameRef.current = setTimeout(updateDisplayText, 16);
      }
    }
  }, [isStreaming, streamingRef]); // Removed displayText from deps to avoid loop breaking

  // Start smooth updates when streaming begins
  useEffect(() => {
    if (isStreaming && streamingRef) {
      // Reset refs at start
      displayTextRef.current = '';
      lastUpdateRef.current = 0;
      updateDisplayText();
    } else {
      // Set final text when streaming completes
      setDisplayText(finalText || '');
      displayTextRef.current = finalText || '';
      if (animationFrameRef.current) {
        if (Platform.OS === 'web' && typeof cancelAnimationFrame !== 'undefined') {
          cancelAnimationFrame(animationFrameRef.current);
        } else {
          clearTimeout(animationFrameRef.current);
        }
      }
    }

    return () => {
      if (animationFrameRef.current) {
        if (Platform.OS === 'web' && typeof cancelAnimationFrame !== 'undefined') {
          cancelAnimationFrame(animationFrameRef.current);
        } else {
          clearTimeout(animationFrameRef.current);
        }
      }
    };
  }, [isStreaming, finalText, updateDisplayText, streamingRef]);

  // Render the text based on platform
  if (Platform.OS === 'web') {
    return (
      <WebHTMLRenderer
        content={displayText}
        theme={theme}
        style={style}
      />
    );
  } else {
    // Mobile: Use Markdown for bot responses if available
    if (theme && Markdown) {
      try {
        return (
          <Markdown
            style={{
              body: {
                color: theme.botMessageText,
                fontWeight: '400',
                fontSize: 16,
                fontFamily: 'System'
              },
              paragraph: {
                marginBottom: 8,
                marginTop: 0,
              },
              strong: {
                fontWeight: 'bold',
              },
              em: {
                fontStyle: 'italic',
              },
              code_inline: {
                backgroundColor: theme.isDark ? '#2a2a2a' : '#f5f5f5',
                color: theme.isDark ? '#ff6b6b' : '#d73a49',
                paddingHorizontal: 6,
                paddingVertical: 3,
                borderRadius: 4,
                fontFamily: 'monospace',
                fontSize: 14,
              },
              code_block: {
                backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f8f8',
                color: theme.isDark ? '#ffffff' : '#000000',
                paddingHorizontal: 12,
                paddingVertical: 8,
                borderRadius: 6,
                fontFamily: 'monospace',
                fontSize: 14,
                marginVertical: 8,
                borderWidth: 1,
                borderColor: theme.isDark ? '#444444' : '#e1e4e8',
              },
            }}
          >
            {displayText}
          </Markdown>
        );
      } catch (error) {
        console.warn('Markdown render error in StreamingMessage.js:', error);
        return (
          <Text style={style}>
            {displayText}
          </Text>
        );
      }
    } else {
      return (
        <Text style={style}>
          {displayText}
        </Text>
      );
    }
  }
});

// Main Streaming Message Component
export const StreamingMessage = memo(({ 
  message, 
  theme, 
  onAnimationComplete, 
  onEdit, 
  onCopy, 
  onShare, 
  onSaveToVault,
  hideFirstGreeting = false, 
  formatTitle, 
  onAnimationProgress,
  isEditing = false,
  editingText = '',
  onEditTextChange,
  onSaveEdit,
  onCancelEdit,
  sessionId,
  sessionName,
  onOpenReader, // For citation links in RichMessageRenderer
  onSuggestionPick,
}) => {
  const legalIconColor = theme.primary || theme.sendButton || '#2F56E9';
  const [isStreamingComplete, setIsStreamingComplete] = useState(!message.isStreaming);
  
  // Handle streaming completion
  useEffect(() => {
    if (!message.isStreaming && message.isStreaming !== isStreamingComplete) {
      setIsStreamingComplete(true);
      onAnimationComplete?.(message.id);
    }
  }, [message.isStreaming, isStreamingComplete, onAnimationComplete, message.id]);

  return (
    <WebRenderFix message={message} theme={theme}>
      <View
        style={[
          styles.messageWrapper,
          message.sender === 'user' && styles.userMessageWrapper,
        ]}>
        {message.sender === 'bot' && (
          <View style={styles.botLogoContainer}>
            <FontAwesome5 name="robot" size={22} color={legalIconColor} />
          </View>
        )}
        <View style={{ flex: 1 }}>
          <View
            style={[
              styles.messageBubble,
              message.sender === 'user'
                ? [
                    styles.userMessage, 
                    { 
                      backgroundColor: theme.userMessageFallback,
                      borderWidth: 0,
                    }
                  ]
                : [
                    styles.botMessage, 
                    { 
                      backgroundColor: theme.botMessageFallback,
                      borderWidth: theme.isDark ? 0 : 1,
                      borderColor: theme.isDark ? 'transparent' : '#E5E5EA',
                    }
                  ],
            ]}>
            {/* Show typing indicator while waiting for stream content */}
            {(message.isTyping || (message.isStreaming && !message.text && !message.streamingRef?.current)) ? (
              <TypingIndicator theme={theme} />
            ) : message.sender === 'bot' ? (
              <>
                {/* Model chain-of-thought streamed as `reasoning` events.
                    Empty (and renders nothing) when LLM_EXPOSE_REASONING is off. */}
                <ReasoningCard
                  reasoning={message.reasoning}
                  isStreaming={message.isStreaming}
                  hasAnswerText={!!(message.text && message.text.length > 0)}
                  theme={theme}
                />
                {/* Plan emitted by the model inside <plan>...</plan> blocks */}
                <PlanCard
                  plans={message.plans}
                  isStreaming={message.isStreaming}
                  hasAnswerText={!!(message.text && message.text.length > 0)}
                  theme={theme}
                />
                {/* Use StreamingRichRenderer for both streaming and complete states */}
                <StreamingRichRenderer
                  content={message.text}
                  theme={theme}
                  isStreaming={message.isStreaming}
                  streamingRef={message.streamingRef}
                  citations={message.citations || []}
                  mermaidBlocks={message.mermaidBlocks || []}
                  suppressSources={false}
                  onOpenReader={onOpenReader}
                  style={[styles.messageText, { color: theme.botMessageText, fontWeight: '400' }]}
                />
                {/* Tool execution indicator — shown during internet search / code execution. */}
                {message.toolExecuting && (
                  <ToolStatusIndicator toolName={message.toolName} theme={theme} />
                )}
                {/* Trailing streaming spinner — visible while text is still arriving so the
                    user can see at a glance that the response isn't finished yet. */}
                {message.isStreaming && !!message.text && !message.toolExecuting && (
                  <View style={{ flexDirection: 'row', alignItems: 'center', marginTop: 6 }}>
                    <ActivityIndicator size="small" color={theme.secondaryText || theme.botMessageText} />
                  </View>
                )}
                {/* Download cards — files produced by Deep Research / Action Chat
                    when this turn was redirected. The bridge in Citra-Service
                    forwards Action Chat's `artifact` events through. */}
                {Array.isArray(message.artifacts) && message.artifacts.length > 0 && (
                  <View>
                    {message.artifacts.map((artifact, idx) => (
                      <DownloadArtifactCard
                        key={`${artifact.url || 'artifact'}-${idx}`}
                        artifact={artifact}
                        theme={theme}
                      />
                    ))}
                  </View>
                )}
              </>
            ) : (
              // User message
              isEditing ? (
                // Inline editing mode
                <View style={{ width: '100%' }}>
                  <TextInput
                    value={editingText}
                    onChangeText={onEditTextChange}
                    multiline
                    autoFocus
                    style={[
                      styles.messageText,
                      {
                        color: theme.userMessageText,
                        fontWeight: '500',
                        minHeight: 40,
                        maxHeight: 200,
                        borderWidth: 1,
                        borderColor: theme.primary || '#2F56E9',
                        borderRadius: 8,
                        padding: 10,
                        backgroundColor: theme.userMessageFallback,
                      }
                    ]}
                  />
                  <View style={{ flexDirection: 'row', justifyContent: 'flex-end', marginTop: 8, gap: 8 }}>
                    <TouchableOpacity
                      onPress={onCancelEdit}
                      style={{
                        paddingHorizontal: 16,
                        paddingVertical: 8,
                        borderRadius: 8,
                        backgroundColor: theme.isDark ? '#333' : '#E5E5EA',
                      }}
                    >
                      <Text style={{ color: theme.text, fontSize: 14, fontWeight: '500' }}>Cancel</Text>
                    </TouchableOpacity>
                    <TouchableOpacity
                      onPress={onSaveEdit}
                      style={{
                        paddingHorizontal: 16,
                        paddingVertical: 8,
                        borderRadius: 8,
                        backgroundColor: theme.primary || '#2F56E9',
                      }}
                    >
                      <Text style={{ color: '#FFFFFF', fontSize: 14, fontWeight: '500' }}>Send</Text>
                    </TouchableOpacity>
                  </View>
                </View>
              ) : (
                // Normal display mode
                Platform.OS === 'web' ? (
                  <FormattedMessageContent
                    content={message.text || ''}
                    theme={theme}
                    formatTitle={formatTitle}
                    isUserMessage={true}
                  />
                ) : (
                  <Text style={[styles.messageText, { color: theme.userMessageText, fontWeight: '500' }]}>
                    {message.text || ''}
                  </Text>
                )
              )
            )}
            {message.image && (
              <Image source={{ uri: message.image }} style={[styles.messageImage, { borderRadius: 12, marginTop: 8 }]} />
            )}
            {message.document && (
              <Text style={[styles.documentText, { color: theme.text }]}>
                {message.document.name}
              </Text>
            )}
          </View>
          {message.sender === 'bot' && Array.isArray(message.suggestions) && message.suggestions.length > 0 && (
            <SuggestionChips
              suggestions={message.suggestions}
              theme={theme}
              disabled={!!message.suggestionsConsumed}
              onPick={(text, option, suggestion) => {
                onSuggestionPick?.(text, option, suggestion, message);
              }}
            />
          )}
          {!message.isTyping && !hideFirstGreeting && (message.sender !== 'bot' || isStreamingComplete) && !message.hideActions && (
            <MessageActions
              message={message}
              theme={theme}
              onEdit={onEdit}
              onCopy={onCopy}
              onShare={onShare}
              onSaveToVault={onSaveToVault}
              sessionId={sessionId}
              sessionName={sessionName}
            />
          )}
        </View>
      </View>
    </WebRenderFix>
  );
});

export default StreamingMessage;
