// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const Coupon = require('../models/Coupon');

/**
 * Validate a coupon code
 * @route POST /api/coupons/validate
 */
const handler = async (req, res) => {
  try {
    const { coupon_code } = req.body;

    if (!coupon_code) {
      return res.status(400).json({
        success: false,
        error: 'Coupon code is required'
      });
    }

    // Find and validate coupon
    const coupon = await Coupon.findValidCoupon(coupon_code);

    if (!coupon) {
      // Check if coupon exists but is invalid
      const existingCoupon = await Coupon.findOne({ 
        coupon_code: coupon_code.toUpperCase() 
      });

      if (existingCoupon) {
        const now = new Date();
        
        if (!existingCoupon.is_active) {
          return res.status(400).json({
            success: false,
            error: 'This coupon has been deactivated',
            errorCode: 'COUPON_INACTIVE'
          });
        }
        
        if (existingCoupon.expiry_date <= now) {
          return res.status(400).json({
            success: false,
            error: 'This coupon has expired',
            errorCode: 'COUPON_EXPIRED'
          });
        }
        
        if (existingCoupon.max_uses !== null && 
            existingCoupon.current_uses >= existingCoupon.max_uses) {
          return res.status(400).json({
            success: false,
            error: 'This coupon has reached its usage limit',
            errorCode: 'COUPON_LIMIT_REACHED'
          });
        }
      }

      return res.status(404).json({
        success: false,
        error: 'Invalid coupon code',
        errorCode: 'COUPON_NOT_FOUND'
      });
    }

    console.log('Valid coupon found:', coupon.coupon_code);

    return res.status(200).json({
      success: true,
      message: 'Coupon is valid',
      coupon: {
        code: coupon.coupon_code,
        name: coupon.coupon_name,
        credit_amount: coupon.credit_amount,
        expiry_date: coupon.expiry_date,
        description: coupon.description
      }
    });

  } catch (error) {
    console.log('Error validating coupon:', error);
    
    return res.status(500).json({
      success: false,
      error: 'Failed to validate coupon'
    });
  }
};

module.exports = handler;




