// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

// Citra AI Service Worker - PWA Offline Caching
const CACHE_NAME = 'citra-ai-v5';
const RUNTIME_CACHE = 'citra-ai-runtime-v5';

// Critical assets to pre-cache on install (app shell)
const PRECACHE_URLS = [
  '/',
  '/site.webmanifest',
  '/favicon.ico',
  '/icon-192.png',
  '/icon-512.png',
  '/apple-touch-icon.png',
];

// Install: pre-cache the app shell (best-effort — never let a single missing
// asset block install, otherwise the old SW keeps handling fetches forever).
self.addEventListener('install', (event) => {
  console.log('[SW] Installing service worker (v5)...');
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(async (cache) => {
        await Promise.all(
          PRECACHE_URLS.map((url) =>
            cache.add(url).catch((err) => {
              console.warn('[SW] Precache skipped:', url, err?.message);
            }),
          ),
        );
      })
      .then(() => self.skipWaiting())
      .catch((err) => {
        console.warn('[SW] Install failed, continuing anyway:', err);
        return self.skipWaiting();
      }),
  );
});

// Activate: clean up old caches
self.addEventListener('activate', (event) => {
  console.log('[SW] Activating service worker...');
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name !== CACHE_NAME && name !== RUNTIME_CACHE)
          .map((name) => {
            console.log('[SW] Deleting old cache:', name);
            return caches.delete(name);
          })
      );
    }).then(() => {
      // Take control of all pages immediately
      return self.clients.claim();
    })
  );
});

// Fetch: network-first for API, stale-while-revalidate for assets
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Skip non-GET requests
  if (event.request.method !== 'GET') return;

  // Skip cross-origin API requests (don't cache API calls)
  if (url.origin !== self.location.origin) return;

  // Skip chrome-extension and other non-http schemes
  if (!url.protocol.startsWith('http')) return;

  // For navigation requests (HTML pages): do NOT intercept.
  //
  // Intercepting navigations forced us to handle redirected responses, opaque
  // responses, and Expo dev-server quirks — all of which can produce "Failed
  // to convert value to 'Response'" inside respondWith(). The browser handles
  // navigation natively just fine; we only sacrifice offline navigation
  // fallback, which isn't critical for an authenticated SPA.
  if (event.request.mode === 'navigate') {
    return;
  }

  // For JS/CSS bundles: Stale-while-revalidate
  // Serve cached version immediately while fetching update in background
  if (url.pathname.match(/\.(js|css)$/) || url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(RUNTIME_CACHE).then((cache) => {
        return cache.match(event.request).then((cachedResponse) => {
          const fetchPromise = fetch(event.request).then((networkResponse) => {
            // Only cache successful responses
            if (networkResponse && networkResponse.status === 200) {
              cache.put(event.request, networkResponse.clone());
            }
            return networkResponse;
          }).catch(() => {
            // Network failed, cached response will be used
            return cachedResponse;
          });

          // Return cached response immediately, or wait for network
          return cachedResponse || fetchPromise;
        });
      })
    );
    return;
  }

  // For images/fonts/icons: Cache-first
  if (url.pathname.match(/\.(png|jpg|jpeg|gif|svg|ico|woff|woff2|ttf|eot)$/)) {
    event.respondWith(
      caches.open(RUNTIME_CACHE).then((cache) => {
        return cache.match(event.request).then((cachedResponse) => {
          if (cachedResponse) {
            return cachedResponse;
          }
          return fetch(event.request).then((networkResponse) => {
            if (networkResponse && networkResponse.status === 200) {
              cache.put(event.request, networkResponse.clone());
            }
            return networkResponse;
          });
        });
      })
    );
    return;
  }

  // Default: Network-first for everything else
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response && response.status === 200) {
          const responseClone = response.clone();
          caches.open(RUNTIME_CACHE).then((cache) => {
            cache.put(event.request, responseClone);
          });
        }
        return response;
      })
      .catch(async () => {
        // Network failed — try cache; if nothing cached, return a synthetic
        // error Response so respondWith() always gets a real Response object.
        // Returning undefined here triggers "Failed to convert value to 'Response'".
        const cached = await caches.match(event.request);
        if (cached) return cached;
        return new Response('', {
          status: 504,
          statusText: 'Gateway Timeout (offline)',
          headers: { 'Content-Type': 'text/plain' },
        });
      })
  );
});
