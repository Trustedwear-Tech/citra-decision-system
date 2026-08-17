<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Text Selection Fix for Citra AI UI

## Problem
Text selection and copy functionality was not working in the Citra AI UI, including:
- Chat input box
- Chat message bubbles (both user and bot messages)
- Text content throughout the interface

## Root Cause
The issue was caused by overly broad CSS rules that prevented text selection:
- Global `user-select: none` being applied to UI elements
- Missing `user-select: text` declarations on text input and content elements
- React Native Web TextInput components not having proper web-specific styles

## Solution Applied

### 1. Created Dedicated CSS File
- **File**: `styles/textSelection.css`
- **Purpose**: Comprehensive CSS rules to enable text selection on appropriate elements
- **Key Rules**:
  - Enable text selection on chat messages, inputs, and text content
  - Override `non-selectable` class for text content
  - Proper selection styling with blue highlight

### 2. Updated Main CSS in App.js
- Made `non-selectable` class more specific to UI controls only
- Added explicit `user-select: text` rules for text content
- Enhanced text input styling

### 3. Enhanced React Components

#### ModernChatComponents.js
- Added `className` attributes for CSS targeting
- Added inline `userSelect: 'text'` styles for cross-browser compatibility
- Applied to both web and mobile text input variants
- Enhanced message bubble text selection

#### Message.js Component
- Updated all Text components to support text selection
- Added proper className attributes
- Applied `userSelect: 'text'` inline styles

#### Modern Styles
- Updated `modernWebTextInput` and `modernMessageText` styles
- Added text selection support to modern UI components

### 4. Debug Tools (Removed)
The previous development-only debug script (`debug/textSelectionFix.js`) has been removed to avoid accidental global overrides. Text selection now relies on scoped CSS and component props. If you need diagnostics, use the browser DevTools to inspect computed styles on affected elements.

## Files Modified

1. **styles/textSelection.css** (NEW) - Comprehensive text selection CSS
2. **App.js** - Updated main CSS rules and added debug import
3. **components/ui/ModernChatComponents.js** - Enhanced text input and message components
4. **components/message/Message.js** - Updated message text components
5. **styles/modernStyles.js** - Added text selection to modern styles
6. (Removed) Development debug tools were previously included as `debug/textSelectionFix.js` but are no longer part of the app.

## How to Test

### After Starting the App:
1. **Chat Input Box**: Click and drag to select text - should work
2. **Type and Select**: Type in chat input, then select your text - should work
3. **Message Bubbles**: Click and drag on any chat message - should work
4. **Copy Functionality**: Select text and Ctrl+C (or Cmd+C) - should copy
5. Use DevTools: Inspect computed styles to confirm `user-select: text` on inputs and message text

### Expected Results:
- ✅ Text selection works in chat input
- ✅ Text selection works in all chat messages
- ✅ Copy (Ctrl+C/Cmd+C) works properly
- ✅ Selection highlights with blue color
- ✅ No interference with UI controls (buttons, icons)

## Browser Compatibility
- ✅ Chrome/Chromium (primary target)
- ✅ Firefox
- ✅ Safari
- ✅ Edge

## Technical Details

### CSS Strategy:
1. **Selective Non-Selection**: Only disable selection on actual UI controls
2. **Explicit Text Selection**: Enable selection on all text content
3. **Cascade Override**: Use `!important` to override React Native Web defaults
4. **Cross-Browser**: Include all vendor prefixes (-webkit-, -moz-, -ms-)

### React Native Web Considerations:
- React Native Web transforms TextInput to HTML input/textarea
- CSS must target both React Native styles and generated HTML
- Inline styles provide additional fallback
- className attributes ensure CSS rules apply correctly

## Troubleshooting

If text selection still doesn't work:

1. Check computed styles: Verify `user-select: text` on the element and ancestors
2. Validate CSS: Ensure `styles/textSelection.css` is loaded and rules apply to the element
3. Emergency: Temporarily add `style="user-select:text!important"` via DevTools to validate hypothesis
4. **Check CSS Loading**: Verify `textSelection.css` loads in Network tab
5. **Inspect Elements**: Check computed styles show `user-select: text`

## Future Maintenance

- Monitor for new components that might need text selection
- Update CSS if new message types are added
- Test with React Native Web updates
- Consider adding automated tests for text selection functionality
