# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# ============================  Citra AI Configuration  =============================
# Purpose: Configuration for Citra AI Service chat history and messaging
# Chat History: Configurable MongoDB storage vs browser-only storage
# --------------------------------------------------------------------------------------

import os
import logging
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

class MemoryAssistConfig:
    """Configuration class for Citra AI Service"""
    
    def __init__(self):
        # Chat History Configuration - load from environment variables
        self.enable_mongodb_chat_history = os.getenv("ENABLE_MONGODB_CHAT_HISTORY", "true").lower() == "true"
        
        # Chat Session Configuration
        self.max_chat_history_messages = int(os.getenv("MAX_CHAT_HISTORY_MESSAGES", "20"))
        
        # Log configuration on initialization
        self._log_configuration()
    
    def _log_configuration(self):
        """Log current Citra AI configuration"""
        logger.info("🧠 Citra AI Configuration:")
        logger.info(f"   - MongoDB chat history enabled: {self.enable_mongodb_chat_history}")
        logger.info(f"   - Max chat history messages: {self.max_chat_history_messages}")
        if self.enable_mongodb_chat_history:
            logger.info("   - Mode: Full chat history (MongoDB + Browser)")
        else:
            logger.info("   - Mode: Browser-only chat history (no MongoDB storage)")
    
    def is_chat_history_enabled(self) -> bool:
        """Check if MongoDB chat history storage is enabled"""
        return self.enable_mongodb_chat_history
    
    def get_max_chat_messages(self) -> int:
        """Get maximum number of chat messages to send to query API"""
        return self.max_chat_history_messages
    
    def get_chat_storage_mode(self) -> str:
        """Get the current chat storage mode"""
        return "mongodb_storage" if self.enable_mongodb_chat_history else "browser_only"
    
    def validate_configuration(self) -> bool:
        """Validate that configuration is reasonable"""
        # Validate max messages is reasonable
        if self.max_chat_history_messages < 1:
            logger.warning(f"⚠️ Max chat messages ({self.max_chat_history_messages}) is less than 1")
            return False
        return True

# Global configuration instance
config = MemoryAssistConfig()

# Convenience functions for easy access
def is_chat_history_enabled() -> bool:
    """Quick check if MongoDB chat history storage is enabled"""
    return config.is_chat_history_enabled()

def get_max_chat_messages() -> int:
    """Get maximum number of chat messages to send to query API"""
    return config.get_max_chat_messages()

def get_chat_storage_mode() -> str:
    """Get the current chat storage mode"""
    return config.get_chat_storage_mode()

# Configuration validation
if __name__ == "__main__":
    # Test configuration
    test_config = MemoryAssistConfig()
    print("Citra AI Configuration Test:")
    print(f"MongoDB chat history enabled: {test_config.is_chat_history_enabled()}")
    print(f"Max chat messages: {test_config.get_max_chat_messages()}")
    print(f"Chat storage mode: {test_config.get_chat_storage_mode()}")
    print(f"Configuration valid: {test_config.validate_configuration()}")
