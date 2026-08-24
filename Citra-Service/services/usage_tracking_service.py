# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Usage Tracking Service - Credit-based billing operations (Synchronous)
Handles all credit checking, usage tracking, and transaction management.

This is a synchronous implementation using PyMongo, eliminating async/await complexity.
Works from any context: FastAPI endpoints, background threads, sync/async functions.
"""

import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from pymongo.database import Database
from citra_cache import get_cache_manager

logger = logging.getLogger(__name__)



class UsageTrackingService:
    """
    Manages credit-based billing for Citra Service (Synchronous).
    All operations work directly with MongoDB collections using PyMongo.
    """
    
    def __init__(self, db: Database):
        """
        Initialize the service with MongoDB database.
        
        Args:
            db: PyMongo Database instance (not cached - always get fresh from pool)
        """
        # Do NOT cache database or collection references
        # MongoDB manager handles connection pooling - always get fresh connections
        
        # Pricing cache (refreshed every ~8 hours)
        self._cached_pricing: Optional[Dict[str, Any]] = None
        self._last_pricing_load: Optional[datetime] = None
        self._cache_duration = timedelta(hours=8)
    
    
    def _get_db(self) -> Database:
        """
        Get fresh database connection from MongoDB manager's connection pool.
        This ensures we never use stale references after MongoDB client auto-reconnects.
        
        Returns:
            Fresh PyMongo Database instance
        """
        from citra_mongo import get_sync_database
        return get_sync_database()
    
    
    def initialize_pricing_cache(self) -> Dict[str, Any]:
        """
        Load pricing configuration from database into memory cache.
        Call this at service startup.
        
        Returns:
            Cached pricing configuration
        """
        try:
            logger.info('💰 Loading pricing configuration from database...')
            
            # Get fresh database connection from pool
            db = self._get_db()
            pricingconfigs = db['pricingconfigs']
            
            # Get active pricing config
            pricing = pricingconfigs.find_one(
                {'is_active': True},
                sort=[('version', -1)]
            )
            
            if not pricing:
                all_configs = list(pricingconfigs.find({}).limit(10))
                logger.error(f'🔍 Available configs: {[(c.get("version"), c.get("is_active"), c.get("active")) for c in all_configs]}')
                raise Exception("No active pricing configuration found in database")
            
            self._cached_pricing = pricing
            self._last_pricing_load = datetime.now()

            logger.info('✅ Pricing cache initialized:')
            logger.info(f'   Version: {pricing.get("version", "unknown")}')
            
            return self._cached_pricing
            
        except Exception as e:
            logger.error(f'❌ Failed to initialize pricing cache: {e}')
            raise
    
    
    def get_pricing(self) -> Dict[str, Any]:
        """
        Get cached pricing configuration, refreshing if stale.
        
        Returns:
            Pricing configuration dictionary
        """
        now = datetime.now()
        
        # Check if cache needs refresh
        if (not self._cached_pricing or 
            not self._last_pricing_load or 
            (now - self._last_pricing_load) > self._cache_duration):
            
            logger.info('🔄 Pricing cache stale or empty, refreshing...')
            self.initialize_pricing_cache()
        
        return self._cached_pricing
    
    
    def check_credits(self, user_id: str, required_amount: float = 0) -> Dict[str, Any]:
        """
        License model: always returns sufficient — no requests are blocked by balance.
        """
        return {
            'success': True,
            'sufficient': True,
            'balance': 999999999,
            'effective_balance': 999999999,
            'pending_buffer_cost': 0,
            'required': required_amount,
            'message': 'Unlimited (license model)'
        }

    def _check_credits_legacy(self, user_id: str, required_amount: float = 0) -> Dict[str, Any]:
        """Legacy DB-based check — not used in license model."""
        try:
            # Get fresh database connection to handle MongoDB client recreation
            from citra_mongo import get_sync_database
            db = get_sync_database()
            userusages = db['userusages']
            
            usage = userusages.find_one({'user_id': user_id})
            
            if not usage:
                # New user with no usage record — no purchased credits
                return {
                    'success': False,
                    'sufficient': False,
                    'balance': 0,
                    'effective_balance': 0,
                    'pending_buffer_cost': 0,
                    'required': required_amount,
                    'message': 'No credits found. Please purchase credits to continue.'
                }
            
            balance = usage.get('credit_balance', 0)
            
            # Check if balance covers the required operation
            if required_amount > 0:
                sufficient = balance >= required_amount
            else:
                sufficient = balance > 0  # Just check if positive when no specific cost given
            
            if sufficient:
                return {
                    'success': True,
                    'sufficient': True,
                    'balance': balance,
                    'effective_balance': balance,
                    'pending_buffer_cost': 0,
                    'required': required_amount,
                    'message': 'Sufficient balance - can proceed'
                }
            else:
                shortfall = max(0, required_amount - balance) if required_amount > 0 else abs(min(0, balance))
                return {
                    'success': False,
                    'sufficient': False,
                    'balance': balance,
                    'effective_balance': balance,
                    'pending_buffer_cost': 0,
                    'required': required_amount,
                    'shortfall': round(shortfall, 4),
                    'message': f'Insufficient token balance. Balance: {balance:.0f} tokens.'
                }
                
        except Exception as e:
            logger.error(f'Error checking credits: {e}')
            # Get balance by retrying with fresh connection
            try:
                from citra_mongo import get_sync_database
                db_retry = get_sync_database()
                userusages_retry = db_retry['userusages']
                usage_retry = userusages_retry.find_one({'user_id': user_id})
                if usage_retry:
                    balance = usage_retry.get('credit_balance', 0)
                    logger.warning(f'✅ Retry successful - got balance: {balance:.0f} tokens for user {user_id}')
                    sufficient = balance > 0
                    return {
                        'success': sufficient,
                        'sufficient': sufficient,
                        'balance': balance,
                        'effective_balance': balance,
                        'pending_buffer_cost': 0,
                        'required': required_amount,
                        'message': 'Positive balance - can proceed' if sufficient else f'Negative balance: {balance:.0f} tokens'
                    }
            except Exception as retry_error:
                logger.error(f'Error on retry checking credits: {retry_error}')
            
            # Both attempts failed — return error (fail-closed)
            return {
                'success': False,
                'sufficient': False,
                'balance': 0,
                'effective_balance': 0,
                'error': str(e),
                'message': 'Error checking credit balance — please retry'
            }
    
    
    def calculate_query_cost(self, model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> Dict[str, Any]:
        """
        Calculate cost for a query based on token usage.
        
        Uses the 'default' pricing tier from the pricing config.
        All models use the same rate — pricing is model-agnostic.
        
        Args:
            model: Model name (for logging only, not used for rate selection)
            input_tokens: Number of input tokens (includes cached tokens)
            output_tokens: Number of output tokens
            cached_tokens: Number of cached tokens (subset of input_tokens)
            
        Returns:
            Dictionary with cost details
        """
        pricing = self.get_pricing()
        token_pricing = pricing.get('token_pricing', {})
        
        # Single default tier — no model-name-based routing
        rates = token_pricing.get('default', {})
        
        # Fallback defaults if pricing DB has no entry
        input_rate = rates.get('input_per_1k', 0.05)
        output_rate = rates.get('output_per_1k', 0.10)
        cached_rate = rates.get('cached_per_1k', 0.02)
        
        # Separate cached tokens from regular input tokens
        regular_input_tokens = input_tokens - cached_tokens
        
        # Calculate costs (rates are per 1000 tokens)
        input_cost = (regular_input_tokens / 1000) * input_rate
        output_cost = (output_tokens / 1000) * output_rate
        cached_cost = (cached_tokens / 1000) * cached_rate
        total_cost = input_cost + output_cost + cached_cost
        
        return {
            'cost': round(total_cost, 4),
            'total_tokens': input_tokens + output_tokens,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'cached_tokens': cached_tokens,
            'regular_input_tokens': regular_input_tokens,
            'input_cost': round(input_cost, 4),
            'output_cost': round(output_cost, 4),
            'cached_cost': round(cached_cost, 4),
            'model': model,
            'pricing_details': {
                'input_rate_per_1k': input_rate,
                'output_rate_per_1k': output_rate,
                'cached_rate_per_1k': cached_rate
            }
        }
    
    
    def calculate_embedding_cost(self, total_characters: int) -> Dict[str, Any]:
        """
        Calculate cost for embedding generation using the configured embedding provider.
        Uses a single rate from the pricing config — provider-agnostic.
        
        Args:
            total_characters: Total character count across all chunks
            
        Returns:
            Dictionary with cost details
        """
        import os
        pricing = self.get_pricing()
        embedding_pricing = pricing.get('embedding_pricing', {})
        
        rate_per_1k_tokens = embedding_pricing.get('per_1k', 0.05)
        
        # Optimized token calculation: divide characters by 4
        estimated_tokens = total_characters / 4
        
        # Calculate cost (rate is per 1000 tokens)
        cost = (estimated_tokens / 1000) * rate_per_1k_tokens
        
        return {
            'cost': round(cost, 4),
            'total_characters': total_characters,
            'estimated_tokens': int(estimated_tokens),
            'provider': 'embedding',
            'pricing_details': {
                'provider': 'embedding',
                'rate_per_1k_tokens': rate_per_1k_tokens,
                'conversion_ratio': 4  # 4 characters ≈ 1 token
            }
        }
    
    
    def track_query_usage(
        self,
        user_id: str,
        email: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        query_id: Optional[str] = None,
        internet_grounding_cost: float = 0.0,
        cached_tokens: int = 0,
        use_buffer: bool = True
    ) -> Dict[str, Any]:
        """
        Track query usage and deduct credits (SYNCHRONOUS).
        Writes directly to MongoDB using atomic $inc operations.
        
        Args:
            user_id: User's unique identifier
            email: User's email
            model: Model name used
            input_tokens: Input tokens consumed
            output_tokens: Output tokens consumed
            query_id: Optional query identifier
            internet_grounding_cost: Cost for internet grounding (auto-detected from citations)
            cached_tokens: Cached tokens used (subset of input_tokens)
            use_buffer: Deprecated, ignored. Kept for backward compatibility.
            
        Returns:
            Dictionary with transaction result
        """
        try:
            # Calculate base cost from tokens (including cached tokens)
            cost_details = self.calculate_query_cost(model, input_tokens, output_tokens, cached_tokens)
            base_cost = cost_details['cost']
            
            # Add any additional costs (internet grounding)
            additional_cost = internet_grounding_cost
            cost = base_cost + additional_cost
            
            # Direct MongoDB write using atomic $inc
            
            # Get fresh database connection from pool
            db = self._get_db()
            userusages = db['userusages']
            credittransactions = db['credittransactions']
            
            # Get or create user usage
            usage = userusages.find_one({'user_id': user_id})
            if not usage:
                # Initialize new user with 0 balance
                usage = {
                    'user_id': user_id,
                    'email': email,
                    'credit_balance': 0,
                    'total_credits_purchased': 0,
                    'total_credits_consumed': 0,
                    'query_usage': {
                        'total_queries': 0,
                        'total_tokens_used': 0,
                        'llm_tokens': 0,
                        
                        'total_cost': 0
                    },
                    'file_upload_usage': {
                        'documents': {'count': 0, 'size_mb': 0, 'cost': 0},
                        'audio': {'count': 0, 'size_mb': 0, 'cost': 0},
                        'video': {'count': 0, 'size_mb': 0, 'cost': 0},
                        'images': {'count': 0, 'size_mb': 0, 'cost': 0},
                        'total_files': 0,
                        'total_size_mb': 0,
                        'total_cost': 0
                    },
                    'recent_transactions': [],
                    'created_at': datetime.now(),
                    'updated_at': datetime.now()
                }
                userusages.insert_one(usage)
            
            balance_before = usage.get('credit_balance', 0)
            
            # Allow balance to go negative - operation already completed, must always record usage
            # This ensures accurate tracking and the pre-check (balance > 0) blocks future operations
            
            # Use atomic $inc operations to prevent race conditions
            inc_updates = {
                'credit_balance': -cost,
                'total_credits_consumed': cost,
                'query_usage.total_queries': 1,
                'query_usage.total_tokens_used': cost_details['total_tokens'],
                'query_usage.total_cost': cost
            }
            
            # Track LLM tokens (model-agnostic)
            inc_updates['query_usage.llm_tokens'] = cost_details['total_tokens']
            
            userusages.update_one(
                {'user_id': user_id},
                {
                    '$inc': inc_updates,
                    '$set': {'updated_at': datetime.now()}
                }
            )
            
            balance_after = balance_before - cost
            
            # Create detailed transaction record
            transaction_id = f"query_{int(datetime.now().timestamp() * 1000)}"
            transaction = {
                'transaction_id': transaction_id,
                'user_id': user_id,
                'email': email,
                'type': 'query_usage',
                'amount': -cost,
                'balance_before': balance_before,
                'balance_after': balance_after,
                'query_metadata': {
                    'tokens_used': cost_details['total_tokens'],
                    'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                    'cached_tokens': cached_tokens,
                    'regular_input_tokens': cost_details.get('regular_input_tokens', input_tokens - cached_tokens),
                    'model': model,
                    'cost_breakdown': {
                        'input_cost': cost_details.get('input_cost', 0),
                        'output_cost': cost_details.get('output_cost', 0),
                        'cached_cost': cost_details.get('cached_cost', 0),
                        'internet_grounding_cost': internet_grounding_cost,
                        'additional_cost': additional_cost
                    },
                    'cost_per_token': cost / cost_details['total_tokens'] if cost_details['total_tokens'] > 0 else 0,
                    'query_id': query_id
                },
                'timestamp': datetime.now()
            }
            
            credittransactions.insert_one(transaction)
            
            logger.info(f'💠 Query usage tracked: {user_id} - {cost_details["total_tokens"]} tokens ({model})')

            return {
                'success': True,
                'cost': cost,
                'new_balance': balance_after,
                'tokens_used': cost_details['total_tokens'],
                'transaction_id': transaction_id,
                'message': f'Query tracked successfully. {cost_details["total_tokens"]} tokens consumed.'
            }
            
        except Exception as e:
            logger.error(f'Error tracking query usage: {e}')
            raise
    
    
    def track_embedding_usage(
        self,
        user_id: str,
        email: str,
        total_characters: int,
        document_id: Optional[str] = None,
        chunk_count: Optional[int] = None,
        use_buffer: bool = True
    ) -> Dict[str, Any]:
        """
        Track embedding generation usage and deduct credits (SYNCHRONOUS).
        Called during document chunking to charge for embedding API usage.
        Uses the configured embedding provider and pricing rate.
        Writes directly to MongoDB using atomic $inc operations.
        
        Args:
            user_id: User's unique identifier
            email: User's email
            total_characters: Total character count across all document chunks
            document_id: Optional document identifier
            chunk_count: Optional number of chunks processed
            use_buffer: Deprecated, ignored. Kept for backward compatibility.
            
        Returns:
            Dictionary with transaction result
        """
        try:
            # Calculate embedding cost
            cost_details = self.calculate_embedding_cost(total_characters)
            cost = cost_details['cost']
            
            # Direct MongoDB write using atomic $inc
            
            # Get fresh database connection from pool
            db = self._get_db()
            userusages = db['userusages']
            credittransactions = db['credittransactions']
            
            # Get or create user usage
            usage = userusages.find_one({'user_id': user_id})
            if not usage:
                # Initialize new user with 0 balance
                usage = {
                    'user_id': user_id,
                    'email': email,
                    'credit_balance': 0,
                    'total_credits_purchased': 0,
                    'total_credits_consumed': 0,
                    'query_usage': {
                        'total_queries': 0,
                        'total_tokens_used': 0,
                        'llm_tokens': 0,
                        
                        'total_cost': 0
                    },
                    'embedding_usage': {
                        'total_documents': 0,
                        'total_chunks': 0,
                        'total_tokens': 0,
                        'total_cost': 0
                    },
                    'file_upload_usage': {
                        'documents': {'count': 0, 'size_mb': 0, 'cost': 0},
                        'audio': {'count': 0, 'size_mb': 0, 'cost': 0},
                        'video': {'count': 0, 'size_mb': 0, 'cost': 0},
                        'images': {'count': 0, 'size_mb': 0, 'cost': 0},
                        'total_files': 0,
                        'total_size_mb': 0,
                        'total_cost': 0
                    },
                    'recent_transactions': [],
                    'created_at': datetime.now(),
                    'updated_at': datetime.now()
                }
                userusages.insert_one(usage)
            
            balance_before = usage.get('credit_balance', 0)
            
            # Allow balance to go negative - no pre-check needed
            # User can work until balance becomes negative
            
            # Deduct credits and update embedding usage stats
            inc_updates = {
                'credit_balance': -cost,
                'total_credits_consumed': cost,
                'embedding_usage.total_documents': 1,
                'embedding_usage.total_tokens': cost_details['estimated_tokens'],
                'embedding_usage.total_cost': cost
            }
            
            if chunk_count:
                inc_updates['embedding_usage.total_chunks'] = chunk_count
            
            userusages.update_one(
                {'user_id': user_id},
                {
                    '$inc': inc_updates,
                    '$set': {'updated_at': datetime.now()}
                }
            )
            
            balance_after = balance_before - cost
            
            # Create transaction record
            transaction_id = f"embed_{int(datetime.now().timestamp() * 1000)}"
            transaction = {
                'transaction_id': transaction_id,
                'user_id': user_id,
                'email': email,
                'type': 'embedding_usage',
                'amount': -cost,
                'balance_before': balance_before,
                'balance_after': balance_after,
                'details': {
                    'document_id': document_id,
                    'total_characters': total_characters,
                    'estimated_tokens': cost_details['estimated_tokens'],
                    'chunk_count': chunk_count,
                    'provider': 'embedding',
                    'rate_per_1k_tokens': cost_details['pricing_details']['rate_per_1k_tokens']
                },
                'timestamp': datetime.now()
            }
            
            credittransactions.insert_one(transaction)
            
            logger.info(
                f"💠 Embedding usage tracked: {user_id} | "
                f"Chars: {total_characters} | Tokens: ~{cost_details['estimated_tokens']}"
            )
            
            return {
                'success': True,
                'transaction_id': transaction_id,
                'cost': cost,
                'balance_before': balance_before,
                'balance_after': balance_after,
                'cost_details': cost_details
            }
            
        except Exception as e:
            logger.error(f'Error tracking embedding usage: {e}')
            raise
    

