// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

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
