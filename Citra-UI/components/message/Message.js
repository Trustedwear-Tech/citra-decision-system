// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

// Main Message component for rendering individual messages
import React, { useState, useEffect, memo } from 'react';
import { View, Text, Image, Platform, TouchableOpacity, TextInput } from 'react-native';
import { Ionicons, FontAwesome5 } from '@expo/vector-icons';
import { styles } from '../../styles';
import { TypingIndicator } from '../ui/TypingIndicator';
import { 
  // AnimatedFormattedContent, 
  // FormattedMessageContent, 
  MessageActions,
  WebHTMLRenderer 
} from './MessageComponents';
import WebRenderFix from '../../WebRenderFix';
import RichMessageRenderer from './RichMessageRenderer';
import SuggestionChips from './SuggestionChips';

// Import markdown renderer for mobile only with error handling
let Markdown = null;
if (Platform.OS !== 'web') {
  try {
    Markdown = require('react-native-markdown-display').default;
  } catch (error) {
    console.warn('Failed to load react-native-markdown-display in Message.js:', error.message);
    // Markdown will remain null and we'll handle it gracefully
  }
}

// Animated Text Component
const AnimatedText = ({ text, style, theme, onAnimationComplete = () => {}, isUpdated = false, shouldAnimate = false, formatTitle, startIndex = 0, onProgress = () => {}, render }) => {
  const safeText = (text === null || text === undefined) ? '' : String(text);
  
  const [displayedText, setDisplayedText] = useState('');
  const [currentIndex, setCurrentIndex] = useState(startIndex);

  useEffect(() => {
    if (isUpdated || !shouldAnimate) {
      setDisplayedText(safeText);
      setCurrentIndex(safeText.length);
      onProgress(safeText.length);
      onAnimationComplete();
      return;
    }
  }, [safeText, isUpdated, shouldAnimate]);

  useEffect(() => {
    if (isUpdated || !shouldAnimate) return;
    
    if (currentIndex < safeText.length) {
      const timer = setTimeout(() => {
        // Larger chunk and paced timer for smoother updates
        const chunkSize = Math.min(60, safeText.length - currentIndex);
        const newIndex = currentIndex + chunkSize;
        setCurrentIndex(newIndex);
        setDisplayedText(safeText.slice(0, newIndex));
        onProgress(newIndex);
      }, 16); // ~60 FPS pacing

      return () => clearTimeout(timer);
    } else {
      onAnimationComplete();
    }
  }, [currentIndex, safeText, isUpdated, shouldAnimate]);

  const displayed = safeText.slice(0, currentIndex);
  
  // If a custom renderer is provided, delegate rendering (used for RichMessageRenderer)
  if (typeof render === 'function') {
    return render(displayed);
  }

  if (Platform.OS === 'web') {
    return (
      <WebHTMLRenderer
        content={displayed}
        theme={theme}
        style={style}
      />
    );
  } else {
    // Mobile: Use Markdown for bot responses if theme is provided
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
              hr: {
                backgroundColor: theme.isDark ? '#4a5568' : '#e2e8f0',
                height: 2,
                marginVertical: 24,
                borderRadius: 1,
              },
              blockquote: {
                backgroundColor: theme.isDark ? 'rgba(74, 158, 255, 0.1)' : 'rgba(0, 122, 204, 0.05)',
                borderLeftWidth: 4,
                borderLeftColor: theme.isDark ? '#4a9eff' : '#007acc',
                paddingHorizontal: 16,
                paddingVertical: 12,
                marginVertical: 12,
                borderRadius: 4,
              },
            }}
          >
            {processLaTeXForMobile(displayed)}
          </Markdown>
        );
      } catch (error) {
        console.warn('Markdown render error in Message.js:', error);
        return (
          <Text 
            className="message-text chat-message"
            style={{
              ...style,
              // Enable text selection
              WebkitUserSelect: 'text',
              MozUserSelect: 'text',
              msUserSelect: 'text',
              userSelect: 'text',
            }}
          >
            {displayed}
          </Text>
        );
      }
    } else {
      return (
        <Text 
          className="message-text chat-message"
          style={{
            ...style,
            // Enable text selection
            WebkitUserSelect: 'text',
            MozUserSelect: 'text',
            msUserSelect: 'text',
            userSelect: 'text',
          }}
        >
          {displayed}
        </Text>
      );
    }
  }
};

// Message Error Boundary Component
const MessageErrorBoundary = ({ children, messageId, theme }) => {
  const [hasError, setHasError] = useState(false);

  useEffect(() => {
    setHasError(false);
  }, [messageId]);

  if (hasError) {
    return (
      <View style={[styles.messageWrapper, { backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f9fa' }]}>
        <View style={styles.botLogoContainer}>
          <Ionicons name="warning" size={24} color={theme.isDark ? '#e67e22' : '#f39c12'} />
        </View>
        <View style={{ flex: 1 }}>
          <View style={[styles.messageBubble, styles.botMessage, { backgroundColor: theme.botMessageFallback }]}>
            <Text 
              className="message-text bot-message"
              style={[
                styles.messageText, 
                { 
                  color: theme.botMessageText,
                  // Enable text selection
                  WebkitUserSelect: 'text',
                  MozUserSelect: 'text',
                  msUserSelect: 'text',
                  userSelect: 'text',
                }
              ]}
            >
              ⚠️ Error rendering this message. Try refreshing the page.
            </Text>
          </View>
        </View>
      </View>
    );
  }

  try {
    return children;
  } catch (error) {
    console.error('Message rendering error:', error);
    setHasError(true);
    return null;
  }
};

// Main Message Component
export const Message = memo(({ 
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
  onOpenReader,
  sessionId,
  sessionName,
  onSuggestionPick,
}) => {
  const legalIconColor = theme.primary || theme.sendButton || '#2F56E9';
  const [animationDone, setAnimationDone] = useState(!message.shouldAnimate);
  
  const handleAnimationComplete = () => {
    setAnimationDone(true);
    onAnimationComplete?.(message.id);
  };

  return (
    <MessageErrorBoundary messageId={message.id} theme={theme}>
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
              {/* Show typing indicator while waiting for content */}
              {(message.isTyping || (message.isStreaming && !message.text && !message.streamingRef?.current)) ? (
                <>
                  <TypingIndicator theme={theme} />
                </>
              ) : message.sender === 'bot' ? (
                <>
                  <AnimatedText
                    text={message.text}
                    style={[styles.messageText, { color: theme.botMessageText, fontWeight: '400' }]}
                    theme={theme}
                    shouldAnimate={message.shouldAnimate}
                    isUpdated={message.isUpdated}
                    onAnimationComplete={handleAnimationComplete}
                    onProgress={onAnimationProgress}
                    formatTitle={formatTitle}
                    render={(displayedText) => (
                      <RichMessageRenderer 
                        content={displayedText}
                        theme={{ ...theme, text: theme.botMessageText, isDark: theme.isDark }}
                        isUserMessage={false}
                        suppressSources={
                          typeof displayedText === 'string'
                            ? displayedText.length < (message?.text ? String(message.text).length : 0)
                            : false
                        }
                        onOpenReader={onOpenReader}
                      />
                    )}
                  />
                  {message.isStreaming && (
                    <View style={styles.streamingIndicator}>
                      <Text style={[styles.streamingText, { color: theme.placeholderText }]}>
                        ⚡ Thinking...
                      </Text>
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
                  <Text 
                    className="message-text user-message"
                    style={[
                      styles.messageText, 
                      { 
                        color: theme.userMessageText, 
                        fontWeight: '500',
                        // Enable text selection
                        WebkitUserSelect: 'text',
                        MozUserSelect: 'text',
                        msUserSelect: 'text',
                        userSelect: 'text',
                      }
                    ]}
                  >
                    {message.text || ''}
                  </Text>
                ) : (
                  <Text 
                    className="message-text user-message"
                    style={[
                      styles.messageText, 
                      { 
                        color: theme.userMessageText, 
                        fontWeight: '500',
                        // Enable text selection
                        WebkitUserSelect: 'text',
                        MozUserSelect: 'text',
                        msUserSelect: 'text',
                        userSelect: 'text',
                      }
                    ]}
                  >
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
              
              {/* Citations are rendered by RichMessageRenderer within the bot message content when needed */}
              
              {message.quickActions && message.quickActions.length > 0 && (
                <View style={styles.quickActionsContainer}>
                  {message.quickActions.map((action, index) => (
                    <TouchableOpacity
                      key={index}
                      style={[
                        styles.quickActionButton,
                        action.isPrimary ? styles.quickActionButtonPrimary : styles.quickActionButtonSecondary,
                        { borderColor: theme.borderColor }
                      ]}
                      onPress={action.onPress}
                    >
                      <Text style={[
                        styles.quickActionText,
                        action.isPrimary 
                          ? [styles.quickActionTextPrimary, { color: theme.buttonText }]
                          : [styles.quickActionTextSecondary, { color: theme.text }]
                      ]}>
                        {action.label}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </View>
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
            {!message.isTyping && !hideFirstGreeting && (message.sender !== 'bot' || animationDone) && !message.hideActions && (
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
    </MessageErrorBoundary>
  );
});

export default Message;
