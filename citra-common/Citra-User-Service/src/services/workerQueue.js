// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Worker queue producer.
 *
 * Citra-Worker (Python) consumes jobs from a Redis STREAM via a consumer
 * group. This module is the Node-side producer so user-service can emit events
 * (e.g. user.deactivated) that the worker picks up to fan out across Mongo
 * collections.
 *
 * Wire format MUST match citra-queue/citra_queue/queue.py:
 *   stream : citra:worker:stream:<queue_name>   STREAM  (XADD, field "data")
 *   group  : "workers"                          consumer group (XREADGROUP)
 *   value  : data = JSON {id, handler, payload, queue, enqueued_at,
 *                         tenant_id, request_id, retries, max_retries}
 *
 * We ensure the consumer group exists (XGROUP CREATE … MKSTREAM) BEFORE XADD so
 * the entry is delivered to the group (an entry added before the group exists
 * would sit past its "$" start id and never be read). We also HSET the per-job
 * state hash so the worker's mark_* calls find a pre-existing record.
 *
 * Uses the DEDICATED queue Redis (QUEUE_REDIS_*) when set, falling back to the
 * shared REDIS_* — must match the worker's queue Redis or jobs are orphaned.
 */

const { createClient } = require('redis');
const crypto = require('crypto');

const REDIS_HOST = process.env.QUEUE_REDIS_HOST || process.env.REDIS_HOST || 'localhost';
const REDIS_PORT = parseInt(process.env.QUEUE_REDIS_PORT || process.env.REDIS_PORT || '6379', 10);
const REDIS_DB = parseInt(process.env.QUEUE_REDIS_DB || process.env.REDIS_DB || '0', 10);
const REDIS_USERNAME = process.env.QUEUE_REDIS_USERNAME || process.env.REDIS_USERNAME || undefined;
const REDIS_PASSWORD = process.env.QUEUE_REDIS_PASSWORD || process.env.REDIS_PASSWORD || undefined;
const REDIS_KEY_PREFIX = process.env.REDIS_KEY_PREFIX || '';
const DEFAULT_QUEUE = 'default';
const GROUP = 'workers';
const RESULT_TTL_SECONDS = parseInt(
  process.env.CITRA_WORKER_RESULT_TTL || String(7 * 24 * 3600),
  10
);
const DEFAULT_MAX_RETRIES = parseInt(
  process.env.CITRA_WORKER_MAX_RETRIES || '3',
  10
);

let _client = null;

async function _getClient() {
  if (_client && _client.isReady) return _client;
  _client = createClient({
    socket: { host: REDIS_HOST, port: REDIS_PORT },
    database: REDIS_DB,
    username: REDIS_USERNAME,
    password: REDIS_PASSWORD,
  });
  _client.on('error', (err) =>
    // eslint-disable-next-line no-console
    console.error('[workerQueue] redis error', err && err.message)
  );
  await _client.connect();
  return _client;
}

function _keyStream(queue) {
  return `${REDIS_KEY_PREFIX}citra:worker:stream:${queue}`;
}

function _keyJob(jobId) {
  return `${REDIS_KEY_PREFIX}citra:worker:job:${jobId}`;
}

async function _ensureGroup(client, stream) {
  // Idempotent: create the stream + consumer group if absent. BUSYGROUP means
  // the group already exists, which is fine.
  try {
    await client.xGroupCreate(stream, GROUP, '$', { MKSTREAM: true });
  } catch (err) {
    if (!String(err && err.message).includes('BUSYGROUP')) throw err;
  }
}

/**
 * Enqueue a job for the Python worker.
 *
 * @param {string} handler  Handler name (must match a @register("name") in
 *                          Citra-Worker, e.g. "user.deactivated").
 * @param {object} payload  JSON-serialisable payload for the handler.
 * @param {object} [opts]
 * @param {string} [opts.queue]     Queue name (default "default").
 * @param {string} [opts.tenantId]  Optional tenant scope.
 * @param {string} [opts.requestId] Optional originating request id.
 * @param {number} [opts.maxRetries] Override default max retries.
 * @returns {Promise<string>} The job id assigned by this producer.
 */
async function enqueue(handler, payload, opts = {}) {
  const queue = opts.queue || DEFAULT_QUEUE;
  const tenantId = opts.tenantId || '';
  const requestId = opts.requestId || '';
  const maxRetries = opts.maxRetries !== undefined ? opts.maxRetries : DEFAULT_MAX_RETRIES;

  const jobId = crypto.randomUUID().replace(/-/g, '');
  const enqueuedAt = Date.now() / 1000;
  const job = {
    id: jobId,
    handler,
    payload: payload || {},
    queue,
    enqueued_at: enqueuedAt,
    tenant_id: tenantId || null,
    request_id: requestId || null,
    retries: 0,
    max_retries: maxRetries,
  };

  const client = await _getClient();
  const stream = _keyStream(queue);
  // Group must exist before XADD so the entry is delivered to the worker group.
  await _ensureGroup(client, stream);
  const multi = client.multi();
  multi.hSet(_keyJob(jobId), {
    id: jobId,
    handler,
    status: 'queued',
    queue,
    enqueued_at: String(enqueuedAt),
    tenant_id: tenantId,
    request_id: requestId,
  });
  multi.expire(_keyJob(jobId), RESULT_TTL_SECONDS);
  multi.xAdd(stream, '*', { data: JSON.stringify(job) });
  await multi.exec();
  // eslint-disable-next-line no-console
  console.log(
    `[workerQueue] enqueued job ${jobId} (${handler}) on stream '${queue}'`
  );
  return jobId;
}

async function disconnect() {
  if (_client && _client.isReady) {
    await _client.quit();
  }
  _client = null;
}

module.exports = {
  enqueue,
  disconnect,
};
