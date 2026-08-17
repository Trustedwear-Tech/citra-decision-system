// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Legal Filter Storage Utility
 * =============================
 * 
 * Manages persistence of legal filter selections (courts and laws) in AsyncStorage.
 * 
 * Default Filters:
 * - Supreme Court of India (court)
 * - Union of India - Acts (central law)
 * - Constitution & Amendments (central law)
 * 
 * Storage Schema:
 * {
 *   selectedCourt: string | null,
 *   selectedCentralLaws: string[],  // max 3
 *   selectedStateLaw: string | null
 * }
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

const STORAGE_KEY = '@legal_filters';

/**
 * Default filter configuration when no saved filters exist
 */
const DEFAULT_FILTERS = {
  selectedCourt: 'Supreme Court of India',
  selectedCentralLaws: [
    'Union of India - Acts'
  ],
  selectedStateLaw: null
};

/**
 * Save legal filter selections to AsyncStorage
 * 
 * @param {Object} filters - Filter selections
 * @param {string|null} filters.selectedCourt - Selected court
 * @param {string[]} filters.selectedCentralLaws - Selected central laws (max 3)
 * @param {string|null} filters.selectedStateLaw - Selected state law
 * @returns {Promise<boolean>} Success status
 */
export const saveLegalFilters = async (filters) => {
  try {
    const filterData = {
      selectedCourt: filters.selectedCourt || null,
      selectedCentralLaws: filters.selectedCentralLaws || [],
      selectedStateLaw: filters.selectedStateLaw || null,
      lastUpdated: new Date().toISOString()
    };

    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(filterData));
    console.log('✅ Legal filters saved to AsyncStorage:', filterData);
    return true;
  } catch (error) {
    console.error('❌ Error saving legal filters:', error);
    return false;
  }
};

/**
 * Load legal filter selections from AsyncStorage
 * If no saved filters exist, returns default filters
 * 
 * @returns {Promise<Object>} Filter selections
 */
export const loadLegalFilters = async () => {
  try {
    const storedData = await AsyncStorage.getItem(STORAGE_KEY);
    
    if (storedData) {
      const filters = JSON.parse(storedData);
      console.log('📋 Loaded legal filters from AsyncStorage:', filters);
      
      // Return stored filters (without lastUpdated timestamp)
      return {
        selectedCourt: filters.selectedCourt,
        selectedCentralLaws: filters.selectedCentralLaws,
        selectedStateLaw: filters.selectedStateLaw
      };
    } else {
      console.log('📋 No saved filters found, using defaults:', DEFAULT_FILTERS);
      
      // Save defaults for first time users
      await saveLegalFilters(DEFAULT_FILTERS);
      
      return DEFAULT_FILTERS;
    }
  } catch (error) {
    console.error('❌ Error loading legal filters:', error);
    console.log('📋 Falling back to default filters');
    return DEFAULT_FILTERS;
  }
};

/**
 * Clear all legal filter selections from AsyncStorage
 * Resets to default filters
 * 
 * @returns {Promise<boolean>} Success status
 */
export const clearLegalFilters = async () => {
  try {
    await AsyncStorage.removeItem(STORAGE_KEY);
    console.log('🗑️ Legal filters cleared from AsyncStorage');
    
    // Reset to defaults
    await saveLegalFilters(DEFAULT_FILTERS);
    return true;
  } catch (error) {
    console.error('❌ Error clearing legal filters:', error);
    return false;
  }
};

/**
 * Get default filter configuration
 * 
 * @returns {Object} Default filter selections
 */
export const getDefaultFilters = () => {
  return { ...DEFAULT_FILTERS };
};

/**
 * Validate filter selections against business rules
 * 
 * Rules:
 * - Only 1 court can be selected
 * - Maximum 3 central laws can be selected
 * - Only 1 state law can be selected
 * 
 * @param {Object} filters - Filter selections to validate
 * @returns {Object} Validation result { valid: boolean, errors: string[] }
 */
export const validateFilters = (filters) => {
  const errors = [];

  // Validate court selection (max 1)
  if (filters.selectedCourt && typeof filters.selectedCourt !== 'string') {
    errors.push('Court selection must be a single value');
  }

  // Validate central laws (max 1)
  if (!Array.isArray(filters.selectedCentralLaws)) {
    errors.push('Central laws must be an array');
  } else if (filters.selectedCentralLaws.length > 1) {
    errors.push(`Maximum 1 central law allowed (selected: ${filters.selectedCentralLaws.length})`);
  }

  // Validate state law (max 1)
  if (filters.selectedStateLaw && typeof filters.selectedStateLaw !== 'string') {
    errors.push('State law selection must be a single value');
  }

  return {
    valid: errors.length === 0,
    errors
  };
};

/**
 * Check if filters are at default values
 * 
 * @param {Object} filters - Current filter selections
 * @returns {boolean} True if filters match defaults
 */
export const areFiltersDefault = (filters) => {
  return (
    filters.selectedCourt === DEFAULT_FILTERS.selectedCourt &&
    JSON.stringify(filters.selectedCentralLaws) === JSON.stringify(DEFAULT_FILTERS.selectedCentralLaws) &&
    filters.selectedStateLaw === DEFAULT_FILTERS.selectedStateLaw
  );
};

/**
 * Get a summary of current filter selections for display
 * 
 * @param {Object} filters - Current filter selections
 * @returns {string} Human-readable summary
 */
export const getFilterSummary = (filters) => {
  const parts = [];

  if (filters.selectedCourt) {
    parts.push(`Court: ${filters.selectedCourt}`);
  }

  if (filters.selectedCentralLaws && filters.selectedCentralLaws.length > 0) {
    parts.push(`Central Laws: ${filters.selectedCentralLaws.length}`);
  }

  if (filters.selectedStateLaw) {
    parts.push(`State Law: ${filters.selectedStateLaw}`);
  }

  return parts.length > 0 ? parts.join(', ') : 'No filters selected';
};
