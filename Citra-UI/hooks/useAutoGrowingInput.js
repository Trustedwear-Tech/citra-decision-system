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