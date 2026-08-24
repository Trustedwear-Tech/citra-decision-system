// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const mongoose = require('mongoose');
const envConfig = require('./environment');

let isConnected = false;

const connectToMongoDB = async () => {
  if (isConnected) {
    console.log('MongoDB already connected');
    return;
  }

  try {
  // Check for MongoDB connection string dynamically
  // Accept multiple env var names for compatibility
  let mongodbConnectionString = process.env.MONGODB_CONNECTION_STRING || process.env.MONGODB_CONN_STRING || process.env.MONGODB_URI || envConfig.mongodbConnectionString || envConfig.mongodbConnString;
  
  // Get database name from environment (separate from connection string)
  const databaseName = process.env.MONGODB_DATABASE || envConfig.mongodbDatabase || 'citra-ai';

    if (!mongodbConnectionString) {
      // MongoDB connection string required - fail fast instead of fallback
      const error = new Error('MongoDB connection string is required. Please configure MONGODB_CONNECTION_STRING environment variable.');
      console.error('MongoDB configuration error:', error.message);
      throw error;
    }

    console.log('Using MongoDB connection string:', mongodbConnectionString ? '[configured]' : '[missing]');
    console.log('Target database name:', databaseName);

    // Set DNS resolution to use custom DNS servers (Google DNS) for MongoDB Atlas
    const dns = require('dns');
    dns.setServers(['8.8.8.8', '8.8.4.4', '1.1.1.1']); // Google DNS and Cloudflare DNS
    console.log('🔧 DNS servers configured:', dns.getServers().slice(0, 3));

    const connection = await mongoose.connect(mongodbConnectionString, {
      dbName: databaseName,
      serverSelectionTimeoutMS: parseInt(process.env.MONGODB_SERVER_SELECTION_TIMEOUT_MS) || 15000, // 15s — allows recovery during Atlas replica set elections (10-15s)
      socketTimeoutMS: parseInt(process.env.MONGODB_SOCKET_TIMEOUT_MS) || 45000,
      maxPoolSize: parseInt(process.env.MONGODB_MAX_POOL_SIZE) || 10,
      minPoolSize: parseInt(process.env.MONGODB_MIN_POOL_SIZE) || 1, // Keep warm connections to avoid cold-start latency
      maxIdleTimeMS: parseInt(process.env.MONGODB_MAX_IDLE_TIME_MS) || 60000, // 1 min — avoids stale connections behind cloud proxies
      heartbeatFrequencyMS: parseInt(process.env.MONGODB_HEARTBEAT_FREQUENCY_MS) || 10000,
      bufferCommands: false,
      family: 4,
      retryReads: true, // Explicitly enable read retries for resilience
      compressors: ['zstd', 'snappy'], // Network compression for WAN/Atlas
    });

    isConnected = true;
    console.log('Connected to MongoDB successfully');
    
    // Handle connection events
    mongoose.connection.on('error', (err) => {
      console.error('MongoDB connection error:', err);
      isConnected = false;
    });

    mongoose.connection.on('disconnected', () => {
      console.log('MongoDB disconnected');
      isConnected = false;
    });

    mongoose.connection.on('reconnected', () => {
      console.log('MongoDB reconnected');
      isConnected = true;
    });

    return connection;
  } catch (error) {
    console.error('Error connecting to MongoDB:', error);
    isConnected = false;
    throw error;
  }
};

const disconnectFromMongoDB = async () => {
  if (!isConnected) {
    console.log('MongoDB not connected');
    return;
  }

  try {
    await mongoose.disconnect();
    isConnected = false;
    console.log('Disconnected from MongoDB');
  } catch (error) {
    console.error('Error disconnecting from MongoDB:', error);
    throw error;
  }
};

const getConnectionStatus = () => {
  return {
    isConnected,
    readyState: mongoose.connection.readyState,
    databaseName: mongoose.connection.db ? mongoose.connection.db.databaseName : 'unknown',
    host: mongoose.connection.host,
    states: {
      0: 'disconnected',
      1: 'connected',
      2: 'connecting',
      3: 'disconnecting'
    }
  };
};

module.exports = {
  connectToMongoDB,
  disconnectFromMongoDB,
  getConnectionStatus,
  mongoose
};
