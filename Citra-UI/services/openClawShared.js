// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * openClawShared — constants and helpers shared between every Citra
 * surface that talks to OpenClaw (currently action chat + smart-app
 * builder). Single source of truth so the two screens can't drift on
 * the user-facing wording for things OpenClaw natively supports.
 */

// OpenClaw queue modes per messages.queue.mode in openclaw.json. The
// per-session mode is set via the inline ``/queue <mode>`` directive
// (parsed in src/auto-reply/reply/queue/directive.ts) sent through
// chat.send. The directive is consumed before reaching the LLM —
// changing the mode costs no model tokens.
export const QueueMode = {
  STEER: 'steer',
  STEER_BACKLOG: 'steer-backlog',
  COLLECT: 'collect',
  FOLLOWUP: 'followup',
};

export const QUEUE_MODE_LABELS = {
  steer: 'Steer (default)',
  'steer-backlog': 'Steer + remember',
  collect: 'Collect',
  followup: 'Wait for next turn',
};

// Compact labels for the inline chip / composer opener, where the
// surrounding UI already says "Mid-task:".
export const QUEUE_MODE_SHORT = {
  steer: 'Steer',
  'steer-backlog': 'Steer + remember',
  collect: 'Collect',
  followup: 'Wait',
};

export const QUEUE_MODE_DESCRIPTIONS = {
  steer:
    'Your mid-task messages land between tool calls. The agent adjusts immediately.',
  'steer-backlog':
    'Inject mid-task AND keep the message in context for the next turn.',
  collect:
    'Multiple rapid messages are coalesced into one follow-up turn after the current one finishes.',
  followup:
    'Mid-task messages wait quietly until the current turn ends, then run as the next turn.',
};

/**
 * Build the ``/queue <mode>`` directive body to send via chat.send.
 * Returns null when the mode is unknown.
 */
export function buildQueueModeDirective(mode) {
  return Object.values(QueueMode).includes(mode) ? `/queue ${mode}` : null;
}
