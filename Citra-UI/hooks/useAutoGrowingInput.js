// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

import { useState, useRef, useCallback } from 'react';

export const useAutoGrowingInput = (options = {}) => {
  const {
    minHeight = 40,
    maxHeight = 160,
    lineHeight = 20,
    maxLines = 8,
  } = options;

  const [height, setHeight] = useState(minHeight);
  const [isAtMaxHeight, setIsAtMaxHeight] = useState(false);
  const textInputRef = useRef(null);

  const handleContentSizeChange = useCallback((event) => {
    const { contentSize } = event.nativeEvent;
    const newHeight = Math.min(
      Math.max(minHeight, contentSize.height + 20),
      maxHeight
    );
    
    setHeight(newHeight);
    setIsAtMaxHeight(newHeight >= maxHeight);
  }, [minHeight, maxHeight]);

  const scrollToEnd = useCallback(() => {
    if (textInputRef.current && isAtMaxHeight) {
      setTimeout(() => {
        // TextInput doesn't have scrollToEnd on web, so check if it exists first
        if (typeof textInputRef.current.scrollToEnd === 'function') {
          textInputRef.current.scrollToEnd({ animated: true });
        } else if (textInputRef.current.scrollTop !== undefined) {
          // For web, use scrollTop instead
          textInputRef.current.scrollTop = textInputRef.current.scrollHeight;
        }
      }, 50);
    }
  }, [isAtMaxHeight]);

  const resetHeight = useCallback(() => {
    setHeight(minHeight);
    setIsAtMaxHeight(false);
  }, [minHeight]);

  return {
    height,
    isAtMaxHeight,
    textInputRef,
    handleContentSizeChange,
    scrollToEnd,
    resetHeight,
  };
};