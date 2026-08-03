"""
Cache Manager for the bot
Provides in-memory caching with TTL support
"""

import time
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)


class CacheManager:
    def __init__(self):
        """Initialize cache manager"""
        self._cache = {}
        self._hits = 0
        self._misses = 0
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        if key in self._cache:
            entry = self._cache[key]
            
            # Check if expired
            if entry['expires_at'] > time.time():
                self._hits += 1
                logger.debug(f"Cache hit: {key}")
                return entry['value']
            else:
                # Remove expired entry
                del self._cache[key]
                self._misses += 1
                logger.debug(f"Cache expired: {key}")
                return None
        
        self._misses += 1
        logger.debug(f"Cache miss: {key}")
        return None
    
    def set(self, key: str, value: Any, ttl: int = 300):
        """
        Set value in cache with TTL (time to live) in seconds
        Default TTL is 300 seconds (5 minutes)
        """
        expires_at = time.time() + ttl
        self._cache[key] = {
            'value': value,
            'expires_at': expires_at,
            'created_at': time.time()
        }
        logger.debug(f"Cache set: {key} (TTL: {ttl}s)")
    
    def delete(self, key: str):
        """Delete a key from cache"""
        if key in self._cache:
            del self._cache[key]
            logger.debug(f"Cache deleted: {key}")
    
    def clear(self):
        """Clear all cache"""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
        logger.info("Cache cleared")
    
    def size(self) -> int:
        """Get number of items in cache"""
        return len(self._cache)
    
    def get_hit_rate(self) -> float:
        """Get cache hit rate percentage"""
        total = self._hits + self._misses
        if total == 0:
            return 0.0
        return (self._hits / total) * 100
    
    def cleanup_expired(self):
        """Remove all expired entries"""
        current_time = time.time()
        expired_keys = [
            key for key, entry in self._cache.items()
            if entry['expires_at'] <= current_time
        ]
        
        for key in expired_keys:
            del self._cache[key]
        
        if expired_keys:
            logger.info(f"Cleaned up {len(expired_keys)} expired cache entries")
        
        return len(expired_keys)
    
    def get_stats(self) -> dict:
        """Get cache statistics"""
        return {
            'size': self.size(),
            'hits': self._hits,
            'misses': self._misses,
            'hit_rate': self.get_hit_rate()
        }
