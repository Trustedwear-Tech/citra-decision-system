// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Reusable test fixtures — node schemas that mirror the real backend format.
 */

export const TRIGGER_SCHEMA = {
  type: 'manual_trigger',
  label: 'Manual Trigger',
  category: 'trigger',
  icon: '▶️',
  color: '#22c55e',
  description: 'Start the workflow manually',
  fields: [],
  inputs: 0,
  outputs: 1,
};

export const START_NODE_SCHEMA = {
  type: 'start_node',
  label: 'Start Node',
  category: 'trigger',
  icon: '🚀',
  color: '#22c55e',
  description: 'Start the workflow with optional input schema',
  fields: [
    { name: 'input_schema', label: 'Input Schema', type: 'schema_builder', required: false },
  ],
  inputs: 0,
  outputs: 1,
};

export const LLM_PROCESSOR_SCHEMA = {
  type: 'llm_processor',
  label: 'LLM Processor',
  category: 'processor',
  icon: '🤖',
  color: '#8b5cf6',
  description: 'Process data with an LLM',
  fields: [
    { name: 'model', label: 'Model', type: 'select', options: ['gpt-4o', 'gpt-4o-mini', 'claude-3'], required: true },
    { name: 'system_prompt', label: 'System Prompt', type: 'textarea', placeholder: 'Enter system prompt...' },
    { name: 'user_prompt', label: 'User Prompt', type: 'textarea', placeholder: 'Enter user prompt...' },
    { name: 'temperature', label: 'Temperature', type: 'number', placeholder: '0.7' },
    { name: 'output_format', label: 'Output Format', type: 'select', options: ['text', 'json'] },
    { name: 'json_schema', label: 'JSON Schema', type: 'json', visible_when: { field: 'output_format', value: 'json' } },
  ],
  inputs: 1,
  outputs: 1,
};

export const CONDITION_SCHEMA = {
  type: 'condition',
  label: 'Condition',
  category: 'logic',
  icon: '🔀',
  color: '#ec4899',
  description: 'Branch based on a condition',
  fields: [
    { name: 'field', label: 'Field', type: 'text', required: true },
    { name: 'operator', label: 'Operator', type: 'select', options: ['equals', 'not_equals', 'contains', 'greater_than'], required: true },
    { name: 'value', label: 'Value', type: 'text', required: true },
  ],
  inputs: 1,
  outputs: 2,
  output_labels: ['True', 'False'],
};

export const SWITCH_ROUTER_SCHEMA = {
  type: 'switch_router',
  label: 'Switch / Router',
  category: 'logic',
  icon: '🔀',
  color: '#d946ef',
  description: 'Route to one of multiple branches based on a field value (up to 6 outputs)',
  fields: [
    { name: 'field', label: 'Field to Match', type: 'text', required: true },
    {
      name: 'routes',
      label: 'Routes (JSON Array)',
      type: 'json',
      required: true,
      default: [
        { label: 'Route A', value: 'a' },
        { label: 'Route B', value: 'b' },
        { label: 'Default', value: '__default__' },
      ],
    },
  ],
  inputs: 1,
  outputs: 6,
  output_labels: ['Route 0', 'Route 1', 'Route 2', 'Route 3', 'Route 4', 'Default'],
};

export const AI_AGENT_SCHEMA = {
  type: 'ai_agent',
  label: 'AI Agent',
  category: 'agent',
  icon: '🧠',
  color: '#a855f7',
  description: 'Autonomous AI agent with tools',
  fields: [
    { name: 'agent_name', label: 'Agent Name', type: 'text', required: true },
    { name: 'model', label: 'Model', type: 'select', options: ['gpt-4o', 'claude-3'] },
    { name: 'system_prompt', label: 'System Prompt', type: 'textarea' },
    { name: 'tools', label: 'Tools', type: 'tool_picker' },
    { name: 'max_iterations', label: 'Max Iterations', type: 'number' },
  ],
  inputs: 1,
  outputs: 1,
};

export const WEBHOOK_OUTPUT_SCHEMA = {
  type: 'webhook_output',
  label: 'Webhook Output',
  category: 'output',
  icon: '🌐',
  color: '#f59e0b',
  description: 'Send results to an HTTP endpoint',
  fields: [
    { name: 'url', label: 'URL', type: 'text', required: true, placeholder: 'https://...' },
    { name: 'method', label: 'Method', type: 'select', options: ['POST', 'PUT', 'PATCH'] },
    { name: 'headers', label: 'Headers', type: 'json' },
  ],
  inputs: 1,
  outputs: 0,
};

export const DATA_SOURCE_SCHEMA = {
  type: 'http_source',
  label: 'HTTP Source',
  category: 'source',
  icon: '📡',
  color: '#3b82f6',
  description: 'Fetch data from an HTTP API',
  fields: [
    { name: 'url', label: 'URL', type: 'text', required: true },
    { name: 'method', label: 'Method', type: 'select', options: ['GET', 'POST'] },
  ],
  inputs: 1,
  outputs: 1,
};

export const ALL_SCHEMAS = [
  TRIGGER_SCHEMA,
  START_NODE_SCHEMA,
  LLM_PROCESSOR_SCHEMA,
  CONDITION_SCHEMA,
  SWITCH_ROUTER_SCHEMA,
  AI_AGENT_SCHEMA,
  WEBHOOK_OUTPUT_SCHEMA,
  DATA_SOURCE_SCHEMA,
];

export const SAMPLE_WORKFLOWS = [
  {
    workflow_id: 'wf1',
    name: 'Data Pipeline',
    description: 'Fetch and process data',
    status: 'draft',
    version: 1,
    node_count: 3,
    edge_count: 2,
    updated_at: '2025-12-01T10:00:00Z',
  },
  {
    workflow_id: 'wf2',
    name: 'AI Research Agent',
    description: 'Research and summarize topics',
    status: 'deployed',
    version: 2,
    node_count: 5,
    edge_count: 4,
    updated_at: '2025-12-15T14:30:00Z',
  },
];

export const SAMPLE_TEMPLATES = [
  {
    template_id: 'tpl1',
    name: 'Research Agent',
    description: 'AI agent that researches topics using web search and returns a summary',
    icon: '🔍',
    tags: ['agent', 'research'],
    nodes: [{ type: 'manual_trigger' }, { type: 'ai_agent' }, { type: 'webhook_output' }],
  },
  {
    template_id: 'tpl2',
    name: 'Data Pipeline',
    description: 'Fetch data, transform it, and store results',
    icon: '📊',
    tags: ['data', 'pipeline'],
    nodes: [{ type: 'manual_trigger' }, { type: 'llm_processor' }],
  },
];

export const SAMPLE_PENDING_APPROVALS = [
  {
    approval_id: 'apr1',
    execution_id: 'exec1',
    workflow_name: 'Review Pipeline',
    node_id: 'node3',
    node_label: 'Human Review',
    message: 'Please review data before proceeding',
    created_at: '2025-12-20T08:00:00Z',
    timeout_hours: 24,
  },
];

export const SAMPLE_ALL_APPROVALS = [
  ...SAMPLE_PENDING_APPROVALS,
  {
    approval_id: 'apr0',
    execution_id: 'exec0',
    workflow_name: 'Old Pipeline',
    node_id: 'node2',
    node_label: 'Verify',
    resolution: 'approved',
    created_at: '2025-11-01T12:00:00Z',
    resolved_at: '2025-11-01T12:30:00Z',
    resolved_by: 'admin',
  },
];

export const SAMPLE_EXECUTION_RUNNING = {
  execution_id: 'exec1',
  status: 'running',
  node_results: {
    node1: { status: 'completed', duration_ms: 120, output_data: { text: 'hello' } },
    node2: { status: 'running' },
  },
};

export const SAMPLE_EXECUTION_COMPLETED = {
  execution_id: 'exec1',
  status: 'completed',
  node_results: {
    node1: { status: 'completed', duration_ms: 120, output_data: { text: 'hello' } },
    node2: { status: 'completed', duration_ms: 450, output_data: { result: 'done' } },
  },
};

export const SAMPLE_EXECUTION_PAUSED = {
  execution_id: 'exec1',
  status: 'paused',
  paused_at_node: 'Human Review',
  node_results: {
    node1: { status: 'completed', duration_ms: 100 },
  },
};

export const SAMPLE_EXECUTION_FAILED = {
  execution_id: 'exec1',
  status: 'failed',
  error: 'LLM timeout after 30s',
  node_results: {
    node1: { status: 'completed', duration_ms: 100 },
    node2: { status: 'failed', error: 'Timeout' },
  },
};
