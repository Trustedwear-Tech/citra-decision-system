// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

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



