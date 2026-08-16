# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Web Content Fetcher Service
Fetches and extracts clean content from web pages and PDFs for AI processing
Calls proxy function directly for full anti-bot protection
"""

import requests
from bs4 import BeautifulSoup
import logging
from typing import Dict, Any, Optional
from urllib.parse import urlparse
import re
import io
from fastapi import Request
from fastapi.datastructures import Headers

# PDF extraction support (using PyMuPDF/fitz - faster and more robust than PyPDF2)
try:
    import fitz  # PyMuPDF
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
    logger = logging.getLogger(__name__)
    logger.warning("⚠️ PyMuPDF not installed. PDF extraction will be disabled. Install with: pip install PyMuPDF")

logger = logging.getLogger(__name__)

# Import proxy function from main.py (will be set after main.py loads)
_proxy_function = None

def set_proxy_function(proxy_func):
    """Set the proxy function reference from main.py"""
    global _proxy_function
    _proxy_function = proxy_func
    logger.info("✅ Proxy function registered in web_content_fetcher")


class WebContentFetcher:
    """Service for fetching and extracting web page content"""
    
    def __init__(self):
        self.timeout = 30
        self.max_content_length = 3000000  # 3MB max
    
    async def fetch_page(self, url: str) -> Dict[str, Any]:
        """
        Fetch and extract clean content from a web page or PDF
        Calls proxy function directly for full anti-bot + Playwright fallback
        
        Args:
            url: URL of the web page or PDF to fetch
            
        Returns:
            Dictionary with page content, title, description, and metadata
        """
        global _proxy_function
        
        try:
            logger.info(f"🌐 Fetch page request: {url}")
            
            # Validate URL
            if not self._is_valid_url(url):
                return {
                    "success": False,
                    "error": "Invalid URL format"
                }
            
            # Call proxy function directly (same as /proxy endpoint)
            if _proxy_function is None:
                logger.error("❌ Proxy function not initialized")
                return {
                    "success": False,
                    "error": "Proxy function not available"
                }
            
            # Detect if URL is a PDF (by extension or query params)
            url_lower = url.lower()
            is_likely_pdf = (
                url_lower.endswith('.pdf') or
                'format=pdf' in url_lower or
                'type=pdf' in url_lower or
                '/pdf/' in url_lower
            )
            
            # Create a mock Request object for the proxy function
            mock_request = type('Request', (), {
                'headers': Headers({}),
                'base_url': 'http://localhost:8085/citra-ai'
            })()
            
            # ALWAYS use Tier 1 (simple HTTP) first for speed (~100ms)
            # Playwright is the LAST resort — only used when Tier 1 is blocked/fails
            # The proxy function handles Tier 1 → Tier 2 fallback automatically
            logger.info(f"📞 Calling proxy (Tier 1 first): {url} (PDF: {is_likely_pdf})")
            response = await _proxy_function(
                request=mock_request,
                url=url,
                token=None,  # No auth needed for internal calls
                fast=True,  # Fast mode - skip URL rewriting for content extraction
                output_format="html",
                use_playwright=False  # NEVER skip Tier 1 — let proxy handle fallback to Playwright
            )
            
            # Extract content from Response object (FastAPI uses .body, requests uses .content)
            if hasattr(response, 'body'):
                content_bytes = response.body
            else:
                logger.error("❌ Unexpected response type from proxy")
                return {
                    "success": False,
                    "error": "Invalid proxy response"
                }
            
            # Get content type from response headers
            content_type = ""
            if hasattr(response, 'headers'):
                content_type = response.headers.get('content-type', '').lower()
            is_pdf = 'application/pdf' in content_type or url.lower().endswith('.pdf')
            
            logger.info(f"📄 Content-Type: {content_type}, is_pdf: {is_pdf}")
            
            # Check content length
            content_length = len(content_bytes)
            if content_length > self.max_content_length and not is_pdf:
                logger.warning(f"⚠️ Page too large: {content_length} bytes")
                return {
                    "success": False,
                    "error": "Page content is too large to process"
                }
            
            # Handle PDF files
            if is_pdf:
                return self._extract_pdf_content(url, content_bytes)
            
            # Parse HTML
            soup = BeautifulSoup(content_bytes, 'lxml')
            
            # Extract metadata
            title = self._extract_title(soup)
            description = self._extract_description(soup)
            
            # Extract main content
            content = self._extract_main_content(soup)
            
            # Clean and format content
            clean_content = self._clean_content(content, is_pdf=False)
            
            # Thin-content detection: if Tier 1 returned very little text,
            # the page is likely JavaScript-heavy (SPA/React) with only a
            # server-rendered hero section.  Retry with Playwright which
            # executes JS and captures the fully-rendered DOM.
            word_count = len(clean_content.split())
            MIN_WORDS_THRESHOLD = 150
            if word_count < MIN_WORDS_THRESHOLD and not is_likely_pdf:
                logger.warning(
                    f"⚠️ Thin content detected ({word_count} words < {MIN_WORDS_THRESHOLD}). "
                    f"Retrying with Playwright for JS-heavy page: {url}"
                )
                try:
                    pw_response = await _proxy_function(
                        request=mock_request,
                        url=url,
                        token=None,
                        fast=True,
                        output_format="html",
                        use_playwright=True  # Force Playwright for JS rendering
                    )
                    pw_bytes = pw_response.body if hasattr(pw_response, 'body') else None
                    if pw_bytes:
                        pw_soup = BeautifulSoup(pw_bytes, 'lxml')
                        pw_title = self._extract_title(pw_soup) or title
                        pw_desc = self._extract_description(pw_soup) or description
                        pw_content = self._extract_main_content(pw_soup)
                        pw_clean = self._clean_content(pw_content, is_pdf=False)
                        if len(pw_clean.split()) > word_count:
                            logger.info(
                                f"✅ Playwright yielded richer content: "
                                f"{len(pw_clean.split())} words (was {word_count})"
                            )
                            title, description = pw_title, pw_desc
                            clean_content = pw_clean
                except Exception as pw_err:
                    logger.warning(f"⚠️ Playwright retry failed (using Tier 1 content): {pw_err}")
            
            logger.info(f"✅ Page fetched successfully: {len(clean_content)} characters")
            
            return {
                "success": True,
                "url": url,
                "title": title,
                "description": description,
                "content": clean_content,
                "content_length": len(clean_content)
            }
            
        except requests.exceptions.Timeout:
            logger.error(f"⏱️ Timeout fetching page: {url}")
            return {
                "success": False,
                "error": "Request timed out. The page took too long to load."
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Error fetching page {url}: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to fetch page: {str(e)}"
            }
        except Exception as e:
            logger.error(f"❌ Unexpected error fetching {url}: {str(e)}")
            return {
                "success": False,
                "error": "An unexpected error occurred while fetching the page"
            }
    
    def _extract_pdf_content(self, url: str, pdf_bytes: bytes) -> Dict[str, Any]:
        """
        Extract text content from PDF file (first 500 pages)
        
        Args:
            url: Original PDF URL
            pdf_bytes: Raw PDF bytes
            
        Returns:
            Dictionary with extracted PDF content
        """
        try:
            if not PDF_SUPPORT:
                logger.error("❌ PDF extraction attempted but PyMuPDF not installed")
                fallback_msg = (
                    "PDF extraction is unavailable on the server (PyMuPDF missing). "
                    "Install PyMuPDF or use OCR to extract text."
                )
                return {
                    "success": True,  # Graceful success to avoid UI hard-fail
                    "url": url,
                    "title": "PDF Document",
                    "description": fallback_msg,
                    "content": fallback_msg,
                    "content_length": len(fallback_msg),
                    "is_pdf": True,
                    "total_pages": 0,
                    "extracted_pages": 0,
                    "warning": "pdf_extract_disabled"
                }
            
            logger.info(f"📚 Extracting PDF content from {len(pdf_bytes)} bytes")
            
            # Open PDF from bytes using PyMuPDF/fitz
            pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
            
            # Get PDF metadata
            total_pages = pdf_document.page_count
            logger.info(f"📄 PDF has {total_pages} pages")
            
            # Extract title from metadata or URL
            title = "PDF Document"
            metadata = pdf_document.metadata
            if metadata:
                if metadata.get('title'):
                    title = metadata['title']
                elif metadata.get('subject'):
                    title = metadata['subject']
            
            # If no metadata title, extract from URL
            if title == "PDF Document":
                from pathlib import Path
                url_path = Path(urlparse(url).path)
                if url_path.name:
                    title = url_path.stem.replace('_', ' ').replace('-', ' ').title()
            
            # Extract text from first 500 pages
            max_pages = min(500, total_pages)
            extracted_text = []
            
            for page_num in range(max_pages):
                try:
                    page = pdf_document[page_num]
                    text = page.get_text("text")  # Extract text in plain text format
                    
                    if text and text.strip():
                        extracted_text.append(f"--- Page {page_num + 1} ---\n{text.strip()}\n")
                        
                except Exception as page_error:
                    logger.warning(f"⚠️ Failed to extract text from page {page_num + 1}: {page_error}")
                    continue
            
            # Close the PDF document
            pdf_document.close()
            
            if not extracted_text:
                logger.error("❌ No text extracted from PDF - likely image-based")
                fallback_msg = (
                    "No extractable text found in this PDF. It may be image-based or scanned. "
                    "Try running OCR or uploading a text-based version."
                )
                return {
                    "success": True,  # Graceful fallback so UI can still respond
                    "url": url,
                    "title": title,
                    "description": f"PDF document with {total_pages} pages (no extractable text)",
                    "content": fallback_msg,
                    "content_length": len(fallback_msg),
                    "is_pdf": True,
                    "total_pages": total_pages,
                    "extracted_pages": 0,
                    "warning": "image_only_pdf"
                }
            
            # Combine all pages
            full_text = "\n\n".join(extracted_text)
            
            # Clean and format
            clean_content = self._clean_content(full_text, is_pdf=True)
            
            logger.info(f"✅ PDF extracted successfully: {max_pages}/{total_pages} pages, {len(clean_content)} characters")
            
            return {
                "success": True,
                "url": url,
                "title": title,
                "description": f"PDF document with {total_pages} pages (extracted first {max_pages} pages)",
                "content": clean_content,
                "content_length": len(clean_content),
                "is_pdf": True,
                "total_pages": total_pages,
                "extracted_pages": max_pages
            }
            
        except Exception as e:
            logger.error(f"❌ PDF extraction error: {str(e)}")
            fallback_msg = (
                "Could not extract text from this PDF. It may be blocked, encrypted, or image-only. "
                "Consider downloading and running OCR."
            )
            return {
                "success": True,  # Keep pipeline flowing; surface warning to UI
                "url": url,
                "title": "PDF Document",
                "description": fallback_msg,
                "content": fallback_msg,
                "content_length": len(fallback_msg),
                "is_pdf": True,
                "total_pages": 0,
                "extracted_pages": 0,
                "warning": "pdf_extract_error"
            }
    
    def _is_valid_url(self, url: str) -> bool:
        """Validate URL format and block internal/private IPs (SSRF protection)"""
        try:
            result = urlparse(url)
            if not all([result.scheme, result.netloc]) or result.scheme not in ['http', 'https']:
                return False
            from security.url_validator import is_safe_url
            return is_safe_url(url)
        except Exception:
            return False
    
    def _extract_title(self, soup: BeautifulSoup) -> str:
        """Extract page title"""
        # Try <title> tag
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        
        # Try og:title meta tag
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return og_title["content"].strip()
        
        # Try h1 tag
        h1 = soup.find("h1")
        if h1:
            return h1.get_text().strip()
        
        return "Untitled Page"
    
    def _extract_description(self, soup: BeautifulSoup) -> str:
        """Extract page description"""
        # Try meta description
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            return meta_desc["content"].strip()
        
        # Try og:description
        og_desc = soup.find("meta", property="og:description")
        if og_desc and og_desc.get("content"):
            return og_desc["content"].strip()
        
        return ""
    
    def _extract_main_content(self, soup: BeautifulSoup) -> str:
        """Extract full main content from page (all sections, not just first)"""
        # Remove unwanted elements
        for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe', 'noscript']):
            element.decompose()
        
        # Strategy: find the best full-page content wrapper
        # 1. Try <main> or [role="main"] — these wrap the entire page content
        main_content = soup.select_one('main') or soup.select_one('[role="main"]')
        
        if not main_content:
            # 2. Try collecting ALL <article> elements (blogs, news sites)
            articles = soup.select('article')
            if articles:
                # Combine text from all articles
                combined = '\n\n'.join(a.get_text(separator='\n', strip=True) for a in articles)
                if len(combined.split()) >= 100:
                    return combined
        
        if not main_content:
            # 3. Try common content container selectors
            for selector in ['.content', '#content', '.article', '.post', '.entry-content', '#main-content']:
                main_content = soup.select_one(selector)
                if main_content and len(main_content.get_text(separator=' ', strip=True).split()) >= 100:
                    break
                main_content = None
        
        # 4. Fallback to body
        if not main_content:
            main_content = soup.body
        
        if not main_content:
            return ""
        
        # Extract text from the full container
        text = main_content.get_text(separator='\n', strip=True)
        
        return text
    
    def _clean_content(self, content: str, is_pdf: bool = False) -> str:
        """Clean and format extracted content"""
        # Remove excessive whitespace
        content = re.sub(r'\n\s*\n\s*\n+', '\n\n', content)
        
        # Remove excessive spaces
        content = re.sub(r' +', ' ', content)
        
        # Trim
        content = content.strip()
        
        # Limit length for AI processing
        # PDFs: 500 pages * 500 words * 5 chars = 1,250,000 chars
        # Webpages: Same limit (500 pages)
        max_chars = 1250000
        
        if len(content) > max_chars:
            approx_pages = len(content) // (500 * 5)  # Estimate pages
            content = content[:max_chars] + f"\n\n[Content truncated to first 500 pages of approximately {approx_pages} total pages...]"
        
        return content


# Singleton instance
web_content_fetcher = WebContentFetcher()
