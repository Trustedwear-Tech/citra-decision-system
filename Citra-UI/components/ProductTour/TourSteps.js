// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Tour Steps Configuration
 * Simplified tour - only targets elements always visible on main UI
 * No tab opening - just shows visible elements and menu tabs
 * For React Expo Web only
 */

import { PERSONAL_VAULT_ENABLED } from '../../config/featureFlags';

// Tour module identifiers
export const TOUR_MODULES = {
  VAULT_BASICS: 'vault_basics',
  HOME_FEATURES: 'home_features',
  MENU_NAVIGATION: 'menu_navigation',
};

// Human-readable module names
export const MODULE_NAMES = {
  [TOUR_MODULES.VAULT_BASICS]: 'Vault Basics',
  [TOUR_MODULES.HOME_FEATURES]: 'Home Features',
  [TOUR_MODULES.MENU_NAVIGATION]: 'Menu Navigation',
};

// Simplified tour step definitions - only elements that are ALWAYS visible.
// VAULT_BASICS steps point at the personal folder panel; that panel is not
// mounted while PERSONAL_VAULT_ENABLED is false, so those steps are filtered
// out below rather than left to spotlight selectors that no longer resolve.
const allTourSteps = [
  // ========================================
  // MODULE 1: VAULT BASICS
  // ========================================
  {
    id: 'vault_create',
    module: TOUR_MODULES.VAULT_BASICS,
    order: 1,
    target: '[data-tour="vault-plus-button"]',
    title: ' Create Your Data Store',
    content: 'Click the + button to create a new data store. Think of the Data Store as your project\'s secure workspace. It\'s where you store every file, note, and meeting. The Citra AI Engine reads this data to generate your outputs.',
    placement: 'left',
    disableBeacon: true,
    spotlightClicks: true,
  },
  {
    id: 'vault_select',
    module: TOUR_MODULES.VAULT_BASICS,
    order: 2,
    target: '[data-tour="vault-select-checkbox"]',
    title: ' Select Data Stores for AI Search',
    content: 'Click on folder cards to select or deselect them for AI search. Selected data stores (shown with checkmark icons) will be included when AI searches for answers - ensuring zero hallucinations and 100% grounded responses from your actual files.',
    placement: 'left',
    disableBeacon: true,
    spotlightClicks: true,
  },
  {
    id: 'vault_open',
    module: TOUR_MODULES.VAULT_BASICS,
    order: 3,
    target: '[data-tour="vault-item-open"]',
    title: ' Open & Manage Data Store',
    content: 'Click the arrow button to open a data store and manage your files. Upload documents (PDFs, Excel, Meetings), organize into folders, record meetings, and create notes. This is your input that feeds the Citra AI Engine.',
    placement: 'left',
    disableBeacon: true,
    spotlightClicks: true,
  },

  // ========================================
  // MODULE 2: HOME FEATURES
  // ========================================
  {
    id: 'create_section',
    module: TOUR_MODULES.HOME_FEATURES,
    order: 4,
    target: '[data-tour="create-section"]',
    title: ' Create Tools',
    content: 'Access powerful tools to transform your data into actionable outputs. Create presentations from your data store content, generate comprehensive reports, and design visual diagrams.',
    placement: 'top',
    disableBeacon: true,
    spotlightClicks: true,
  },
  {
    id: 'presentation_feature',
    module: TOUR_MODULES.HOME_FEATURES,
    order: 5,
    target: '[data-tour="presentation-card"]',
    title: ' AI Presentation Composer',
    content: 'Transform your data store documents into professional presentations. AI analyzes your content and creates slide decks with key insights, charts, and recommendations automatically.',
    placement: 'top',
    disableBeacon: true,
    spotlightClicks: true,
  },
  {
    id: 'report_feature',
    module: TOUR_MODULES.HOME_FEATURES,
    order: 6,
    target: '[data-tour="report-card"]',
    title: ' Deep Analysis Reports',
    content: 'Generate comprehensive reports from your documents. AI performs deep analysis across multiple files, extracting insights, trends, and actionable recommendations.',
    placement: 'top',
    disableBeacon: true,
    spotlightClicks: true,
  },
  {
    id: 'diagram_feature',
    module: TOUR_MODULES.HOME_FEATURES,
    order: 7,
    target: '[data-tour="diagram-card"]',
    title: ' Visual Flow Diagrams',
    content: 'Create diagrams from your documents using natural language. Describe what you want to visualize, and AI generates flowcharts, process diagrams, and visual representations.',
    placement: 'top',
    disableBeacon: true,
    spotlightClicks: true,
  },
  {
    id: 'explore_section',
    module: TOUR_MODULES.HOME_FEATURES,
    order: 8,
    target: '[data-tour="explore-section"]',
    title: ' Explore & Research Tools',
    content: 'Powerful capabilities to explore your data. Query your data store with AI and analyze documents.',
    placement: 'top',
    disableBeacon: true,
    spotlightClicks: true,
  },
  {
    id: 'chat_query_feature',
    module: TOUR_MODULES.HOME_FEATURES,
    order: 9,
    target: '[data-tour="chat-query-card"]',
    title: ' AI Chat & Query',
    content: 'Ask questions about your documents in natural language. AI searches across your selected data stores and provides answers with citations from your source materials.',
    placement: 'top',
    disableBeacon: true,
    spotlightClicks: true,
  },
  {
    id: 'reader_review_feature',
    module: TOUR_MODULES.HOME_FEATURES,
    order: 11,
    target: '[data-tour="reader-review-card"]',
    title: ' Document Reader & Review',
    content: 'Browse and analyze documents from your data store. Read files, extract key information, and get AI-powered summaries and insights from your content.',
    placement: 'top',
    disableBeacon: true,
    spotlightClicks: true,
  },
  {
    id: 'organize_section',
    module: TOUR_MODULES.HOME_FEATURES,
    order: 14,
    target: '[data-tour="organize-section"]',
    title: ' Organize Your Knowledge Base',
    content: 'Build and manage your personal knowledge base. Create secure data stores for your documents, take notes, record meetings, and organize all your information in one place.',
    placement: 'top',
    disableBeacon: true,
    spotlightClicks: true,
  },
  {
    id: 'create_vault_feature',
    module: TOUR_MODULES.HOME_FEATURES,
    order: 15,
    target: '[data-tour="create-vault-card"]',
    title: ' Create Data Stores & Upload Files',
    content: 'Start by creating a data store - your secure, AI-searchable container. Upload documents, images, PDFs, and other files to build your knowledge base.',
    placement: 'top',
    disableBeacon: true,
    spotlightClicks: true,
  },
  {
    id: 'notes_feature',
    module: TOUR_MODULES.HOME_FEATURES,
    order: 16,
    target: '[data-tour="notes-card"]',
    title: ' Quick Notes',
    content: 'Take quick notes and save them to your data store. AI can help organize, summarize, and connect your notes with other documents in your knowledge base.',
    placement: 'top',
    disableBeacon: true,
    spotlightClicks: true,
  },
  {
    id: 'audio_meeting_feature',
    module: TOUR_MODULES.HOME_FEATURES,
    order: 17,
    target: '[data-tour="audio-meeting-card"]',
    title: ' Audio Meeting Recording',
    content: 'Record audio meetings and conversations. AI automatically transcribes the audio and makes it searchable within your data store for future reference.',
    placement: 'top',
    disableBeacon: true,
    spotlightClicks: true,
  },
  {
    id: 'video_meeting_feature',
    module: TOUR_MODULES.HOME_FEATURES,
    order: 18,
    target: '[data-tour="video-meeting-card"]',
    title: ' Video Meeting Recording',
    content: 'Record video meetings with automatic transcription. Capture both audio and visual content, with AI processing for searchable transcripts and key insights.',
    placement: 'top',
    disableBeacon: true,
    spotlightClicks: true,
  },

  // ========================================
  // MODULE 3: MENU NAVIGATION (Tab headers only - with descriptions)
  // ========================================
  {
    id: 'help_tab',
    module: TOUR_MODULES.MENU_NAVIGATION,
    order: 19,
    target: '[data-tour="help-tab"]',
    title: ' Help & Support',
    content: 'Find help resources, tutorials, and documentation here.\n\nYou can restart this tour anytime from the Help menu, or access the first-time user tutorial to learn about Citra AI features!',
    placement: 'bottom',
    disableBeacon: true,
    spotlightClicks: true,
  },
];

export const tourSteps = PERSONAL_VAULT_ENABLED
  ? allTourSteps
  : allTourSteps.filter((step) => step.module !== TOUR_MODULES.VAULT_BASICS);

// Helper functions
export const getStepsByModule = (moduleId) => {
  return tourSteps.filter(step => step.module === moduleId);
};

export const getStepById = (stepId) => {
  return tourSteps.find(step => step.id === stepId);
};

export const getModuleInfo = (moduleId) => {
  const steps = getStepsByModule(moduleId);
  return {
    id: moduleId,
    name: MODULE_NAMES[moduleId] || moduleId,
    totalSteps: steps.length,
    steps,
  };
};

export default tourSteps;
