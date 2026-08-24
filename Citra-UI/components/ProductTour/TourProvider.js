// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * TourProvider - Context provider for the interactive product tour
 * Manages tour state, step progression, and persistence
 */

import React, { createContext, useContext, useState, useEffect, useCallback, Suspense, lazy } from 'react';
import { Platform } from 'react-native';
import Joyride, { STATUS, EVENTS, ACTIONS } from 'react-joyride';
import { tourSteps, TOUR_MODULES, MODULE_NAMES } from './TourSteps';
import {
  saveTourProgress,
  loadTourProgress,
  resetTourProgress,
  defaultTourProgress
} from './tourPersistence';

// Lazy load TourProgressTracker to avoid require cycle
const TourProgressTracker = lazy(() => import('./TourProgressTracker'));

// Create context
const TourContext = createContext(null);

// Custom hook to use tour context
export const useTour = () => {
  const context = useContext(TourContext);
  if (!context) {
    throw new Error('useTour must be used within a TourProvider');
  }
  return context;
};

// Custom tooltip component for better styling
const CustomTooltip = ({
  continuous,
  index,
  step,
  backProps,
  closeProps,
  primaryProps,
  tooltipProps,
  isLastStep,
  size,
}) => {
  const progress = `Step ${index + 1} of ${size}`;

  return (
    <div {...tooltipProps} style={styles.tooltip}>
      {/* Header */}
      <div style={styles.tooltipHeader}>
        <span style={styles.tooltipProgress}>{progress}</span>
      </div>

      {/* Content */}
      <div style={styles.tooltipContent}>
        <h3 style={styles.tooltipTitle}>{step.title}</h3>
        <p style={styles.tooltipText}>{step.content}</p>
      </div>

      {/* Footer - Only Next/Finish buttons, no Back or Skip */}
      <div style={styles.tooltipFooter}>
        <button {...primaryProps} style={styles.primaryButton}>
          {isLastStep ? '✓ Finish Tour' : 'Next →'}
        </button>
      </div>
    </div>
  );
};

// Tooltip styles
const styles = {
  tooltip: {
    backgroundColor: '#1a1f2e',
    borderRadius: '12px',
    padding: '0',
    maxWidth: '340px',
    boxShadow: '0 8px 32px rgba(0, 0, 0, 0.4)',
    border: '1px solid rgba(59, 130, 246, 0.3)',
    overflow: 'hidden',
  },
  tooltipHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '12px 16px',
    borderBottom: '1px solid rgba(255, 255, 255, 0.1)',
    backgroundColor: 'rgba(59, 130, 246, 0.1)',
  },
  tooltipProgress: {
    fontSize: '12px',
    color: '#3b82f6',
    fontWeight: '600',
  },
  closeButton: {
    background: 'none',
    border: 'none',
    color: '#888',
    fontSize: '20px',
    cursor: 'pointer',
    padding: '0 4px',
    lineHeight: 1,
  },
  tooltipContent: {
    padding: '16px',
  },
  tooltipTitle: {
    margin: '0 0 8px 0',
    fontSize: '16px',
    fontWeight: '700',
    color: '#ffffff',
  },
  tooltipText: {
    margin: 0,
    fontSize: '14px',
    color: '#b0b8c8',
    lineHeight: '1.5',
  },
  tooltipFooter: {
    display: 'flex',
    justifyContent: 'flex-end',
    gap: '8px',
    padding: '12px 16px',
    borderTop: '1px solid rgba(255, 255, 255, 0.1)',
    backgroundColor: 'rgba(0, 0, 0, 0.2)',
  },
  backButton: {
    padding: '8px 16px',
    fontSize: '13px',
    fontWeight: '500',
    color: '#888',
    backgroundColor: 'transparent',
    border: '1px solid rgba(255, 255, 255, 0.2)',
    borderRadius: '6px',
    cursor: 'pointer',
  },
  primaryButton: {
    padding: '8px 20px',
    fontSize: '13px',
    fontWeight: '600',
    color: '#ffffff',
    backgroundColor: '#3b82f6',
    border: 'none',
    borderRadius: '6px',
    cursor: 'pointer',
  },
};

// Tour Provider Component
export const TourProvider = ({ children }) => {
  const [tourState, setTourState] = useState({
    run: false,
    stepIndex: 0,
    steps: tourSteps,
    completedSteps: [],
    completedModules: [],
    tourCompleted: false,
  });

  // Load saved progress on mount (but DON'T auto-start the tour)
  // The only remaining trigger is the Tour button in the menu ribbon's Help
  // tab (components/ProductTour/TourButton.js). The old "after
  // FirstTimeUserTutorial completes" path is gone — that tutorial was removed.
  useEffect(() => {
    const loadProgress = async () => {
      const savedProgress = await loadTourProgress();
      // Only restore completed steps/modules, NOT the running state
      // This prevents tour from auto-starting on page load
      if (savedProgress) {
        setTourState(prev => ({
          ...prev,
          run: false, // Never auto-start on page load
          stepIndex: savedProgress.currentStepIndex || 0,
          completedSteps: savedProgress.completedSteps || [],
          completedModules: savedProgress.completedModules || [],
          tourCompleted: savedProgress.tourCompleted || false,
        }));
      }
    };
    loadProgress();
  }, []);

  // Save progress whenever state changes
  useEffect(() => {
    if (tourState.run || tourState.completedSteps.length > 0) {
      saveTourProgress({
        isActive: tourState.run,
        currentStepIndex: tourState.stepIndex,
        completedSteps: tourState.completedSteps,
        completedModules: tourState.completedModules,
        tourCompleted: tourState.tourCompleted,
      });
    }
  }, [tourState.run, tourState.stepIndex, tourState.completedSteps, tourState.completedModules, tourState.tourCompleted]);

  // Start tour
  const startTour = useCallback((fromStep = 0) => {
    console.log('🎯 [TOUR] Starting tour from step:', fromStep);
    setTourState(prev => ({
      ...prev,
      run: true,
      stepIndex: fromStep,
    }));
  }, []);

  // Listen for custom event to start tour from HowToUseModal/Help menu
  useEffect(() => {
    if (Platform.OS !== 'web') return;

    const handleStartTour = () => {
      console.log('🎯 [TOUR] Received startProductTour event from Help menu');
      startTour(0);
    };

    window.addEventListener('startProductTour', handleStartTour);
    return () => window.removeEventListener('startProductTour', handleStartTour);
  }, [startTour]);

  // Stop/pause tour
  const stopTour = useCallback(() => {
    console.log('⏸️ [TOUR] Tour stopped');
    setTourState(prev => ({
      ...prev,
      run: false,
    }));
  }, []);

  // Mark tour as explicitly completed by user
  const completeTour = useCallback(async () => {
    console.log('✅ [TOUR] Tour explicitly marked as complete by user');
    // Mark everything complete and hide the tour
    const allStepIds = tourSteps.map(s => s.id);
    const allModules = [...new Set(tourSteps.map(s => s.module))];

    setTourState(prev => ({
      ...prev,
      run: false,
      tourCompleted: true,
      completedSteps: allStepIds,
      completedModules: allModules,
      stepIndex: tourSteps.length - 1,
    }));

    await saveTourProgress({
      isActive: false,
      currentStepIndex: tourSteps.length - 1,
      completedSteps: allStepIds,
      completedModules: allModules,
      tourCompleted: true,
      tourDismissed: false,
    });
  }, []);

  // Safety net: auto-complete when all steps are done (even if Joyride status FINISHED isn't emitted)
  useEffect(() => {
    if (!tourState.tourCompleted && tourState.completedSteps.length >= tourSteps.length) {
      completeTour();
    }
  }, [tourState.completedSteps, tourState.tourCompleted, completeTour]);

  // Skip tour entirely (also marks as complete to hide progress tracker)
  const skipTour = useCallback(async () => {
    console.log('⏭️ [TOUR] Tour skipped/completed');
    setTourState(prev => ({
      ...prev,
      run: false,
      tourCompleted: true,
    }));
    await saveTourProgress({
      ...defaultTourProgress,
      tourCompleted: true,
      tourDismissed: true,
    });
  }, []);

  // Reset and restart tour
  const restartTour = useCallback(async () => {
    console.log('🔄 [TOUR] Restarting tour');
    await resetTourProgress();
    setTourState(prev => ({
      ...prev,
      run: true,
      stepIndex: 0,
      completedSteps: [],
      completedModules: [],
      tourCompleted: false,
    }));
  }, []);

  // Go to specific step
  const goToStep = useCallback((stepIndex) => {
    console.log('📍 [TOUR] Going to step:', stepIndex);
    setTourState(prev => ({
      ...prev,
      stepIndex,
    }));
  }, []);

  // Handle Joyride callback
  const handleJoyrideCallback = useCallback((data) => {
    const { action, index, status, type } = data;

    console.log('🎯 [TOUR] Callback:', { action, index, status, type });

    // Handle step before - run beforeAction if defined with delay
    if (type === EVENTS.STEP_BEFORE) {
      const step = tourSteps[index];
      if (step && step.beforeAction) {
        console.log('🔧 [TOUR] Running beforeAction for step:', step.id);

        // Pause the tour while we prepare the UI
        setTourState(prev => ({ ...prev, run: false }));

        // Execute the beforeAction (e.g., click a tab to expand it)
        try {
          step.beforeAction();
        } catch (error) {
          console.log('⚠️ [TOUR] beforeAction error:', error);
        }

        // Wait for UI to render, then resume tour
        setTimeout(() => {
          console.log('▶️ [TOUR] Resuming after beforeAction delay');
          setTourState(prev => ({ ...prev, run: true }));
        }, 1500); // 1500ms delay for UI to expand and render

        return;
      }
    }

    // Handle target not found - pause tour but keep progress
    if (type === EVENTS.TARGET_NOT_FOUND) {
      console.log('⚠️ [TOUR] Target not found for step:', index, '- pausing tour');
      // Don't advance, just pause so user can interact with UI
      // Tour will resume from this step when user clicks Resume
      setTourState(prev => ({
        ...prev,
        run: false,
      }));
      return;
    }

    // Handle step completion
    if (type === EVENTS.STEP_AFTER) {
      const completedStep = tourSteps[index];

      setTourState(prev => {
        const newCompletedSteps = prev.completedSteps.includes(completedStep.id)
          ? prev.completedSteps
          : [...prev.completedSteps, completedStep.id];

        // Check if module is complete
        const moduleSteps = tourSteps.filter(s => s.module === completedStep.module);
        const moduleCompleted = moduleSteps.every(s => newCompletedSteps.includes(s.id));
        const newCompletedModules = moduleCompleted && !prev.completedModules.includes(completedStep.module)
          ? [...prev.completedModules, completedStep.module]
          : prev.completedModules;

        // Calculate next step index, capping at the last valid index
        const nextIndex = action === ACTIONS.PREV ? index - 1 : index + 1;
        const cappedIndex = Math.min(Math.max(0, nextIndex), tourSteps.length - 1);

        return {
          ...prev,
          stepIndex: cappedIndex,
          completedSteps: newCompletedSteps,
          completedModules: newCompletedModules,
        };
      });
    }

    // Handle tour finish - AUTO-COMPLETE when user finishes last step
    if ([STATUS.FINISHED, STATUS.SKIPPED].includes(status)) {
      console.log('✅ [TOUR] Tour finished - auto-completing and closing UI');

      // Mark tour as completed
      setTourState(prev => ({
        ...prev,
        run: false,
        tourCompleted: true, // Auto-complete when finished
      }));

      // Save completed state
      saveTourProgress({
        isActive: false,
        currentStepIndex: tourSteps.length - 1,
        completedSteps: tourSteps.map(s => s.id), // Mark all steps complete
        completedModules: [...new Set(tourSteps.map(s => s.module))], // Mark all modules complete
        tourCompleted: true,
        tourDismissed: false,
      });
    }

    // Handle close button
    if (action === ACTIONS.CLOSE) {
      stopTour();
    }
  }, [tourState.completedSteps, tourState.completedModules, stopTour]);

  // Get progress stats
  const getProgress = useCallback(() => {
    const totalSteps = tourSteps.length;
    const completedCount = tourState.completedSteps.length;
    const percentage = Math.round((completedCount / totalSteps) * 100);

    return {
      totalSteps,
      completedCount,
      percentage,
      currentStep: tourSteps[tourState.stepIndex],
      currentModule: tourSteps[tourState.stepIndex]?.module,
    };
  }, [tourState.stepIndex, tourState.completedSteps]);

  // Check if a step is completed
  const isStepCompleted = useCallback((stepId) => {
    return tourState.completedSteps.includes(stepId);
  }, [tourState.completedSteps]);

  // Context value
  const contextValue = {
    // State
    isRunning: tourState.run,
    currentStepIndex: tourState.stepIndex,
    completedSteps: tourState.completedSteps,
    completedModules: tourState.completedModules,
    tourCompleted: tourState.tourCompleted,
    steps: tourSteps,

    // Actions
    startTour,
    stopTour,
    skipTour,
    completeTour,
    restartTour,
    goToStep,

    // Helpers
    getProgress,
    isStepCompleted,

    // Constants
    TOUR_MODULES,
    MODULE_NAMES,
  };

  return (
    <TourContext.Provider value={contextValue}>
      {children}
      {/* Floating progress tracker - only shown on web */}
      {Platform.OS === 'web' && (
        <Suspense fallback={null}>
          <TourProgressTracker />
        </Suspense>
      )}
      <Joyride
        steps={tourState.steps}
        run={tourState.run}
        stepIndex={tourState.stepIndex}
        continuous
        showSkipButton={false}
        showProgress={false}
        disableOverlayClose
        disableCloseOnEsc={false}
        hideCloseButton={false}
        disableScrolling={false}
        scrollToFirstStep
        spotlightClicks={false}
        callback={handleJoyrideCallback}
        tooltipComponent={CustomTooltip}
        styles={{
          options: {
            zIndex: 10000,
            primaryColor: '#3b82f6',
            backgroundColor: '#1a1f2e',
            textColor: '#ffffff',
            arrowColor: '#1a1f2e',
            overlayColor: 'rgba(0, 0, 0, 0.75)',
          },
          spotlight: {
            borderRadius: '8px',
            boxShadow: '0 0 0 4px rgba(59, 130, 246, 0.5)',
          },
          overlay: {
            cursor: 'not-allowed',
          },
        }}
        floaterProps={{
          disableAnimation: true,
        }}
        locale={{
          back: 'Back',
          close: 'End Tour',
          last: 'Finish',
          next: 'Next',
          skip: 'Skip',
        }}
      />
    </TourContext.Provider>
  );
};

export default TourProvider;
