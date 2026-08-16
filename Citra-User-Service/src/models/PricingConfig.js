// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const mongoose = require('mongoose');

/**
 * PricingConfig Model - Stores pricing configuration for pay-as-you-go billing
 * Allows dynamic pricing updates without code changes
 */
const PricingConfigSchema = new mongoose.Schema({
  version: {
    type: Number,
    required: true,
    unique: true
  },
  is_active: {  // Changed from 'active' to 'is_active' for consistency
    type: Boolean,
    default: false
  },
  active: {  // Keep for backward compatibility
    type: Boolean,
    default: false
  },

  // Token Pricing (per 1000 tokens) — single default tier, model-agnostic
  token_pricing: {
    default: {
      input_per_1k: { type: Number, default: 0.05 },
      output_per_1k: { type: Number, default: 0.10 },
      cached_per_1k: { type: Number, default: 0.02 },
      internet_grounding_price: { type: Number, default: 1.0 },
      image_generation_price: { type: Number, default: 1.0 }
    },
    image_generation: {
      type: Map,
      of: new mongoose.Schema({
        image_generation_price: { type: Number, required: true, min: 0 }
      }, { _id: false })
    }
  },

  // Embedding Pricing (per 1000 tokens)
  embedding_pricing: {
    per_1k: { type: Number, default: 0.05 }
  },

  // Currency Configuration
  // User-facing unit is credits/tokens; no INR conversion is performed.
  currency_config: {},

  // Metadata
  description: String,
  effective_date: { type: Date, default: Date.now },
  created_by: String,

  created_at: { type: Date, default: Date.now },
  updated_at: { type: Date, default: Date.now }
}, {
  timestamps: { createdAt: 'created_at', updatedAt: 'updated_at' }
});

// Indexes
// Note: version is indexed via unique:true on the field definition above.
PricingConfigSchema.index({ is_active: 1 });
PricingConfigSchema.index({ active: 1 });  // Keep for backward compatibility
PricingConfigSchema.index({ effective_date: -1 });

// Static Methods
PricingConfigSchema.statics.getActivePricing = async function () {
  let pricing = await this.findOne({ $or: [{ is_active: true }, { active: true }] }).sort({ version: -1 });

  // If no active pricing found, create default
  if (!pricing) {
    pricing = await this.createDefaultPricing();
  }

  return pricing;
};

PricingConfigSchema.statics.createDefaultPricing = async function () {
  // Deactivate any existing active pricing
  await this.updateMany({ $or: [{ is_active: true }, { active: true }] }, { is_active: false, active: false });

  // Find the highest version number
  const latestPricing = await this.findOne().sort({ version: -1 });
  const newVersion = latestPricing ? latestPricing.version + 1 : 1;

  // Create default pricing
  const defaultPricing = await this.create({
    version: newVersion,
    is_active: true,
    active: true,  // Set both for backward compatibility
    token_pricing: {
      default: {
        input_per_1k: 0.05,        // 0.05 credits per 1K input tokens
        output_per_1k: 0.10,       // 0.10 credits per 1K output tokens
        cached_per_1k: 0.02,       // 0.02 credits per 1K cached tokens
        internet_grounding_price: 1.0,  // 1.0 credits per internet source
        image_generation_price: 1.0     // 1.0 credits per image
      },
      image_generation: {
        "default": { image_generation_price: 1.0 }  // 1.0 credits per image
      }
    },
    embedding_pricing: {
      per_1k: 0.05               // 0.05 credits per 1K embedding tokens
    },
    currency_config: {},
    description: 'Default pricing - model-agnostic, single tier',
    effective_date: new Date()
  });

  return defaultPricing;
};

PricingConfigSchema.statics.activateVersion = async function (version) {
  // Deactivate all
  await this.updateMany({}, { is_active: false, active: false });

  // Activate specified version
  const pricing = await this.findOneAndUpdate(
    { version },
    { is_active: true, active: true },
    { new: true }
  );

  if (!pricing) {
    throw new Error(`Pricing version ${version} not found`);
  }

  return pricing;
};

// Instance Methods
PricingConfigSchema.methods.calculateQueryCost = function (model, inputTokens, outputTokens, cachedTokens = 0) {
  // Single default tier — model name is for logging only, not rate selection
  const modelPricing = this.token_pricing.default;

  if (!modelPricing) {
    throw new Error('Default pricing tier not found in pricing config');
  }

  // Separate cached tokens from input tokens
  const regularInputTokens = inputTokens - cachedTokens;

  const inputCost = (regularInputTokens / 1000) * (modelPricing.input_per_1k || 0.05);
  const outputCost = (outputTokens / 1000) * (modelPricing.output_per_1k || 0.10);
  const cachedCost = (cachedTokens / 1000) * (modelPricing.cached_per_1k || 0.02);

  return Number((inputCost + outputCost + cachedCost).toFixed(4));
};

PricingConfigSchema.methods.calculateBonusCredits = function (amount) {
  // On-prem: no bonus tiers — return amount as-is
  return {
    bonus_percentage: 0,
    bonus_amount: 0,
    total_credits: amount
  };
};

module.exports = mongoose.model('PricingConfig', PricingConfigSchema);
