import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  TextInput,
  ScrollView,
  Animated,
  Platform,
  KeyboardAvoidingView,
  useWindowDimensions,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { modernStyles, SPACING, BORDER_RADIUS } from '../../styles/modernStyles';
import { ModernIconButton } from './ModernComponents';
import { useAutoGrowingInput } from '../../hooks/useAutoGrowingInput';
import RichMessageRenderer from '../message/RichMessageRenderer';

export const ModernChatInput = React.forwardRef(({
  theme,
  inputText,
  onChangeText,
  onSendMessage,
  onAttachmentPress,
  onMicPress,
  onClipboardPaste,
  onConnectionScopePress,
  activeConnectionsCount = 0,
  isRecording = false,
  isLoading = false,
  questionAttachments = [],
  disabled = false,
  placeholder = "Type a message...",
  containerStyle = {},
  ...restProps
}, forwardedRef) => {
  const {
    onVideoPress,
    isVideoRecording,
    onKeyPress: externalOnKeyPress,
    onPaste: externalOnPaste,
    ...otherProps
  } = restProps;

  const textInputProps = { ...otherProps };
  delete textInputProps.onRemoveAttachment;

  const [isFocused, setIsFocused] = useState(false);
  const micPulseAnim = useRef(new Animated.Value(1)).current;

  // Auto-growing input configuration
  const MIN_HEIGHT = 56; // Provide a taller baseline input area for easier drafting
  const MAX_HEIGHT = 240; // Increased to ~12 lines for large pastes
  
  const {
    height: inputHeight,
    isAtMaxHeight,
    textInputRef,
    handleContentSizeChange,
    scrollToEnd,
    resetHeight,
  } = useAutoGrowingInput({
    minHeight: MIN_HEIGHT,
    maxHeight: MAX_HEIGHT,
    lineHeight: 20,
    maxLines: 14,
  });

  const assignRef = useCallback((node) => {
    textInputRef.current = node;
    if (!forwardedRef) return;
    if (typeof forwardedRef === 'function') {
      forwardedRef(node);
    } else {
      forwardedRef.current = node;
    }
  }, [forwardedRef, textInputRef]);

  // Ensure height resets when parent clears the input programmatically (e.g. after send)
  useEffect(() => {
    if (!inputText || inputText.trim() === '') {
      // Defer slightly to allow TextInput to finish clearing its internal value
      requestAnimationFrame(() => {
        resetHeight();
      });
    }
  }, [inputText, resetHeight]);

  useEffect(() => {
    if (isRecording) {
      const pulse = Animated.loop(
        Animated.sequence([
          Animated.timing(micPulseAnim, {
            toValue: 1.2,
            duration: 600,
            useNativeDriver: Platform.OS !== 'web',
          }),
          Animated.timing(micPulseAnim, {
            toValue: 1,
            duration: 600,
            useNativeDriver: Platform.OS !== 'web',
          }),
        ])
      );
      pulse.start();

      return () => pulse.stop();
    }

    micPulseAnim.setValue(1);
  }, [isRecording, micPulseAnim]);

  const hasContent = inputText.trim().length > 0 || questionAttachments.length > 0;
  const canSend = hasContent && !disabled; // Always allow sending if there's content and not disabled
  const isSendDisabled = !canSend || isLoading;

  // Force height recalculation after paste
  const forceHeightRecalculation = useCallback(() => {
    if (textInputRef.current && Platform.OS === 'web') {
      // On web, manually trigger height adjustment after paste
      requestAnimationFrame(() => {
        if (textInputRef.current) {
          const element = textInputRef.current;
          const scrollHeight = element.scrollHeight;
          const newHeight = Math.min(
            Math.max(MIN_HEIGHT, scrollHeight),
            MAX_HEIGHT
          );
          
          handleContentSizeChange({
            nativeEvent: {
              contentSize: { height: scrollHeight }
            }
          });
          
          // Scroll to bottom if content is large
          if (scrollHeight > MAX_HEIGHT) {
            scrollToEnd();
          }
        }
      });
    }
  }, [handleContentSizeChange, scrollToEnd]);

  // Enhanced text change handler with auto-growing and scrolling
  const handleTextChange = (text) => {
    const previousLength = inputText?.length || 0;
    const newLength = text?.length || 0;
    const lengthDifference = Math.abs(newLength - previousLength);
    
    onChangeText(text);
    
    // If text length changed significantly (likely a paste), force recalculation
    if (lengthDifference > 10 && Platform.OS === 'web') {
      setTimeout(() => {
        forceHeightRecalculation();
      }, 50);
    }
    
    // Auto-scroll to bottom when typing and at max height
    if (textInputRef.current && text.length > 0 && isAtMaxHeight) {
      scrollToEnd();
    }
    
    // Reset height when text is cleared
    if (text.trim() === '') {
      resetHeight();
    }
  };

  // Enhanced send handler with height reset
  const handleSendPress = useCallback(() => {
    if (canSend && !isLoading && onSendMessage) {
      onSendMessage();
      resetHeight(); // Reset height after sending
    }
  }, [canSend, isLoading, onSendMessage, resetHeight]);

  const handlePasteEvent = useCallback((event) => {
    if (Platform.OS === 'web' && onClipboardPaste) {
      event?.preventDefault?.();
      event?.stopPropagation?.();
      onClipboardPaste();
      
      // Force height recalculation after clipboard paste
      setTimeout(() => {
        forceHeightRecalculation();
      }, 50);
    }

    if (externalOnPaste) {
      externalOnPaste(event);
    }
    
    // Also force recalculation for regular paste events
    setTimeout(() => {
      forceHeightRecalculation();
    }, 50);
  }, [externalOnPaste, onClipboardPaste, forceHeightRecalculation]);

  // Handle Enter-to-send on web, Shift+Enter for newline and Ctrl/Cmd+V for paste
  const handleKeyPress = useCallback((e) => {
    if (Platform.OS === 'web') {
      const key = e?.nativeEvent?.key;
      const shift = e?.shiftKey || e?.nativeEvent?.shiftKey;
      const ctrlOrMeta = e?.ctrlKey || e?.metaKey || e?.nativeEvent?.ctrlKey || e?.nativeEvent?.metaKey;

      if (ctrlOrMeta && (key === 'v' || key === 'V')) {
        handlePasteEvent(e);
        externalOnKeyPress?.(e);
        return;
      }

      if (!externalOnKeyPress && key === 'Enter' && !shift && !ctrlOrMeta) {
        e?.preventDefault?.();
        e?.stopPropagation?.();
        if (canSend) handleSendPress();
        return;
      }
    }

    if (externalOnKeyPress) {
      externalOnKeyPress(e);
    }
  }, [externalOnKeyPress, handlePasteEvent, canSend, handleSendPress]);

  // Web-specific simplified input (no mobile controls)
  if (Platform.OS === 'web') {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    const { width: screenWidth } = useWindowDimensions();
    const isMobileInput = screenWidth < 768;

    if (isMobileInput) {
      // === MOBILE WEB: Two-row layout for maximum typing space ===
      return (
        <View 
          style={[{ 
            width: '100%',
            paddingHorizontal: 0,
            paddingVertical: 0,
            margin: 0,
            backgroundColor: 'transparent',
          }, containerStyle]}
          dataSet={{ tour: 'chat-input' }}
        >
          {/* Row 1: Full-width TextInput + Send button */}
          <View 
            style={{
              flexDirection: 'row',
              alignItems: 'flex-end',
              backgroundColor: theme.inputBackground,
              borderRadius: 16,
              borderWidth: 0,
              paddingLeft: 12,
              paddingRight: 4,
              paddingVertical: 4,
              minHeight: 48,
              width: '100%',
              gap: 4,
            }}
          >
            {/* Auto-Growing Text Input Container */}
            <View style={{
              flex: 1,
              borderRadius: 12,
              backgroundColor: 'transparent',
              minHeight: 40,
              maxHeight: MAX_HEIGHT,
              paddingHorizontal: 2,
            }}>
              <TextInput
                ref={assignRef}
                className="chat-input text-input modern-chat-input auto-growing-input"
                data-testid="modern-chat-input"
                style={{
                  width: '100%',
                  fontSize: 16,
                  color: theme.inputText,
                  height: inputHeight,
                  textAlignVertical: 'top',
                  paddingVertical: 6,
                  borderWidth: 0,
                  outline: 'none',
                  backgroundColor: 'transparent',
                  fontFamily: 'system-ui, -apple-system, sans-serif',
                  WebkitUserSelect: 'text',
                  MozUserSelect: 'text',
                  msUserSelect: 'text',
                  userSelect: 'text',
                  lineHeight: 20,
                  transition: 'height 0.15s ease-out',
                }}
                value={inputText}
                onChangeText={handleTextChange}
                onContentSizeChange={handleContentSizeChange}
                placeholder={placeholder}
                placeholderTextColor={theme.inputPlaceholder}
                multiline
                scrollEnabled={isAtMaxHeight}
                showsVerticalScrollIndicator={isAtMaxHeight}
                blurOnSubmit={false}
                returnKeyType="send"
                onKeyPress={handleKeyPress}
                onPaste={handlePasteEvent}
                editable={!disabled}
                onFocus={() => setIsFocused(true)}
                onBlur={() => setIsFocused(false)}
                {...textInputProps}
              />
            </View>

            {/* Send Button */}
            <TouchableOpacity
              style={{
                width: 38,
                height: 38,
                borderRadius: 19,
                backgroundColor: isSendDisabled ? theme.disabled : theme.sendButton,
                justifyContent: 'center',
                alignItems: 'center',
                opacity: isSendDisabled ? 0.6 : 1,
                alignSelf: 'flex-end',
                marginBottom: 2,
              }}
              onPress={handleSendPress}
              disabled={isSendDisabled}
            >
              <Ionicons name="send" size={18} color="#FFFFFF" />
            </TouchableOpacity>
          </View>

          {/* Row 2: Action buttons underneath */}
          <View 
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              paddingHorizontal: 8,
              paddingTop: 6,
              paddingBottom: 2,
              gap: 16,
            }}
          >
            {/* Upload Button — rendered only when the parent supplies a
                handler. Main chat passes none: uploads land in a personal
                vault, and that surface is enterprise-only. */}
            {onAttachmentPress && (
              <View dataSet={{ tour: 'upload-button' }}>
                <TouchableOpacity
                  style={{
                    width: 32,
                    height: 32,
                    borderRadius: 16,
                    backgroundColor: 'transparent',
                    justifyContent: 'center',
                    alignItems: 'center',
                  }}
                  onPress={onAttachmentPress}
                  disabled={disabled}
                >
                  <Ionicons name="add" size={22} color={theme.sendButton} />
                </TouchableOpacity>
              </View>
            )}

            {/* Connection Scope Button */}
            {onConnectionScopePress && (
              <View dataSet={{ tour: 'query-sources-button' }}>
                <TouchableOpacity
                  style={{
                    width: 32,
                    height: 32,
                    borderRadius: 16,
                    backgroundColor: 'transparent',
                    justifyContent: 'center',
                    alignItems: 'center',
                  }}
                  onPress={onConnectionScopePress}
                  disabled={disabled}
                >
                  <Ionicons name="link-outline" size={20} color={theme.sendButton} />
                  {activeConnectionsCount > 0 && (
                    <View style={{
                      position: 'absolute',
                      top: -2,
                      right: -2,
                      backgroundColor: '#10b981',
                      borderRadius: 8,
                      minWidth: 16,
                      height: 16,
                      justifyContent: 'center',
                      alignItems: 'center',
                      paddingHorizontal: 4,
                    }}>
                      <Text style={{ color: 'white', fontSize: 10, fontWeight: 'bold', lineHeight: 12 }}>
                        {activeConnectionsCount}
                      </Text>
                    </View>
                  )}
                </TouchableOpacity>
              </View>
            )}

            {/* Mic Button */}
            <Animated.View
              style={{
                justifyContent: 'center',
                alignItems: 'center',
                transform: [{ scale: micPulseAnim }],
              }}
            >
              <TouchableOpacity
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: 16,
                  backgroundColor: isRecording ? theme.error : 'transparent',
                  justifyContent: 'center',
                  alignItems: 'center',
                }}
                onPress={onMicPress}
                disabled={disabled}
              >
                <Ionicons
                  name={isRecording ? "stop" : "mic"}
                  size={20}
                  color={isRecording ? "#FFFFFF" : theme.sendButton}
                />
              </TouchableOpacity>
            </Animated.View>
          </View>
        </View>
      );
    }

    // === DESKTOP WEB: Original single-row layout ===
    return (
      <View 
        style={[{ 
          width: '100%', // Full width for web input
          paddingHorizontal: 0,
          paddingVertical: 0,
          paddingTop: 0, // Remove top padding to reduce gap
          paddingBottom: 0,
          margin: 0,
          marginTop: 0, // Explicitly remove top margin for web
          backgroundColor: 'transparent',
        }, containerStyle]}
        dataSet={{ tour: 'chat-input' }}
      >
        {/* Web Input Row with integrated controls and auto-growing */}
        <View 
          style={{
            flexDirection: 'row',
            alignItems: 'flex-end', // Changed from center to flex-end for auto-growing
            backgroundColor: theme.inputBackground,
            borderRadius: 16,
            borderWidth: 0,
            paddingHorizontal: 12,
            paddingVertical: 8,
            minHeight: 56,
            width: '100%',
            margin: 0,
            marginRight: 0,
            boxShadow: 'none',
            outline: 'none',
            gap: 6,
          }}
        >
          {/* Left Controls - Upload Button. Same rule as the stacked layout
              above: no handler from the parent → no "+". */}
          {onAttachmentPress && (
            <View dataSet={{ tour: 'upload-button' }}>
              <TouchableOpacity
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: 16,
                  backgroundColor: 'transparent',
                  justifyContent: 'center',
                  alignItems: 'center',
                }}
                onPress={onAttachmentPress}
                disabled={disabled}
              >
                <Ionicons name="add" size={20} color={theme.sendButton} />
              </TouchableOpacity>
            </View>
          )}

          {/* Connection Scope Button */}
          {onConnectionScopePress && (
            <View dataSet={{ tour: 'query-sources-button' }}>
              <TouchableOpacity
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: 16,
                  backgroundColor: 'transparent',
                  justifyContent: 'center',
                  alignItems: 'center',
                  marginLeft: 8,
                }}
                onPress={onConnectionScopePress}
                disabled={disabled}
              >
                <Ionicons name="link-outline" size={20} color={theme.sendButton} />
              {/* Badge showing active connections count */}
              {activeConnectionsCount > 0 && (
                <View style={{
                  position: 'absolute',
                  top: -2,
                  right: -2,
                  backgroundColor: '#10b981',
                  borderRadius: 8,
                  minWidth: 16,
                  height: 16,
                  justifyContent: 'center',
                  alignItems: 'center',
                  paddingHorizontal: 4,
                }}>
                  <Text style={{ 
                    color: 'white', 
                    fontSize: 10, 
                    fontWeight: 'bold',
                    lineHeight: 12,
                  }}>
                    {activeConnectionsCount}
                  </Text>
                </View>
              )}
              </TouchableOpacity>
            </View>
          )}

          {/* Auto-Growing Text Input Container */}
          <View style={{
            flex: 1,
            borderRadius: 12,
            backgroundColor: 'transparent',
            minHeight: MIN_HEIGHT,
            maxHeight: MAX_HEIGHT,
            paddingHorizontal: 4,
          }}>
            <TextInput
              ref={assignRef}
              className="chat-input text-input modern-chat-input auto-growing-input"
              data-testid="modern-chat-input"
              style={{
                width: '100%',
                fontSize: 16,
                color: theme.inputText,
                height: inputHeight,
                textAlignVertical: 'top',
                paddingVertical: 8,
                borderWidth: 0,
                outline: 'none',
                backgroundColor: 'transparent',
                fontFamily: Platform.OS === 'web' ? 'system-ui, -apple-system, sans-serif' : undefined,
                WebkitUserSelect: 'text',
                MozUserSelect: 'text',
                msUserSelect: 'text',
                userSelect: 'text',
                lineHeight: 20,
                ...(Platform.OS === 'web' && {
                  transition: 'height 0.15s ease-out',
                }),
              }}
              value={inputText}
              onChangeText={handleTextChange}
              onContentSizeChange={handleContentSizeChange}
              placeholder={placeholder}
              placeholderTextColor={theme.inputPlaceholder}
              multiline
              scrollEnabled={isAtMaxHeight}
              showsVerticalScrollIndicator={isAtMaxHeight}
              blurOnSubmit={false}
              returnKeyType={Platform.OS === 'web' ? 'send' : 'default'}
              onKeyPress={handleKeyPress}
              onPaste={handlePasteEvent}
              editable={!disabled}
              onFocus={() => setIsFocused(true)}
              onBlur={() => setIsFocused(false)}
              {...textInputProps}
            />
          </View>

          {/* Right Controls */}
          <Animated.View
            style={{
              justifyContent: 'center',
              alignItems: 'center',
              alignSelf: 'flex-end',
              marginRight: 6,
              transform: [
                { scale: micPulseAnim },
              ],
            }}
          >
            <TouchableOpacity
              style={{
                width: 36,
                height: 36,
                borderRadius: 18,
                backgroundColor: isRecording ? theme.error : 'transparent',
                justifyContent: 'center',
                alignItems: 'center',
              }}
              onPress={onMicPress}
              disabled={disabled}
            >
              <Ionicons
                name={isRecording ? "stop" : "mic"}
                size={20}
                color={isRecording ? "#FFFFFF" : theme.sendButton}
              />
            </TouchableOpacity>
          </Animated.View>

          <TouchableOpacity
            style={{
              width: 40,
              height: 40,
              borderRadius: 20,
              backgroundColor: isSendDisabled ? theme.disabled : theme.sendButton,
              justifyContent: 'center',
              alignItems: 'center',
              opacity: isSendDisabled ? 0.6 : 1,
              alignSelf: 'flex-end', // Align to bottom for auto-growing input
              marginLeft: 2,
            }}
            onPress={handleSendPress}
            disabled={isSendDisabled}
          >
            <Ionicons
              name="send"
              size={20}
              color="#FFFFFF"
            />
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  // Mobile version with all controls and auto-growing
  return (
    <View style={[modernStyles.modernInputContainer, { backgroundColor: theme.background }, containerStyle]}>
      {/* Mobile Input Row with auto-growing support */}
      <View 
        style={[
          modernStyles.modernInputWrapper,
          {
            backgroundColor: theme.inputBackground,
            minHeight: MIN_HEIGHT,
            maxHeight: MAX_HEIGHT + 24, // Add space for padding
            alignItems: 'flex-end', // Align controls to bottom for auto-growing
          }
        ]}
      >
        {/* Auto-Growing Text Input Container */}
        <View style={{
          flex: 1,
          minHeight: MIN_HEIGHT,
          maxHeight: MAX_HEIGHT,
        }}>
          <TextInput
            ref={assignRef}
            className="chat-input text-input modernTextInput auto-growing-input"
            style={[
              modernStyles.modernTextInput,
              { 
                color: theme.inputText,
                height: inputHeight,
                textAlignVertical: 'top',
                lineHeight: 20,
                // Ensure text selection works
                WebkitUserSelect: 'text',
                MozUserSelect: 'text',
                msUserSelect: 'text',
                userSelect: 'text',
                ...(Platform.OS === 'web' && {
                  transition: 'height 0.15s ease-out',
                }),
              }
            ]}
            value={inputText}
            onChangeText={handleTextChange}
            onContentSizeChange={handleContentSizeChange}
            placeholder={placeholder}
            placeholderTextColor={theme.inputPlaceholder}
            multiline
            scrollEnabled={isAtMaxHeight}
            showsVerticalScrollIndicator={isAtMaxHeight}
            editable={!disabled}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
            onKeyPress={handleKeyPress}
            onPaste={handlePasteEvent}
            {...textInputProps}
          />
        </View>

        <View style={modernStyles.modernInputActions}>
            {/* Clipboard Paste Button */}
            <ModernIconButton
              icon="clipboard"
              size={40}
              variant="ghost"
              theme={theme}
              onPress={onClipboardPaste}
              disabled={disabled}
            />

            {/* Microphone Button */}
            <Animated.View style={{ transform: [{ scale: micPulseAnim }] }}>
              <ModernIconButton
                icon={isRecording ? "stop" : "mic"}
                size={40}
                variant={isRecording ? "primary" : "ghost"}
                theme={theme}
                onPress={onMicPress}
                disabled={disabled}
                style={{
                  backgroundColor: isRecording ? theme.error : 'transparent',
                }}
              />
            </Animated.View>

            {/* Send Button */}
            <ModernIconButton
              icon={isLoading ? "refresh" : "send"}
              size={44}
              variant={canSend ? "primary" : "ghost"}
              theme={theme}
              onPress={handleSendPress}
              disabled={!canSend}
              style={{
                backgroundColor: canSend ? theme.sendButton : theme.disabled,
              }}
            />

            {/* Attachment Button - Moved to extreme right. Same rule as the
                other two layouts: no handler from the parent → no "+". */}
            {onAttachmentPress && (
              <ModernIconButton
                icon="add"
                size={40}
                variant="ghost"
                theme={theme}
                onPress={onAttachmentPress}
                disabled={disabled}
              />
            )}
          </View>
      </View>
    </View>
  );
});

ModernChatInput.displayName = 'ModernChatInput';

export const ModernMessageBubble = ({
  message,
  isUser = false,
  theme,
  onEdit,
  onCopy,
  onShare,
  onOpenReader,
  children,
  ...props
}) => {
  const [showActions, setShowActions] = useState(false);
  const actionsOpacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.timing(actionsOpacity, {
      toValue: showActions ? 1 : 0,
      duration: 200,
      useNativeDriver: Platform.OS !== 'web',
    }).start();
  }, [showActions, actionsOpacity]);

  const toggleActions = () => {
    setShowActions(!showActions);
  };

  const bubbleStyle = [
    modernStyles.modernMessageBubble,
    {
      backgroundColor: isUser ? theme.userMessage : theme.botMessage,
      alignSelf: isUser ? 'flex-end' : 'flex-start',
    },
    isUser ? modernStyles.modernUserMessageBubble : modernStyles.modernBotMessageBubble,
  ];

  const isWeb = Platform.OS === 'web';

  return (
    <View style={[modernStyles.modernMessageWrapper, isUser ? modernStyles.modernUserMessageWrapper : {}]}>
      {!isUser && (
        <View style={[
          modernStyles.modernBotAvatar,
          { backgroundColor: theme.primary }
        ]}>
          <Ionicons name="chatbubble" size={18} color={theme.textOnPrimary} />
        </View>
      )}

      {/* On web, use a non-touchable container to allow text selection */}
      <View
        style={[
          bubbleStyle,
          isWeb ? { position: 'relative' } : null,
        ]}
        data-testid="message-content"
        className="modern-chat-message"
        {...props}
      >
        {isUser ? (
          <Text
            selectable
            className="message-text chat-message modernMessageText modern-message-content"
            data-testid="message-text"
            style={[
              modernStyles.modernMessageText,
              {
                color: isUser ? theme.userMessageText : theme.botMessageText,
                WebkitUserSelect: 'text',
                MozUserSelect: 'text',
                msUserSelect: 'text',
                userSelect: 'text',
                cursor: 'text',
              },
            ]}
          >
            {children || message?.text || message}
          </Text>
        ) : (
          <View style={{
            // Ensure rich renderer inherits bubble text color and spacing
            width: '100%'
          }}>
            <RichMessageRenderer 
              content={children || message?.text || ''}
              theme={{ ...theme, text: theme.botMessageText, isDark: theme.isDark }}
              isUserMessage={false}
              citations={message?.citations || []}
              onOpenReader={onOpenReader}
            />
          </View>
        )}

        {/* Inline actions bar */}
        <Animated.View
          style={{
            opacity: actionsOpacity,
            flexDirection: 'row',
            justifyContent: isUser ? 'flex-end' : 'flex-start',
            marginTop: SPACING.sm,
            gap: SPACING.sm,
          }}
          pointerEvents={showActions ? 'auto' : 'none'}
        >
          <ModernIconButton
            icon="copy-outline"
            size={32}
            variant="ghost"
            theme={theme}
            onPress={() => onCopy && onCopy(message?.text || message)}
          />
          {isUser && onEdit && (
            <ModernIconButton
              icon="create-outline"
              size={32}
              variant="ghost"
              theme={theme}
              onPress={() => onEdit(message)}
            />
          )}
          <ModernIconButton
            icon="share-outline"
            size={32}
            variant="ghost"
            theme={theme}
            onPress={() => onShare && onShare(message?.text || message)}
          />
        </Animated.View>

        {/* Web-only floating toggle button to show/hide actions without blocking selection */}
        {isWeb && (
          <TouchableOpacity
            onPress={toggleActions}
            activeOpacity={0.8}
            style={{
              position: 'absolute',
              top: 6,
              right: isUser ? 6 : undefined,
              left: isUser ? undefined : 6,
              padding: 6,
              borderRadius: 12,
              backgroundColor: 'rgba(0,0,0,0.08)',
            }}
          >
            <Ionicons name="ellipsis-horizontal" size={18} color={isUser ? theme.userMessageText : theme.botMessageText} />
          </TouchableOpacity>
        )}
      </View>
    </View>
  );
};

export const ModernTypingIndicator = ({ theme, visible = true }) => {
  const dot1Anim = useRef(new Animated.Value(0)).current;
  const dot2Anim = useRef(new Animated.Value(0)).current;
  const dot3Anim = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (visible) {
      const animateDots = () => {
        const createDotAnimation = (animValue, delay) =>
          Animated.loop(
            Animated.sequence([
              Animated.delay(delay),
              Animated.timing(animValue, {
                toValue: 1,
                duration: 400,
                useNativeDriver: Platform.OS !== 'web',
              }),
              Animated.timing(animValue, {
                toValue: 0,
                duration: 400,
                useNativeDriver: Platform.OS !== 'web',
              }),
            ])
          );

        Animated.parallel([
          createDotAnimation(dot1Anim, 0),
          createDotAnimation(dot2Anim, 150),
          createDotAnimation(dot3Anim, 300),
        ]).start();
      };

      animateDots();

      return () => {
        dot1Anim.stopAnimation();
        dot2Anim.stopAnimation();
        dot3Anim.stopAnimation();
      };
    }
  }, [visible, dot1Anim, dot2Anim, dot3Anim]);

  if (!visible) return null;

  const getDotStyle = (animValue) => ({
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: theme.textTertiary,
    marginHorizontal: 2,
    opacity: animValue,
  });

  return (
    <View style={[modernStyles.modernMessageWrapper]}>
      <View style={[
        modernStyles.modernBotAvatar,
        { backgroundColor: theme.primary }
      ]}>
        <Ionicons name="chatbubble" size={18} color={theme.textOnPrimary} />
      </View>

      <View style={[
        modernStyles.modernMessageBubble,
        modernStyles.modernBotMessageBubble,
        { backgroundColor: theme.botMessage }
      ]}>
        <View style={{
          flexDirection: 'row',
          alignItems: 'center',
          paddingVertical: SPACING.sm,
        }}>
          <Text style={[
            modernStyles.modernMessageText,
            { color: theme.botMessageText, marginRight: SPACING.sm }
          ]}>
            AI is typing
          </Text>
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <Animated.View style={getDotStyle(dot1Anim)} />
            <Animated.View style={getDotStyle(dot2Anim)} />
            <Animated.View style={getDotStyle(dot3Anim)} />
          </View>
        </View>
      </View>
    </View>
  );
};

export const ModernSideMenu = ({
  visible,
  onClose,
  theme,
  currentScreen,
  onNavigate,
  menuItems = [],
  children,
}) => {
  const slideAnim = useRef(new Animated.Value(-320)).current;
  const overlayOpacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (visible) {
      Animated.parallel([
        Animated.timing(slideAnim, {
          toValue: 0,
          duration: 300,
          useNativeDriver: Platform.OS !== 'web',
        }),
        Animated.timing(overlayOpacity, {
          toValue: 1,
          duration: 300,
          useNativeDriver: Platform.OS !== 'web',
        }),
      ]).start();
    } else {
      Animated.parallel([
        Animated.timing(slideAnim, {
          toValue: -320,
          duration: 250,
          useNativeDriver: Platform.OS !== 'web',
        }),
        Animated.timing(overlayOpacity, {
          toValue: 0,
          duration: 250,
          useNativeDriver: Platform.OS !== 'web',
        }),
      ]).start();
    }
  }, [visible, slideAnim, overlayOpacity]);

  if (!visible) return null;

  return (
    <>
      {/* Overlay */}
      <Animated.View
        style={[
          modernStyles.modernSideMenuOverlay,
          { opacity: overlayOpacity }
        ]}
      >
        <TouchableOpacity
          style={{ flex: 1 }}
          onPress={onClose}
          activeOpacity={1}
        />
      </Animated.View>

      {/* Menu */}
      <Animated.View
        style={[
          modernStyles.modernSideMenu,
          {
            backgroundColor: theme.menuBackground,
            transform: [{ translateX: slideAnim }],
          }
        ]}
      >
        {/* Header */}
        <View style={[
          modernStyles.modernSideMenuHeader,
          { backgroundColor: theme.headerBackground, borderBottomColor: theme.border }
        ]}>
          <Text style={[
            modernStyles.modernSideMenuTitle,
            { color: theme.headerText }
          ]}>
            Citra AI
          </Text>
        </View>

        {/* Content */}
        <ScrollView style={modernStyles.modernSideMenuContent}>
          {menuItems.map((item, index) => (
            <TouchableOpacity
              key={item.id || index}
              style={[
                modernStyles.modernMenuItem,
                {
                  backgroundColor: currentScreen === item.screen ? theme.menuItemActive : 'transparent',
                }
              ]}
              onPress={() => {
                onNavigate(item.screen);
                onClose();
              }}
              activeOpacity={0.7}
            >
              <View style={modernStyles.modernMenuItemIcon}>
                <Ionicons
                  name={item.icon}
                  size={24}
                  color={currentScreen === item.screen ? theme.primary : theme.text}
                />
              </View>
              <Text style={[
                modernStyles.modernMenuItemText,
                {
                  color: currentScreen === item.screen ? theme.primary : theme.text,
                  fontWeight: currentScreen === item.screen ? '600' : '500',
                }
              ]}>
                {item.title}
              </Text>
            </TouchableOpacity>
          ))}
          
          {children}
        </ScrollView>
      </Animated.View>
    </>
  );
};
