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
