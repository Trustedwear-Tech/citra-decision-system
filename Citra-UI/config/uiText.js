// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

// uiText.js - static UI copy.
//
// Was persona-keyed (config/personaTextConfig.js + hooks/usePersonaText.js).
// That indirection resolved to exactly ONE profile - 'General Professional' was
// the only key in the config, and PersonaSetupFlow logged it as "the only
// supported profession" - so every lookup returned these values already.
// Generated from that config so no user-visible string changed.
const UI_TEXT = {
  projectManagementTitle: "Project Management",
  projectManagementDescription: "Manage projects with AI assistance",
  caseVaultTitle: "Personal Data Store",
  caseVaultDescription: "Curate documents, notes, and private research inside your Personal Data Store. Citra AI references this material whenever you need to access your professional knowledge.",
  caseVaultAddButton: "Add Documents, Notes & Audio",
  caseVaultAddButtonDescription: "Upload documents, recordings, and working drafts for personal reference",
  caseVaultEmptyMessage: "No documents available. Upload documents to see them here.",
  caseVaultBrowseFiles: "Browse Personal Data Store Files",
  enterpriseDriveTitle: "Personal Shared Repository",
  enterpriseDriveDescription: "Centralize your playbooks, templates, and project files. Store common documents and resources you use across multiple projects for quick reference.",
  firmKnowledgeTitle: "General Knowledge Library",
  firmKnowledgeDescription: "Store templates, resources, and documents you use across all your projects",
  entitySpecificTitle: "Project Repository",
  entitySpecificDescription: "Create dedicated repositories for specific projects and work streams",
  manageEntitiesTitle: "Manage Projects & Clients",
  manageEntitiesDescription: "Maintain your catalog of projects, clients, and work areas to keep your data organized",
  menuPersonalDrive: "Personal Data Store",
  menuEnterpriseDrive: "Personal Shared Repository",
  menuBuildLibrary: "Build Document Library - Personal Data Stores",
  folderEmptyMessage: "No documents in this data store. Add documents to build your library.",
  documentTypeLabel: "Document",
  internetSearchLabel: "Research using Internet",
  internetSearchHint: "Enable or disable internet search for research",
  upcomingFeaturesSubtitle: "Empowering Professionals with the Next Generation of AI-Powered Intelligence",
  upcomingTeamContext: "professional team",
  upcomingDataType: "project files",
  upcomingMatterType: "projects",
  upcomingWorkspaceType: "teams or departments",
  upcomingRepositoryType: "documents, reports, and internal archives",
  reportComposerTitle: "Report Composer",
  reportComposerDescription: "Create professional reports, summaries, and documents",
  reportComposerGoalPrompt: "What type of document do you want to create?",
  reportComposerGoalPlaceholder: "E.g., Project Report, Executive Summary, Research Paper...",
  reportComposerSectionLabel: "Report Section",
  reportComposerPageSingular: "section",
  reportComposerPagePlural: "sections",
  reportComposerAiChatPlaceholder: "Ask AI to help draft sections, summarize content, or refine text...",
  reportComposerExportButton: "Export Report",
  reportComposerVaultLabel: "Data Store",
};

export default UI_TEXT;
