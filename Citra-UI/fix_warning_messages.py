#!/usr/bin/env python3
"""
Script to fix remaining warning messages in App.js
Removes all remaining upload warning messages that are still being added to chat
"""

import re

# Read the file
with open('App.js', 'r', encoding='utf-8') as f:
    content = f.read()

# Pattern to match the warning message blocks
pattern = re.compile(
    r'(\s+)const warningMessage = {\s+id: uuidv4\(\),\s+text: `⚠️ You have selected multiple workspace items.*?};.*?setMessages\(prev => {\s+const newMessages = \[\.\.\.prev, warningMessage\];\s+storageManager\.storeMessagePairs\(.*?\);\s+return newMessages;\s+}\);\s+scheduleScrollToBottom\(1\);',
    re.DOTALL
)

# Count matches
matches = pattern.findall(content)
print(f"Found {len(matches)} warning message blocks to fix")

# Replace with commented out versions and toast notifications
def replace_warning_block(match):
    indent = match.group(1)
    return f'''{indent}// Use toast notification instead of chat message
{indent}const warningText = "⚠️ Multiple workspaces selected. Upload will use the first selected workspace.";
{indent}showUploadToast(warningText);
{indent}
{indent}// Skip adding warning message to chat - we use toast instead
{indent}// const warningMessage = {{
{indent}//   id: uuidv4(),
{indent}//   text: "Warning message removed - now using toast notification",
{indent}//   sender: 'bot',
{indent}//   hideActions: true,
{indent}// }};
{indent}//
{indent}// setMessages(prev => {{
{indent}//   const newMessages = [...prev, warningMessage];
{indent}//   storageManager.storeMessagePairs(newMessages, activeSessionId);
{indent}//   return newMessages;
{indent}// }});
{indent}// scheduleScrollToBottom(1);'''

# Replace all matches
new_content = pattern.sub(replace_warning_block, content)

# Write back to file
with open('App.js', 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Fixed all remaining warning message blocks!")
print("They are now commented out and use toast notifications instead.")
