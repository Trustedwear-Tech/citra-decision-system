// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const mongoose = require('mongoose');

// Schema for Coupons
const couponSchema = new mongoose.Schema({
  coupon_name: {
    type: String,
    required: true,
    trim: true
  },
  coupon_code: {
    type: String,
    required: true,
    unique: true,
    uppercase: true,
    trim: true,
    index: true
  },
  expiry_date: {
    type: Date,
    required: true,
    index: true
  },
  validity_days: {
    type: Number,
    required: false,
    min: 1,
    default: null,
    description: 'Legacy field for time-based trials (deprecated for credit system)'
  },
  credit_amount: {
    type: Number,
    required: false,
    min: 0,
    default: 1000,
    description: 'Amount of credits to give when coupon is applied (in credits)'
  },
  is_active: {
    type: Boolean,
    default: true
  },
  max_uses: {
    type: Number,
    default: null // null means unlimited uses
  },
  current_uses: {
    type: Number,
    default: 0
  },
  coupon_type: {
    type: String,
    enum: ['standard', 'referral'],
    default: 'standard',
    index: true
  },
  referrer_email: {
    type: String,
    default: null,
    description: 'Email of the user who owns this referral coupon (only for referral type)'
  },
  description: {
    type: String,
    required: false
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
  collection: 'coupons',
  timestamps: true
});

// Pre-save middleware to update the updatedAt field
couponSchema.pre('save', function(next) {
  this.updatedAt = Date.now();
  next();
});

// Instance method to check if coupon is valid
couponSchema.methods.isValid = function() {
  const now = new Date();
  const isNotExpired = this.expiry_date > now;
  const isActive = this.is_active === true;
  const hasUsesLeft = this.max_uses === null || this.current_uses < this.max_uses;
  
  return isNotExpired && isActive && hasUsesLeft;
};

// Instance method to increment usage count (atomic to prevent race conditions)
// Guards against exceeding max_uses even under concurrent requests
couponSchema.methods.incrementUsage = async function() {
  const query = { _id: this._id };
  if (this.max_uses !== null) {
    query.current_uses = { $lt: this.max_uses };
  }
  const result = await mongoose.model('Coupon').findOneAndUpdate(
    query,
    { $inc: { current_uses: 1 } },
    { new: true }
  );
  if (result) this.current_uses = result.current_uses;
  return result;
};

// Static method to find valid coupon by code
couponSchema.statics.findValidCoupon = async function(couponCode) {
  const coupon = await this.findOne({ 
    coupon_code: couponCode.toUpperCase(),
    is_active: true
  });
  
  if (!coupon) {
    return null;
  }
  
  return coupon.isValid() ? coupon : null;
};

// Static method to create a new coupon
couponSchema.statics.createCoupon = async function(couponData) {
  const coupon = new this({
    coupon_name: couponData.coupon_name,
    coupon_code: couponData.coupon_code.toUpperCase(),
    expiry_date: couponData.expiry_date,
    validity_days: couponData.validity_days || null,
    credit_amount: couponData.credit_amount || 1000,
    max_uses: couponData.max_uses || null,
    description: couponData.description || '',
    coupon_type: couponData.coupon_type || 'standard',
    referrer_email: couponData.referrer_email || null
  });
  
  return coupon.save();
};

const Coupon = mongoose.model('Coupon', couponSchema);

module.exports = Coupon;
