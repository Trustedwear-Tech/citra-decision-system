// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * TourProgressTracker - Floating progress panel for the product tour
 * Shows current progress, allows pause/resume, and manual completion
 */

import React, { useState, useEffect, useRef, useContext } from 'react';
import { View, Text, TouchableOpacity, Animated, Platform, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTour } from './TourProvider';
import { MODULE_NAMES, TOUR_MODULES } from './TourSteps';

// Default dark theme fallback
const defaultTheme = {
  isDark: true,
  text: '#ffffff',
  background: '#1a1f2e',
  primary: '#3b82f6',
  surface: '#252b3d',
  border: 'rgba(255, 255, 255, 0.1)',
};

// Try to import theme context - with fallback
let ThemeContext = null;
try {
  ThemeContext = require('../../hooks/useModernTheme').ThemeContext;
} catch (e) {
  console.log('[TourProgressTracker] Theme context not available, using defaults');
}

const TourProgressTracker = () => {
  // Safely try to use theme context
  let theme = defaultTheme;
  try {
    const themeContext = ThemeContext ? useContext(ThemeContext) : null;
    if (themeContext?.theme) {
      theme = themeContext.theme;
    }
  } catch (e) {
    // Use default theme
  }
  const {
    isRunning,
    tourCompleted,
    completedSteps,
    completedModules,
    currentStepIndex,
    steps,
    getProgress,
    stopTour,
    startTour,
    completeTour,
  } = useTour();

  const [isMinimized, setIsMinimized] = useState(false);
  const [isVisible, setIsVisible] = useState(false);
  const slideAnim = useRef(new Animated.Value(300)).current;
  const fadeAnim = useRef(new Animated.Value(0)).current;

  // Show tracker when tour is running or has progress
  useEffect(() => {
    const hasProgress = completedSteps.length > 0;
    const shouldShow = isRunning || (hasProgress && !tourCompleted);
    
    if (shouldShow && !isVisible) {
      setIsVisible(true);
      Animated.parallel([
        Animated.spring(slideAnim, {
          toValue: 0,
          tension: 50,
          friction: 10,
          useNativeDriver: Platform.OS !== 'web',
        }),
        Animated.timing(fadeAnim, {
          toValue: 1,
          duration: 300,
          useNativeDriver: Platform.OS !== 'web',
        }),
      ]).start();
    } else if (!shouldShow && isVisible) {
      Animated.parallel([
        Animated.timing(slideAnim, {
          toValue: 300,
          duration: 200,
          useNativeDriver: Platform.OS !== 'web',
        }),
        Animated.timing(fadeAnim, {
          toValue: 0,
          duration: 200,
          useNativeDriver: Platform.OS !== 'web',
        }),
      ]).start(() => setIsVisible(false));
    }
  }, [isRunning, completedSteps, tourCompleted, isVisible, slideAnim, fadeAnim]);

  if (!isVisible && !isRunning) {
    return null;
  }

  const progress = getProgress();
  const currentStep = steps[currentStepIndex];
  
  // Get module progress
  // Modules with no steps in this build (e.g. Vault Basics while the personal
  // Data Store is off) are dropped — a 0/0 row would otherwise read "complete".
  const modules = Object.values(TOUR_MODULES);
  const moduleProgress = modules
    .map(moduleId => {
      const moduleSteps = steps.filter(s => s.module === moduleId);
      const completed = moduleSteps.filter(s => completedSteps.includes(s.id)).length;
      return {
        id: moduleId,
        name: MODULE_NAMES[moduleId] || moduleId,
        total: moduleSteps.length,
        completed,
        isComplete: completed === moduleSteps.length,
      };
    })
    .filter(m => m.total > 0);

  const handleClose = () => {
    completeTour();
  };

  const handleMarkComplete = async () => {
    await completeTour();
  };

  const handleResume = () => {
    startTour(currentStepIndex);
  };

  const handleMinimize = () => {
    setIsMinimized(!isMinimized);
  };

  const themedStyles = createThemedStyles(theme);

  if (isMinimized) {
    return (
      <Animated.View
        style={[
          themedStyles.minimizedContainer,
          {
            transform: [{ translateX: slideAnim }],
            opacity: fadeAnim,
          },
        ]}
      >
        <TouchableOpacity
          style={themedStyles.minimizedButton}
          onPress={handleMinimize}
        >
          <Ionicons name="school" size={20} color="#3b82f6" />
          <View style={themedStyles.minimizedBadge}>
            <Text style={themedStyles.minimizedBadgeText}>
              {progress.completedCount}/{progress.totalSteps}
            </Text>
          </View>
        </TouchableOpacity>
      </Animated.View>
    );
  }

  return (
    <Animated.View
      style={[
        themedStyles.container,
        {
          transform: [{ translateX: slideAnim }],
          opacity: fadeAnim,
        },
      ]}
    >
      {/* Header */}
      <View style={themedStyles.header}>
        <View style={themedStyles.headerLeft}>
          <Ionicons name="school" size={18} color="#3b82f6" />
          <Text style={themedStyles.headerTitle}>Product Tour</Text>
        </View>
        <View style={themedStyles.headerRight}>
          <TouchableOpacity onPress={handleMinimize} style={themedStyles.headerButton}>
            <Ionicons name="remove" size={18} color={theme?.text || '#888'} />
          </TouchableOpacity>
          <TouchableOpacity onPress={handleClose} style={themedStyles.headerButton}>
            <Ionicons name="close" size={18} color={theme?.text || '#888'} />
          </TouchableOpacity>
        </View>
      </View>

      {/* Progress Bar */}
      <View style={themedStyles.progressSection}>
        <View style={themedStyles.progressBarContainer}>
          <View 
            style={[
              themedStyles.progressBar, 
              { width: `${progress.percentage}%` }
            ]} 
          />
        </View>
        <Text style={themedStyles.progressText}>
          Step {Math.min(currentStepIndex + 1, progress.totalSteps)} of {progress.totalSteps}
        </Text>
      </View>

      {/* Current Step */}
      {currentStep && (
        <View style={themedStyles.currentStepSection}>
          <Text style={themedStyles.currentStepLabel}>{isRunning ? 'Current Step:' : 'Paused at:'}</Text>
          <Text style={themedStyles.currentStepTitle}>{currentStep.title}</Text>
        </View>
      )}

      {/* Action Buttons */}
      <View style={themedStyles.actionsSection}>
        {!isRunning && completedSteps.length > 0 && (
          <TouchableOpacity
            style={themedStyles.resumeButton}
            onPress={handleResume}
          >
            <Ionicons name="play" size={14} color="#ffffff" />
            <Text style={themedStyles.resumeButtonText}>Resume Tour</Text>
          </TouchableOpacity>
        )}

        <TouchableOpacity
          style={themedStyles.endTourButton}
          onPress={handleMarkComplete}
        >
          <Ionicons name="stop" size={14} color="#ef4444" />
          <Text style={themedStyles.endTourButtonText}>End Tour</Text>
        </TouchableOpacity>
      </View>
    </Animated.View>
  );
};

const createThemedStyles = (theme) => StyleSheet.create({
  container: {
    position: 'absolute',
    bottom: 20,
    right: 20,
    width: 280,
    backgroundColor: theme?.isDark ? '#1a1f2e' : '#ffffff',
    borderRadius: 12,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 12,
    elevation: 10,
    borderWidth: 1,
    borderColor: theme?.isDark ? 'rgba(59, 130, 246, 0.3)' : 'rgba(0, 0, 0, 0.1)',
    overflow: 'hidden',
    zIndex: 9999,
  },
  minimizedContainer: {
    position: 'absolute',
    bottom: 20,
    right: 20,
    zIndex: 9999,
  },
  minimizedButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: theme?.isDark ? '#1a1f2e' : '#ffffff',
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.2,
    shadowRadius: 8,
    elevation: 6,
    borderWidth: 1,
    borderColor: 'rgba(59, 130, 246, 0.3)',
    gap: 8,
  },
  minimizedBadge: {
    backgroundColor: '#3b82f6',
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 10,
  },
  minimizedBadgeText: {
    color: '#ffffff',
    fontSize: 11,
    fontWeight: '600',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 12,
    borderBottomWidth: 1,
    borderBottomColor: theme?.isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
    backgroundColor: theme?.isDark ? 'rgba(59, 130, 246, 0.1)' : 'rgba(59, 130, 246, 0.05)',
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  headerTitle: {
    fontSize: 14,
    fontWeight: '600',
    color: theme?.text || '#333',
  },
  headerRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  headerButton: {
    padding: 4,
    borderRadius: 4,
  },
  progressSection: {
    padding: 12,
    borderBottomWidth: 1,
    borderBottomColor: theme?.isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.05)',
  },
  progressBarContainer: {
    height: 6,
    backgroundColor: theme?.isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
    borderRadius: 3,
    overflow: 'hidden',
    marginBottom: 8,
  },
  progressBar: {
    height: '100%',
    backgroundColor: '#3b82f6',
    borderRadius: 3,
  },
  progressText: {
    fontSize: 12,
    color: theme?.isDark ? '#888' : '#666',
  },
  currentStepSection: {
    padding: 12,
    backgroundColor: theme?.isDark ? 'rgba(59, 130, 246, 0.05)' : 'rgba(59, 130, 246, 0.03)',
    borderBottomWidth: 1,
    borderBottomColor: theme?.isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.05)',
  },
  currentStepLabel: {
    fontSize: 10,
    color: '#3b82f6',
    fontWeight: '600',
    textTransform: 'uppercase',
    marginBottom: 4,
  },
  currentStepTitle: {
    fontSize: 13,
    fontWeight: '500',
    color: theme?.text || '#333',
  },
  modulesSection: {
    padding: 12,
  },
  modulesLabel: {
    fontSize: 10,
    color: theme?.isDark ? '#888' : '#666',
    fontWeight: '600',
    textTransform: 'uppercase',
    marginBottom: 8,
  },
  moduleItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 6,
  },
  moduleLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  moduleName: {
    fontSize: 12,
    color: theme?.text || '#333',
  },
  moduleNameComplete: {
    color: '#10b981',
  },
  moduleCount: {
    fontSize: 11,
    color: theme?.isDark ? '#666' : '#999',
  },
  actionsSection: {
    padding: 12,
    borderTopWidth: 1,
    borderTopColor: theme?.isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.05)',
    flexDirection: 'row',
    gap: 8,
  },
  resumeButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#3b82f6',
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 6,
    gap: 6,
  },
  resumeButtonText: {
    color: '#ffffff',
    fontSize: 12,
    fontWeight: '600',
  },
  endTourButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme?.isDark ? 'rgba(239, 68, 68, 0.1)' : 'rgba(239, 68, 68, 0.1)',
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: 'rgba(239, 68, 68, 0.3)',
    gap: 6,
  },
  endTourButtonText: {
    color: '#ef4444',
    fontSize: 12,
    fontWeight: '600',
  },
  completeButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme?.isDark ? 'rgba(16, 185, 129, 0.1)' : 'rgba(16, 185, 129, 0.1)',
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: 'rgba(16, 185, 129, 0.3)',
    gap: 6,
  },
  completeButtonText: {
    color: '#10b981',
    fontSize: 12,
    fontWeight: '600',
  },
});

export default TourProgressTracker;
