// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

// Enhanced Message component with improved markdown formatting
import React, { memo, useState, useEffect } from 'react';
import { View, Text, Image, Platform, TouchableOpacity, TextInput } from 'react-native';
import { Ionicons, FontAwesome5 } from '@expo/vector-icons';
import { styles } from '../../styles';
import { TypingIndicator } from '../ui/TypingIndicator';
import { MessageActions, WebHTMLRenderer } from './MessageComponents';
import SuggestionChips from './SuggestionChips';
import WebRenderFix from '../../WebRenderFix';
import { processLaTeXForMobile } from '../../utils/textProcessing';
import RichMessageRenderer from './RichMessageRenderer';

// Legal icon color helper will replace previous logo usage

// Import markdown renderer for mobile only with error handling
let Markdown = null;
if (Platform.OS !== 'web') {
  try {
    Markdown = require('react-native-markdown-display').default;
  } catch (error) {
    console.warn('Failed to load react-native-markdown-display:', error.message);
  }
}

// Enhanced markdown styles for better formatting
const createEnhancedMarkdownStyle = (theme) => ({
  body: {
    color: theme.botMessageText,
    fontWeight: '400',
    fontSize: 16,
    fontFamily: 'System',
    lineHeight: 22,
  },
  paragraph: {
    marginBottom: 8,
    marginTop: 0,
    lineHeight: 22,
  },
  heading1: {
    fontSize: 20,
    fontWeight: 'bold',
    marginBottom: 12,
    marginTop: 16,
    color: theme.text,
  },
  heading2: {
    fontSize: 18,
    fontWeight: 'bold',
    marginBottom: 10,
    marginTop: 14,
    color: theme.text,
  },
  heading3: {
    fontSize: 16,
    fontWeight: 'bold',
    marginBottom: 8,
    marginTop: 12,
    color: theme.text,
  },
  strong: {
    fontWeight: 'bold',
    color: theme.text,
  },
  em: {
    fontStyle: 'italic',
    color: theme.text,
  },
  code_inline: {
    backgroundColor: theme.isDark ? '#2a2a2a' : '#f5f5f5',
    color: theme.isDark ? '#ff6b6b' : '#d73a49',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 4,
    fontFamily: 'monospace',
    fontSize: 14,
  },
  code_block: {
    backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f8f8',
    color: theme.isDark ? '#ffffff' : '#000000',
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 6,
    fontFamily: 'monospace',
    fontSize: 13,
    marginVertical: 10,
    borderWidth: 1,
    borderColor: theme.isDark ? '#444444' : '#e1e4e8',
    lineHeight: 18,
  },
  fence: {
    backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f8f8',
    color: theme.isDark ? '#ffffff' : '#000000',
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 6,
    fontFamily: 'monospace',
    fontSize: 13,
    marginVertical: 10,
    borderWidth: 1,
    borderColor: theme.isDark ? '#444444' : '#e1e4e8',
    lineHeight: 18,
  },
  pre: {
    backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f8f8',
    color: theme.isDark ? '#ffffff' : '#000000',
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 6,
    fontFamily: 'monospace',
    fontSize: 13,
    marginVertical: 10,
    borderWidth: 1,
    borderColor: theme.isDark ? '#444444' : '#e1e4e8',
    lineHeight: 18,
  },
  blockquote: {
    backgroundColor: theme.isDark ? 'rgba(74, 158, 255, 0.1)' : 'rgba(0, 122, 204, 0.05)',
    borderLeftColor: theme.isDark ? '#4a9eff' : '#007acc',
    borderLeftWidth: 4,
    paddingLeft: 12,
    paddingVertical: 10,
    marginVertical: 10,
    borderRadius: 4,
  },
  list_item: {
    marginBottom: 6,
    paddingLeft: 4,
    lineHeight: 20,
  },
  bullet_list: {
    marginBottom: 10,
    marginTop: 5,
  },
  ordered_list: {
    marginBottom: 10,
    marginTop: 5,
  },
  bullet_list_icon: {
    color: theme.text,
    fontSize: 16,
    marginRight: 8,
  },
  ordered_list_icon: {
    color: theme.text,
    fontSize: 14,
    fontWeight: 'bold',
    marginRight: 8,
  },
  table: {
    borderWidth: 1,
    borderColor: theme.isDark ? '#4a5568' : '#e2e8f0',
    borderRadius: 8,
    marginVertical: 12,
    backgroundColor: theme.isDark ? '#1a202c' : '#ffffff',
  },
  thead: {
    backgroundColor: theme.isDark ? '#2d3748' : '#f7fafc',
  },
  tbody: {},
  th: {
    color: theme.text,
    fontSize: 14,
    fontWeight: 'bold',
    padding: 12,
    borderRightWidth: 1,
    borderRightColor: theme.isDark ? '#4a5568' : '#e2e8f0',
    borderBottomWidth: 1,
    borderBottomColor: theme.isDark ? '#4a5568' : '#e2e8f0',
  },
  tr: {
    borderBottomWidth: 1,
    borderBottomColor: theme.isDark ? '#4a5568' : '#e2e8f0',
  },
  td: {
    color: theme.text,
    fontSize: 13,
    padding: 12,
    borderRightWidth: 1,
    borderRightColor: theme.isDark ? '#4a5568' : '#e2e8f0',
    lineHeight: 18,
  },
  link: {
    color: theme.isDark ? '#4a9eff' : '#007acc',
    textDecorationLine: 'underline',
  },
  text: {
    color: theme.text,
  },
});

// Enhanced animated text component
const EnhancedAnimatedText = ({ 
  text, 
  theme, 
  shouldAnimate, 
  isUpdated, 
  onAnimationComplete, 
  onProgress,
  textColor,
  render, // optional render prop for custom renderer (e.g., RichMessageRenderer)
}) => {
  const [displayedText, setDisplayedText] = useState('');
  const [currentIndex, setCurrentIndex] = useState(0);
  const [animationDone, setAnimationDone] = useState(!shouldAnimate);

  const safeText = (text === null || text === undefined) ? '' : String(text);

  useEffect(() => {
    if (isUpdated || !shouldAnimate) {
      setDisplayedText(safeText);
      setCurrentIndex(safeText.length);
      setAnimationDone(true);
      onProgress?.(safeText.length);
      onAnimationComplete?.();
      return;
    }
  }, [safeText, isUpdated, shouldAnimate]);

  useEffect(() => {
    if (isUpdated || !shouldAnimate || animationDone) return;
    
    if (currentIndex < safeText.length) {
      const timer = setTimeout(() => {
        // Animate in larger chunks for smoother rendering with fewer reflows
        const chunkSize = Math.min(60, safeText.length - currentIndex);
        const newIndex = currentIndex + chunkSize;
        setCurrentIndex(newIndex);
        setDisplayedText(safeText.slice(0, newIndex));
        onProgress?.(newIndex);
      }, 16); // ~60 FPS pacing

      return () => clearTimeout(timer);
    } else {
      setAnimationDone(true);
      onAnimationComplete?.();
    }
  }, [currentIndex, safeText, isUpdated, shouldAnimate, animationDone]);

  const textToRender = shouldAnimate && !isUpdated ? displayedText : safeText;

  // If a custom render function is provided, delegate rendering
  if (typeof render === 'function') {
    return render(textToRender);
  }

  // For web, use enhanced HTML renderer
  if (Platform.OS === 'web') {
    return (
      <WebHTMLRenderer
        content={textToRender}
        theme={theme}
        style={{
          color: textColor || theme.botMessageText,
          fontWeight: '400',
          fontSize: 16,
          lineHeight: 1.6
        }}
      />
    );
  }

  // For mobile, use enhanced markdown with better styling
  if (Markdown) {
    const markdownStyle = createEnhancedMarkdownStyle(theme);
    
    try {
      return (
        <Markdown style={markdownStyle}>
          {processLaTeXForMobile(textToRender)}
        </Markdown>
      );
    } catch (markdownError) {
      console.warn('Enhanced markdown render error:', markdownError);
      // Fallback to plain text with basic formatting
      return (
        <Text style={[styles.messageText, { 
          color: textColor || theme.botMessageText, 
          fontWeight: '400',
          fontSize: 16,
          lineHeight: 22
        }]}>
          {textToRender}
        </Text>
      );
    }
  }

  // Final fallback to plain text
  return (
    <Text style={[styles.messageText, { 
      color: textColor || theme.botMessageText, 
      fontWeight: '400',
      fontSize: 16,
      lineHeight: 22
    }]}>
      {textToRender}
    </Text>
  );
};

// Enhanced static text display
const EnhancedStaticText = ({ content, theme, textColor, isUserMessage = false, render }) => {
  if (!content) return null;

  const contentStr = typeof content === 'string' ? content : String(content);

  // If a custom render function is provided, delegate rendering
  if (typeof render === 'function') {
    return render(contentStr);
  }

  // For web, use enhanced HTML renderer
  if (Platform.OS === 'web') {
    return (
      <WebHTMLRenderer
        content={contentStr}
        theme={theme}
        style={{
          color: textColor || (isUserMessage ? theme.userMessageText : theme.botMessageText),
          fontWeight: isUserMessage ? '500' : '400',
          fontSize: 16,
          lineHeight: 1.6
        }}
      />
    );
  }

  // For mobile, use enhanced markdown for bot messages
  if (Markdown && !isUserMessage) {
    const markdownStyle = createEnhancedMarkdownStyle(theme);
    
    try {
      return (
        <Markdown style={markdownStyle}>
          {processLaTeXForMobile(contentStr)}
        </Markdown>
      );
    } catch (markdownError) {
      console.warn('Enhanced static markdown render error:', markdownError);
      // Fallback to plain text
      return (
        <Text style={[styles.messageText, { 
          color: textColor || (isUserMessage ? theme.userMessageText : theme.botMessageText), 
          fontWeight: isUserMessage ? '500' : '400',
          fontSize: 16,
          lineHeight: 22
        }]}>
          {contentStr}
        </Text>
      );
    }
  }

  // For user messages or when markdown is not available
  return (
    <Text style={[styles.messageText, { 
      color: textColor || (isUserMessage ? theme.userMessageText : theme.botMessageText), 
      fontWeight: isUserMessage ? '500' : '400',
      fontSize: 16,
      lineHeight: 22
    }]}>
      {contentStr}
    </Text>
  );
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
            <Text style={[styles.messageText, { color: theme.botMessageText }]}>
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

// Main Enhanced Message Component
export const EnhancedMessage = memo(({ 
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
  
  // Debug: Check if onOpenReader is received
  useEffect(() => {
    console.log('🟡 ENHANCED_MESSAGE: Component mounted/updated', {
      messageId: message?.id,
      hasOnOpenReader: !!onOpenReader,
      onOpenReaderType: typeof onOpenReader
    });
  }, [message?.id, onOpenReader]);
  
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
                  {message.shouldAnimate ? (
                    <EnhancedAnimatedText
                      text={message.text}
                      theme={theme}
                      shouldAnimate={message.shouldAnimate}
                      isUpdated={message.isUpdated}
                      onAnimationComplete={handleAnimationComplete}
                      onProgress={onAnimationProgress}
                      textColor={theme.botMessageText}
                      render={(textToRender) => (
                        <RichMessageRenderer
                          content={textToRender}
                          theme={{ ...theme, text: theme.botMessageText, isDark: theme.isDark }}
                          isUserMessage={false}
                          suppressSources={!animationDone}
                          onOpenReader={onOpenReader}
                        />
                      )}
                    />
                  ) : (
                    <EnhancedStaticText
                      content={message.text}
                      theme={theme}
                      textColor={theme.botMessageText}
                      isUserMessage={false}
                      render={(contentStr) => (
                        <RichMessageRenderer
                          content={contentStr}
                          theme={{ ...theme, text: theme.botMessageText, isDark: theme.isDark }}
                          isUserMessage={false}
                          suppressSources={false}
                          onOpenReader={onOpenReader}
                        />
                      )}
                    />
                  )}
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
                  <EnhancedStaticText
                    content={message.text || ''}
                    theme={theme}
                    textColor={theme.userMessageText}
                    isUserMessage={true}
                  />
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
                          ? [styles.quickActionTextPrimary, { color: '#FFFFFF' }]
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

export default EnhancedMessage;
