/**
 * FloatingChecklist - Shows tour progress in a floating panel
 * Displays completed steps and current progress
 */

import React, { useState } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  Animated,
  Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTour } from './TourProvider';
import { tourSteps, MODULE_NAMES, TOUR_MODULES } from './TourSteps';

const FloatingChecklist = ({ theme, visible = true }) => {
  const [isExpanded, setIsExpanded] = useState(true);
  const [isMinimized, setIsMinimized] = useState(false);
  
  const {
    isRunning,
    completedSteps,
    currentStepIndex,
    getProgress,
    skipTour,
    restartTour,
  } = useTour();

  // Don't render if tour is not running or visibility is false
  if (!isRunning || !visible) {
    return null;
  }

  const progress = getProgress();
  const currentStep = tourSteps[currentStepIndex];
  const currentModule = currentStep?.module;

  // Group steps by module for display. Modules whose steps were filtered out
  // (e.g. Vault Basics while the personal Data Store is off) are dropped —
  // an empty, titled group reads as a broken checklist.
  const moduleGroups = Object.values(TOUR_MODULES)
    .map(moduleName => ({
      name: moduleName,
      displayName: MODULE_NAMES[moduleName],
      steps: tourSteps.filter(s => s.module === moduleName),
    }))
    .filter(group => group.steps.length > 0);

  if (isMinimized) {
    return (
      <TouchableOpacity
        style={[styles.minimizedButton, { backgroundColor: theme?.sendButton || '#3b82f6' }]}
        onPress={() => setIsMinimized(false)}
      >
        <Ionicons name="school" size={20} color="#ffffff" />
        <Text style={styles.minimizedText}>{progress.completedCount}/{progress.totalSteps}</Text>
      </TouchableOpacity>
    );
  }

  return (
    <View style={[styles.container, { backgroundColor: theme?.isDark ? '#1a1f2e' : '#ffffff' }]}>
      {/* Header */}
      <View style={[styles.header, { borderBottomColor: theme?.borderColor || '#333' }]}>
        <View style={styles.headerLeft}>
          <Ionicons name="school" size={20} color={theme?.sendButton || '#3b82f6'} />
          <Text style={[styles.headerTitle, { color: theme?.text || '#ffffff' }]}>
            Getting Started Tour
          </Text>
        </View>
        <View style={styles.headerActions}>
          <TouchableOpacity onPress={() => setIsMinimized(true)} style={styles.headerButton}>
            <Ionicons name="remove" size={18} color={theme?.text || '#888'} />
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setIsExpanded(!isExpanded)} style={styles.headerButton}>
            <Ionicons name={isExpanded ? 'chevron-up' : 'chevron-down'} size={18} color={theme?.text || '#888'} />
          </TouchableOpacity>
        </View>
      </View>

      {isExpanded && (
        <>
          {/* Progress Bar */}
          <View style={styles.progressSection}>
            <View style={[styles.progressBar, { backgroundColor: theme?.borderColor || '#333' }]}>
              <View 
                style={[
                  styles.progressFill, 
                  { 
                    width: `${progress.percentage}%`,
                    backgroundColor: theme?.sendButton || '#3b82f6' 
                  }
                ]} 
              />
            </View>
            <Text style={[styles.progressText, { color: theme?.isDark ? '#888' : '#666' }]}>
              {progress.completedCount} of {progress.totalSteps} completed
            </Text>
          </View>

          {/* Steps List */}
          <ScrollView style={styles.stepsList} showsVerticalScrollIndicator={false}>
            {moduleGroups.map((module, moduleIndex) => (
              <View key={module.name} style={styles.moduleSection}>
                {/* Module Header */}
                <View style={styles.moduleHeader}>
                  <Text style={[styles.moduleName, { color: theme?.isDark ? '#aaa' : '#666' }]}>
                    {module.displayName}
                  </Text>
                </View>

                {/* Module Steps */}
                {module.steps.slice(0, 3).map((step, stepIndex) => {
                  const isCompleted = completedSteps.includes(step.id);
                  const isCurrent = step.order - 1 === currentStepIndex;
                  
                  return (
                    <View 
                      key={step.id} 
                      style={[
                        styles.stepItem,
                        isCurrent && styles.currentStepItem,
                        isCurrent && { borderLeftColor: theme?.sendButton || '#3b82f6' }
                      ]}
                    >
                      <View style={[
                        styles.stepIcon,
                        isCompleted && { backgroundColor: '#10b981' },
                        isCurrent && !isCompleted && { backgroundColor: theme?.sendButton || '#3b82f6' },
                      ]}>
                        {isCompleted ? (
                          <Ionicons name="checkmark" size={12} color="#ffffff" />
                        ) : isCurrent ? (
                          <Ionicons name="arrow-forward" size={10} color="#ffffff" />
                        ) : (
                          <Text style={styles.stepNumber}>{step.order}</Text>
                        )}
                      </View>
                      <Text 
                        style={[
                          styles.stepTitle,
                          { color: theme?.text || '#ffffff' },
                          isCompleted && styles.completedStepTitle,
                          isCurrent && { fontWeight: '600' }
                        ]}
                        numberOfLines={1}
                      >
                        {step.title}
                      </Text>
                    </View>
                  );
                })}

                {module.steps.length > 3 && (
                  <Text style={[styles.moreSteps, { color: theme?.isDark ? '#666' : '#999' }]}>
                    +{module.steps.length - 3} more steps
                  </Text>
                )}
              </View>
            ))}
          </ScrollView>

          {/* Footer Actions */}
          <View style={[styles.footer, { borderTopColor: theme?.borderColor || '#333' }]}>
            <TouchableOpacity onPress={skipTour} style={styles.skipButton}>
              <Text style={[styles.skipButtonText, { color: theme?.isDark ? '#888' : '#666' }]}>
                Skip Tour
              </Text>
            </TouchableOpacity>
          </View>
        </>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    bottom: 20,
    right: 20,
    width: 280,
    maxHeight: 400,
    borderRadius: 12,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 12,
    elevation: 10,
    overflow: 'hidden',
    zIndex: 9999,
    ...(Platform.OS === 'web' && {
      boxShadow: '0 4px 20px rgba(0, 0, 0, 0.3)',
    }),
  },
  minimizedButton: {
    position: 'absolute',
    bottom: 20,
    right: 20,
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 10,
    paddingHorizontal: 16,
    borderRadius: 24,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3,
    shadowRadius: 6,
    elevation: 6,
    zIndex: 9999,
  },
  minimizedText: {
    color: '#ffffff',
    fontSize: 13,
    fontWeight: '600',
    marginLeft: 8,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 12,
    borderBottomWidth: 1,
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
  headerActions: {
    flexDirection: 'row',
    gap: 4,
  },
  headerButton: {
    padding: 4,
  },
  progressSection: {
    padding: 12,
  },
  progressBar: {
    height: 4,
    borderRadius: 2,
    overflow: 'hidden',
    marginBottom: 6,
  },
  progressFill: {
    height: '100%',
    borderRadius: 2,
  },
  progressText: {
    fontSize: 11,
    textAlign: 'center',
  },
  stepsList: {
    maxHeight: 220,
    paddingHorizontal: 12,
  },
  moduleSection: {
    marginBottom: 12,
  },
  moduleHeader: {
    marginBottom: 6,
  },
  moduleName: {
    fontSize: 10,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  stepItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 6,
    paddingHorizontal: 8,
    borderRadius: 6,
    marginBottom: 2,
  },
  currentStepItem: {
    backgroundColor: 'rgba(59, 130, 246, 0.1)',
    borderLeftWidth: 3,
    marginLeft: -3,
  },
  stepIcon: {
    width: 20,
    height: 20,
    borderRadius: 10,
    backgroundColor: 'rgba(255, 255, 255, 0.1)',
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 8,
  },
  stepNumber: {
    fontSize: 10,
    color: '#888',
    fontWeight: '500',
  },
  stepTitle: {
    fontSize: 12,
    flex: 1,
  },
  completedStepTitle: {
    textDecorationLine: 'line-through',
    opacity: 0.6,
  },
  moreSteps: {
    fontSize: 10,
    marginLeft: 28,
    marginTop: 2,
  },
  footer: {
    padding: 12,
    borderTopWidth: 1,
    alignItems: 'center',
  },
  skipButton: {
    paddingVertical: 6,
    paddingHorizontal: 12,
  },
  skipButtonText: {
    fontSize: 12,
    fontWeight: '500',
  },
});

export default FloatingChecklist;
