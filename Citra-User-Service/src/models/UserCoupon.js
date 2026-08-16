// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const mongoose = require('mongoose');

// Schema for User-Coupon relationship
const userCouponSchema = new mongoose.Schema({
  user_id: {
    type: mongoose.Schema.Types.ObjectId,
    ref: 'CitraAIUser',
    required: true,
    index: true
  },
  email: {
    type: String,
    required: true,
    index: true
  },
  coupon_code: {
    type: String,
    required: true,
    uppercase: true,
    trim: true,
    index: true
  },
  is_used: {
    type: Boolean,
    default: true
  },
  used_at: {
    type: Date,
    default: Date.now
  },
  trial_start_date: {
    type: Date,
    required: true
  },
  trial_expiry_date: {
    type: Date,
    required: true
  },
  validity_days: {
    type: Number,
    required: true
  },
  createdAt: {
    type: Date,
    default: Date.now
  },
  updatedAt: {
    type: Date,
    default: Date.now
  }
}, {
  collection: 'user_coupons',
  timestamps: true
});

// Compound index to ensure a user can only use a specific coupon once
userCouponSchema.index({ user_id: 1, coupon_code: 1 }, { unique: true });
userCouponSchema.index({ email: 1, coupon_code: 1 });

// Pre-save middleware to update the updatedAt field
userCouponSchema.pre('save', function(next) {
  this.updatedAt = Date.now();
  next();
});

// Static method to check if user has used a specific coupon
userCouponSchema.statics.hasUserUsedCoupon = async function(userId, couponCode) {
  const usage = await this.findOne({
    user_id: userId,
    coupon_code: couponCode.toUpperCase(),
    is_used: true
  });
  
  return !!usage;
};

// Static method to check if email has used a specific coupon
userCouponSchema.statics.hasEmailUsedCoupon = async function(email, couponCode) {
  const usage = await this.findOne({
    email: email,
    coupon_code: couponCode.toUpperCase(),
    is_used: true
  });
  
  return !!usage;
};

// Static method to record coupon usage
userCouponSchema.statics.recordUsage = async function(userId, email, couponCode, validityDays) {
  const startDate = new Date();
  const expiryDate = new Date(startDate);
  expiryDate.setDate(expiryDate.getDate() + validityDays);
  
  const userCoupon = new this({
    user_id: userId,
    email: email,
    coupon_code: couponCode.toUpperCase(),
    is_used: true,
    used_at: startDate,
    trial_start_date: startDate,
    trial_expiry_date: expiryDate,
    validity_days: validityDays
  });
  
  return userCoupon.save();
};

// Static method to get user's coupon history
userCouponSchema.statics.getUserCouponHistory = async function(userId) {
  return this.find({ user_id: userId }).sort({ used_at: -1 });
};

const UserCoupon = mongoose.model('UserCoupon', userCouponSchema);

module.exports = UserCoupon;
