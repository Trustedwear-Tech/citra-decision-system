// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * ProductTour - Main export file
 * Exports all tour components for easy importing
 */

export { TourProvider, useTour } from './TourProvider';
export { default as FloatingChecklist } from './FloatingChecklist';
export { default as TourButton } from './TourButton';
export { default as TourProgressTracker } from './TourProgressTracker';
export { 
  tourSteps, 
  TOUR_MODULES, 
  MODULE_NAMES,
  getStepsByModule,
  getStepById,
  getModuleInfo,
} from './TourSteps';
export {
  saveTourProgress,
  loadTourProgress,
  resetTourProgress,
  dismissTour,
  shouldShowTour,
  defaultTourProgress,
} from './tourPersistence';
