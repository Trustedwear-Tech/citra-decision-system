// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * apiError.js — turn a non-OK FastAPI response into a rich Error.
 *
 * Shared by every client of a FastAPI backend (WorkflowService, DiscoveryService,
 * …) so they all render `detail` the same way. FastAPI puts three different
 * shapes in that field, and only the string case is obvious:
 *
 *   {"detail": "some message"}                        -> plain message
 *   {"detail": [{loc, msg, type}, ...]}               -> 422 request validation
 *   {"detail": {error, message, violations:[...]}}    -> our own rich errors,
 *       e.g. 400 dept_source_contract_violation / 403 dept_scope_authz_violation
 *
 * Passing the array/object shapes straight to `new Error()` renders
 * "[object Object]", which is why this lives in one place.
 */

/**
 * @param {Response} resp    the non-OK fetch response
 * @param {string}   fallback message to use when the body carries no usable detail
 * @returns {Promise<Error>} Error with `.status` and `.detail` attached
 */
export async function buildApiError(resp, fallback) {
  let body = null;
  try { body = await resp.json(); } catch { /* non-JSON body */ }
  const detail = body && body.detail;
  let message = fallback;
  if (typeof detail === 'string') {
    message = detail;
  } else if (Array.isArray(detail)) {
    // FastAPI request-validation errors: [{ loc: [...], msg, type }, ...].
    // Render each as "field: message" so the user sees something readable
    // instead of "[object Object]".
    const lines = detail.map((e) => {
      if (!e || typeof e !== 'object') return String(e);
      const loc = Array.isArray(e.loc)
        ? e.loc.filter((p) => p !== 'body').join('.')
        : '';
      const msg = e.msg || 'Invalid value';
      return loc ? `${loc}: ${msg}` : msg;
    });
    message = lines.length ? lines.join('\n• ') : fallback;
  } else if (detail && typeof detail === 'object') {
    message = detail.message || detail.error || fallback;
    if (Array.isArray(detail.violations) && detail.violations.length) {
      message += '\n\n• ' + detail.violations.join('\n• ');
    }
  }
  const err = new Error(message);
  err.status = resp.status;
  err.detail = detail;
  return err;
}

export default buildApiError;
