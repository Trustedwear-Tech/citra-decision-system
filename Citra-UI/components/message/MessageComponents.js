// Message rendering and animation components
import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  Image,
  Platform,
  Animated,
  Modal,
  TextInput,
  ActivityIndicator
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { styles } from '../../styles';
import { ShareButton } from '../ShareManager';
import { parseMessageContent, formatTitle, processLaTeXForMobile } from '../../utils/textProcessing';
import { CodeBlock } from '../ui/BasicComponents';
import WebRenderFix from '../../WebRenderFix';
import RichMessageRenderer from './RichMessageRenderer';

// Import markdown renderer for mobile only
let Markdown = null;
if (Platform.OS !== 'web') {
  Markdown = require('react-native-markdown-display').default;
}

// Note: Citation cleaning is now handled only in RichMessageRenderer

// Web HTML Renderer Component
export const WebHTMLRenderer = ({ content, theme, style, shouldAnimate }) => {
  const sanitizeHtml = (html) => {
    try {
      // Remove any orphaned text nodes that could cause React Native Web errors
      return html
        .replace(/>\s+</g, '><') // Remove whitespace between tags
        .replace(/^\s+|\s+$/g, '') // Trim leading/trailing whitespace
        .replace(/\n\s*\n/g, '<br><br>') // Ensure proper line breaks
        .replace(/([^>])\n([^<])/g, '$1<br>$2'); // Convert standalone newlines to <br>
    } catch (error) {
      console.error('Error sanitizing HTML:', error);
      return html || '';
    }
  };

  const processContent = (text) => {
    try {
      if (!text || typeof text !== 'string') {
        return '';
      }

      // Enhanced processing for better formatting (citation cleaning handled in RichMessageRenderer)
      let processedText = text
        // Handle headers
        .replace(/###\s+(.*?)(?:\n|$)/g, (_, heading) => `
          <h3 style="
            font-size: 18px;
            font-weight: bold;
            color: ${theme.text};
            margin: 16px 0 8px 0;
            padding: 0;
            line-height: 1.4;
          ">${heading}</h3>
        `)
        .replace(/##\s+(.*?)(?:\n|$)/g, (_, heading) => `
          <h2 style="
            font-size: 20px;
            font-weight: bold;
            color: ${theme.text};
            margin: 20px 0 10px 0;
            padding: 0;
            line-height: 1.4;
          ">${heading}</h2>
        `)
        .replace(/#\s+(.*?)(?:\n|$)/g, (_, heading) => `
          <h1 style="
            font-size: 22px;
            font-weight: bold;
            color: ${theme.text};
            margin: 24px 0 12px 0;
            padding: 0;
            line-height: 1.4;
          ">${heading}</h1>
        `)
        // Handle unordered lists
        .replace(/^\s*[-*+]\s+(.+)$/gm, (match, item) => `
          <li style="
            margin-bottom: 4px;
            line-height: 1.5;
            color: ${theme.text};
          ">${item}</li>
        `)
        // Handle ordered lists
        .replace(/^\s*\d+\.\s+(.+)$/gm, (match, item) => `
          <li style="
            margin-bottom: 4px;
            line-height: 1.5;
            color: ${theme.text};
          ">${item}</li>
        `)
        // Wrap consecutive list items in ul/ol tags
        .replace(/(<li[^>]*>.*?<\/li>\s*)+/gs, (listItems) => {
          // Determine if it's ordered or unordered based on original format
          const isOrdered = /^\s*\d+\.\s+/.test(text);
          const tag = isOrdered ? 'ol' : 'ul';
          return `<${tag} style="
            margin: 8px 0;
            padding-left: 20px;
          ">${listItems}</${tag}>`;
        });

      return processedText
        // Handle horizontal rules first (before other conversions)
        .replace(/^---+\s*$/gm, `<hr style="
          border: none;
          border-top: 2px solid ${theme.isDark ? '#4a5568' : '#e2e8f0'};
          margin: 24px 0;
          width: 100%;
        " />`)
        // Handle code blocks (before other formatting to protect content)
        .replace(/```([\s\S]*?)```/g, (match, code) => {
          const lines = code.split('\n');
          const language = lines[0].trim();
          const codeContent = lines.slice(language ? 1 : 0).join('\n').trim();
          
          return `<pre style="
            background-color: ${theme.isDark ? '#2a2a2a' : '#f8f8f8'};
            color: ${theme.isDark ? '#ffffff' : '#333333'};
            padding: 16px;
            margin: 16px 0;
            border-radius: 8px;
            border: 1px solid ${theme.isDark ? '#444444' : '#e1e4e8'};
            overflow-x: auto;
            font-family: 'Courier New', monospace;
            font-size: 14px;
            line-height: 1.4;
          "><code>${codeContent}</code></pre>`;
        })
        // Handle inline code (before bold/italic to protect code content)
        .replace(/`([^`]+)`/g, `<code style="
          background-color: ${theme.isDark ? '#404040' : '#f3f4f4'};
          color: ${theme.isDark ? '#f8f8f2' : '#e83e8c'};
          padding: 2px 4px;
          border-radius: 3px;
          font-family: 'Courier New', monospace;
          font-size: 14px;
        ">$1</code>`)
        // Handle display math \[...\]
        .replace(/\\\[([\s\S]*?)\\\]/g, (match, math) => {
          const processedMath = math
            .replace(/\\frac\{([^}]+)\}\{([^}]+)\}/g, '<span style="display: inline-block; text-align: center;"><span style="display: block; border-bottom: 1px solid; padding-bottom: 2px;">$1</span><span style="display: block; padding-top: 2px;">$2</span></span>')
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
          
          return `<div style="
            background-color: ${theme.isDark ? '#2a2a2a' : '#f8f9fa'};
            color: ${theme.isDark ? '#ffffff' : '#333333'};
            padding: 16px;
            margin: 12px 0;
            border-radius: 8px;
            border-left: 4px solid ${theme.isDark ? '#4a9eff' : '#007acc'};
            text-align: center;
            font-size: 18px;
            font-weight: 500;
            line-height: 1.5;
            font-family: 'Times New Roman', serif;
          ">${processedMath}</div>`;
        })
        // Handle inline math \(...\)
        .replace(/\\\(([\s\S]*?)\\\)/g, (match, math) => {
          const processedMath = math
            .replace(/\\frac\{([^}]+)\}\{([^}]+)\}/g, '$1/$2')
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
        
          return `<span style="
            background-color: ${theme.isDark ? '#3a3a3a' : '#f0f0f0'};
            color: ${theme.isDark ? '#ffffff' : '#333333'};
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 16px;
            font-weight: 500;
            font-family: 'Times New Roman', serif;
          ">${processedMath}</span>`;
        })
        // Handle images
        .replace(/!\[([^\]]*)\]\s*\(([^)]+)\)/g, (match, alt, src) => {
          return `<div style="text-align: center; margin: 16px 0;">
            <img src="${src}" alt="${alt}" style="
              max-width: 100%;
              height: auto;
              border-radius: 8px;
              box-shadow: 0 2px 8px rgba(0,0,0,0.1);
              border: 1px solid #e0e0e0;
            " />
            ${alt ? `<div style="font-size: 12px; color: ${theme.text}; opacity: 0.7; margin-top: 8px; font-style: italic;">${alt}</div>` : ''}
          </div>`;
        })
        // Handle bold text - robust patterns to handle multiline and complex text
        .replace(/\*\*((?:[^*]|\*(?!\*))+?)\*\*/g, '<strong>$1</strong>')
        .replace(/__((?:[^_]|_(?!_))+?)__/g, '<strong>$1</strong>')
        // Handle italic text - robust patterns to handle multiline and complex text  
        .replace(/\*((?:[^*]|\*\*)+?)\*/g, '<em>$1</em>')
        .replace(/_((?:[^_]|__)+?)_/g, '<em>$1</em>')
        // Handle strikethrough text - robust patterns to handle multiline and complex text
        .replace(/~~((?:[^~]|~(?!~))+?)~~/g, '<del style="text-decoration: line-through;">$1</del>')
        // Handle blockquotes
        .replace(/^>\s+(.+)$/gm, `<blockquote style="
          background-color: ${theme.isDark ? 'rgba(74, 158, 255, 0.1)' : 'rgba(0, 122, 204, 0.05)'};
          border-left: 4px solid ${theme.isDark ? '#4a9eff' : '#007acc'};
          padding: 12px 16px;
          margin: 12px 0;
          border-radius: 4px;
          font-style: italic;
        ">$1</blockquote>`)
        // Handle line breaks with proper spacing
        .replace(/\n\n/g, '<br><br>')
        .replace(/\n/g, '<br>');
    } catch (error) {
      console.error('Error processing content for WebHTMLRenderer:', error);
      return (text || '').replace(/\n/g, '<br>');
    }
  };

  const safeGetHtmlContent = () => {
    try {
      const processedContent = processContent(content);
      return sanitizeHtml(processedContent);
    } catch (error) {
      console.error('Error generating HTML content:', error);
      return sanitizeHtml((content || '').replace(/\n/g, '<br>'));
    }
  };

  const htmlContent = safeGetHtmlContent();

  if (Platform.OS === 'web') {
    try {
      return (
        <div 
          className={`web-bot-message ${shouldAnimate ? 'web-bot-message-content' : ''}`}
          style={{
            color: style?.color || theme.text,
            fontSize: style?.fontSize || 16,
            fontWeight: style?.fontWeight || '400',
            lineHeight: 1.6,
            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
            opacity: style?.opacity ?? 1,
          }}
          dangerouslySetInnerHTML={{ __html: htmlContent }}
        />
      );
    } catch (error) {
      console.error('Error rendering HTML content:', error);
      return (
        <div className="web-bot-message">
          {content || ''}
        </div>
      );
    }
  } else {
    return <Text style={style}>{content}</Text>;
  }
};

// Web Markdown Text Component
export const WebMarkdownText = ({ text, theme, style }) => {
  if (Platform.OS === 'web') {
    return (
      <WebHTMLRenderer
        content={text}
        theme={theme}
        style={style}
      />
    );
  } else {
    const formattedText = formatTitle(text, theme);
    
    if (typeof formattedText === 'string') {
      return <Text style={style}>{formattedText}</Text>;
    }
    
    return <Text style={style}>{formattedText}</Text>;
  }
};

// Helper function to detect rich content (tables, complex formatting)
const hasRichContent = (content) => {
  // Always use rich content renderer for consistent formatting and citation cleaning
  return true;
};

// Formatted Message Content Component
export const FormattedMessageContent = ({ content, theme, formatTitle, textColor, isUserMessage = false, onOpenReader }) => {
  if (content === null || content === undefined) {
    return null;
  }

  const contentStr = typeof content === 'string' ? content : String(content);

  // Check if content has rich formatting that should use RichMessageRenderer
  if (hasRichContent(contentStr)) {
    return <RichMessageRenderer content={contentStr} theme={theme} isUserMessage={isUserMessage} onOpenReader={onOpenReader} />;
  }

  // For non-rich content, still apply basic markdown formatting
  let parts;
  
  try {
    parts = parseMessageContent(contentStr);
  } catch (error) {
    console.error('Error parsing message content:', error);
    parts = [{ type: 'text', content: contentStr || '' }];
  }

  return (
    <View style={styles.formattedMessageContainer}>
      {parts.map((part, index) => {
        try {
          if (part.type === 'codeblock') {
            return (
              <CodeBlock
                key={index}
                code={part.content}
                language={part.language}
                theme={theme}
              />
            );
          } else if (part.type === 'image') {
            console.log('🖼️ [IMAGE_DEBUG] Rendering image part:', part.alt, 'src length:', part.src?.length);
            return (
              <View key={index} style={{ marginVertical: 8, alignItems: 'center' }}>
                <Image
                  source={{ uri: part.src }}
                  style={{
                    maxWidth: '100%',
                    width: 400,
                    height: 300,
                    resizeMode: 'contain',
                    borderRadius: 8,
                    ...(Platform.OS === 'web' && {
                      objectFit: 'contain',
                      border: '1px solid #e0e0e0',
                      boxShadow: '0 2px 8px rgba(0,0,0,0.1)'
                    })
                  }}
                  onLoad={() => {}}
                  onError={(e) => console.error('🖼️ [IMAGE_DEBUG] Image load error:', e)}
                />
                {part.alt && (
                  <Text style={{
                    fontSize: 12,
                    color: textColor || theme.botMessageText,
                    opacity: 0.7,
                    textAlign: 'center',
                    marginTop: 4,
                    fontStyle: 'italic'
                  }}>
                    {part.alt}
                  </Text>
                )}
              </View>
            );
          } else {
            if (Platform.OS === 'web') {
              return (
                <WebHTMLRenderer
                  key={index}
                  content={part.content}
                  theme={theme}
                  style={{
                    color: textColor || (isUserMessage ? theme.userMessageText : theme.botMessageText),
                    fontWeight: isUserMessage ? '500' : '400',
                    marginBottom: index < parts.length - 1 ? 8 : 0,
                    fontSize: 16,
                    lineHeight: 1.6
                  }}
                />
              );
            } else {
              // Apply basic markdown formatting for mobile even for "non-rich" content
              if (Markdown && !isUserMessage) {
                try {
                  return (
                    <Markdown
                      key={index}
                      style={{
                        body: {
                          color: textColor || theme.botMessageText,
                          fontWeight: '400',
                          fontSize: 16,
                          fontFamily: 'System'
                        },
                        paragraph: {
                          marginBottom: 8,
                          marginTop: 0,
                        },
                        strong: {
                          fontWeight: 'bold',
                        },
                        em: {
                          fontStyle: 'italic',
                        },
                        code_inline: {
                          backgroundColor: theme.isDark ? '#2a2a2a' : '#f5f5f5',
                          color: theme.isDark ? '#ff6b6b' : '#d73a49',
                          paddingHorizontal: 6,
                          paddingVertical: 3,
                          borderRadius: 4,
                          fontFamily: 'monospace',
                          fontSize: 14,
                        },
                        list_item: {
                          marginBottom: 4,
                        },
                        bullet_list: {
                          marginBottom: 8,
                        },
                        ordered_list: {
                          marginBottom: 8,
                        },
                        hr: {
                          backgroundColor: theme.isDark ? '#4a5568' : '#e2e8f0',
                          height: 2,
                          marginVertical: 24,
                          borderRadius: 1,
                        },
                        blockquote: {
                          backgroundColor: theme.isDark ? 'rgba(74, 158, 255, 0.1)' : 'rgba(0, 122, 204, 0.05)',
                          borderLeftWidth: 4,
                          borderLeftColor: theme.isDark ? '#4a9eff' : '#007acc',
                          paddingHorizontal: 16,
                          paddingVertical: 12,
                          marginVertical: 12,
                          borderRadius: 4,
                        },
                      }}
                    >
                      {processLaTeXForMobile(part.content)}
                    </Markdown>
                  );
                } catch (markdownError) {
                  console.warn('Markdown render error in FormattedMessageContent:', markdownError);
                  // Fallback to plain text
                  return (
                    <Text 
                      key={index}
                      style={[styles.messageText, { 
                        color: textColor || (isUserMessage ? theme.userMessageText : theme.botMessageText), 
                        fontWeight: isUserMessage ? '500' : '400',
                        marginBottom: index < parts.length - 1 ? 8 : 0
                      }]}
                    >
                      {part.content}
                    </Text>
                  );
                }
              } else {
                return (
                  <Text 
                    key={index}
                    style={[styles.messageText, { 
                      color: textColor || (isUserMessage ? theme.userMessageText : theme.botMessageText), 
                      fontWeight: isUserMessage ? '500' : '400',
                      marginBottom: index < parts.length - 1 ? 8 : 0
                    }]}
                  >
                    {part.content}
                  </Text>
                );
              }
            }
          }
        } catch (renderError) {
          console.error('Error rendering message part:', renderError);
          return (
            <Text 
              key={index}
              style={[styles.messageText, { 
                color: textColor || (isUserMessage ? theme.userMessageText : theme.botMessageText), 
                fontWeight: isUserMessage ? '500' : '400',
                marginBottom: index < parts.length - 1 ? 8 : 0
              }]}
            >
              {part.content || ''}
            </Text>
          );
        }
      })}
    </View>
  );
};

// Animated Formatted Content Component
export const AnimatedFormattedContent = ({ 
  text, 
  theme, 
  shouldAnimate, 
  isUpdated, 
  onAnimationComplete, 
  onProgress, 
  formatTitle 
}) => {
  const safeText = (text === null || text === undefined) ? '' : String(text);
  
  const [displayedText, setDisplayedText] = useState('');
  const [currentIndex, setCurrentIndex] = useState(0);
  const [animationDone, setAnimationDone] = useState(!shouldAnimate);

  useEffect(() => {
    if (isUpdated || !shouldAnimate) {
      setDisplayedText(safeText);
      setCurrentIndex(text.length);
      setAnimationDone(true);
      onProgress(text.length);
      onAnimationComplete();
      return;
    }
  }, [text, isUpdated, shouldAnimate]);

  useEffect(() => {
    if (isUpdated || !shouldAnimate || animationDone) return;
    
    if (currentIndex < safeText.length) {
      const timer = setTimeout(() => {
        const chunkSize = Math.min(20, safeText.length - currentIndex);
        const newIndex = currentIndex + chunkSize;
        setCurrentIndex(newIndex);
        setDisplayedText(safeText.slice(0, newIndex));
        onProgress(newIndex);
      }, 0);

      return () => clearTimeout(timer);
    } else {
      setAnimationDone(true);
      if (onAnimationComplete) {
        onAnimationComplete();
      }
    }
  }, [currentIndex, safeText, isUpdated, shouldAnimate, animationDone]);

  const textToRender = shouldAnimate && !isUpdated ? displayedText : safeText;

  if (animationDone || !shouldAnimate || isUpdated) {
    return (
      <FormattedMessageContent
        content={textToRender}
        theme={theme}
        formatTitle={formatTitle}
        isUserMessage={false}
      />
    );
  }

  if (Platform.OS === 'web') {
    return (
      <FormattedMessageContent
        content={textToRender}
        theme={theme}
        formatTitle={formatTitle}
        isUserMessage={false}
      />
    );
  } else {
    if (Markdown) {
      try {
        return (
          <Markdown
            style={{
              body: {
                color: theme.botMessageText,
                fontWeight: '400',
                fontSize: 16,
                fontFamily: 'System'
              },
              paragraph: {
                marginBottom: 8,
                marginTop: 0,
              },
              strong: {
                fontWeight: 'bold',
              },
              em: {
                fontStyle: 'italic',
              },
              code_inline: {
                backgroundColor: theme.isDark ? '#2a2a2a' : '#f5f5f5',
                color: theme.isDark ? '#ff6b6b' : '#d73a49',
                paddingHorizontal: 6,
                paddingVertical: 3,
                borderRadius: 4,
                fontFamily: 'monospace',
                fontSize: 14,
              },
              code_block: {
                backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f8f8',
                color: theme.isDark ? '#ffffff' : '#000000',
                paddingHorizontal: 12,
                paddingVertical: 8,
                borderRadius: 6,
                fontFamily: 'monospace',
                fontSize: 14,
                marginVertical: 8,
                borderWidth: 1,
                borderColor: theme.isDark ? '#444444' : '#e1e4e8',
              },
              hr: {
                backgroundColor: theme.isDark ? '#4a5568' : '#e2e8f0',
                height: 2,
                marginVertical: 24,
                borderRadius: 1,
              },
              blockquote: {
                backgroundColor: theme.isDark ? 'rgba(74, 158, 255, 0.1)' : 'rgba(0, 122, 204, 0.05)',
                borderLeftWidth: 4,
                borderLeftColor: theme.isDark ? '#4a9eff' : '#007acc',
                paddingHorizontal: 16,
                paddingVertical: 12,
                marginVertical: 12,
                borderRadius: 4,
              },
            }}
          >
            {processLaTeXForMobile(textToRender)}
          </Markdown>
        );
      } catch (error) {
        console.warn('Markdown render error in MessageComponents.js:', error);
        return (
          <Text style={[styles.messageText, { color: theme.botMessageText, fontWeight: '400' }]}>
            {textToRender}
          </Text>
        );
      }
    } else {
      return (
        <Text style={[styles.messageText, { color: theme.botMessageText, fontWeight: '400' }]}>
          {textToRender}
        </Text>
      );
    }
  }
};

// Enhanced copy function that preserves rich formatting
const copyToClipboardWithFormatting = async (messageText, theme) => {
  if (!messageText) return;
  
  try {
    if (Platform.OS === 'web') {
      // For web platform, use the Clipboard API with HTML support
      const htmlContent = convertMarkdownToHTML(messageText, theme);
      
      if (navigator.clipboard && navigator.clipboard.write) {
        // Modern browsers that support multiple formats
        await navigator.clipboard.write([
          new ClipboardItem({
            'text/html': new Blob([htmlContent], { type: 'text/html' }),
            'text/plain': new Blob([messageText], { type: 'text/plain' })
          })
        ]);
        console.log('✅ Rich text copied with formatting');
      } else if (navigator.clipboard && navigator.clipboard.writeText) {
        // Fallback for browsers that only support plain text
        await navigator.clipboard.writeText(messageText);
        console.log('✅ Plain text copied (fallback)');
      } else {
        // Ultimate fallback using document.execCommand
        const textArea = document.createElement('textarea');
        textArea.value = messageText;
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand('copy');
        document.body.removeChild(textArea);
        console.log('✅ Text copied using execCommand fallback');
      }
    } else {
      // For mobile platforms, use Expo Clipboard
      const Clipboard = await import('expo-clipboard');
      await Clipboard.setStringAsync(messageText);
      console.log('✅ Text copied on mobile');
    }
  } catch (error) {
    console.error('❌ Failed to copy text:', error);
    // Final fallback
    try {
      if (Platform.OS === 'web') {
        const textArea = document.createElement('textarea');
        textArea.value = messageText;
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand('copy');
        document.body.removeChild(textArea);
      }
    } catch (fallbackError) {
      console.error('❌ All copy methods failed:', fallbackError);
    }
  }
};

// Convert markdown to HTML for rich text copying
const convertMarkdownToHTML = (markdown, theme) => {
  if (!markdown || typeof markdown !== 'string') return '';
  
  // Normalize line endings — Windows \r\n breaks table/list regexes
  let html = markdown.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  
  html = html
    // Process tables first (before other conversions)
    .replace(/(?:^|\n)(\|[^\n]+\|\n\|[-\s:|]+\|\n(?:\|[^\n]+\|\n?)*)/g, (match, table) => {
      // Split table into lines and process
      const lines = table.trim().split('\n');
      const headerLine = lines[0];
      const separatorLine = lines[1];
      const dataLines = lines.slice(2);
      
      // Parse header
      const headers = headerLine.split('|')
        .map(cell => cell.trim())
        .filter(cell => cell.length > 0)
        .map(cell => cell.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')); // Handle bold in headers
      
      // Parse data rows
      const rows = dataLines.map(line => 
        line.split('|')
          .map(cell => cell.trim())
          .filter(cell => cell.length > 0)
          .map(cell => cell
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>') // Bold
            .replace(/\*(.*?)\*/g, '<em>$1</em>') // Italic
            .replace(/`([^`]+)`/g, '<code style="background-color: #f3f4f4; color: #e83e8c; padding: 2px 4px; border-radius: 3px; font-family: monospace;">$1</code>') // Inline code
          )
      );
      
      // Generate HTML table
      let tableHTML = `<table style="border-collapse: collapse; width: 100%; margin: 16px 0; border: 1px solid #ddd;">`;
      
      // Header
      tableHTML += `<thead><tr style="background-color: #f8f9fa;">`;
      headers.forEach(header => {
        tableHTML += `<th style="border: 1px solid #ddd; padding: 12px 8px; text-align: left; font-weight: bold; background-color: #f8f9fa;">${header}</th>`;
      });
      tableHTML += `</tr></thead>`;
      
      // Body
      tableHTML += `<tbody>`;
      rows.forEach((row, rowIndex) => {
        const rowStyle = rowIndex % 2 === 0 ? 'background-color: #ffffff;' : 'background-color: #f8f9fa;';
        tableHTML += `<tr style="${rowStyle}">`;
        row.forEach(cell => {
          tableHTML += `<td style="border: 1px solid #ddd; padding: 8px; vertical-align: top;">${cell}</td>`;
        });
        tableHTML += `</tr>`;
      });
      tableHTML += `</tbody></table>`;
      
      return tableHTML;
    })
    
    // Headers (after table processing)
    .replace(/###\s+(.*?)(?:\n|$)/g, '<h3 style="font-size: 18px; font-weight: bold; color: #000000; margin: 16px 0 8px 0;">$1</h3>')
    .replace(/##\s+(.*?)(?:\n|$)/g, '<h2 style="font-size: 20px; font-weight: bold; color: #000000; margin: 20px 0 10px 0;">$1</h2>')
    .replace(/#\s+(.*?)(?:\n|$)/g, '<h1 style="font-size: 22px; font-weight: bold; color: #000000; margin: 24px 0 12px 0;">$1</h1>')
    
    // Lists
    .replace(/^\s*[-*+]\s+(.+)$/gm, '<li style="margin-bottom: 4px; line-height: 1.5;">$1</li>')
    .replace(/^\s*\d+\.\s+(.+)$/gm, '<li style="margin-bottom: 4px; line-height: 1.5;">$1</li>')
    
    // Bold and italic (but not in already processed tables)
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    
    // Inline code (but not in already processed tables)
    .replace(/`([^`]+)`/g, '<code style="background-color: #f3f4f4; color: #e83e8c; padding: 2px 4px; border-radius: 3px; font-family: monospace;">$1</code>')
    
    // Code blocks
    .replace(/```[\s\S]*?```/g, (match) => {
      const code = match.replace(/```(\w+)?\n?/, '').replace(/\n?```$/, '');
      return `<pre style="background-color: #f8f8f8; color: #000000; padding: 12px; border-radius: 6px; font-family: monospace; white-space: pre-wrap; border: 1px solid #e1e4e8; margin: 10px 0;"><code>${code}</code></pre>`;
    })
    
    // Blockquotes
    .replace(/^>\s+(.+)$/gm, '<blockquote style="background-color: rgba(0, 122, 204, 0.05); border-left: 4px solid #007acc; padding: 10px 12px; margin: 10px 0; border-radius: 4px;">$1</blockquote>')
    
    // Line breaks
    .replace(/\n\n/g, '</p><p style="margin: 8px 0; line-height: 1.6;">')
    .replace(/\n/g, '<br>');
    
  // Wrap consecutive list items in ul tags
  html = html.replace(/(<li[^>]*>.*?<\/li>\s*)+/gs, '<ul style="margin: 8px 0; padding-left: 20px;">$&</ul>');
  
  // Wrap in paragraphs if not already structured (but avoid wrapping tables)
  if (!html.includes('<h1>') && !html.includes('<h2>') && !html.includes('<h3>') && !html.includes('<ul>') && !html.includes('<blockquote>') && !html.includes('<table>')) {
    html = `<p style="margin: 8px 0; line-height: 1.6; color: #000000; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; font-size: 14px;">${html}</p>`;
  }
  
  // Wrap in HTML document structure for better Word compatibility
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="Generator" content="Citra AI">
<style>
body { 
  font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
  font-size: 14px; 
  line-height: 1.6; 
  color: #000000; 
  margin: 0; 
  padding: 16px; 
}
p { margin: 8px 0; }
h1, h2, h3 { color: #000000; }
ul { margin: 8px 0; padding-left: 20px; }
li { margin-bottom: 4px; }
code { 
  background-color: #f3f4f4; 
  color: #e83e8c; 
  padding: 2px 4px; 
  border-radius: 3px; 
  font-family: monospace; 
}
pre { 
  background-color: #f8f8f8; 
  padding: 12px; 
  border-radius: 6px; 
  border: 1px solid #e1e4e8; 
  margin: 10px 0; 
}
blockquote { 
  background-color: rgba(0, 122, 204, 0.05); 
  border-left: 4px solid #007acc; 
  padding: 10px 12px; 
  margin: 10px 0; 
  border-radius: 4px; 
}
table { 
  border-collapse: collapse; 
  width: 100%; 
  margin: 16px 0; 
  border: 1px solid #ddd; 
}
th { 
  border: 1px solid #ddd; 
  padding: 12px 8px; 
  text-align: left; 
  font-weight: bold; 
  background-color: #f8f9fa; 
}
td { 
  border: 1px solid #ddd; 
  padding: 8px; 
  vertical-align: top; 
}
tr:nth-child(even) { 
  background-color: #f8f9fa; 
}
tr:nth-child(odd) { 
  background-color: #ffffff; 
}
</style>
</head>
<body>
${html}
</body>
</html>`;
};

// Message Actions Component
// Now uses ShareButton for link-based chat sharing instead of native share
export const MessageActions = ({ message, theme, onEdit, onCopy, onShare, onSaveToVault, sessionId, sessionName }) => {
  const isUserMessage = message.sender === 'user';
  // 'idle' | 'loading' | 'saved' | 'error'
  const [saveState, setSaveState] = useState('idle');
  const saveTimeoutRef = useRef(null);

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
    };
  }, []);

  const handleCopy = async () => {
    try {
      await copyToClipboardWithFormatting(message.text, theme);
      // You can add a toast notification here if desired
      console.log('✅ Message copied with rich formatting');
    } catch (error) {
      console.error('❌ Failed to copy message:', error);
      // Fallback to the original onCopy if provided
      if (onCopy) {
        onCopy(message.text);
      }
    }
  };

  const handleSaveToVault = async () => {
    if (!message.text || !message.text.trim() || saveState === 'loading' || !onSaveToVault) return;
    setSaveState('loading');
    try {
      await onSaveToVault(message.text);
      setSaveState('saved');
      saveTimeoutRef.current = setTimeout(() => setSaveState('idle'), 3000);
    } catch (err) {
      console.error('❌ Save to Vault failed:', err);
      setSaveState('error');
      saveTimeoutRef.current = setTimeout(() => setSaveState('idle'), 3000);
    }
  };
  
  return (
    <View style={[
      styles.messageActionsContainer,
      !isUserMessage && { justifyContent: 'flex-start' }
    ]}>
      <TouchableOpacity
        style={[styles.messageActionButton, { backgroundColor: theme.borderColor }]}
        onPress={handleCopy}
      >
        <Ionicons name="copy-outline" size={16} color={theme.text} />
      </TouchableOpacity>
      {isUserMessage && (
        <TouchableOpacity
          style={[styles.messageActionButton, { backgroundColor: theme.borderColor }]}
          onPress={() => onEdit(message)}
        >
          <Ionicons name="create-outline" size={16} color={theme.text} />
        </TouchableOpacity>
      )}
      {/* Share entire chat session using link-based sharing */}
      {sessionId ? (
        <ShareButton
          contentType="chat"
          sourceId={sessionId}
          title={sessionName || 'Chat Conversation'}
          theme={theme}
          size="small"
          showLabel={false}
        />
      ) : (
        <TouchableOpacity
          style={[styles.messageActionButton, { backgroundColor: theme.borderColor }]}
          onPress={() => onShare(message.text)}
        >
          <Ionicons name="share-outline" size={16} color={theme.text} />
        </TouchableOpacity>
      )}
      {/* Save to Vault — only for bot messages */}
      {!isUserMessage && onSaveToVault && (
        <TouchableOpacity
          style={[
            styles.messageActionButton,
            {
              backgroundColor:
                saveState === 'saved' ? 'rgba(34, 197, 94, 0.15)'
                : saveState === 'error' ? 'rgba(239, 68, 68, 0.15)'
                : theme.borderColor,
            },
          ]}
          onPress={handleSaveToVault}
          disabled={saveState === 'loading' || saveState === 'saved' || !message.text?.trim()}
          accessibilityLabel="Save to Data Store"
        >
          {saveState === 'loading' ? (
            <ActivityIndicator size={14} color={theme.text} />
          ) : saveState === 'saved' ? (
            <Ionicons name="checkmark-circle-outline" size={16} color="#22c55e" />
          ) : saveState === 'error' ? (
            <Ionicons name="alert-circle-outline" size={16} color="#ef4444" />
          ) : (
            <Ionicons name="cloud-upload-outline" size={16} color={theme.text} />
          )}
        </TouchableOpacity>
      )}
      {/* Inline toast label for save result */}
      {!isUserMessage && saveState === 'saved' && (
        <Text style={{ fontSize: 11, color: '#22c55e', marginLeft: 4, alignSelf: 'center' }}>
          Saved to Data Store
        </Text>
      )}
      {!isUserMessage && saveState === 'error' && (
        <Text style={{ fontSize: 11, color: '#ef4444', marginLeft: 4, alignSelf: 'center' }}>
          Failed to save
        </Text>
      )}
    </View>
  );
};

// Edit Message Modal Component
export const EditMessageModal = ({ isVisible, onClose, message, onSave, theme }) => {
  const [editedText, setEditedText] = useState('');

  useEffect(() => {
    if (message) {
      setEditedText(message.text || '');
    }
  }, [message]);

  const handleSave = () => {
    if (editedText.trim()) {
      onSave(message.id, editedText.trim());
      onClose();
    }
  };

  return (
    <Modal
      animationType="slide"
      transparent={true}
      visible={isVisible}
      onRequestClose={onClose}
    >
      <View style={styles.editMessageModal}>
        <View style={[styles.editMessageContent, { backgroundColor: theme.background }]}>
          <View style={styles.modalHeader}>
            <Text style={[styles.modalTitle, { color: theme.text }]}>Edit Message</Text>
            <TouchableOpacity onPress={onClose}>
              <Ionicons name="close" size={24} color={theme.text} />
            </TouchableOpacity>
          </View>
          <TextInput
            style={[
              styles.editMessageInput,
              { 
                color: theme.text, 
                backgroundColor: theme.inputBackground,
                borderColor: theme.borderColor,
                borderWidth: 1
              }
            ]}
            multiline
            placeholder="Edit your message..."
            placeholderTextColor={theme.placeholderText}
            value={editedText}
            onChangeText={setEditedText}
            autoFocus
          />
          <View style={styles.editMessageButtons}>
            <TouchableOpacity 
              onPress={onClose}
              style={[styles.editMessageButton, { backgroundColor: theme.borderColor }]}
            >
              <Text style={[styles.editMessageButtonText, { color: theme.text }]}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity 
              onPress={handleSave}
              style={[styles.editMessageButton, { backgroundColor: theme.sendButton }]}
            >
              <Text style={[styles.editMessageButtonText, { color: theme.buttonText }]}>Send</Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>
    </Modal>
  );
};
