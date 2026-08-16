# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# ============================  Vision Processing Module  =============================
# Purpose: Centralized vision processing for images and OCR document processing
# Features: Unified vision API calls, OCR support, image processing, text extraction
# --------------------------------------------------------------------------------------

import os
import io
import json
import logging
import asyncio
from typing import Dict, Any, Optional, List
from pathlib import Path
from fastapi import HTTPException
from PIL import Image

# LLM SDK removed — OCR handled by qwen_ocr_proxy
genai = None
genai_types = None
_llm_AVAILABLE = False

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

class VisionProcessor:
    """Centralized vision processing for images and OCR documents"""
    
    def __init__(self):
        self.vision_api_enabled = os.getenv("VISION_API_ENABLED_FOR_DOCUMENT", "true").lower() == "true"
        self.logger = logging.getLogger(__name__)
        self.llm_model = os.getenv("VISION_MODEL", "qwen/qwen3-vl-32b-instruct")
        self._llm_prompt = (
            "Extract all readable text from this image. Return plain text only, preserving line breaks and ordering."
        )
        self.llm_max_output_tokens = int(os.getenv("llm_MAX_OUTPUT_TOKENS", "20000"))
        self._llm_client = None  # Deprecated — OCR now uses qwen_ocr_proxy
        self._qwen_available = False
        try:
            from qwen_ocr_proxy import extract_text_from_image
            self._qwen_available = True
            self.logger.info("✅ Vision processor initialized with qwen_ocr_proxy")
        except ImportError:
            self.logger.warning("⚠️ qwen_ocr_proxy not available. OCR will be unavailable.")
        
        self.logger.info(f"🔍 Vision Processor initialized:")
        self.logger.info(f"   - API Enabled: {self.vision_api_enabled}")
        self.logger.info(f"   - llm model: {self.llm_model}")
    
    def is_vision_enabled(self) -> bool:
        """Check if vision processing is enabled"""
        return self.vision_api_enabled and self._qwen_available
    
    def is_image_file(self, filename: str) -> bool:
        """Check if file is an image"""
        file_ext = Path(filename).suffix.lower()
        return file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp']
    
    def is_pdf_file(self, filename: str) -> bool:
        """Check if file is a PDF"""
        file_ext = Path(filename).suffix.lower()
        return file_ext == '.pdf'
    
    def is_ocr_supported_file(self, filename: str) -> bool:
        """Check if file supports OCR processing"""
        return self.is_image_file(filename) or self.is_pdf_file(filename)
    
    async def process_with_vision_api(
        self, 
        file_content: bytes, 
        filename: str, 
        content_type: str,
        use_ocr: bool = False,
        ocr_options: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Process file with Vision API (images or OCR documents)
        
        Args:
            file_content: File bytes
            filename: Original filename
            content_type: MIME content type
            use_ocr: Whether to use OCR processing
            ocr_options: Additional OCR options
            
        Returns:
            Dictionary with extracted text and metadata
        """
        if not self._qwen_available:
            raise HTTPException(
                status_code=503,
                detail="Vision OCR client is not available. Ensure qwen_ocr_proxy is configured."
            )

        if not self.is_image_file(filename):
            raise HTTPException(
                status_code=400,
                detail="OCR currently supports image files (.jpg, .jpeg, .png, .gif, .bmp, .tiff, .webp)."
            )

        prompt = self._llm_prompt
        max_output_tokens: Optional[int] = None
        if ocr_options:
            prompt = ocr_options.get("prompt") or prompt
            max_output_tokens = ocr_options.get("max_output_tokens")

        if not max_output_tokens:
            max_output_tokens = self.llm_max_output_tokens
        self.logger.info(f"🤖 Vision OCR request prepared for {filename}")

        extracted_text = await self._extract_text_with_llm(
            file_content,
            prompt,
            max_output_tokens=max_output_tokens
        )

        metadata = {
            "llm_model": self.llm_model,
            "prompt_used": prompt,
            "vision_api_enabled": self.vision_api_enabled
        }

        return {
            "text": extracted_text,
            "metadata": metadata,
            "file_type": "pdf" if self.is_pdf_file(filename) else "image"
        }

    async def process_batch_with_vision_api(
        self,
        image_payloads: List[Dict[str, Any]],
        prompt: Optional[str] = None,
        max_output_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """Process multiple images in a single Vision OCR invocation."""

        if not self._qwen_available:
            raise HTTPException(
                status_code=503,
                detail="Vision OCR client is not available. Ensure qwen_ocr_proxy is configured."
            )

        if not image_payloads:
            raise ValueError("At least one image payload is required for batch OCR")

        prompt_to_use = prompt or self._llm_prompt
        effective_tokens = max_output_tokens or self.llm_max_output_tokens

        image_bytes: List[bytes] = []
        for payload in image_payloads:
            content = payload.get("file_content")
            if content is None:
                raise ValueError("Each payload must include 'file_content' bytes")
            if isinstance(content, bytearray):
                content = bytes(content)
            if not isinstance(content, (bytes, bytearray)):
                raise ValueError("Image payloads must be provided as bytes")
            image_bytes.append(content)

        extracted_text = await self._extract_text_with_llm_batch(
            image_bytes,
            prompt_to_use,
            max_output_tokens=effective_tokens
        )

        metadata = {
            "llm_model": self.llm_model,
            "prompt_used": prompt_to_use,
            "vision_api_enabled": self.vision_api_enabled,
            "batch_size": len(image_payloads),
            "max_output_tokens": effective_tokens
        }

        return {
            "text": extracted_text,
            "metadata": metadata,
            "file_type": "image_batch"
        }

    async def _extract_text_with_llm(
        self,
        image_bytes: bytes,
        prompt: str,
        max_output_tokens: Optional[int] = None
    ) -> str:
        """Run llm multimodal extraction for a single image in a worker thread."""

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._run_llm_sync,
            prompt,
            [image_bytes],
            max_output_tokens
        )

    async def _extract_text_with_llm_batch(
        self,
        image_payloads: List[bytes],
        prompt: str,
        max_output_tokens: Optional[int] = None
    ) -> str:
        """Run llm multimodal extraction for multiple images in a single request."""

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._run_llm_sync,
            prompt,
            image_payloads,
            max_output_tokens
        )

    def _run_llm_sync(
        self,
        prompt: str,
        image_payloads: List[bytes],
        max_output_tokens: Optional[int] = None
    ) -> str:
        """Synchronous OCR call using qwen_ocr_proxy (replaces llm generate_content)."""
        from qwen_ocr_proxy import extract_text_from_image

        if not image_payloads:
            raise ValueError("At least one image payload is required for OCR")

        all_texts: List[str] = []
        for payload in image_payloads:
            try:
                text = extract_text_from_image(
                    image_bytes=payload,
                    prompt=prompt,
                )
                all_texts.append(text or "")
                self.logger.debug("OCR extracted %d chars from image", len(text or ""))
            except Exception as exc:
                self.logger.error(f"❌ qwen_ocr_proxy failed for image: {exc}")
                all_texts.append("")

        combined = "\n\n".join(t for t in all_texts if t)
        self.logger.info(f"✅ OCR extracted {len(combined)} characters total")
        return combined.strip()
    
    
# Global vision processor instance
_vision_processor = None

def get_vision_processor() -> VisionProcessor:
    """Get global vision processor instance"""
    global _vision_processor
    if _vision_processor is None:
        _vision_processor = VisionProcessor()
    return _vision_processor

# Convenience functions for backward compatibility
async def extract_text_with_vision_api(
    file_content: bytes, 
    filename: str, 
    content_type: str,
    use_ocr: bool = False,
    ocr_options: Optional[Dict] = None
) -> str:
    """Extract text using vision API (backward compatibility function)."""
    processor = get_vision_processor()
    result = await processor.process_with_vision_api(
        file_content=file_content,
        filename=filename,
        content_type=content_type,
        use_ocr=use_ocr,
        ocr_options=ocr_options
    )
    return result["text"]


async def extract_text_batch_with_vision_api(
    image_payloads: List[Dict[str, Any]],
    prompt: Optional[str] = None,
    max_output_tokens: Optional[int] = None
) -> str:
    """Extract text from multiple images using a single Vision OCR request."""
    processor = get_vision_processor()
    result = await processor.process_batch_with_vision_api(
        image_payloads=image_payloads,
        prompt=prompt,
        max_output_tokens=max_output_tokens
    )
    return result["text"]

    