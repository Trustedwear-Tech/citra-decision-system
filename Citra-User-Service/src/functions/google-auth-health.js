// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const { connectToMongoDB, getConnectionStatus } = require('../config/mongodb');
const envConfig = require('../config/environment');

/**
 * Health Check Handler for Google Authentication System
 */
const healthCheckHandler = async (request, context) => {
  try {
    const healthStatus = {
      timestamp: new Date().toISOString(),
      service: 'Google Authentication Service',
      status: 'healthy',
      checks: {}
    };

    // Check environment variables
    healthStatus.checks.environment = {
      status: 'healthy',
      details: {
        googleClientIds: envConfig.googleClientIds ? envConfig.googleClientIds.length : 0,
        supportedPlatforms: envConfig.googleClientIds ? ['web'] : [],
        jwtSecret: !!envConfig.jwtSecret,
        mongodbConnectionString: !!envConfig.mongodbConnectionString
      }
    };

    // Check MongoDB connection
    if (envConfig.mongodbConnectionString) {
      try {
        await connectToMongoDB();
        const mongoStatus = getConnectionStatus();
        healthStatus.checks.mongodb = {
          status: mongoStatus.isConnected ? 'healthy' : 'unhealthy',
          details: {
            connected: mongoStatus.isConnected,
            readyState: mongoStatus.states[mongoStatus.readyState],
            readyStateCode: mongoStatus.readyState
          }
        };
      } catch (mongoError) {
        healthStatus.checks.mongodb = {
          status: 'unhealthy',
          error: mongoError.message
        };
        healthStatus.status = 'degraded';
      }
    } else {
      healthStatus.checks.mongodb = {
        status: 'not_configured',
        details: 'MongoDB connection string not provided'
      };
    }

    // Check Google Auth configuration
    const clientIds = envConfig.googleClientIds || [];
    
    if (clientIds.length === 0) {
      healthStatus.checks.googleAuth = {
        status: 'not_configured',
        error: 'No Google Client IDs configured'
      };
      healthStatus.status = 'degraded';
    } else {
      healthStatus.checks.googleAuth = {
        status: 'healthy',
        details: {
          message: 'Google Client IDs configured',
          clientIdCount: clientIds.length,
          platforms: ['web', 'ios', 'android'].slice(0, clientIds.length)
        }
      };
    }

    // Check JWT configuration
    if (!envConfig.jwtSecret) {
      healthStatus.checks.jwt = {
        status: 'not_configured',
        error: 'JWT secret not configured'
      };
      healthStatus.status = 'degraded';
    } else {
      healthStatus.checks.jwt = {
        status: 'healthy',
        details: 'JWT secret configured'
      };
    }

    // Determine overall status
    const unhealthyChecks = Object.values(healthStatus.checks).filter(
      check => check.status === 'unhealthy'
    );
    
    if (unhealthyChecks.length > 0) {
      healthStatus.status = 'unhealthy';
    } else {
      const degradedChecks = Object.values(healthStatus.checks).filter(
        check => check.status === 'not_configured'
      );
      
      if (degradedChecks.length > 0) {
        healthStatus.status = 'degraded';
      }
    }

    if (context) {
      console.log(`Health check completed: ${healthStatus.status}`);
    }

    const statusCode = healthStatus.status === 'healthy' ? 200 : 
                      healthStatus.status === 'degraded' ? 200 : 503;

    return {
      status: statusCode,
      body: JSON.stringify(healthStatus, null, 2),
      headers: { 'Content-Type': 'application/json' }
    };

  } catch (error) {
    if (context) {
      console.error('Health check error:', error);
    }

    return {
      status: 500,
      body: JSON.stringify({
        timestamp: new Date().toISOString(),
        service: 'Google Authentication Service',
        status: 'unhealthy',
        error: error.message
      }, null, 2),
      headers: { 'Content-Type': 'application/json' }
    };
  }
};

module.exports = {
  healthCheckHandler
};




