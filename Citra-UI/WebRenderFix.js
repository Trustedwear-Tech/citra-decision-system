// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

import React, { useState, useEffect, useRef } from 'react';
import { Platform } from 'react-native';

// Web-specific rendering fix for DeepSeek responses
const WebRenderFix = ({ children, message, theme }) => {
  const [forceRender, setForceRender] = useState(0);
  const lastContentRef = useRef('');
  const renderTimeoutRef = useRef(null);

  useEffect(() => {
    // Only apply fix for web platform
    if (Platform.OS !== 'web') return;

    // Check if we have content but it might not be rendering
    if (message && message.text && message.text !== lastContentRef.current) {
      lastContentRef.current = message.text;
      
      // Clear any existing timeout
      if (renderTimeoutRef.current) {
        clearTimeout(renderTimeoutRef.current);
      }

      // Set a timeout to force re-render if content isn't displaying
      renderTimeoutRef.current = setTimeout(() => {
        // Force a re-render by updating state
        setForceRender(prev => prev + 1);
      }, 100); // Small delay to allow normal rendering
    }

    return () => {
      if (renderTimeoutRef.current) {
        clearTimeout(renderTimeoutRef.current);
      }
    };
  }, [message?.text]);

  // For web, add a key to force re-render when needed
  if (Platform.OS === 'web') {
    return (
      <div key={`render-${forceRender}`} style={{ width: '100%' }}>
        {children}
      </div>
    );
  }

  return children;
};

export default WebRenderFix;
