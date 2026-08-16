// Typing indicator and other small UI components
import React, { useRef, useEffect } from 'react';
import { View, Text, Animated, Platform } from 'react-native';
import { styles } from '../../styles';

// Typing Indicator Component
export const TypingIndicator = ({ theme }) => {
  const dot1 = useRef(new Animated.Value(0)).current;
  const dot2 = useRef(new Animated.Value(0)).current;
  const dot3 = useRef(new Animated.Value(0)).current;

  const shouldUseNativeDriver = Platform.OS !== 'web';

  useEffect(() => {
    const createDotAnimation = (dot, delay) => {
      return Animated.loop(
        Animated.sequence([
          Animated.delay(delay),
          Animated.timing(dot, {
            toValue: 1,
            duration: 400,
            useNativeDriver: shouldUseNativeDriver,
          }),
          Animated.timing(dot, {
            toValue: 0,
            duration: 400,
            useNativeDriver: shouldUseNativeDriver,
          }),
        ])
      );
    };

    const animation1 = createDotAnimation(dot1, 0);
    const animation2 = createDotAnimation(dot2, 200);
    const animation3 = createDotAnimation(dot3, 400);

    animation1.start();
    animation2.start();
    animation3.start();

    return () => {
      animation1.stop();
      animation2.stop();
      animation3.stop();
    };
  }, [dot1, dot2, dot3]);

  return (
    <View style={styles.typingContainer}>
      <Text style={[styles.typingText, { color: theme.botMessageText }]}>Thinking</Text>
      <View style={styles.dotsContainer}>
        {[dot1, dot2, dot3].map((dot, index) => (
          <Animated.View
            key={index}
            style={[
              styles.dot,
              { 
                backgroundColor: theme.botMessageText,
                opacity: dot,
              },
            ]}
          />
        ))}
      </View>
    </View>
  );
};
