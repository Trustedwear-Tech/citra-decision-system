// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const http = require('http');
const https = require('https');
const url = require('url');

const PORT = 8085;

// Allowed API path prefixes — blocks proxying to arbitrary endpoints
const ALLOWED_PATH_PREFIXES = [
  '/v2/',
  '/api/',
  '/chat',
  '/search',
  '/proxy',
  '/fetch-page',
  '/presentation/',
  '/printable/',
  '/report/',
  '/user-service/',
  '/llm-image/',
  '/audio/',
];

const server = http.createServer((req, res) => {
  // Set CORS headers — restrict to known development origins only
  const origin = req.headers.origin;
  const allowedOrigins = [
    'http://localhost:8081',
    'http://localhost:8082',
    'http://192.168.29.5:8081',
    'http://192.168.29.5:8082'
  ];

  if (allowedOrigins.includes(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
    res.setHeader('Access-Control-Allow-Credentials', 'true');
  }
  // No wildcard fallback — unknown origins get no CORS headers

  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS, PATCH');
  res.setHeader('Access-Control-Allow-Headers', 'Origin, X-Requested-With, Content-Type, Accept, Authorization');

  // Handle preflight OPTIONS requests
  if (req.method === 'OPTIONS') {
    res.writeHead(200);
    res.end();
    return;
  }

  // Create target URL 
  let targetPath;

  // Remove /api/ prefix if present (legacy support)
  if (req.url.startsWith('/api/')) {
    targetPath = req.url.substring(4); // Remove '/api' from the beginning
  } else {
    // Use the URL as-is for direct paths
    targetPath = req.url;
  }

  // Validate path against allowlist to prevent open proxy abuse
  const pathOnly = targetPath.split('?')[0]; // strip query string for matching
  const isAllowed = ALLOWED_PATH_PREFIXES.some(prefix => pathOnly.startsWith(prefix));
  if (!isAllowed) {
    console.warn(`❌ Blocked proxy request to disallowed path: ${pathOnly}`);
    res.writeHead(403);
    res.end(JSON.stringify({ error: 'Forbidden', message: 'Path not allowed' }));
    return;
  }

  const targetUrl = `https://api.citra-ai.com${targetPath}`;

  console.log(`Proxying ${req.method} ${targetUrl}`);

  const parsedUrl = url.parse(targetUrl);

  const options = {
    hostname: parsedUrl.hostname,
    port: parsedUrl.port || 443,
    path: parsedUrl.path,
    method: req.method,
    headers: {
      ...req.headers,
      host: parsedUrl.hostname
    }
  };

  // Remove headers that might cause issues
  delete options.headers['origin'];
  delete options.headers['referer'];

  const proxyReq = https.request(options, (proxyRes) => {
    // Copy headers from target response, but skip CORS headers
    const responseHeaders = {};
    Object.keys(proxyRes.headers).forEach(key => {
      if (!key.toLowerCase().startsWith('access-control-')) {
        responseHeaders[key] = proxyRes.headers[key];
      }
    });

    // Set response status and headers
    res.writeHead(proxyRes.statusCode, proxyRes.statusMessage, responseHeaders);

    // Pipe the response data
    proxyRes.pipe(res);
  });

  proxyReq.on('error', (err) => {
    console.error('Proxy request error:', err.message);
    res.writeHead(500);
    res.end(JSON.stringify({ error: 'Proxy error', message: err.message }));
  });

  // Pipe the request data
  req.pipe(proxyReq);
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`Proxy server running on http://localhost:${PORT}`);
  console.log(`Mobile access available at: http://192.168.29.5:${PORT}`);
  console.log(`Forward API calls from web app to: http://localhost:${PORT}/[any-path]`);
  console.log(`Supports direct paths and removes /api/ prefix`);
  console.log(`CORS enabled for: localhost and mobile devices`);
});
