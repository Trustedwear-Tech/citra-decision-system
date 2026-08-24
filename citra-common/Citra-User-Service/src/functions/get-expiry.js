// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const handler = async (request) => {
    try {
      const { deviceId } = await request.json();

      if (!deviceId) {
        return {
          body: JSON.stringify({ success: false, error: "Missing required field (deviceId)." }),
          status: 400,
        };
      }

      const query = `
        SELECT Expiry_Date 
        FROM SUBSCRIPTION 
        WHERE user_id = ?
      `;

      const results = await executeQuery( query, [deviceId]);

      console.log("Database results:", results);

      if (results.length > 0) {
        const expiryDate = results[0].Expiry_Date;
        console.log("Fetched Expiry_Date:", expiryDate);

        return {
          body: JSON.stringify({
            success: true,
            expiryDate: expiryDate || null,
          }),
          status: 200,
        };
      } else {
        return {
          body: JSON.stringify({
            success: true,
            expiryDate: null,
          }),
          status: 200,
        };
      }
    } catch (error) {
      console.error("Error in get-expiry-date:", error);
      return {
        body: JSON.stringify({
          success: false,
          error: "An error occurred while processing your request.",
        }),
        status: 500,
      };
    }
  };

module.exports = handler;



