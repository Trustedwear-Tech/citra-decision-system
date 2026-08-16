<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Playwright Render Service

A high-performance microservice for rendering web pages using headless Chromium. Designed to bypass bot protection and CORS restrictions for iframe-based browsing.

## Features

- 🚀 **Browser Pool**: Maintains warm browser instances for instant response
- 📄 **Dual Output**: Supports both HTML and PDF output formats
- 🛡️ **Stealth Mode**: Bypasses common bot detection mechanisms
- ⚡ **Fast**: Pre-warmed browsers serve requests in ~2-4 seconds
- 🔄 **Auto-scaling**: Scales browser instances based on load
- 🐳 **Docker Ready**: Easy deployment with Docker

## Quick Start

### Local Development

1. **Install dependencies**:
```bash
cd playwright-render-service
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

pip install -r requirements.txt
playwright install chromium
```

2. **Configure environment**:
```bash
copy .env.example .env
# Edit .env as needed
```

3. **Run the service**:
```bash
python main.py
```

The service will start on `http://localhost:3001`

### Docker Deployment

```bash
docker-compose up -d
```

## API Endpoints

### GET /render

Render a webpage and return HTML or PDF.

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | string | required | URL to render |
| `output_format` | string | "html" | Output format: "html" or "pdf" |
| `wait_for` | string | "networkidle" | Wait condition: "load", "domcontentloaded", "networkidle" |
| `timeout` | int | 30 | Timeout in seconds |
| `inject_base_tag` | bool | true | Inject `<base>` tag for relative URLs |
| `inject_interceptor` | bool | true | Inject link click interceptor script |

**Example:**
```bash
# Get HTML
curl "http://localhost:3001/render?url=https://example.com"

# Get PDF
curl "http://localhost:3001/render?url=https://example.com&output_format=pdf" --output page.pdf
```

### POST /render

Same as GET but accepts JSON body for complex requests.

**Example:**
```bash
curl -X POST http://localhost:3001/render \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "output_format": "pdf",
    "wait_for": "networkidle",
    "timeout": 30
  }'
```

### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "service": "playwright-render-service",
  "browser_pool": {
    "total_browsers": 1,
    "in_use": 0,
    "available": 1,
    "min_browsers": 1,
    "max_browsers": 3
  },
  "default_output_format": "html"
}
```

### GET /stats

Get service statistics.

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `PORT` | 3001 | Service port |
| `HOST` | 0.0.0.0 | Bind host |
| `MIN_BROWSERS` | 1 | Minimum warm browser instances |
| `MAX_BROWSERS` | 3 | Maximum browser instances |
| `BROWSER_IDLE_TIMEOUT` | 300 | Seconds before idle browser is closed |
| `DEFAULT_OUTPUT_FORMAT` | html | Default output: "html" or "pdf" |
| `REQUEST_TIMEOUT` | 30 | Request timeout in seconds |
| `STEALTH_MODE` | true | Enable bot detection bypass |
| `ALLOWED_ORIGINS` | * | CORS allowed origins (comma-separated) |

## Integration with Citra-AI

This service is designed as a fallback for the Citra-AI proxy. When the simple HTTP proxy fails (403, bot detection), requests are forwarded to this service.

**Flow:**
```
UI → Citra-AI Proxy (/proxy)
           ↓
    [Try simple request]
           ↓ (if blocked)
    [Call Playwright Service]
           ↓
    Return rendered HTML/PDF
```

## Performance

| Metric | Value |
|--------|-------|
| Cold start (first request) | ~5-8 seconds |
| Warm request (browser ready) | ~2-4 seconds |
| Memory per browser | ~200-400 MB |
| Recommended CPU | 2+ cores |
| Recommended RAM | 2+ GB |

## Troubleshooting

### Browser crashes
- Increase `shm_size` in docker-compose.yml
- Reduce `MAX_BROWSERS`

### High memory usage
- Reduce `MAX_BROWSERS`
- Lower `BROWSER_IDLE_TIMEOUT`

### Timeout errors
- Increase `REQUEST_TIMEOUT`
- Use `wait_for=domcontentloaded` for faster (less complete) renders

## License

Proprietary. Copyright (c) 2024–2026 Citra AI Private Limited. All rights reserved. See the root [LICENSE.md](../LICENSE.md) for full terms. Contact licensing@citra-ai.com for license inquiries.
