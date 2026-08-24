// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

// API configuration and constants
import { Dimensions } from 'react-native';
import { logEnvironmentInfo } from './debug';
import { API_CONFIG } from '../config/config';

// API Configuration from environment
export const TRANSCRIBE_URL = API_CONFIG.TRANSCRIBE_URL;
export const CITRA_SERVICE = API_CONFIG.CITRA_SERVICE_URL;
export const AUDIO_UPLOAD_URL = API_CONFIG.AUDIO_UPLOAD_URL;
export const AUDIO_DATA_URL = API_CONFIG.AUDIO_DATA_URL;
export const DOCUMENT_URL = API_CONFIG.DOCUMENT_URL;
export const CHAT_URL = API_CONFIG.CHAT_URL;
export const NOTE_URL = API_CONFIG.NOTE_URL;
export const QUERY_URL = API_CONFIG.QUERY_URL;
export const TRANSCRIPT_URL = API_CONFIG.TRANSCRIPT_URL;
// Note: user_id removed - now using user email dynamically in App.js

// Screen dimensions
export const { width } = Dimensions.get('window');

// Initialize environment logging
logEnvironmentInfo();
