// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const mongoose = require('mongoose');

/**
 * CreditTransaction Model - Detailed transaction history for auditing
 * Separate from UserUsage for detailed logging and reporting
 */
const CreditTransactionSchema = new mongoose.Schema({
  transaction_id: { 
    type: String, 
    required: true, 
    unique: true,
    index: true
  },
  user_id: { 
    type: String, 
    required: true,
    index: true
  },
  email: { 
    type: String, 
    required: true,
    index: true
  },
  
  // Transaction Details
  type: { 
    type: String, 
    required: true,
    enum: ['purchase', 'query_usage', 'upload_usage', 'refund', 'bonus', 'referral_bonus', 'adjustment'],
    index: true
  },
  amount: { 
    type: Number, 
    required: true 
  },
  balance_before: { 
    type: Number, 
    required: true 
  },
  balance_after: { 
    type: Number, 
    required: true 
  },
  
  // Payment source and currency metadata
  source: {
    type: String,
    enum: ['google_play', 'app_store', 'admin', 'system'],
    default: null
  },
  currency: { type: String, default: 'USD' },  // Original payment currency (USD)
  original_amount: Number,          // Amount in original currency before conversion
  exchange_rate: Number,            // Legacy/unused: no INR conversion is performed (payments are USD/credits)
  bonus_amount: Number,             // Bonus credits granted
  bonus_percentage: Number,         // Bonus tier percentage applied
  
  // Usage-specific fields
  query_metadata: {
    tokens_used: Number,
    input_tokens: Number,
    output_tokens: Number,
    model: String,
    cost_per_token: Number,
    query_id: String
  },
  
  upload_metadata: {
    file_id: String,
    filename: String,
    file_size_bytes: Number,
    file_size_mb: Number,
    file_type: String,
    cost_per_mb: Number
  },
  
  // Additional metadata
  notes: String,
  ip_address: String,
  user_agent: String,
  
  timestamp: { type: Date, default: Date.now, index: true },
  created_at: { type: Date, default: Date.now }
}, {
  timestamps: { createdAt: 'created_at', updatedAt: false }
});

// Compound indexes for queries
CreditTransactionSchema.index({ user_id: 1, timestamp: -1 });
CreditTransactionSchema.index({ user_id: 1, type: 1, timestamp: -1 });
CreditTransactionSchema.index({ type: 1, timestamp: -1 });

// Static Methods
CreditTransactionSchema.statics.createPurchaseTransaction = async function(data) {
  const transaction = await this.create({
    transaction_id: data.transaction_id || `purchase_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
    user_id: data.user_id,
    email: data.email,
    type: 'purchase',
    amount: data.amount,
    balance_before: data.balance_before,
    balance_after: data.balance_after,
    source: data.source || null,
    currency: data.currency || 'USD',
    original_amount: data.original_amount || null,
    exchange_rate: data.exchange_rate || null,
    bonus_amount: data.bonus_amount || null,
    bonus_percentage: data.bonus_percentage || null,
    ip_address: data.ip_address,
    user_agent: data.user_agent,
    timestamp: new Date()
  });
  
  return transaction;
};

CreditTransactionSchema.statics.createQueryTransaction = async function(data) {
  const transaction = await this.create({
    transaction_id: data.transaction_id || `query_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
    user_id: data.user_id,
    email: data.email,
    type: 'query_usage',
    amount: -data.amount, // Negative for debit
    balance_before: data.balance_before,
    balance_after: data.balance_after,
    query_metadata: {
      tokens_used: data.tokens_used,
      input_tokens: data.input_tokens,
      output_tokens: data.output_tokens,
      model: data.model,
      cost_per_token: data.cost_per_token,
      query_id: data.query_id
    },
    timestamp: new Date()
  });
  
  return transaction;
};

CreditTransactionSchema.statics.createUploadTransaction = async function(data) {
  const transaction = await this.create({
    transaction_id: data.transaction_id || `upload_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
    user_id: data.user_id,
    email: data.email,
    type: 'upload_usage',
    amount: -data.amount, // Negative for debit
    balance_before: data.balance_before,
    balance_after: data.balance_after,
    upload_metadata: {
      file_id: data.file_id,
      filename: data.filename,
      file_size_bytes: data.file_size_bytes,
      file_size_mb: data.file_size_mb,
      file_type: data.file_type,
      cost_per_mb: data.cost_per_mb
    },
    timestamp: new Date()
  });
  
  return transaction;
};

CreditTransactionSchema.statics.getUserTransactions = async function(user_id, options = {}) {
  const {
    type = null,
    limit = 50,
    skip = 0,
    startDate = null,
    endDate = null
  } = options;
  
  const query = { user_id };
  
  if (type) {
    query.type = type;
  }
  
  if (startDate || endDate) {
    query.timestamp = {};
    if (startDate) query.timestamp.$gte = new Date(startDate);
    if (endDate) query.timestamp.$lte = new Date(endDate);
  }
  
  const transactions = await this.find(query)
    .sort({ timestamp: -1 })
    .limit(limit)
    .skip(skip)
    .lean();
  
  const total = await this.countDocuments(query);
  
  return {
    transactions,
    total,
    limit,
    skip
  };
};

CreditTransactionSchema.statics.getUserTransactionsSummary = async function(user_id, startDate = null, endDate = null) {
  const matchStage = { user_id };
  
  if (startDate || endDate) {
    matchStage.timestamp = {};
    if (startDate) matchStage.timestamp.$gte = new Date(startDate);
    if (endDate) matchStage.timestamp.$lte = new Date(endDate);
  }
  
  const summary = await this.aggregate([
    { $match: matchStage },
    {
      $group: {
        _id: '$type',
        total_amount: { $sum: '$amount' },
        count: { $sum: 1 }
      }
    }
  ]);
  
  return summary;
};

module.exports = mongoose.model('CreditTransaction', CreditTransactionSchema);
