# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Browser Pool Manager
Maintains a pool of warm browser instances for instant request serving.
"""
import asyncio
import logging
import time
from typing import Optional
from dataclasses import dataclass, field
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from config import config

logger = logging.getLogger(__name__)


@dataclass
class BrowserInstance:
    """Represents a browser instance in the pool"""
    browser: Browser
    context: BrowserContext
    page: Page
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    in_use: bool = False
    request_count: int = 0


class BrowserPool:
    """
    Manages a pool of headless browser instances.
    - Maintains MIN_BROWSERS warm instances ready to serve
    - Scales up to MAX_BROWSERS under load
    - Recycles browsers after idle timeout
    """
    
    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._pool: list[BrowserInstance] = []
        self._lock = asyncio.Lock()
        self._initialized = False
        self._cleanup_task: Optional[asyncio.Task] = None
        
    async def initialize(self):
        """Initialize the browser pool with minimum browsers"""
        if self._initialized:
            return
            
        logger.info(f"🚀 Initializing browser pool (min: {config.MIN_BROWSERS}, max: {config.MAX_BROWSERS})")
        
        self._playwright = await async_playwright().start()
        
        # Create minimum number of warm browsers
        for i in range(config.MIN_BROWSERS):
            instance = await self._create_browser_instance()
            self._pool.append(instance)
            logger.info(f"✅ Warm browser {i + 1}/{config.MIN_BROWSERS} ready")
        
        self._initialized = True
        
        # Start cleanup task
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(f"🔥 Browser pool initialized with {len(self._pool)} warm browsers")
    
    async def _create_browser_instance(self) -> BrowserInstance:
        """Create a new browser instance with stealth settings"""
        
        launch_args = [
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-accelerated-2d-canvas',
            '--disable-gpu',
        ]
        
        browser = await self._playwright.chromium.launch(
            headless=True,
            args=launch_args
        )
        
        # Create context with stealth settings
        context_options = {
            'viewport': {'width': 1920, 'height': 1080},
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'locale': 'en-US',
            'timezone_id': 'America/New_York',
            'permissions': ['geolocation'],
            'java_script_enabled': True,
            'bypass_csp': True,  # Bypass Content Security Policy for better rendering
        }
        
        if config.STEALTH_MODE:
            context_options['extra_http_headers'] = {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                'Sec-Ch-Ua-Mobile': '?0',
                'Sec-Ch-Ua-Platform': '"Windows"',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Upgrade-Insecure-Requests': '1',
            }
        
        context = await browser.new_context(**context_options)
        
        # Add stealth scripts to evade bot detection
        if config.STEALTH_MODE:
            await context.add_init_script("""
                // Override webdriver property
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                
                // Override plugins
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                
                // Override languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });
                
                // Override chrome
                window.chrome = {
                    runtime: {}
                };
                
                // Override permissions
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
            """)
        
        page = await context.new_page()
        
        return BrowserInstance(
            browser=browser,
            context=context,
            page=page
        )
    
    async def acquire(self, _retries: int = 0) -> BrowserInstance:
        """
        Acquire a browser instance from the pool.
        Returns a warm browser if available, or creates a new one.
        Max retries to prevent infinite recursion when pool is exhausted.
        """
        MAX_ACQUIRE_RETRIES = 20  # 20 * 0.5s = 10s max wait
        
        if _retries >= MAX_ACQUIRE_RETRIES:
            raise RuntimeError(f"Browser pool exhausted: all {config.MAX_BROWSERS} browsers in use after {MAX_ACQUIRE_RETRIES} retries")
        
        async with self._lock:
            # Find an available browser
            for instance in self._pool:
                if not instance.in_use:
                    instance.in_use = True
                    instance.last_used = time.time()
                    instance.request_count += 1
                    logger.info(f"📤 Acquired warm browser (pool size: {len(self._pool)})")
                    return instance
            
            # No available browser, create new if under limit
            if len(self._pool) < config.MAX_BROWSERS:
                logger.info(f"🆕 Creating new browser (pool size: {len(self._pool)} -> {len(self._pool) + 1})")
                instance = await self._create_browser_instance()
                instance.in_use = True
                instance.last_used = time.time()
                instance.request_count = 1
                self._pool.append(instance)
                return instance
            
            # At max capacity, wait for one to become available
            logger.warning(f"⏳ Pool at max capacity ({config.MAX_BROWSERS}), waiting... (retry {_retries + 1}/{MAX_ACQUIRE_RETRIES})")
            
        # Wait and retry (outside lock)
        await asyncio.sleep(0.5)
        return await self.acquire(_retries=_retries + 1)
    
    async def release(self, instance: BrowserInstance):
        """Release a browser instance back to the pool"""
        async with self._lock:
            instance.in_use = False
            instance.last_used = time.time()
            
            # Reset page state for next use
            try:
                await instance.page.goto('about:blank')
                await instance.context.clear_cookies()
            except Exception as e:
                logger.warning(f"⚠️ Failed to reset page: {e}")
            
            logger.info(f"📥 Released browser back to pool (pool size: {len(self._pool)})")
    
    async def _cleanup_loop(self):
        """Periodically cleanup idle browsers above minimum threshold"""
        while True:
            await asyncio.sleep(60)  # Check every minute
            
            async with self._lock:
                now = time.time()
                to_remove = []
                
                for instance in self._pool:
                    # Keep minimum browsers always
                    if len(self._pool) - len(to_remove) <= config.MIN_BROWSERS:
                        break
                    
                    # Remove idle browsers
                    if not instance.in_use and (now - instance.last_used) > config.BROWSER_IDLE_TIMEOUT:
                        to_remove.append(instance)
                
                for instance in to_remove:
                    try:
                        await instance.browser.close()
                        self._pool.remove(instance)
                        logger.info(f"🧹 Cleaned up idle browser (pool size: {len(self._pool)})")
                    except Exception as e:
                        logger.error(f"❌ Error closing browser: {e}")
    
    async def shutdown(self):
        """Shutdown the browser pool"""
        logger.info("🛑 Shutting down browser pool...")
        
        if self._cleanup_task:
            self._cleanup_task.cancel()
        
        async with self._lock:
            for instance in self._pool:
                try:
                    await instance.browser.close()
                except Exception as e:
                    logger.error(f"❌ Error closing browser: {e}")
            
            self._pool.clear()
        
        if self._playwright:
            await self._playwright.stop()
        
        self._initialized = False
        logger.info("✅ Browser pool shutdown complete")
    
    @property
    def stats(self) -> dict:
        """Get pool statistics"""
        in_use = sum(1 for i in self._pool if i.in_use)
        return {
            "total_browsers": len(self._pool),
            "in_use": in_use,
            "available": len(self._pool) - in_use,
            "min_browsers": config.MIN_BROWSERS,
            "max_browsers": config.MAX_BROWSERS,
        }


# Singleton instance
browser_pool = BrowserPool()
