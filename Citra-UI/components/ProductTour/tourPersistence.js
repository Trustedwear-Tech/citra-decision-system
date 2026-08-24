// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Tour Persistence Utilities
 * Save and load tour progress from AsyncStorage
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

const TOUR_STORAGE_KEY = '@citra_ai_tour_progress';

/**
 * Default tour progress state
 */
export const defaultTourProgress = {
  isActive: false,
  currentStepIndex: 0,
  completedSteps: [],
  completedModules: [],
  tourDismissed: false,
  tourCompleted: false,
  lastTourDate: null,
  startedAt: null,
};

/**
 * Save tour progress to AsyncStorage
 */
export const saveTourProgress = async (progress) => {
  try {
    const data = {
      ...progress,
      lastUpdated: new Date().toISOString(),
    };
    await AsyncStorage.setItem(TOUR_STORAGE_KEY, JSON.stringify(data));
    console.log('📦 [TOUR] Progress saved:', data);
    return true;
  } catch (error) {
    console.error('❌ [TOUR] Failed to save progress:', error);
    return false;
  }
};

/**
 * Load tour progress from AsyncStorage
 */
export const loadTourProgress = async () => {
  try {
    const data = await AsyncStorage.getItem(TOUR_STORAGE_KEY);
    if (data) {
      const parsed = JSON.parse(data);
      console.log('📦 [TOUR] Progress loaded:', parsed);
      return { ...defaultTourProgress, ...parsed };
    }
    console.log('📦 [TOUR] No saved progress, using defaults');
    return defaultTourProgress;
  } catch (error) {
    console.error('❌ [TOUR] Failed to load progress:', error);
    return defaultTourProgress;
  }
};

/**
 * Reset tour progress (start fresh)
 */
export const resetTourProgress = async () => {
  try {
    await AsyncStorage.removeItem(TOUR_STORAGE_KEY);
    console.log('🔄 [TOUR] Progress reset');
    return defaultTourProgress;
  } catch (error) {
    console.error('❌ [TOUR] Failed to reset progress:', error);
    return defaultTourProgress;
  }
};

/**
 * Mark tour as dismissed (user skipped)
 */
export const dismissTour = async () => {
  try {
    const current = await loadTourProgress();
    const updated = {
      ...current,
      isActive: false,
      tourDismissed: true,
    };
    await saveTourProgress(updated);
    return updated;
  } catch (error) {
    console.error('❌ [TOUR] Failed to dismiss tour:', error);
    return null;
  }
};

/**
 * Check if user should see tour (first time or restart)
 */
export const shouldShowTour = async (isFirstTimeUser = false) => {
  try {
    const progress = await loadTourProgress();
    
    // First time user always sees tour (unless they dismissed it before)
    if (isFirstTimeUser && !progress.tourCompleted) {
      return true;
    }
    
    // If tour is currently active, show it
    if (progress.isActive) {
      return true;
    }
    
    return false;
  } catch (error) {
    console.error('❌ [TOUR] Failed to check tour status:', error);
    return isFirstTimeUser;
  }
};

export default {
  saveTourProgress,
  loadTourProgress,
  resetTourProgress,
  dismissTour,
  shouldShowTour,
  defaultTourProgress,
};
