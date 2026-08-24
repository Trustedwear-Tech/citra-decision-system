# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Playwright Render Service
A microservice for rendering web pages using headless Chromium.
Supports HTML and PDF output formats.
"""
# Vault bootstrap MUST be first — populates SENTRY_DSN / OTEL_* before
# observability.init_sentry() reads them. Fails fast in prod if Vault is
# unreachable. In local dev (VAULT_ADDR unset) it's a no-op.
import os
if os.getenv("VAULT_ADDR"):
    from citra_service_utils import load_from_vault
    load_from_vault()

import asyncio
import logging
import time
import sys
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse, urljoin, quote

# Fix for Windows: Use ProactorEventLoop for subprocess support
# MUST be set before importing FastAPI/Uvicorn
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, Request, Query, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl

from config import config
from browser_pool import browser_pool

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add filter to exclude health check logs
class HealthCheckFilter(logging.Filter):
    def filter(self, record):
        # Filter out uvicorn access logs for /health endpoint
        if hasattr(record, 'getMessage'):
            message = record.getMessage()
            return '/health' not in message
        return True

# Apply filter to uvicorn access logger
uvicorn_logger = logging.getLogger('uvicorn.access')
uvicorn_logger.addFilter(HealthCheckFilter())


def get_user_friendly_error_message(error):
    """Convert Playwright/Chromium error messages to user-friendly descriptions"""
    error_str = str(error).lower()
    
    if "net::err_aborted" in error_str:
        return "Request aborted by server (possibly due to large file, server timeout, or content blocking)"
    elif "net::err_connection_refused" in error_str:
        return "Connection refused by server"
    elif "net::err_name_not_resolved" in error_str:
        return "DNS resolution failed - domain not found"
    elif "net::err_internet_disconnected" in error_str:
        return "Internet connection lost"
    elif "net::err_connection_timed_out" in error_str:
        return "Connection timed out"
    elif "net::err_ssl_protocol_error" in error_str:
        return "SSL/TLS protocol error"
    elif "net::err_cert_common_name_invalid" in error_str:
        return "SSL certificate error (invalid domain)"
    elif "net::err_cert_date_invalid" in error_str:
        return "SSL certificate expired"
    elif "timeout" in error_str:
        return "Request timed out"
    elif "navigation timeout" in error_str:
        return "Page navigation timed out"
    elif "page crashed" in error_str:
        return "Browser page crashed during rendering"
    elif "browser closed" in error_str:
        return "Browser instance was closed unexpectedly"
    else:
        # Return the original error if no specific match
        return str(error)


# Lifespan context manager for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 Starting Playwright Render Service...")
    await browser_pool.initialize()
    yield
    # Shutdown
    logger.info("🛑 Stopping Playwright Render Service...")
    await browser_pool.shutdown()


# Error tracker (GlitchTip / Sentry) — no-op unless SENTRY_DSN is set
try:
    from observability import init_sentry, install_trace_id_middleware
    init_sentry("playwright-render-service")
except Exception as _exc:
    logger.warning(f"[sentry] init skipped: {_exc}")
    install_trace_id_middleware = None  # type: ignore[assignment]

app = FastAPI(
    title="Playwright Render Service",
    description="Headless browser rendering service for bypassing bot protection",
    version="1.0.0",
    lifespan=lifespan
)

if install_trace_id_middleware is not None:
    try:
        install_trace_id_middleware(app)
    except Exception as _exc:
        logger.warning(f"[sentry] trace_id middleware skipped: {_exc}")

# Distributed tracing — OTel spans → Tempo
try:
    from citra_service_utils import setup_tracing as _setup_tracing, request_id_middleware as _request_id_middleware
    app.middleware("http")(_request_id_middleware)
    _setup_tracing(app, service_name="playwright-render-service")
except ImportError:
    logger.warning("citra-service-utils not installed; distributed tracing disabled")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS if config.ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    
    # Skip logging for health checks to reduce noise
    if request.url.path != "/health":
        logger.info(f"📨 {request.method} {request.url} - {request.client.host if request.client else 'unknown'}")
    
    response = await call_next(request)
    
    process_time = time.time() - start_time
    if request.url.path != "/health":
        logger.info(f"📤 {request.method} {request.url} - {response.status_code} - {process_time:.3f}s")
    
    return response


class RenderRequest(BaseModel):
    """Request body for POST /render"""
    url: HttpUrl
    output_format: Optional[str] = None  # "html" or "pdf"
    wait_for: Optional[str] = "networkidle"  # "load", "domcontentloaded", "networkidle"
    timeout: Optional[int] = None  # Override default timeout
    inject_base_tag: Optional[bool] = True  # Inject <base> tag for relative URLs
    inject_interceptor: Optional[bool] = True  # Inject link click interceptor


class RenderResponse(BaseModel):
    """Response for successful render"""
    success: bool
    url: str
    output_format: str
    render_time_ms: int
    content_length: int


# Link interceptor script (same as in main.py)
LINK_INTERCEPTOR_SCRIPT = '''
<script>
(function() {
    // Intercept link clicks
    document.addEventListener('click', function(e) {
        var link = e.target.closest('a');
        if (link && link.href) {
            e.preventDefault();
            e.stopPropagation();
            var isDownload = link.hasAttribute('download') || /\\.(pdf|doc|docx|xls|xlsx|zip|rar|mp3|mp4)(\\?|$)/i.test(link.href);
            if (window.parent !== window) {
                window.parent.postMessage({
                    type: isDownload ? 'PROXY_DOWNLOAD_LINK' : 'PROXY_LINK_CLICK',
                    url: link.href
                }, '*');
            } else {
                window.open(link.href, '_blank');
            }
            return false;
        }
    }, true);
    
    // Intercept form submissions
    document.addEventListener('submit', function(e) {
        e.preventDefault();
        var form = e.target;
        var formData = {};
        form.querySelectorAll('input, select, textarea').forEach(function(el) {
            if (el.name && (el.type !== 'checkbox' && el.type !== 'radio' || el.checked)) {
                formData[el.name] = el.value || '';
            }
        });
        if (window.parent !== window) {
            window.parent.postMessage({
                type: 'PROXY_FORM_SUBMIT',
                action: form.action || window.location.href,
                method: form.method || 'GET',
                formData: formData
            }, '*');
        }
        return false;
    }, true);
})();
</script>
'''


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    stats = browser_pool.stats
    return {
        "status": "healthy",
        "service": "playwright-render-service",
        "browser_pool": stats,
        "default_output_format": config.DEFAULT_OUTPUT_FORMAT
    }


@app.get("/render")
async def render_get(
    url: str = Query(..., description="URL to render"),
    output_format: Optional[str] = Query(None, description="Output format: 'html' or 'pdf'"),
    wait_for: str = Query("networkidle", description="Wait condition: 'load', 'domcontentloaded', 'networkidle'"),
    timeout: Optional[int] = Query(None, description="Timeout in seconds"),
    inject_base_tag: bool = Query(True, description="Inject <base> tag for relative URLs"),
    inject_interceptor: bool = Query(True, description="Inject link click interceptor script"),
    proxy_base_url: Optional[str] = Query(None, description="Base URL for proxy links (e.g., http://localhost:8085/proxy?url=)")
):
    """
    Render a webpage and return HTML or PDF.
    
    GET endpoint for simple requests.
    """
    return await _render_page(
        url=url,
        output_format=output_format or config.DEFAULT_OUTPUT_FORMAT,
        wait_for=wait_for or config.DEFAULT_WAIT_FOR,
        timeout=timeout or config.REQUEST_TIMEOUT,
        inject_base_tag=inject_base_tag,
        inject_interceptor=inject_interceptor,
        proxy_base_url=proxy_base_url
    )


@app.post("/render")
async def render_post(request: RenderRequest):
    """
    Render a webpage and return HTML or PDF.
    
    POST endpoint for complex requests.
    """
    return await _render_page(
        url=str(request.url),
        output_format=request.output_format or config.DEFAULT_OUTPUT_FORMAT,
        wait_for=request.wait_for or config.DEFAULT_WAIT_FOR,
        timeout=request.timeout or config.REQUEST_TIMEOUT,
        inject_base_tag=request.inject_base_tag if request.inject_base_tag is not None else True,
        inject_interceptor=request.inject_interceptor if request.inject_interceptor is not None else True,
        proxy_base_url=None
    )


class RenderHTMLRequest(BaseModel):
    """Request body for POST /render-html — renders raw HTML content to PDF."""
    html: str
    output_format: Optional[str] = "pdf"


@app.post("/render-html")
async def render_html_post(request: RenderHTMLRequest):
    """Render raw HTML content to PDF without requiring a URL."""
    start_time = time.time()
    instance = None
    try:
        instance = await browser_pool.acquire()
        page = instance.page
        await page.set_content(request.html, wait_until="networkidle")
        if request.output_format == "pdf":
            pdf_bytes = await page.pdf(format="A4", print_background=True)
            render_time = int((time.time() - start_time) * 1000)
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={"X-Render-Time-Ms": str(render_time)},
            )
        else:
            content = await page.content()
            render_time = int((time.time() - start_time) * 1000)
            return Response(content=content, media_type="text/html")
    except Exception as e:
        logger.error(f"Render HTML failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if instance:
            await browser_pool.release(instance)


async def _render_page(
    url: str,
    output_format: str,
    wait_for: str,
    timeout: int,
    inject_base_tag: bool,
    inject_interceptor: bool,
    proxy_base_url: Optional[str] = None
):
    """Core rendering logic"""
    
    start_time = time.time()
    instance = None
    
    try:
        # Validate output format
        if output_format not in ["html", "pdf"]:
            raise HTTPException(status_code=400, detail=f"Invalid output_format: {output_format}. Use 'html' or 'pdf'")
        
        # Validate wait_for
        if wait_for not in ["load", "domcontentloaded", "networkidle"]:
            raise HTTPException(status_code=400, detail=f"Invalid wait_for: {wait_for}")
        
        # Detect PDF URLs - Playwright cannot navigate to direct PDF URLs
        # Browsers trigger download → net::ERR_ABORTED
        url_lower = url.lower()
        is_likely_pdf_url = (
            url_lower.endswith('.pdf') or
            'format=pdf' in url_lower or
            'type=pdf' in url_lower
        )
        if is_likely_pdf_url and output_format == "html":
            logger.warning(f"⚠️ PDF URL requested for HTML rendering — this will likely fail: {url}")
            logger.warning(f"⚠️ PDFs should be fetched via simple HTTP and parsed with PyMuPDF")
            # Don't hard-reject — let it try and fail naturally so caller gets proper error
            # The caller (proxy in main.py) handles this case and won't send PDFs here
        
        logger.info(f"🌐 Rendering {url} (format: {output_format}, wait: {wait_for})")
        
        # Acquire browser from pool
        instance = await browser_pool.acquire()
        page = instance.page
        
        # Navigate to URL with retry fallback strategy
        response = None
        last_error = None
        
        # Try with requested wait strategy first
        try:
            response = await page.goto(
                url,
                wait_until=wait_for,
                timeout=timeout * 1000  # Convert to milliseconds
            )
            
            if response and response.status >= 400:
                logger.warning(f"⚠️ Page returned status {response.status}: {url}")
        
        except Exception as e:
            last_error = e
            logger.warning(f"⚠️ Failed with wait_until={wait_for}: {get_user_friendly_error_message(e)}")
            
            # Retry with 'domcontentloaded' if original was 'networkidle' (more lenient)
            if wait_for == "networkidle":
                try:
                    logger.info(f"🔄 Retrying with wait_until=domcontentloaded...")
                    response = await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=timeout * 1000
                    )
                    logger.info(f"✅ Retry succeeded with domcontentloaded")
                    last_error = None  # Clear error since retry worked
                    
                    if response and response.status >= 400:
                        logger.warning(f"⚠️ Page returned status {response.status}: {url}")
                        
                except Exception as e2:
                    logger.error(f"❌ Retry also failed: {get_user_friendly_error_message(e2)}")
                    last_error = e2
            
        # If both attempts failed, raise error
        if last_error:
            logger.error(f"❌ Navigation failed after all retries: {get_user_friendly_error_message(last_error)}")
            raise HTTPException(status_code=502, detail=f"Failed to load page: {get_user_friendly_error_message(last_error)}")
        
        # Wait a bit for any remaining JS to execute
        await asyncio.sleep(0.5)
        
        # Generate output based on format
        if output_format == "pdf":
            # Generate PDF
            pdf_bytes = await page.pdf(
                format="A4",
                print_background=True,
                margin={
                    "top": "0.5in",
                    "right": "0.5in",
                    "bottom": "0.5in",
                    "left": "0.5in"
                }
            )
            
            render_time = int((time.time() - start_time) * 1000)
            logger.info(f"✅ PDF generated in {render_time}ms | Size: {len(pdf_bytes)} bytes | {url[:60]}")
            
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'inline; filename="rendered.pdf"',
                    "Access-Control-Allow-Origin": "*",
                    "X-Render-Time-Ms": str(render_time),
                    "X-Output-Format": "pdf"
                }
            )
        
        else:
            # Generate HTML
            html_content = await page.content()
            
            # Parse URL for base tag
            parsed_url = urlparse(url)
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            
            # Inject base tag if requested
            if inject_base_tag:
                charset_meta = '<meta charset="UTF-8">'
                base_tag = f'<base href="{base_url}/">'
                head_injection = f'{charset_meta}\n{base_tag}'
                
                if '<head>' in html_content:
                    html_content = html_content.replace('<head>', f'<head>\n{head_injection}\n', 1)
                elif '<HEAD>' in html_content:
                    html_content = html_content.replace('<HEAD>', f'<HEAD>\n{head_injection}\n', 1)
                else:
                    html_content = f'<!DOCTYPE html>\n<html><head>\n{head_injection}\n</head>\n{html_content}</html>'
            
            # Inject link interceptor if requested
            if inject_interceptor:
                if '</body>' in html_content:
                    html_content = html_content.replace('</body>', f'{LINK_INTERCEPTOR_SCRIPT}\n</body>', 1)
                elif '</BODY>' in html_content:
                    html_content = html_content.replace('</BODY>', f'{LINK_INTERCEPTOR_SCRIPT}\n</BODY>', 1)
                else:
                    html_content = f'{html_content}\n{LINK_INTERCEPTOR_SCRIPT}'
            
            render_time = int((time.time() - start_time) * 1000)
            logger.info(f"✅ HTML rendered in {render_time}ms | Size: {len(html_content)} bytes | {url[:60]}")
            
            return Response(
                content=html_content.encode('utf-8'),
                media_type="text/html; charset=utf-8",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "X-Frame-Options": "ALLOWALL",
                    "Content-Security-Policy": "frame-ancestors *",
                    "X-Render-Time-Ms": str(render_time),
                    "X-Output-Format": "html"
                }
            )
    
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        logger.error(f"⏱️ Timeout rendering: {url}")
        raise HTTPException(status_code=504, detail="Render timeout")
    except Exception as e:
        logger.error(f"❌ Render error: {get_user_friendly_error_message(e)}")
        raise HTTPException(status_code=500, detail=f"Render failed: {get_user_friendly_error_message(e)}")
    finally:
        # Release browser back to pool
        if instance:
            await browser_pool.release(instance)


@app.get("/stats")
async def get_stats():
    """Get service statistics"""
    return {
        "browser_pool": browser_pool.stats,
        "config": {
            "default_output_format": config.DEFAULT_OUTPUT_FORMAT,
            "request_timeout": config.REQUEST_TIMEOUT,
            "stealth_mode": config.STEALTH_MODE
        }
    }


@app.get("/screenshot")
async def screenshot(
    url: str = Query(..., description="URL to screenshot"),
    width: int = Query(1200, ge=100, le=3840, description="Viewport width"),
    height: int = Query(630, ge=100, le=2160, description="Viewport height"),
    timeout: int = Query(None, ge=1, le=60, description="Timeout in seconds"),
    full_page: bool = Query(False, description="Capture the full scrollable page, not just the viewport"),
    settle_ms: int = Query(0, ge=0, le=15000, description="Extra wait after load for late-rendering content (e.g. charts)"),
):
    """
    Capture a PNG screenshot of a URL. Returns image/png bytes.

    `full_page=true` captures the whole scrollable page (dashboards taller than
    the viewport). `settle_ms` adds a post-load delay so client-rendered charts
    (echarts/canvas) finish painting before the shot. Apps that hold persistent
    connections (SSE/polling) never reach `networkidle`, so we wait on
    `domcontentloaded` and rely on `settle_ms` instead.
    """
    instance = None
    try:
        effective_timeout = (timeout or config.REQUEST_TIMEOUT) * 1000
        instance = await browser_pool.acquire()
        page = instance.page

        await page.set_viewport_size({"width": width, "height": height})
        await page.goto(url, wait_until="domcontentloaded", timeout=effective_timeout)
        if settle_ms:
            await page.wait_for_timeout(settle_ms)

        shot_kwargs = {"type": "png"}
        if full_page:
            shot_kwargs["full_page"] = True
        else:
            shot_kwargs["clip"] = {"x": 0, "y": 0, "width": width, "height": height}
        png_bytes = await page.screenshot(**shot_kwargs)

        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=86400",
            },
        )
    except asyncio.TimeoutError:
        logger.error(f"⏱️ Screenshot timeout for: {url}")
        raise HTTPException(status_code=504, detail="Screenshot timeout")
    except Exception as e:
        logger.error(f"❌ Screenshot error: {get_user_friendly_error_message(e)}")
        raise HTTPException(status_code=500, detail=f"Screenshot failed: {get_user_friendly_error_message(e)}")
    finally:
        if instance:
            await browser_pool.release(instance)


if __name__ == "__main__":
    # Set event loop policy BEFORE importing uvicorn (critical for Windows)
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    import uvicorn
    
    # Disable reload on Windows to prevent event loop issues
    # For development, manually restart after code changes
    use_reload = sys.platform != 'win32'
    
    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        reload=use_reload,
        loop="asyncio"  # Use asyncio event loop
    )
