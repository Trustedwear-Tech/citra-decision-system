// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const mongoose = require('mongoose');
const Coupon = require('../models/Coupon');
const envConfig = require('../config/environment');

/**
 * Create a new coupon (admin function)
 * @route POST /api/coupons/create
 * @access Admin only (requireAdmin middleware applied in router)
 */
const handler = async (req, res) => {
  try {
    const { 
      coupon_name, 
      coupon_code, 
      expiry_date, 
      validity_days,
      credit_amount,
      max_uses,
      description 
    } = req.body;

    if (!coupon_name || !coupon_code || !expiry_date) {
      return res.status(400).json({
        success: false,
        error: 'coupon_name, coupon_code, and expiry_date are required'
      });
    }

    // Check if coupon code already exists
    const existingCoupon = await Coupon.findOne({ 
      coupon_code: coupon_code.toUpperCase() 
    });

    if (existingCoupon) {
      return res.status(400).json({
        success: false,
        error: 'A coupon with this code already exists',
        errorCode: 'DUPLICATE_COUPON_CODE'
      });
    }

    // Create the coupon
    const coupon = await Coupon.createCoupon({
      coupon_name,
      coupon_code,
      expiry_date: new Date(expiry_date),
      validity_days: validity_days ? parseInt(validity_days) : null,
      credit_amount: credit_amount ? parseInt(credit_amount) : null,
      max_uses: max_uses ? parseInt(max_uses) : null,
      description: description || ''
    });

    console.log('Coupon created successfully:', coupon.coupon_code);

    return res.status(201).json({
      success: true,
      message: 'Coupon created successfully',
      coupon: {
        id: coupon._id,
        name: coupon.coupon_name,
        code: coupon.coupon_code,
        expiry_date: coupon.expiry_date,
        validity_days: coupon.validity_days,
        credit_amount: coupon.credit_amount,
        max_uses: coupon.max_uses,
        current_uses: coupon.current_uses,
        description: coupon.description,
        is_active: coupon.is_active
      }
    });

  } catch (error) {
    console.log('Error creating coupon:', error);
    
    return res.status(500).json({
      success: false,
      error: 'Failed to create coupon'
    });
  }
};

module.exports = handler;




