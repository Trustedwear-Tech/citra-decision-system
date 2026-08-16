// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

// DEPRECATED: This was an Azure Timer Trigger function for SQL Database
// Now using MongoDB with scheduled jobs in src/jobs/

const handler = async (myTimer, context) => {
    console.warn('time-trigger-expirydate is deprecated. Use MongoDB scheduled jobs instead.');
    return {
        status: 410,
        body: JSON.stringify({ 
            error: 'This endpoint is deprecated. Subscription expiry is now handled by MongoDB scheduled jobs.' 
        })
    };
};

module.exports = handler;



