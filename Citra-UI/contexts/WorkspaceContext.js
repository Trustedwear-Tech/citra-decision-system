// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { CONFIG as API_CONFIG } from '../config/config';
import { authService } from '../services/authService';  // Import auth service
import { useUser } from '../components/UserProvider';  // Import useUser hook

// Create the Workspace Context
const WorkspaceContext = createContext();

// Custom hook to use the Workspace Context
const useWorkspace = () => {
  const context = useContext(WorkspaceContext);
  if (!context) {
    throw new Error('useWorkspace must be used within a WorkspaceProvider');
  }
  return context;
};

// Workspace Provider Component
const WorkspaceProvider = ({ children, useUploadedData, setUseUploadedData }) => {
  // Get userEmail from UserProvider context
  const { userEmail } = useUser();
  // Folder-related state
  const [folders, setFolders] = useState([]);
  const [selectedFolderIds, setSelectedFolderIds] = useState([]);
  const [showFolderSetup, setShowFolderSetup] = useState(false);
  const [isFoldersLoading, setIsFoldersLoading] = useState(true);
  const [authChecked, setAuthChecked] = useState(false); // Track auth verification

  // Folder selection toggle function
  const toggleFolderSelection = useCallback((folderId) => {
    // CRITICAL: Ensure folderId is never an array
    let normalizedId = folderId;
    if (Array.isArray(folderId)) {
      console.warn('⚠️ [WORKSPACE] folderId was an array, extracting first element:', folderId);
      normalizedId = folderId[0];
      // If still an array after extraction, keep extracting
      while (Array.isArray(normalizedId)) {
        normalizedId = normalizedId[0];
      }
    }

    console.log('🔍 TOGGLE_FOLDER_SELECTION_DEBUG:', {
      original: folderId,
      normalized: normalizedId,
      folderIdType: typeof normalizedId,
      isArray: Array.isArray(normalizedId),
      currentSelectedIds: selectedFolderIds,
      isCurrentlySelected: selectedFolderIds.includes(normalizedId)
    });

    setSelectedFolderIds(prev => {
      const isSelected = prev.includes(normalizedId);
      let newSelection;

      if (isSelected) {
        // Remove from selection
        newSelection = prev.filter(id => id !== normalizedId);
        console.log('🔍 REMOVING_FOLDER_FROM_SELECTION:', {
          folderId: normalizedId,
          previousSelection: prev,
          newSelection: newSelection
        });
      } else {
        // Add to selection
        newSelection = [...prev, normalizedId];
        console.log('🔍 ADDING_FOLDER_TO_SELECTION:', {
          folderId: normalizedId,
          previousSelection: prev,
          newSelection: newSelection
        });
      }

      // Automatically enable Personal Data when any folder is selected
      // Note: AI Only mode is automatically disabled in App.js via useEffect (lines 9963-9969)
      if (newSelection.length > 0 && setUseUploadedData && !useUploadedData) {
        setUseUploadedData(true);
        console.log('💡 Automatically enabled Personal Data due to folder selection');
      }

      console.log('🔍 FOLDER_SELECTION_COMPLETE:', {
        folderId: normalizedId,
        finalSelection: newSelection,
        selectedFolders: newSelection.map(id => {
          const folder = folders.find(f => f.id === id);
          return folder ? { id: folder.id, name: folder.name } : { id, name: 'Unknown' };
        })
      });

      // Save to AsyncStorage with detailed logging
      console.log('💾 [WORKSPACE] Saving to AsyncStorage:', { newSelection });
      AsyncStorage.setItem('selectedFolderIds', JSON.stringify(newSelection))
        .then(() => {
          console.log('✅ [WORKSPACE] Successfully saved to AsyncStorage:', newSelection);
        })
        .catch(err => {
          console.error('❌ [WORKSPACE] Failed to save folder selection to AsyncStorage:', err);
        });

      return newSelection;
    });
  }, [selectedFolderIds, folders, useUploadedData, setUseUploadedData]);

  // Single-select: replace entire selection with one folder (atomic operation, one AsyncStorage write)
  const selectSingleFolder = useCallback((folderId) => {
    let normalizedId = folderId;
    if (Array.isArray(folderId)) {
      while (Array.isArray(normalizedId)) normalizedId = normalizedId[0];
    }

    const newSelection = [normalizedId];
    setSelectedFolderIds(newSelection);

    // Auto-enable vault when selecting a folder
    if (setUseUploadedData && !useUploadedData) {
      setUseUploadedData(true);
      console.log('💡 Automatically enabled Personal Data due to folder selection');
    }

    console.log('💾 [WORKSPACE] Saving single folder selection:', newSelection);
    AsyncStorage.setItem('selectedFolderIds', JSON.stringify(newSelection))
      .then(() => console.log('✅ [WORKSPACE] Saved single folder selection:', newSelection))
      .catch(err => console.error('❌ [WORKSPACE] Failed to save single folder selection:', err));
  }, [useUploadedData, setUseUploadedData]);

  // Function to get current selected workspace folder
  const getSelectedWorkspaceFolder = useCallback(() => {
    const nonDefaultFolders = selectedFolderIds.filter(id => id !== 'documents');

    if (nonDefaultFolders.length > 0) {
      const selectedFolder = folders.find(f => f.id === nonDefaultFolders[0]);
      return {
        id: nonDefaultFolders[0],
        name: selectedFolder ? selectedFolder.name : 'Unknown Folder'
      };
    }

    return {
      id: null,
      name: 'Documents'
    };
  }, [selectedFolderIds, folders]);

  // Function to get multiple selected folders (for warnings)
  const getSelectedFolders = useCallback(() => {
    const nonDefaultFolders = selectedFolderIds.filter(id => id !== 'documents');
    return nonDefaultFolders.map(id => {
      const folder = folders.find(f => f.id === id);
      return {
        id,
        name: folder ? folder.name : 'Unknown'
      };
    });
  }, [selectedFolderIds, folders]);

  // folder_management.py (/api/folders/*) was deleted — the personal-vault
  // folder CRUD API, a product-scope removal. fetchFolders keeps its shape
  // (callers still exist: the login/workspace-change effects below, plus
  // App.js call sites) but there is nothing left to fetch, so it resolves
  // straight to the terminal "no folders" state instead of hitting a route
  // that would 404 on every call. Every consumer of `folders`/
  // `selectedFolderIds` already has an existing `'general'`-bucket fallback
  // for the empty case — this doesn't add new fallback code, it just means
  // that existing branch is now the only one ever reached.
  const fetchFolders = useCallback(async () => {
    setFolders([]);
    setIsFoldersLoading(false);
    setAuthChecked(true);
  }, []);

  // Resolve to the terminal empty state once a user is known. fetchFolders
  // is a stable no-op now (see above), so this only needs to run once per
  // login, not on every render.
  useEffect(() => {
    if (userEmail) {
      fetchFolders();
    }
  }, [userEmail, fetchFolders]);

  // The "reload vaults when workspace (team) changes" effect that used to
  // live here is deleted along with Teams — activeTeamId no longer exists.
  // fetchFolders (a stable no-op, see above) already runs once per login;
  // there is no second "workspace" dimension left to react to.

  // Remove retry mechanism - it causes duplicate fetches
  // The main fetch in the useEffect above handles initial load

  // Listen for successful authentication events (debounced to prevent duplicates)
  useEffect(() => {
    let authSuccessTimeout = null;

    const handleAuthSuccess = () => {
      console.log('🗂️ Authentication success detected, scheduling folder fetch...');

      // Clear any pending fetch
      if (authSuccessTimeout) {
        clearTimeout(authSuccessTimeout);
      }

      // Reset state and schedule fetch after a delay
      setAuthChecked(false);

      // Debounce to prevent rapid successive calls
      authSuccessTimeout = setTimeout(() => {
        fetchFolders();
      }, 1500);
    };

    const handleFolderCreated = () => {
      console.log('🗂️ Folder created event detected, refreshing folders...');
      fetchFolders();
    };

    // Listen for custom auth success events
    if (typeof window !== 'undefined' && window.addEventListener) {
      window.addEventListener('authSuccess', handleAuthSuccess);
      window.addEventListener('folderCreated', handleFolderCreated);
    }

    return () => {
      if (authSuccessTimeout) {
        clearTimeout(authSuccessTimeout);
      }
      if (typeof window !== 'undefined' && window.removeEventListener) {
        window.removeEventListener('authSuccess', handleAuthSuccess);
        window.removeEventListener('folderCreated', handleFolderCreated);
      }
    };
  }, [fetchFolders]);

  // The AsyncStorage-restore / auto-select-first-folder effect that used to
  // live here is deleted, not just unused: it only ever ran inside
  // `if (folders.length > 0)`, and folders is now permanently `[]` (nothing
  // populates it since folder_management.py is gone) — the whole body was
  // genuinely unreachable, not merely dead weight.

  // The scope-push effect that used to live here is gone with its service:
  // it POSTed the active folder / vault toggle to action-chat-service, which
  // was removed from the repo in eb2257a5 ("drop Quick Chat, Operations
  // Analytics and Deep Research"). Every call had been landing on nothing,
  // and the .catch only console.warn'd, so the breakage was invisible.

  // Context value
  const contextValue = {
    // State
    folders,
    selectedFolderIds,
    showFolderSetup,
    isFoldersLoading,

    // State setters (for components that need direct access)
    setFolders,
    setSelectedFolderIds,
    setShowFolderSetup,
    setIsFoldersLoading,

    // Actions
    toggleFolderSelection,
    selectSingleFolder,
    fetchFolders,

    // Computed values
    getSelectedWorkspaceFolder,
    getSelectedFolders,

    // Helper functions
    isDocumentsSelected: selectedFolderIds.includes('documents'),
    hasSelectedFolders: selectedFolderIds.length > 0,
    nonDefaultSelectedFolders: selectedFolderIds.filter(id => id !== 'documents'),
    useUploadedData,
    setUseUploadedData,
    userEmail,
  };

  return (
    <WorkspaceContext.Provider value={contextValue}>
      {children}
    </WorkspaceContext.Provider>
  );
};

// Export the components
export { useWorkspace, WorkspaceProvider };
export default WorkspaceContext;
