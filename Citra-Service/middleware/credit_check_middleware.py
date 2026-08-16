# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Centralized Credit Check Middleware for Citra Service

This middleware provides all credit-based billing operations directly in Python.
No external HTTP calls - all MongoDB operations are local.

Architecture:
- All usage tracking logic is in Python (Citra-Service)
- Node.js user service ONLY handles authentication and user profiles
- Direct MongoDB access for maximum performance
- No HTTP overhead, no network latency

Usage:
    from middleware.credit_check_middleware import (
        get_usage_service,
        check_user_credits,
        track_query_usage,
    )
    
    # Initialize service at startup
    usage_service = get_usage_service()
    usage_service.initialize_pricing_cache()
    
    # Check credits
    result = check_user_credits(user_id, estimated_cost)
    
    # Track usage
    track_query_usage(user_id, email, model, input_tokens, output_tokens)
"""

import os
import logging
from typing import Dict, Any, Optional
from citra_mongo import get_sync_database, MONGODB_DATABASE

# Import the synchronous usage tracking service
from services.usage_tracking_service import UsageTrackingService

# Configure logging
logger = logging.getLogger(__name__)

# Custom exception for insufficient credits
class InsufficientCreditsError(Exception):
    """Raised when user has insufficient credits for an operation"""
    def __init__(self, message="Insufficient credits", required=0, balance=0):
        self.message = message
        self.required = required
        self.balance = balance
        super().__init__(self.message)

# Global usage tracking service instance
_usage_service: Optional[UsageTrackingService] = None


def get_usage_service() -> UsageTrackingService:
    """Get or create the global UsageTrackingService instance (SYNC)"""
    global _usage_service
    if _usage_service is None:
        db = get_sync_database()
        _usage_service = UsageTrackingService(db)
        logger.info("✅ UsageTrackingService (sync) initialized")
    return _usage_service


async def get_active_pricing() -> Dict[str, Any]:
    """Get the active pricing configuration (delegates to UsageTrackingService)"""
    service = get_usage_service()
    return service.get_pricing()


def initialize_pricing_cache():
    """Initialize pricing cache at server startup"""
    try:
        logger.info("💰 Initializing pricing cache...")
        service = get_usage_service()
        pricing = service.initialize_pricing_cache()
        return pricing
    except Exception as e:
        logger.error(f"❌ Failed to initialize pricing cache: {e}")
        return None


def refresh_pricing_cache():
    """Force refresh pricing cache"""
    global _usage_service
    _usage_service = None
    logger.info("🔄 Pricing cache invalidated, will refresh on next request")
    return initialize_pricing_cache()


# ============ CREDIT CHECKING ============


def check_user_credits(user_id: str, estimated_cost: float) -> Dict[str, Any]:
    """
    License model: credit enforcement is disabled.
    Always returns sufficient so no request is ever blocked by credit balance.
    Usage is still tracked for consumption analytics.
    """
    return {
        'success': True,
        'sufficient': True,
        'balance': 999999999,
        'shortfall': 0,
        'message': 'Unlimited (license model)'
    }


# ============ USAGE TRACKING ============


def track_query_usage(
    user_id: str,
    email: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    query_id: Optional[str] = None,
    internet_grounding_cost: float = 0.0
) -> Dict[str, Any]:
    """
    Track query usage and deduct credits.
    
    Args:
        user_id: User's unique identifier
        email: User's email
        model: Model name used
        input_tokens: Input tokens consumed (includes cached tokens)
        output_tokens: Output tokens consumed
        cached_tokens: Cached tokens used (subset of input_tokens)
        query_id: Optional query identifier
        internet_grounding_cost: Cost for internet grounding (auto-detected from citations)
        
    Returns:
        Dictionary with transaction result
    """
    service = get_usage_service()
    return service.track_query_usage(
        user_id=user_id,
        email=email,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        query_id=query_id,
        internet_grounding_cost=internet_grounding_cost
    )


# ============ COST ESTIMATION ============


def estimate_query_cost(input_tokens: int, output_tokens: int, model: str = 'llm') -> float:
    """
    Estimate query cost based on pricing.
    
    Args:
        input_tokens: Estimated input tokens
        output_tokens: Estimated output tokens
        model: AI model name
        
    Returns:
        Estimated cost in credits
    """
    service = get_usage_service()
    cost_details = service.calculate_query_cost(model, input_tokens, output_tokens)
    return cost_details['cost']


# ============ FASTAPI DECORATOR ============


def require_credits(estimated_cost: float):
    """
    Decorator for FastAPI endpoints to check credits before processing.
    
    Usage:
        @app.post("/api/query")
        @require_credits(estimated_cost=5.0)
        async def query_endpoint(request: Request):
            # This will only execute if user has sufficient credits
            pass
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # Extract request from args/kwargs
            request = kwargs.get('request') or (args[0] if args else None)
            
            if not request:
                raise ValueError("Request object not found in function arguments")
            
            # Get user_id from request
            from citra_auth import get_secure_user_id
            user_id = get_secure_user_id(request)
            
            if not user_id:
                from fastapi import HTTPException
                raise HTTPException(status_code=401, detail="Authentication required")
            
            # Check credits
            credit_result = check_user_credits(user_id, estimated_cost)
            
            if not credit_result['success'] or not credit_result.get('sufficient', False):
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=402,
                    detail={
                        'code': 'INSUFFICIENT_CREDITS',
                        'balance': credit_result['balance'],
                        'required': credit_result['required'],
                        'shortfall': credit_result.get('shortfall', 0),
                        'message': credit_result['message']
                    }
                )
            
            # Credits are sufficient, proceed
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator
