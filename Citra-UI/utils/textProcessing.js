// Text processing utilities for markdown, LaTeX, and content parsing
import React from 'react';
import { Platform, Text } from 'react-native';

/**
 * Sanitize Mermaid diagram code - minimal processing to preserve valid syntax
 * @param {string} mermaidCode - Raw Mermaid diagram code
 * @returns {string} - Sanitized Mermaid code
 */
const ensureMermaidFlowchartLayout = (code) => {
  if (!code || typeof code !== 'string') {
    return code;
  }

  const normalized = code
    .replace(/\r\n/g, '\n')
    .replace(/\t/g, ' ')
    .replace(/\u00A0/g, ' ');

  const headerMatch = normalized.match(/^\s*(flowchart|graph)\s+[A-Za-z]+\b/i);

  if (!headerMatch) {
    return normalized.trim();
  }

  const header = headerMatch[0].trim();
  const remainder = normalized.slice(headerMatch[0].length).trim();

  // Aggressively split statements: insert newline BEFORE each new node-id that starts an edge
  // This handles mega-lines like: "A --> B B --> C C --> D" => "A --> B\nB --> C\nC --> D"
  // Strategy: look for patterns like "] NodeID -->" or "} NodeID -->" or end of previous statement + NodeID
  
  let body = remainder;
  
  // 1) Split before style/linkStyle/classDef/click directives
  body = body.replace(/\s+(style|linkStyle|classDef|click)\s+/gi, '\n$1 ');
  
  // 2) Split after closing bracket/brace/paren when followed by space + NodeID (starting a new statement)
  //    Matches: "] B" or "} C" etc. when B/C is followed by an arrow
  body = body.replace(/(\]|\}|\))(\s+)([A-Za-z][\w]*)\s+(-->|---|-.->|==>)/g, '$1\n$3 $4');
  
  // 3) Split before any NodeID that follows a completed arrow statement
  //    Matches sequences like "NodeA --> NodeB NodeC --> NodeD"
  //    We look for: (arrow) (target node/label) (space) (next source node) (arrow)
  //    Pattern: after any arrow target (which could be [label], {label}, or plain node), if we see a new NodeID starting an edge
  body = body.replace(/((?:-->|---|-.->|==>)\s*(?:[A-Za-z][\w]*|\[[^\]]+\]|\{[^}]+\}|\([^)]+\)))(\s+)([A-Za-z][\w]*)\s+(-->|---|-.->|==>)/g, '$1\n$3 $4');
  
  // 4) Catch any remaining cases where plain node is followed by another node starting an edge
  //    This handles cases like "O --> P D --> Q" where P is a plain node (no label)
  body = body.replace(/(\s[A-Za-z][\w]*)(\s+)([A-Za-z][\w]*)\s+(-->|---|-.->|==>)/g, '$1\n$3 $4');

  const bodyLines = body
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) => line.replace(/\s{2,}/g, ' ')); // collapse multiple spaces

  if (bodyLines.length === 0) {
    return header;
  }

  return `${header}\n${bodyLines.map((line) => `  ${line}`).join('\n')}`.trim();
};

export const sanitizeMermaidCode = (mermaidCode) => {
  if (!mermaidCode || typeof mermaidCode !== 'string') {
    return '';
  }

  try {
    let normalized = mermaidCode
      .replace(/\r\n/g, '\n')
      .replace(/\t/g, ' ')
      .replace(/\u00A0/g, ' ')
      .replace(/<br\s*\/?>/gi, '<br/>')
      .trim();

    // CRITICAL FIX 1: Square brackets inside node labels cause parse errors
    // Pattern: NodeID[Label with [nested] brackets]
    // Fix: Replace nested brackets with parentheses
    // We need to match node definitions carefully to avoid breaking edge syntax
    let prevNormalized = '';
    let iterations = 0;
    const maxIterations = 10;
    
    while (prevNormalized !== normalized && iterations < maxIterations) {
      prevNormalized = normalized;
      iterations++;
      
      // Match node definitions with labels: NodeID[Label content]
      // But the label content may contain nested [brackets]
      // We need to find and fix these nested brackets
      normalized = normalized.replace(/(\w+)\[([^\n]*?)\]/g, (match, nodeId, labelContent) => {
        // Check if label contains nested square brackets
        if (labelContent.includes('[') || labelContent.includes(']')) {
          console.log(`🔧 [MERMAID_SANITIZE] Found nested brackets in: ${match.substring(0, 50)}...`);
          // Replace nested brackets with parentheses
          const fixedLabel = labelContent
            .replace(/\[/g, '(')
            .replace(/\]/g, ')');
          console.log(`🔧 [MERMAID_SANITIZE] Fixed to: ${nodeId}[${fixedLabel.substring(0, 50)}...]`);
          return `${nodeId}[${fixedLabel}]`;
        }
        return match;
      });
    }

    // CRITICAL FIX 2: Parentheses inside curly braces {} cause parse errors
    // Replace parentheses in decision node labels
    // Pattern: {text with (parentheses) inside}
    prevNormalized = '';
    iterations = 0;
    
    while (prevNormalized !== normalized && iterations < maxIterations) {
      prevNormalized = normalized;
      iterations++;
      
      normalized = normalized.replace(/\{([^}]*)\(([^)]*)\)([^}]*)\}/g, (match, before, inside, after) => {
        const sanitized = `${before}${inside}${after}`.replace(/\s+/g, ' ').trim();
        console.log(`🔧 [MERMAID_SANITIZE] Removed parentheses from decision node: ${match} -> {${sanitized}}`);
        return `{${sanitized}}`;
      });
    }

    const headerNormalized = normalized.replace(/^(flowchart|graph)\s+[A-Za-z]+\b\s*/i, (match) => `${match.trim()}\n`);

    return ensureMermaidFlowchartLayout(headerNormalized);
  } catch (error) {
    console.error('❌ [MERMAID_SANITIZE] Error sanitizing Mermaid code:', error.message);
    return mermaidCode; // Return original if sanitization fails
  }
};

// Parse message content to identify code blocks, images, and text
export const parseMessageContent = (text) => {
  // Safety check for undefined or null text, convert to string if needed
  if (text === null || text === undefined) {
    return [{
      type: 'text',
      content: ''
    }];
  }

  // Convert to string if it's not already a string
  const textStr = typeof text === 'string' ? text : String(text);

  // Enhanced regex patterns
  const codeBlockRegex = /```(\w+)?\n?([\s\S]*?)```/g;
  const imageRegex = /!\[([^\]]*)\]\s*\(([^)]+)\)/g;
  const inlineCodeRegex = /`([^`]+)`/g;
  
  const parts = [];
  let lastIndex = 0;
  
  // Create array of all matches with their types
  const allMatches = [];
  
  // Find all code blocks (including Mermaid diagrams)
  let match;
  while ((match = codeBlockRegex.exec(textStr)) !== null) {
    const language = (match[1] || 'text').toLowerCase();
    const content = match[2].trim();
    
    // Check if this is a Mermaid diagram
    const isMermaidDiagram = 
      language === 'mermaid' || 
      content.startsWith('graph ') || 
      content.startsWith('flowchart ') || 
      content.startsWith('timeline') || 
      content.startsWith('gantt') ||
      content.match(/^(graph|flowchart|timeline|gantt|sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|pie)\s/);
    
    if (isMermaidDiagram) {
      // Sanitize the Mermaid code to fix common syntax errors
      const sanitizedContent = sanitizeMermaidCode(content);
      allMatches.push({
        type: 'mermaid',
        index: match.index,
        length: match[0].length,
        content: sanitizedContent,
        fullMatch: match[0]
      });
    } else {
      allMatches.push({
        type: 'codeblock',
        index: match.index,
        length: match[0].length,
        content: content,
        language: language,
        fullMatch: match[0]
      });
    }
  }
  
  // Reset regex
  codeBlockRegex.lastIndex = 0;
  
  // Find all images
  while ((match = imageRegex.exec(textStr)) !== null) {
    allMatches.push({
      type: 'image',
      index: match.index,
      length: match[0].length,
      alt: match[1] || '',
      src: match[2],
      fullMatch: match[0]
    });
  }
  
  // Sort matches by index
  allMatches.sort((a, b) => a.index - b.index);
  
  // Process all matches in order
  for (const match of allMatches) {
    // Add text before current match
    if (match.index > lastIndex) {
      const beforeText = textStr.slice(lastIndex, match.index);
      if (beforeText.trim()) {
        parts.push({
          type: 'text',
          content: beforeText.trim()
        });
      }
    }

    // Add the match based on its type
    if (match.type === 'codeblock') {
      parts.push({
        type: 'codeblock',
        content: match.content,
        language: match.language
      });
    } else if (match.type === 'mermaid') {
      parts.push({
        type: 'mermaid',
        content: match.content
      });
    } else if (match.type === 'image') {
      parts.push({
        type: 'image',
        alt: match.alt,
        src: match.src
      });
    }

    lastIndex = match.index + match.length;
  }

  // Add remaining text
  if (lastIndex < textStr.length) {
    const remainingText = textStr.slice(lastIndex);
    if (remainingText.trim()) {
      parts.push({
        type: 'text',
        content: remainingText.trim()
      });
    }
  }

  // If no matches found, treat as single text part
  if (parts.length === 0) {
    parts.push({
      type: 'text',
      content: textStr
    });
  }

  return parts;
};

// Markdown formatting function for web platform
export const formatTitle = (text, theme) => {
  // For mobile platforms, return plain text
  if (Platform.OS !== 'web') {
    return text;
  }

  // For web platform, format markdown properly
  const parts = [];
  let lastIndex = 0;

  // Combined regex for markdown formatting including LaTeX math
  const markdownRegex = /(\\\[(.+?)\\\])|(\\\((.+?)\\\))|(###\s+(.+?)(?=\n|$))|(##\s+(.+?)(?=\n|$))|(\*\*(.+?)\*\*)|(\*(.+?)\*)|(__(.+?)__)|(_(.+?)_)|(~~(.+?)~~)|(`(.+?)`)/g;
  
  let match;
  while ((match = markdownRegex.exec(text)) !== null) {
    // Add text before the match
    if (match.index > lastIndex) {
      parts.push(<Text key={`text-${lastIndex}`} style={{ color: theme.botMessageText }}>{text.slice(lastIndex, match.index)}</Text>);
    }

    if (match[1]) {
      // \[ Display Math \] - Convert LaTeX to readable format
      const mathContent = match[2];
      const readableMath = mathContent
        .replace(/\\frac\{([^}]+)\}\{([^}]+)\}/g, '($1)/($2)') // Convert fractions
        .replace(/\\sqrt\{([^}]+)\}/g, '√($1)') // Convert square roots
        .replace(/\\cdot/g, '·') // Convert multiplication dots
        .replace(/\\times/g, '×') // Convert multiplication crosses
        .replace(/\\div/g, '÷') // Convert division
        .replace(/\\pm/g, '±') // Convert plus-minus
        .replace(/\\leq/g, '≤') // Convert less than or equal
        .replace(/\\geq/g, '≥') // Convert greater than or equal
        .replace(/\\neq/g, '≠') // Convert not equal
        .replace(/\\alpha/g, 'α').replace(/\\beta/g, 'β').replace(/\\gamma/g, 'γ') // Greek letters
        .replace(/\\sum/g, 'Σ').replace(/\\int/g, '∫').replace(/\\infty/g, '∞') // Math symbols
        .replace(/\{([^}]+)\}/g, '$1') // Remove remaining braces
        .replace(/\\/g, ''); // Remove remaining backslashes
      
      parts.push(<Text key={`displaymath-${match.index}`} style={{ 
        fontSize: 18,
        backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f9fa',
        color: theme.isDark ? '#ffffff' : '#333333',
        paddingHorizontal: 16,
        paddingVertical: 12,
        marginVertical: 8,
        borderRadius: 8,
        borderLeftWidth: 4,
        borderLeftColor: theme.isDark ? '#ffffff' : '#007acc',
        textAlign: 'center',
        fontWeight: '500',
        lineHeight: 24
      }}>{readableMath}</Text>);
    } else if (match[3]) {
      // \( Inline Math \) - Convert LaTeX to readable format
      const mathContent = match[4];
      const readableMath = mathContent
        .replace(/\\frac\{([^}]+)\}\{([^}]+)\}/g, '($1)/($2)')
        .replace(/\\sqrt\{([^}]+)\}/g, '√($1)')
        .replace(/\\cdot/g, '·')
        .replace(/\\times/g, '×')
        .replace(/\\div/g, '÷')
        .replace(/\\pm/g, '±')
        .replace(/\\leq/g, '≤')
        .replace(/\\geq/g, '≥')
        .replace(/\\neq/g, '≠')
        .replace(/\\alpha/g, 'α').replace(/\\beta/g, 'β').replace(/\\gamma/g, 'γ')
        .replace(/\{([^}]+)\}/g, '$1')
        .replace(/\\/g, '');
      
      parts.push(<Text key={`inlinemath-${match.index}`} style={{ 
        backgroundColor: theme.isDark ? '#3a3a3a' : '#f0f0f0',
        color: theme.isDark ? '#ffffff' : '#333333',
        paddingHorizontal: 8,
        paddingVertical: 4,
        borderRadius: 4,
        fontSize: 16,
        fontWeight: '500'
      }}>{readableMath}</Text>);
    } else if (match[5]) {
      // ### Heading (H3)
      parts.push(<Text key={`h3-${match.index}`} style={{ fontSize: 18, fontWeight: 'bold', color: theme.botMessageText, marginVertical: 4 }}>{match[6]}</Text>);
    } else if (match[7]) {
      // ## Heading (H2)  
      parts.push(<Text key={`h2-${match.index}`} style={{ fontSize: 20, fontWeight: 'bold', color: theme.botMessageText, marginVertical: 6 }}>{match[8]}</Text>);
    } else if (match[9]) {
      // **Bold**
      parts.push(<Text key={`bold-${match.index}`} style={{ fontWeight: 'bold', color: theme.botMessageText }}>{match[10]}</Text>);
    } else if (match[11]) {
      // *Italic*
      parts.push(<Text key={`italic-${match.index}`} style={{ fontStyle: 'italic', color: theme.botMessageText }}>{match[12]}</Text>);
    } else if (match[13]) {
      // __Bold__
      parts.push(<Text key={`bold2-${match.index}`} style={{ fontWeight: 'bold', color: theme.botMessageText }}>{match[14]}</Text>);
    } else if (match[15]) {
      // _Italic_
      parts.push(<Text key={`italic2-${match.index}`} style={{ fontStyle: 'italic', color: theme.botMessageText }}>{match[16]}</Text>);
    } else if (match[17]) {
      // ~~Strikethrough~~
      parts.push(<Text key={`strike-${match.index}`} style={{ textDecorationLine: 'line-through', color: theme.botMessageText }}>{match[18]}</Text>);
    } else if (match[19]) {
      // `Code`
      parts.push(<Text key={`code-${match.index}`} style={{ 
        backgroundColor: theme.isDark ? '#404040' : '#f3f4f4',
        color: theme.isDark ? '#ffffff' : '#e83e8c',
        paddingHorizontal: 4,
        paddingVertical: 2,
        borderRadius: 3,
        fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
        fontSize: 14
      }}>{match[20]}</Text>);
    }

    lastIndex = match.index + match[0].length;
  }

  // Add remaining text
  if (lastIndex < text.length) {
    parts.push(<Text key={`text-${lastIndex}`} style={{ color: theme.botMessageText }}>{text.slice(lastIndex)}</Text>);
  }

  // If no markdown found, return plain text
  if (parts.length === 0) {
    return text;
  }

  return parts;
};

// Mobile-friendly math processor
export const processLaTeXForMobile = (text) => {
  return text
    // Handle display math \[...\] - Convert to clean format
    .replace(/\\\[([\s\S]*?)\\\]/g, (match, math) => {
      const processedMath = math
        .replace(/\\frac\{([^}]+)\}\{([^}]+)\}/g, '($1)/($2)')
        .replace(/\\sqrt\{([^}]+)\}/g, '√($1)')
        .replace(/\\cdot/g, '·')
        .replace(/\\times/g, '×')
        .replace(/\\div/g, '÷')
        .replace(/\\pm/g, '±')
        .replace(/\\leq/g, '≤')
        .replace(/\\geq/g, '≥')
        .replace(/\\neq/g, '≠')
        .replace(/\\alpha/g, 'α').replace(/\\beta/g, 'β').replace(/\\gamma/g, 'γ')
        .replace(/\\delta/g, 'δ').replace(/\\epsilon/g, 'ε').replace(/\\theta/g, 'θ')
        .replace(/\\lambda/g, 'λ').replace(/\\mu/g, 'μ').replace(/\\pi/g, 'π')
        .replace(/\\sigma/g, 'σ').replace(/\\phi/g, 'φ').replace(/\\omega/g, 'ω')
        .replace(/\\sum/g, 'Σ').replace(/\\int/g, '∫').replace(/\\infty/g, '∞')
        .replace(/\\partial/g, '∂').replace(/\\nabla/g, '∇')
        .replace(/\{([^}]+)\}/g, '$1')
        .replace(/\\/g, '');
      
      return `\n\n${processedMath}\n\n`;
    })
    // Handle inline math \(...\) - Convert to clean format  
    .replace(/\\\(([\s\S]*?)\\\)/g, (match, math) => {
      const processedMath = math
        .replace(/\\frac\{([^}]+)\}\{([^}]+)\}/g, '($1)/($2)')
        .replace(/\\sqrt\{([^}]+)\}/g, '√($1)')
        .replace(/\\cdot/g, '·')
        .replace(/\\times/g, '×')
        .replace(/\\div/g, '÷')
        .replace(/\\pm/g, '±')
        .replace(/\\leq/g, '≤')
        .replace(/\\geq/g, '≥')
        .replace(/\\neq/g, '≠')
        .replace(/\\alpha/g, 'α').replace(/\\beta/g, 'β').replace(/\\gamma/g, 'γ')
        .replace(/\\delta/g, 'δ').replace(/\\epsilon/g, 'ε').replace(/\\theta/g, 'θ')
        .replace(/\\lambda/g, 'λ').replace(/\\mu/g, 'μ').replace(/\\pi/g, 'π')
        .replace(/\\sigma/g, 'σ').replace(/\\phi/g, 'φ').replace(/\\omega/g, 'ω')
        .replace(/\\sum/g, 'Σ').replace(/\\int/g, '∫').replace(/\\infty/g, '∞')
        .replace(/\\partial/g, '∂').replace(/\\nabla/g, '∇')
        .replace(/\{([^}]+)\}/g, '$1')
        .replace(/\\/g, '');
      
      return ` ${processedMath} `;
    });
};
