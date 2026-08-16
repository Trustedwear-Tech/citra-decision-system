import React, { useState, useRef, useEffect } from 'react';
import { View, Text, TouchableOpacity, Animated, PanResponder, Dimensions, Platform, StyleSheet, ScrollView } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

const { width: SCREEN_WIDTH, height: SCREEN_HEIGHT } = Dimensions.get('window');

// Profession-specific configurations
const PROFESSION_CONFIGS = {
  general: {
    label: 'General Professional',
    icon: 'briefcase',
    questions: [
      {
        id: 2,
        title: 'Document Analysis',
        question: 'Analyze and summarize the key points from my uploaded documents, highlighting important findings and action items',
        type: 'analysis'
      },
      {
        id: 3,
        title: 'Draft a Professional Report',
        question: 'Help me draft a comprehensive report summarizing my research findings, including an executive summary, methodology, results, and recommendations',
        type: 'draft'
      },
      {
        id: 4,
        title: 'Strategic Planning',
        question: 'What strategic approach should I consider for achieving my project goals? Provide a structured analysis with potential challenges and solutions',
        type: 'advice'
      }
    ]
  }
};

const ProfessionSuggestionsBox = ({
  profession = 'general',
  onQuestionSelect,
  isDarkMode = false,
  isVisible: isVisibleProp,
  isMinimized: isMinimizedProp,
  onClose,
  onMinimizeToggle,
}) => {
  const [isVisible, setIsVisible] = useState(isVisibleProp ?? true);
  const [isMinimized, setIsMinimized] = useState(isMinimizedProp ?? false);
  const [expandedQuestion, setExpandedQuestion] = useState(null);

  // Always use general configuration, ignoring the specific profession prop
  const getProfessionKey = () => 'general';

  const professionKey = getProfessionKey(profession);

  // Start with zero offset; base position anchored via top/right in containerStyle
  const pan = useRef(new Animated.ValueXY({ x: 0, y: 0 })).current;
  const fadeAnim = useRef(new Animated.Value(1)).current;
  const scaleAnim = useRef(new Animated.Value(1)).current;

  const config = PROFESSION_CONFIGS[professionKey] || PROFESSION_CONFIGS.general;

  // Pan responder for dragging
  const panResponder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderGrant: () => {
        pan.setOffset({
          x: pan.x._value,
          y: pan.y._value
        });
        pan.setValue({ x: 0, y: 0 });

        Animated.spring(scaleAnim, {
          toValue: 1.05,
          useNativeDriver: false
        }).start();
      },
      onPanResponderMove: Animated.event(
        [null, { dx: pan.x, dy: pan.y }],
        { useNativeDriver: false }
      ),
      onPanResponderRelease: (e, gesture) => {
        pan.flattenOffset();

        Animated.spring(scaleAnim, {
          toValue: 1,
          useNativeDriver: false
        }).start();

        // Constrain to screen bounds
        const newX = Math.max(10, Math.min(pan.x._value, SCREEN_WIDTH - 370));
        const newY = Math.max(10, Math.min(pan.y._value, SCREEN_HEIGHT - 400));

        Animated.spring(pan, {
          toValue: { x: newX, y: newY },
          useNativeDriver: false
        }).start();
      }
    })
  ).current;

  useEffect(() => {
    if (typeof isVisibleProp === 'boolean') {
      setIsVisible(isVisibleProp);
    }
  }, [isVisibleProp]);

  useEffect(() => {
    if (typeof isMinimizedProp === 'boolean') {
      setIsMinimized(isMinimizedProp);
    }
  }, [isMinimizedProp]);

  const handleClose = () => {
    Animated.parallel([
      Animated.timing(fadeAnim, {
        toValue: 0,
        duration: 200,
        useNativeDriver: false
      }),
      Animated.timing(scaleAnim, {
        toValue: 0.8,
        duration: 200,
        useNativeDriver: false
      })
    ]).start(() => {
      setIsVisible(false);
      if (onClose) {
        onClose();
      }
    });
  };

  const handleMinimize = () => {
    // Treat minimize as a permanent close for the session
    if (onMinimizeToggle) {
      onMinimizeToggle(true);
    }
    handleClose();
  };

  const handleQuestionClick = (question) => {
    if (onQuestionSelect) {
      onQuestionSelect(question.question);
      handleClose();
    }
  };

  const getQuestionIcon = (type) => {
    switch (type) {
      case 'draft': return 'document-text';
      case 'advice': return 'bulb';
      default: return 'help-circle';
    }
  };

  if (!isVisible) return null;

  const containerStyle = {
    position: Platform.OS === 'web' ? 'fixed' : 'absolute',
    top: 90,
    right: 380, // push left of the right-side folder panel
    width: 340,
    maxHeight: isMinimized ? 60 : 520,
    backgroundColor: isDarkMode ? '#1a1a2e' : '#ffffff',
    borderRadius: 16,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 20,
    overflow: 'hidden',
    zIndex: 999999,
    borderWidth: 1,
    borderColor: isDarkMode ? '#2d2d44' : '#e0e0e0',
    pointerEvents: 'box-none',
  };

  return (
    <Animated.View
      {...panResponder.panHandlers}
      style={[
        containerStyle,
        {
          transform: [
            { translateX: pan.x },
            { translateY: pan.y },
            { scale: scaleAnim }
          ],
          opacity: fadeAnim
        }
      ]}
    >
      {/* Header */}
      <View style={[
        localStyles.header,
        { backgroundColor: isDarkMode ? '#2d2d44' : '#f5f5f5' }
      ]}>
        <View style={localStyles.headerLeft}>
          <Ionicons
            name={config.icon}
            size={20}
            color={isDarkMode ? '#64b5f6' : '#2196F3'}
          />
          <Text style={[
            localStyles.headerTitle,
            { color: isDarkMode ? '#ffffff' : '#333333' }
          ]}>
            Suggested Questions
          </Text>
        </View>
        <View style={localStyles.headerRight}>
          <TouchableOpacity onPress={handleMinimize} style={localStyles.iconButton}>
            <Ionicons
              name={isMinimized ? 'chevron-down' : 'chevron-up'}
              size={20}
              color={isDarkMode ? '#bbbbbb' : '#666666'}
            />
          </TouchableOpacity>
          <TouchableOpacity onPress={handleClose} style={localStyles.iconButton}>
            <Ionicons
              name="close"
              size={20}
              color={isDarkMode ? '#bbbbbb' : '#666666'}
            />
          </TouchableOpacity>
        </View>
      </View>

      {/* Content */}
      {!isMinimized && (
        <View style={localStyles.content}>
          <Text style={[
            localStyles.professionLabel,
            { color: isDarkMode ? '#64b5f6' : '#2196F3' }
          ]}>
            {config.label}
          </Text>

          <View style={localStyles.questionsContainerWrapper}>
            <ScrollView
              style={localStyles.scrollArea}
              contentContainerStyle={{ paddingBottom: 8 }}
              showsVerticalScrollIndicator={true}
            >
              {config.questions.map((q, index) => (
                <TouchableOpacity
                  key={q.id}
                  style={[
                    localStyles.questionCard,
                    {
                      backgroundColor: isDarkMode ? '#252538' : '#f9f9f9',
                      borderColor: isDarkMode ? '#3d3d5c' : '#e0e0e0'
                    }
                  ]}
                  onPress={() => handleQuestionClick(q)}
                  activeOpacity={0.7}
                >
                  <View style={localStyles.questionHeader}>
                    <Ionicons
                      name={getQuestionIcon(q.type)}
                      size={18}
                      color={isDarkMode ? '#64b5f6' : '#2196F3'}
                    />
                    <Text style={[
                      localStyles.questionTitle,
                      { color: isDarkMode ? '#ffffff' : '#333333' }
                    ]}>
                      {q.title}
                    </Text>
                  </View>
                  <Text
                    style={[
                      localStyles.questionText,
                      { color: isDarkMode ? '#bbbbbb' : '#666666' }
                    ]}
                    numberOfLines={expandedQuestion === q.id ? undefined : 2}
                  >
                    {q.question}
                  </Text>
                  {q.question.length > 100 && (
                    <TouchableOpacity
                      onPress={(e) => {
                        e.stopPropagation();
                        setExpandedQuestion(expandedQuestion === q.id ? null : q.id);
                      }}
                    >
                      <Text style={[
                        localStyles.expandText,
                        { color: isDarkMode ? '#64b5f6' : '#2196F3' }
                      ]}>
                        {expandedQuestion === q.id ? 'Show less' : 'Show more'}
                      </Text>
                    </TouchableOpacity>
                  )}
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </View>
      )}

      {/* Drag Indicator */}
      <View style={[
        localStyles.dragIndicator,
        { backgroundColor: isDarkMode ? '#3d3d5c' : '#e0e0e0' }
      ]} />
    </Animated.View>
  );
};

const localStyles = StyleSheet.create({
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 12,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(0,0,0,0.05)',
    cursor: Platform.OS === 'web' ? 'move' : 'default',
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  headerTitle: {
    fontSize: 14,
    fontWeight: '600',
  },
  headerRight: {
    flexDirection: 'row',
    gap: 8,
  },
  iconButton: {
    padding: 4,
  },
  content: {
    padding: 12,
    maxHeight: 440,
  },
  professionLabel: {
    fontSize: 12,
    fontWeight: '600',
    marginBottom: 12,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  questionsContainer: {
    gap: 8,
  },
  questionsContainerWrapper: {
    maxHeight: 360,
  },
  scrollArea: {
    maxHeight: 360,
  },
  questionCard: {
    padding: 12,
    borderRadius: 8,
    borderWidth: 1,
    marginBottom: 4,
  },
  questionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginBottom: 6,
  },
  questionTitle: {
    fontSize: 13,
    fontWeight: '600',
    flex: 1,
  },
  questionText: {
    fontSize: 12,
    lineHeight: 18,
  },
  expandText: {
    fontSize: 11,
    marginTop: 4,
    fontWeight: '500',
  },
  dragIndicator: {
    position: 'absolute',
    top: 6,
    left: '50%',
    width: 40,
    height: 4,
    borderRadius: 2,
    marginLeft: -20,
  },
});

export default ProfessionSuggestionsBox;
