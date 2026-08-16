# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Performance monitoring and metrics for Citra AI Query API
Provides real-time performance insights and optimization recommendations
"""

import time
import logging
from typing import Dict, List, Optional
from collections import defaultdict, deque
from datetime import datetime, timedelta
import json
import os

class QueryPerformanceMonitor:
    """
    Monitor and analyze query performance to identify optimization opportunities
    """
    
    def __init__(self):
        self.query_times = deque(maxlen=1000)  # Last 1000 queries
        self.slow_queries = deque(maxlen=100)   # Last 100 slow queries
        self.cache_stats = {
            'embedding_hits': 0,
            'embedding_misses': 0,
            'context_hits': 0,
            'context_misses': 0
        }
        self.model_usage = defaultdict(int)
        self.query_types = defaultdict(int)
        self.optimization_suggestions = []
        
        # Configuration from environment
        self.slow_threshold = float(os.getenv('LOG_SLOW_QUERY_THRESHOLD', '3.0'))
        self.very_slow_threshold = float(os.getenv('ALERT_VERY_SLOW_QUERY_THRESHOLD', '10.0'))
        self.enable_monitoring = os.getenv('ENABLE_QUERY_PERFORMANCE_METRICS', 'true').lower() == 'true'
        
    def log_error(self, error_message: str, query_text: str = "", processing_time: float = 0):
        """Log query error for debugging purposes"""
        if not self.enable_monitoring:
            return
            
        error_data = {
            'timestamp': time.time(),
            'error_message': error_message,
            'query_preview': query_text[:100] + '...' if len(query_text) > 100 else query_text,
            'processing_time': processing_time
        }
        
        logging.error(f"🔍 Query Error: {error_message} - Query: '{query_text[:50]}...' - Time: {processing_time:.2f}s")

    def log_query_performance(self, 
                            query_text: str, 
                            processing_time: float, 
                            model_used: str,
                            context_count: int = 0,
                            cache_hits: Dict[str, bool] = None,
                            fast_path_used: bool = False):
        """Log performance metrics for a query"""
        
        if not self.enable_monitoring:
            return
            
        # Store timing data
        query_data = {
            'timestamp': time.time(),
            'query_length': len(query_text),
            'processing_time': processing_time,
            'model_used': model_used,
            'context_count': context_count,
            'fast_path_used': fast_path_used,
            'cache_hits': cache_hits or {}
        }
        
        self.query_times.append(query_data)
        self.model_usage[model_used] += 1
        
        # Categorize query type
        if fast_path_used:
            self.query_types['simple_generic'] += 1
        elif context_count == 0:
            self.query_types['no_context'] += 1
        elif context_count <= 5:
            self.query_types['simple'] += 1
        else:
            self.query_types['complex'] += 1
            
        # Track cache performance
        if cache_hits:
            if cache_hits.get('embedding'):
                self.cache_stats['embedding_hits'] += 1
            else:
                self.cache_stats['embedding_misses'] += 1
                
            if cache_hits.get('context'):
                self.cache_stats['context_hits'] += 1
            else:
                self.cache_stats['context_misses'] += 1
        
        # Log slow queries
        if processing_time > self.slow_threshold:
            slow_query_data = {
                **query_data,
                'query_preview': query_text[:100] + '...' if len(query_text) > 100 else query_text
            }
            self.slow_queries.append(slow_query_data)
            
            if processing_time > self.very_slow_threshold:
                logging.warning(f"🐌 VERY SLOW QUERY: {processing_time:.2f}s - '{query_text[:50]}...'")
            else:
                logging.info(f"🐌 Slow query: {processing_time:.2f}s - '{query_text[:50]}...'")
    
    def get_performance_stats(self) -> Dict:
        """Get comprehensive performance statistics"""
        
        if not self.query_times:
            return {"message": "No queries recorded yet"}
            
        recent_times = [q['processing_time'] for q in self.query_times]
        
        # Calculate statistics
        avg_time = sum(recent_times) / len(recent_times)
        min_time = min(recent_times)
        max_time = max(recent_times)
        
        # Percentiles
        sorted_times = sorted(recent_times)
        p50_idx = len(sorted_times) // 2
        p95_idx = int(len(sorted_times) * 0.95)
        p99_idx = int(len(sorted_times) * 0.99)
        
        p50 = sorted_times[p50_idx] if p50_idx < len(sorted_times) else sorted_times[-1]
        p95 = sorted_times[p95_idx] if p95_idx < len(sorted_times) else sorted_times[-1]
        p99 = sorted_times[p99_idx] if p99_idx < len(sorted_times) else sorted_times[-1]
        
        # Cache hit rates
        total_embedding_requests = self.cache_stats['embedding_hits'] + self.cache_stats['embedding_misses']
        total_context_requests = self.cache_stats['context_hits'] + self.cache_stats['context_misses']
        
        embedding_hit_rate = (self.cache_stats['embedding_hits'] / total_embedding_requests * 100) if total_embedding_requests > 0 else 0
        context_hit_rate = (self.cache_stats['context_hits'] / total_context_requests * 100) if total_context_requests > 0 else 0
        
        # Query type distribution
        total_queries = sum(self.query_types.values()) or 1
        query_distribution = {k: (v / total_queries * 100) for k, v in self.query_types.items()}
        
        return {
            "performance_summary": {
                "total_queries": len(self.query_times),
                "average_time": round(avg_time, 3),
                "median_time": round(p50, 3),
                "p95_time": round(p95, 3),
                "p99_time": round(p99, 3),
                "min_time": round(min_time, 3),
                "max_time": round(max_time, 3),
                "slow_queries": len(self.slow_queries)
            },
            "cache_performance": {
                "embedding_hit_rate": round(embedding_hit_rate, 1),
                "context_hit_rate": round(context_hit_rate, 1),
                "total_embedding_requests": total_embedding_requests,
                "total_context_requests": total_context_requests
            },
            "query_distribution": {k: f"{v:.1f}%" for k, v in query_distribution.items()},
            "model_usage": dict(self.model_usage),
            "optimization_opportunities": self._generate_optimization_recommendations()
        }
    
    def _generate_optimization_recommendations(self) -> List[str]:
        """Generate optimization recommendations based on performance data"""
        recommendations = []
        
        if not self.query_times:
            return recommendations
            
        recent_times = [q['processing_time'] for q in self.query_times]
        avg_time = sum(recent_times) / len(recent_times)
        
        # Slow query recommendations
        if avg_time > 2.0:
            recommendations.append("🔴 HIGH: Average query time is high (>2s). Consider enabling more caching.")
        elif avg_time > 1.0:
            recommendations.append("🟡 MEDIUM: Average query time is moderate (>1s). Monitor for improvements.")
        
        # Cache recommendations
        total_embedding = self.cache_stats['embedding_hits'] + self.cache_stats['embedding_misses']
        total_context = self.cache_stats['context_hits'] + self.cache_stats['context_misses']
        
        if total_embedding > 0:
            hit_rate = self.cache_stats['embedding_hits'] / total_embedding
            if hit_rate < 0.3:
                recommendations.append("🟡 MEDIUM: Low embedding cache hit rate. Consider longer TTL.")
        
        if total_context > 0:
            hit_rate = self.cache_stats['context_hits'] / total_context
            if hit_rate < 0.2:
                recommendations.append("🟡 MEDIUM: Low context cache hit rate. Consider optimizing cache keys.")
        
        # Model usage recommendations
        total_queries = sum(self.model_usage.values())
        if total_queries > 0:
            nano_usage = self.model_usage.get('nano', 0) / total_queries
            if nano_usage < 0.3:
                recommendations.append("🟢 GOOD: Consider using nano model for more simple queries to improve speed.")
        
        # Slow query pattern analysis
        if len(self.slow_queries) > 10:
            recommendations.append("🔴 HIGH: Many slow queries detected. Review query patterns and indexing.")
        
        if not recommendations:
            recommendations.append("✅ Performance looks good! No major optimizations needed.")
            
        return recommendations
    
    def get_slow_query_analysis(self) -> Dict:
        """Analyze slow queries for patterns"""
        
        if not self.slow_queries:
            return {"message": "No slow queries recorded"}
        
        # Analyze patterns in slow queries
        patterns = {
            "long_queries": 0,
            "high_context_queries": 0,
            "complex_model_queries": 0,
            "no_cache_queries": 0
        }
        
        for query in self.slow_queries:
            if query['query_length'] > 200:
                patterns["long_queries"] += 1
            if query['context_count'] > 10:
                patterns["high_context_queries"] += 1
            if query['model_used'] in ['pro', 'reasoning']:
                patterns["complex_model_queries"] += 1
            if not any(query.get('cache_hits', {}).values()):
                patterns["no_cache_queries"] += 1
        
        return {
            "slow_query_count": len(self.slow_queries),
            "patterns": patterns,
            "slowest_queries": [
                {
                    "time": q['processing_time'],
                    "query": q['query_preview'],
                    "model": q['model_used'],
                    "contexts": q['context_count']
                }
                for q in sorted(self.slow_queries, key=lambda x: x['processing_time'], reverse=True)[:5]
            ]
        }

# Global performance monitor instance
performance_monitor = QueryPerformanceMonitor()

def log_query_performance(*args, **kwargs):
    """Convenience function to log query performance"""
    performance_monitor.log_query_performance(*args, **kwargs)

def get_performance_stats():
    """Get current performance statistics"""
    return performance_monitor.get_performance_stats()

def get_slow_query_analysis():
    """Get slow query analysis"""
    return performance_monitor.get_slow_query_analysis()
