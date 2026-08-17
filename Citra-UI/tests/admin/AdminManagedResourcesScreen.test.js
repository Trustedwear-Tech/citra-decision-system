// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import AdminManagedResourcesScreen from '../../screens/admin/AdminManagedResourcesScreen';
import {
  listAdminWorkflows, listAdminSmartApps,
} from '../../services/AdminManagedResourcesService';
import { authService } from '../../services/authService';

jest.mock('../../services/AdminManagedResourcesService', () => ({
  listAdminWorkflows: jest.fn(),
  listAdminSmartApps: jest.fn(),
}));

jest.mock('../../services/authService', () => ({
  authService: { getCurrentUser: jest.fn() },
}));

// Renders its own Modal + pulls unrelated deps; not under test here.
jest.mock('../../screens/SmartAppAuditScreen', () => () => null);

const defaultProps = { visible: true, onClose: jest.fn() };

beforeEach(() => {
  jest.clearAllMocks();
  authService.getCurrentUser.mockReturnValue({
    user_id: 'u1',
    org_id: 'acme-power',
    dept_ids: ['central_pmu'],
    roles: ['org_admin'],
  });
  listAdminWorkflows.mockResolvedValue([]);
  listAdminSmartApps.mockResolvedValue([]);
});

describe('AdminManagedResourcesScreen', () => {
  // Workflow Automation PARKED 2026-07-28 — the Workflows tab is hidden until
  // the product returns (plan: docs/workflow-agent-and-connectivity-plan.md).
  it('renders only the Decision Apps tab (Workflows parked)', async () => {
    render(<AdminManagedResourcesScreen {...defaultProps} />);
    await waitFor(() => expect(screen.getByText('Decision Apps')).toBeTruthy());
    expect(screen.queryByText('Workflows')).toBeNull();
  });

  it('defaults to the Decision Apps list, never calling the workflow API', async () => {
    render(<AdminManagedResourcesScreen {...defaultProps} />);
    await waitFor(() => expect(listAdminSmartApps).toHaveBeenCalled());
    expect(listAdminWorkflows).not.toHaveBeenCalled();
  });

  // Skill-Service was retired 2026-07-17. A Skills tab here would point at a
  // service that no longer exists, so it must never come back.
  it('has no Skills tab', async () => {
    render(<AdminManagedResourcesScreen {...defaultProps} />);
    await waitFor(() => expect(screen.getByText('Decision Apps')).toBeTruthy());
    expect(screen.queryByText('Skills')).toBeNull();
  });

  it('subtitle lists the managed resource taxonomy, without skill', async () => {
    render(<AdminManagedResourcesScreen {...defaultProps} />);
    await waitFor(() => expect(screen.getByText('Decision Apps')).toBeTruthy());
    // Taxonomy string still names workflow — MANAGED_RESOURCES is a cross-
    // service contract (departures/handoff) and only the UI tab is parked.
    expect(screen.getByText(/workflow · smart_app — dept\/org admin view/)).toBeTruthy();
    expect(screen.queryByText(/· skill/)).toBeNull();
  });
});
