// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const mongoose = require('mongoose');

/**
 * UserUsage Model - Tracks credit balance and usage for pay-as-you-go billing
 * Replaces subscription-based billing with credit-based system
 */
const UserUsageSchema = new mongoose.Schema({
  user_id: { 
    type: String, 
    required: true, 
    unique: true, 
    index: true 
  },
  email: { 
    type: String, 
    required: true, 
    index: true 
  },
  
  // Credit Balance
  // NOTE: No min:0 constraint — the Python service intentionally allows negative balances
  // (new-user grace period, race-condition buffer). Mongoose min:0 would block .save() on
  // any document the Python service has driven negative via atomic $inc.
  credit_balance: { 
    type: Number, 
    default: 0
  },
  total_credits_purchased: { 
    type: Number, 
    default: 0 
  },
  total_credits_consumed: { 
    type: Number, 
    default: 0 
  },
  
  // Query Usage Tracking
  query_usage: {
    total_queries: { type: Number, default: 0 },
    total_tokens_used: { type: Number, default: 0 },
    llm_tokens: { type: Number, default: 0 },
    cached_tokens: { type: Number, default: 0 },
    total_cost: { type: Number, default: 0 }
  },
  
  // File Upload Usage Tracking
  file_upload_usage: {
    total_files: { type: Number, default: 0 },
    total_size_mb: { type: Number, default: 0 },
    documents: { 
      count: { type: Number, default: 0 }, 
      size_mb: { type: Number, default: 0 }, 
      cost: { type: Number, default: 0 } 
    },
    audio: { 
      count: { type: Number, default: 0 }, 
      size_mb: { type: Number, default: 0 }, 
      cost: { type: Number, default: 0 } 
    },
    video: { 
      count: { type: Number, default: 0 }, 
      size_mb: { type: Number, default: 0 }, 
      cost: { type: Number, default: 0 } 
    },
    images: { 
      count: { type: Number, default: 0 }, 
      size_mb: { type: Number, default: 0 }, 
      cost: { type: Number, default: 0 } 
    },
    total_cost: { type: Number, default: 0 }
  },
  
  // Storage Tracking (for daily billing)
  aws_storage_bytes: {
    type: Number,
    default: 0,
    min: 0,
    description: "AWS S3 file storage in bytes (audio/video files). Updated on upload/delete."
  },
  database_storage_bytes: {
    type: Number,
    default: 0,
    min: 0,
    description: "MongoDB text storage in bytes (transcripts, extracted text). Calculated as character_count * 3 (UTF-8 avg). Updated on upload/delete."
  },
  
  // Recent Transactions (embedded for quick access - last 20)
  recent_transactions: [{
    transaction_id: String,
    type: { 
      type: String, 
      enum: ['credit_purchase', 'query_debit', 'upload_debit', 'refund', 'bonus'] 
    },
    amount: Number,              // Positive for credit, negative for debit
    balance_after: Number,
    timestamp: { type: Date, default: Date.now },
    metadata: mongoose.Schema.Types.Mixed
  }],
  
  // Last reset date for usage statistics
  last_reset_date: { type: Date, default: Date.now },
  
  created_at: { type: Date, default: Date.now },
  updated_at: { type: Date, default: Date.now }
}, {
  timestamps: { createdAt: 'created_at', updatedAt: 'updated_at' }
});

// Indexes for performance
// Note: user_id (unique:true) and email (index:true) are indexed at the field level above.
UserUsageSchema.index({ credit_balance: 1 });

// Instance Methods
UserUsageSchema.methods.hasSufficientCredits = function(requiredAmount) {
  return this.credit_balance >= requiredAmount;
};

UserUsageSchema.methods.addCredits = async function(amount, transactionId, razorpayData = {}) {
  // Atomic $inc — avoids stale-read race condition from read-modify-write + .save() pattern.
  // balance_after is estimated from the pre-read balance; exact value is in the returned doc.
  const estimatedNewBalance = this.credit_balance + amount;
  const transaction = {
    transaction_id: transactionId,
    type: 'credit_purchase',
    amount: amount,
    balance_after: estimatedNewBalance,
    timestamp: new Date(),
    metadata: razorpayData
  };

  const updated = await this.constructor.findOneAndUpdate(
    { _id: this._id },
    {
      $inc: {
        credit_balance: amount,           // atomic float addition
        total_credits_purchased: amount
      },
      $push: {
        recent_transactions: {
          $each: [transaction],
          $position: 0,
          $slice: 20  // keep last 20
        }
      },
      $set: { updated_at: new Date() }
    },
    { new: true, runValidators: false }  // runValidators:false — Python may have driven balance negative
  );

  // Sync instance state from DB result
  this.credit_balance = updated.credit_balance;
  this.total_credits_purchased = updated.total_credits_purchased;
  this.recent_transactions = updated.recent_transactions;
  this.updated_at = updated.updated_at;

  return this;
};

UserUsageSchema.methods.deductCredits = async function(amount, transactionType, metadata = {}) {
  if (!this.hasSufficientCredits(amount)) {
    throw new Error('Insufficient credits');
  }

  // NOTE: We use $inc for credit_balance and total_credits_consumed to avoid
  // stale-read race conditions. Other analytics fields ($push, sub-doc $inc) are
  // updated in the same atomic operation where possible.
  this.credit_balance -= amount;  // keep in-memory state in sync for balance_after below

  // Add to recent transactions
  const transaction = {
    transaction_id: metadata.transaction_id || `tx_${Date.now()}`,
    type: transactionType,
    amount: -amount,
    balance_after: this.credit_balance,
    timestamp: new Date(),
    metadata: metadata
  };
  
  // Build atomic update — $inc for monetary fields, $push for transaction log
  const incUpdate = {
    credit_balance: -amount,
    total_credits_consumed: amount
  };
  if (transactionType === 'query_debit') {
    incUpdate['query_usage.total_queries'] = 1;
    incUpdate['query_usage.total_tokens_used'] = (metadata.tokens_used || 0);
    incUpdate['query_usage.total_cost'] = amount;
    incUpdate['query_usage.llm_tokens'] = (metadata.tokens_used || 0);
    if (metadata.cached_tokens)           incUpdate['query_usage.cached_tokens']  = metadata.cached_tokens;
  } else if (transactionType === 'upload_debit') {
    incUpdate['file_upload_usage.total_files'] = 1;
    incUpdate['file_upload_usage.total_size_mb'] = (metadata.file_size_mb || 0);
    incUpdate['file_upload_usage.total_cost'] = amount;
    const fileType = metadata.file_type || 'documents';
    incUpdate[`file_upload_usage.${fileType}.count`] = 1;
    incUpdate[`file_upload_usage.${fileType}.size_mb`] = (metadata.file_size_mb || 0);
    incUpdate[`file_upload_usage.${fileType}.cost`] = amount;
  }

  const updated = await this.constructor.findOneAndUpdate(
    { _id: this._id },
    {
      $inc: incUpdate,
      $push: {
        recent_transactions: {
          $each: [transaction],
          $position: 0,
          $slice: 20
        }
      },
      $set: { updated_at: new Date() }
    },
    { new: true, runValidators: false }  // runValidators:false — balance may legitimately be negative
  );

  // Sync instance state from DB result
  this.credit_balance = updated.credit_balance;
  this.recent_transactions = updated.recent_transactions;
  this.updated_at = updated.updated_at;

  return this;
};

// Static Methods
UserUsageSchema.statics.getOrCreateUsage = async function(user_id, email) {
  let usage = await this.findOne({ user_id });
  
  if (!usage) {
    usage = await this.create({
      user_id,
      email,
      credit_balance: 999999999,
      total_credits_purchased: 0,
      total_credits_consumed: 0
    });
  }
  
  return usage;
};

UserUsageSchema.statics.initializeNewUser = async function(user_id, email, welcomeBonus = 0) {
  const usage = await this.create({
    user_id,
    email,
    credit_balance: welcomeBonus,
    total_credits_purchased: welcomeBonus === 999999999 ? 0 : welcomeBonus,
    recent_transactions: welcomeBonus > 0 ? [{
      transaction_id: `welcome_${Date.now()}`,
      type: 'bonus',
      amount: welcomeBonus,
      balance_after: welcomeBonus,
      timestamp: new Date(),
      notes: 'Welcome bonus',
      metadata: { reason: 'Welcome bonus' }
    }] : []
  });
  
  return usage;
};

module.exports = mongoose.model('UserUsage', UserUsageSchema);
