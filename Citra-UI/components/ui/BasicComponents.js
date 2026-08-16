// Basic UI components used across the app
import React, { useState, useRef, useEffect } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  TextInput,
  ScrollView,
  Platform,
  Animated,
  Easing,
  Clipboard
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { styles } from '../../styles';

// History Logo Component
export const HistoryLogo = ({ theme }) => (
  <View style={styles.historyLogoContainer}>
    <Ionicons name="chatbubbles-outline" size={24} color={theme.text} />
  </View>
);

// Simple Chat Input Component
export const SimpleChatInput = ({ 
  theme, 
  inputText, 
  onChangeText, 
  onSendMessage,
  onStartRecording,
  isRecording,
  isLoading,
  questionAttachments = [],
  ...otherProps 
}) => {
  // Handle Enter-to-send on web, Shift+Enter for newline
  const handleKeyPress = (e) => {
    if (Platform.OS === 'web') {
      const key = e?.nativeEvent?.key;
      if (key === 'Enter') {
        const shift = e?.shiftKey || e?.nativeEvent?.shiftKey;
        if (!shift && (inputText.trim() || questionAttachments.length > 0) && !isLoading) {
          e.preventDefault();
          e.stopPropagation();
          onSendMessage();
        }
      }
    }
  };

  return (
    <View style={styles.inputContainer}>
      <View style={[styles.inputWrapper, { backgroundColor: theme.inputBackground, borderColor: theme.borderColor }]}>
        <TextInput
          style={[styles.textInput, { color: theme.text }]}
          placeholder="Ask me anything about your memories..."
          placeholderTextColor={theme.placeholderText}
          value={inputText}
          onChangeText={onChangeText}
          multiline
          maxLength={1000}
          onKeyPress={handleKeyPress}
          {...otherProps}
        />
        
        <TouchableOpacity
          style={[
            styles.sendButton, 
            { 
              backgroundColor: (inputText.trim() || questionAttachments.length > 0) && !isLoading ? theme.sendButton : theme.borderColor 
            }
          ]}
          onPress={onSendMessage}
        >
          <Ionicons 
            name="send" 
            size={20} 
            color={(inputText.trim() || questionAttachments.length > 0) && !isLoading ? theme.buttonText : theme.placeholderText} 
          />
        </TouchableOpacity>
      </View>
    </View>
  );
};

// Stop Generating Button Component
export const StopGeneratingButton = ({ onPress, theme }) => {
  const scaleAnim = useRef(new Animated.Value(0)).current;
  const rotateAnim = useRef(new Animated.Value(0)).current;
  const pulseAnim = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    // Entry animation
    Animated.spring(scaleAnim, {
      toValue: 1,
      tension: 120,
      friction: 8,
      useNativeDriver: Platform.OS !== 'web',
    }).start();

    // Rotation animation
    const rotate = Animated.loop(
      Animated.timing(rotateAnim, {
        toValue: 1,
        duration: 2000,
        easing: Easing.linear,
        useNativeDriver: Platform.OS !== 'web',
      })
    );

    // Pulse animation
    const pulse = Animated.loop(
      Animated.sequence([
        Animated.timing(pulseAnim, {
          toValue: 1.1,
          duration: 800,
          easing: Easing.inOut(Easing.ease),
          useNativeDriver: Platform.OS !== 'web',
        }),
        Animated.timing(pulseAnim, {
          toValue: 1,
          duration: 800,
          easing: Easing.inOut(Easing.ease),
          useNativeDriver: Platform.OS !== 'web',
        }),
      ])
    );

    rotate.start();
    pulse.start();

    return () => {
      rotate.stop();
      pulse.stop();
      scaleAnim.stopAnimation();
    };
  }, [scaleAnim, rotateAnim, pulseAnim]);

  const handlePress = () => {
    // Exit animation before calling onPress
    Animated.timing(scaleAnim, {
      toValue: 0,
      duration: 200,
      easing: Easing.inOut(Easing.ease),
      useNativeDriver: Platform.OS !== 'web',
    }).start(() => {
      onPress();
    });
  };

  const rotateInterpolate = rotateAnim.interpolate({
    inputRange: [0, 1],
    outputRange: ['0deg', '360deg'],
  });

  return (
    <Animated.View
      style={[
        styles.stopGeneratingButton,
        {
          transform: [
            { scale: scaleAnim },
            { scale: pulseAnim },
            { rotate: rotateInterpolate },
          ],
        },
      ]}
    >
      <TouchableOpacity
        onPress={handlePress}
        style={{
          width: '100%',
          height: '100%',
          justifyContent: 'center',
          alignItems: 'center',
        }}
        activeOpacity={0.8}
      >
        <Ionicons name="stop" size={24} color="#FFFFFF" />
      </TouchableOpacity>
    </Animated.View>
  );
};

// Theme Toggle Component
export const ThemeToggle = ({ isDarkMode, toggleTheme }) => {
  const translateX = useRef(new Animated.Value(isDarkMode ? 28 : 0)).current;

  useEffect(() => {
    Animated.spring(translateX, {
      toValue: isDarkMode ? 28 : 0,
      useNativeDriver: Platform.OS !== 'web',
    }).start();
  }, [isDarkMode, translateX]);

  return (
    <TouchableOpacity
      onPress={toggleTheme}
      style={[
        styles.themeToggle,
        {
          backgroundColor: isDarkMode ? '#333' : '#d1d5db',
          borderColor: isDarkMode ? '#555' : '#9ca3af',
          borderWidth: 1,
        }
      ]}
      activeOpacity={0.8}
      accessibilityRole="switch"
      accessibilityState={{ checked: isDarkMode }}
      accessibilityLabel="Toggle Theme">
      <Animated.View
        style={[
          styles.toggleButton, 
          { 
            transform: [{ translateX }],
            backgroundColor: isDarkMode ? '#ffd700' : '#f59e0b',
            shadowColor: '#000',
            shadowOffset: { width: 0, height: 1 },
            shadowOpacity: 0.3,
            shadowRadius: 2,
            elevation: 3,
          }
        ]}>
        <Ionicons
          name={isDarkMode ? 'moon' : 'sunny'}
          size={20}
          color={isDarkMode ? '#222' : '#fff'}
        />
      </Animated.View>
    </TouchableOpacity>
  );
};

// Web Theme Toggle Component
export const ThemeToggleWeb = ({ isDarkMode, toggleTheme }) => (
  <TouchableOpacity
    onPress={toggleTheme}
    activeOpacity={0.7}
    style={{
      width: 50,
      height: 26,
      borderRadius: 13,
      padding: 3,
      flexDirection: 'row',
      justifyContent: isDarkMode ? 'flex-end' : 'flex-start',
      alignItems: 'center',
      backgroundColor: isDarkMode ? '#333' : '#d1d5db',
      border: isDarkMode ? 'none' : '1px solid #9ca3af',
      boxShadow: '0 2px 6px rgba(0,0,0,0.2)',
      transition: 'background-color 0.3s',
      cursor: 'pointer'
    }}
  >
    <View style={{
      width: 20,
      height: 20,
      borderRadius: 10,
      backgroundColor: isDarkMode ? '#ffd700' : '#f59e0b',
      justifyContent: 'center',
      alignItems: 'center',
      transform: [{ translateX: isDarkMode ? 0 : 0 }],
      boxShadow: isDarkMode ? 'none' : '0 1px 3px rgba(0,0,0,0.3)',
    }}>
      <Ionicons
        name={isDarkMode ? 'moon' : 'sunny'}
        size={20}
        color={isDarkMode ? '#222' : '#fff'}
      />
    </View>
  </TouchableOpacity>
);

// Code Block Component
export const CodeBlock = ({ code, language, theme }) => {
  const [copied, setCopied] = useState(false);

  const copyToClipboard = async () => {
    try {
      await Clipboard.setStringAsync(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      console.error('Error copying to clipboard:', error);
    }
  };

  return (
    <View style={[styles.codeBlock, { 
      backgroundColor: theme.isDark ? '#2d2d30' : '#f6f8fa',
      borderColor: theme.isDark ? '#404040' : '#d1d9e0'
    }]}>
      <View style={[styles.codeBlockHeader, { 
        backgroundColor: theme.isDark ? '#1e1e1e' : '#f1f3f4',
        borderBottomColor: theme.isDark ? '#404040' : '#d1d9e0'
      }]}>
        <Text style={[styles.codeBlockLanguage, { 
          color: theme.isDark ? '#cccccc' : '#586069' 
        }]}>
          {language || 'code'}
        </Text>
        <TouchableOpacity
          style={[styles.copyButton, { 
            backgroundColor: copied ? (theme.isDark ? '#28a745' : '#28a745') : 'transparent',
            borderColor: theme.isDark ? '#404040' : '#d1d9e0'
          }]}
          onPress={copyToClipboard}
        >
          <Ionicons 
            name={copied ? "checkmark" : "copy-outline"} 
            size={16} 
            color={copied ? 'white' : (theme.isDark ? '#cccccc' : '#586069')} 
          />
          <Text style={[styles.copyButtonText, { 
            color: copied ? 'white' : (theme.isDark ? '#cccccc' : '#586069') 
          }]}>
            {copied ? 'Copied!' : 'Copy'}
          </Text>
        </TouchableOpacity>
      </View>
      <ScrollView 
        horizontal 
        style={styles.codeBlockContent}
        showsHorizontalScrollIndicator={false}
      >
        <Text style={[styles.codeText, { 
          color: theme.isDark ? '#d4d4d4' : '#24292e',
          fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace'
        }]}>
          {code}
        </Text>
      </ScrollView>
    </View>
  );
};
