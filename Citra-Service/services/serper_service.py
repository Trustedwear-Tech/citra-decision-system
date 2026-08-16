# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Serper API Service
Provides Google search functionality using Serper API
"""

import requests
import logging
import os
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class SerperService:
    """Service for interacting with Serper API"""
    
    def __init__(self):
        self.api_key = os.getenv("SERPER_API_KEY")
        self.api_url = os.getenv("SERPER_API_URL", "https://google.serper.dev/search")
        
        if not self.api_key:
            logger.warning("⚠️ SERPER_API_KEY not set in environment variables")
    
    def search(
        self, 
        query: str, 
        country: str = "in",  # India
        language: str = "en",  # English
        page: int = 1,
        num_results: int = 10
    ) -> Dict[str, Any]:
        """
        Perform a Google search using Serper API
        
        Args:
            query: Search query string
            country: Country code (default: "in" for India)
            language: Language code (default: "en" for English)
            page: Page number for pagination
            num_results: Number of results per page
            
        Returns:
            Structured search results from Serper API
        """
        try:
            logger.info(f"🔍 Serper search: '{query}' (country: {country}, lang: {language})")
            
            headers = {
                "X-Api-Key": self.api_key,
                "Content-Type": "application/json"
            }
            
            payload = {
                "q": query,
                "gl": country,
                "hl": language,
                "page": page,
                "num": num_results
            }
            
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=10
            )
            
            response.raise_for_status()
            
            data = response.json()
            
            # Structure the response for easier frontend consumption
            structured_results = self._structure_results(data)
            
            logger.info(f"✅ Serper search completed: {len(structured_results.get('organic', []))} organic results")
            
            return {
                "success": True,
                "query": query,
                "results": structured_results
            }
            
        except requests.exceptions.Timeout:
            logger.error("⏱️ Serper API timeout")
            return {
                "success": False,
                "error": "Search request timed out. Please try again."
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Serper API error: {str(e)}")
            return {
                "success": False,
                "error": f"Search failed: {str(e)}"
            }
        except Exception as e:
            logger.error(f"❌ Unexpected error in Serper search: {str(e)}")
            return {
                "success": False,
                "error": "An unexpected error occurred during search"
            }
    
    def _structure_results(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Structure Serper API response for frontend consumption
        
        Args:
            data: Raw Serper API response
            
        Returns:
            Structured results with knowledge graph, organic results, PAA, related searches
        """
        structured = {}
        
        # Knowledge Graph
        if "knowledgeGraph" in data:
            kg = data["knowledgeGraph"]
            structured["knowledgeGraph"] = {
                "title": kg.get("title", ""),
                "type": kg.get("type", ""),
                "description": kg.get("description", ""),
                "descriptionSource": kg.get("descriptionSource", ""),
                "descriptionLink": kg.get("descriptionLink", ""),
                "imageUrl": kg.get("imageUrl", ""),
                "website": kg.get("website", ""),
                "attributes": kg.get("attributes", {})
            }
        
        # Organic Results
        if "organic" in data:
            structured["organic"] = []
            for result in data["organic"]:
                structured["organic"].append({
                    "position": result.get("position", 0),
                    "title": result.get("title", ""),
                    "link": result.get("link", ""),
                    "snippet": result.get("snippet", ""),
                    "sitelinks": result.get("sitelinks", [])
                })
        
        # People Also Ask
        if "peopleAlsoAsk" in data:
            structured["peopleAlsoAsk"] = []
            for paa in data["peopleAlsoAsk"]:
                structured["peopleAlsoAsk"].append({
                    "question": paa.get("question", ""),
                    "snippet": paa.get("snippet", ""),
                    "title": paa.get("title", ""),
                    "link": paa.get("link", "")
                })
        
        # Related Searches
        if "relatedSearches" in data:
            structured["relatedSearches"] = [
                rs.get("query", "") for rs in data["relatedSearches"]
            ]
        
        return structured


# Singleton instance
serper_service = SerperService()
