import React, { useEffect, useRef, useState, useCallback } from 'react';
import { View, Text, StyleSheet, Platform, ActivityIndicator, TouchableOpacity } from 'react-native';
import { loadMermaidFromCDN, getMermaidInitConfig, sanitizeMermaidCode, MERMAID_VERSION } from '../utils/mermaidConfig';

// Global render mutex — Mermaid v11 cannot handle concurrent render() calls.
// Calling initialize() or render() while another render is in-flight corrupts
// internal parser state, causing "Cannot read properties of undefined (reading 'type')".
let _renderQueue = Promise.resolve();
let _mermaidInitialized = false;

const ensureMermaidReady = async (isDark) => {
  if (!window.mermaid) {
    console.log(`🔧 [MERMAID_CHAT] Mermaid not loaded, loading v${MERMAID_VERSION} from CDN...`);
    await loadMermaidFromCDN();
  }
  // Initialize only once (or when theme changes)
  if (!_mermaidInitialized) {
    window.mermaid.initialize(getMermaidInitConfig(isDark));
    _mermaidInitialized = true;
  }
};

/**
 * MermaidDiagram Component
 * Renders Mermaid v11.4 diagrams inline in chat messages with download functionality.
 * Auto-loads Mermaid from CDN if not already present.
 * Uses shared sanitization pipeline and retry logic for robust rendering.
 */
const MermaidDiagram = ({ diagramCode, theme }) => {
  const diagramRef = useRef(null);
  const [renderError, setRenderError] = useState(null);
  const [isRendering, setIsRendering] = useState(true);
  const [isDownloading, setIsDownloading] = useState(false);
  const [uniqueId] = useState(`mermaid-chat-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`);

  useEffect(() => {
    if (Platform.OS === 'web' && diagramRef.current) {
      renderDiagram();
    }
  }, [diagramCode, theme]);

  const renderDiagram = async () => {
    // Queue this render behind any in-flight renders to avoid concurrent mermaid.render() calls
    const myRender = _renderQueue.then(() => doRender());
    _renderQueue = myRender.catch(() => {}); // swallow so queue continues
    return myRender;
  };

  const doRender = async () => {
    try {
      setIsRendering(true);
      setRenderError(null);

      const isDark = theme?.isDark !== false;
      await ensureMermaidReady(isDark);

      // Clear previous content
      if (diagramRef.current) {
        diagramRef.current.innerHTML = '';
      }

      // Use shared sanitization pipeline
      const processedCode = sanitizeMermaidCode(diagramCode);

      const renderId = `${uniqueId}-${Date.now()}`;
      let svg = null;
      let lastError = null;

      // Attempt 1: render with current config
      try {
        const result = await window.mermaid.render(renderId, processedCode);
        svg = result.svg;
      } catch (err) {
        lastError = err;
        console.warn('⚠️ [MERMAID_CHAT] Render attempt 1 failed:', err.message);
        // Clean up leftover DOM element from failed render
        const failedEl = document.getElementById(renderId);
        if (failedEl) failedEl.remove();
      }

      // Attempt 2: strip comments and retry
      if (!svg) {
        const strippedCode = processedCode
          .split('\n')
          .filter(line => !line.trim().startsWith('%%'))
          .join('\n');
        const retryId = `${uniqueId}-retry-${Date.now()}`;
        try {
          const result = await window.mermaid.render(retryId, strippedCode);
          svg = result.svg;
        } catch (err) {
          lastError = err;
          console.warn('⚠️ [MERMAID_CHAT] Render attempt 2 failed:', err.message);
          const failedEl = document.getElementById(retryId);
          if (failedEl) failedEl.remove();
        }
      }

      // Attempt 3: re-initialize with htmlLabels:false
      if (!svg) {
        try {
          window.mermaid.initialize({
            startOnLoad: false,
            theme: isDark ? 'dark' : 'default',
            securityLevel: 'loose',
            flowchart: { useMaxWidth: true, htmlLabels: false },
          });
          const safeId = `${uniqueId}-safe-${Date.now()}`;
          const result = await window.mermaid.render(safeId, processedCode);
          svg = result.svg;
        } catch (err) {
          lastError = err;
          console.error('❌ [MERMAID_CHAT] All 3 render attempts failed:', err.message);
          const safeEl = document.getElementById(`${uniqueId}-safe-${Date.now()}`);
          if (safeEl) safeEl.remove();
        } finally {
          // Restore full config
          window.mermaid.initialize(getMermaidInitConfig(isDark));
        }
      }

      if (svg && diagramRef.current) {
        // Sanitize SVG output — strip any <script> tags from SVG before injection
        const cleanSvg = svg.replace(/<script[\s\S]*?<\/script>/gi, '');
        diagramRef.current.innerHTML = cleanSvg;
        setIsRendering(false);
      } else {
        throw lastError || new Error('Diagram render failed');
      }
    } catch (error) {
      console.error('❌ [MERMAID_CHAT] Failed to render diagram:', error.message);
      setRenderError(error.message);
      setIsRendering(false);
    }
  };

  const handleRetry = useCallback(() => {
    // Allow re-initialization on manual retry
    _mermaidInitialized = false;
    renderDiagram();
  }, [diagramCode, theme]);

  // Download diagram as PNG (using SVG-to-Canvas conversion)
  const handleDownloadPNG = async () => {
    try {
      setIsDownloading(true);

      const svgElement = diagramRef.current?.querySelector('svg');
      if (!svgElement) {
        throw new Error('SVG element not found');
      }

      // Get SVG dimensions
      const bbox = svgElement.getBBox();
      const viewBox = svgElement.getAttribute('viewBox');
      let width, height;

      if (viewBox) {
        const [, , vbWidth, vbHeight] = viewBox.split(' ').map(Number);
        width = vbWidth;
        height = vbHeight;
      } else {
        width = bbox.width || svgElement.width.baseVal.value || 800;
        height = bbox.height || svgElement.height.baseVal.value || 600;
      }

      // Clone and serialize SVG
      const svgClone = svgElement.cloneNode(true);
      svgClone.setAttribute('width', width);
      svgClone.setAttribute('height', height);

      const svgData = new XMLSerializer().serializeToString(svgClone);

      // Use data URL instead of blob URL to avoid CORS tainted canvas issue
      const svgDataUrl = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgData);

      // Create image from SVG
      const img = new Image();
      img.onload = () => {
        try {
          // Create canvas
          const canvas = document.createElement('canvas');
          const scale = 2; // Higher resolution
          canvas.width = width * scale;
          canvas.height = height * scale;

          const ctx = canvas.getContext('2d');
          ctx.scale(scale, scale);

          // Fill background
          ctx.fillStyle = theme.isDark ? '#1a1a1a' : '#ffffff';
          ctx.fillRect(0, 0, width, height);

          // Draw image
          ctx.drawImage(img, 0, 0, width, height);

          // Convert to PNG and download
          canvas.toBlob((blob) => {
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `diagram-${Date.now()}.png`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
            setIsDownloading(false);
          }, 'image/png');
        } catch (error) {
          throw error;
        }
      };

      img.onerror = (error) => {
        throw new Error('Failed to load SVG as image');
      };

      img.src = svgDataUrl;
    } catch (error) {
      console.error('❌ [MERMAID_CHAT] PNG download failed:', error.message);
      alert('Failed to download PNG: ' + error.message);
      setIsDownloading(false);
    }
  };

  // Download diagram as SVG
  const handleDownloadSVG = () => {
    try {
      setIsDownloading(true);

      const svgElement = diagramRef.current?.querySelector('svg');
      if (!svgElement) {
        throw new Error('SVG element not found');
      }

      // Clone and serialize SVG
      const svgClone = svgElement.cloneNode(true);
      const svgData = new XMLSerializer().serializeToString(svgClone);
      const blob = new Blob([svgData], { type: 'image/svg+xml' });

      // Download
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `diagram-${Date.now()}.svg`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      setIsDownloading(false);
    } catch (error) {
      console.error('❌ [MERMAID_CHAT] SVG download failed:', error.message);
      alert('Failed to download SVG: ' + error.message);
      setIsDownloading(false);
    }
  };

  // Fallback for non-web platforms
  if (Platform.OS !== 'web') {
    return (
      <View style={[styles.fallbackContainer, { backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f9fa' }]}>
        <Text style={[styles.fallbackText, { color: theme.text }]}>
          📊 Diagram view is only available on web platform
        </Text>
        <Text style={[styles.codeText, { color: theme.subtext }]}>{diagramCode}</Text>
      </View>
    );
  }

  // Error fallback - Show friendly message with retry option
  if (renderError) {
    return (
      <View style={[styles.errorContainer, { backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f9fa', borderWidth: 1, borderColor: theme.isDark ? '#4a4a4a' : '#e0e0e0' }]}>
        <Text style={[styles.errorTitle, { color: theme.isDark ? '#ff9966' : '#ff6b35' }]}>
          📊 Diagram Preview Unavailable
        </Text>
        <Text style={[styles.errorText, { color: theme.subtext }]}>
          The diagram couldn't be rendered due to incomplete or invalid syntax from the AI response.
          This usually happens when the response is truncated. Please try asking again.
        </Text>
        <TouchableOpacity
          onPress={handleRetry}
          style={[styles.retryButton, { borderColor: theme.isDark ? '#4a9eff' : '#007acc' }]}
        >
          <Text style={[styles.retryButtonText, { color: theme.isDark ? '#4a9eff' : '#007acc' }]}>
            🔄 Retry Render
          </Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <View style={[styles.container, { backgroundColor: theme.isDark ? '#1a1a1a' : '#ffffff' }]}>
      {/* Download Action Bar */}
      {!isRendering && !renderError && (
        <View style={[styles.actionBar, { backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f9fa' }]}>
          <Text style={[styles.actionBarTitle, { color: theme.subtext }]}>📊 Diagram</Text>
          <View style={styles.actionButtons}>
            <TouchableOpacity
              onPress={handleDownloadPNG}
              disabled={isDownloading}
              style={[
                styles.downloadButton,
                {
                  backgroundColor: theme.isDark ? '#1a1a1a' : '#ffffff',
                  borderColor: theme.isDark ? '#4a9eff' : '#007acc',
                  opacity: isDownloading ? 0.5 : 1
                }
              ]}
            >
              <Text style={[styles.downloadButtonText, { color: theme.isDark ? '#4a9eff' : '#007acc' }]}>
                📥 PNG
              </Text>
            </TouchableOpacity>

            <TouchableOpacity
              onPress={handleDownloadSVG}
              disabled={isDownloading}
              style={[
                styles.downloadButton,
                {
                  backgroundColor: theme.isDark ? '#1a1a1a' : '#ffffff',
                  borderColor: theme.isDark ? '#4a9eff' : '#007acc',
                  opacity: isDownloading ? 0.5 : 1
                }
              ]}
            >
              <Text style={[styles.downloadButtonText, { color: theme.isDark ? '#4a9eff' : '#007acc' }]}>
                📥 SVG
              </Text>
            </TouchableOpacity>
          </View>
        </View>
      )}

      {isRendering && (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="small" color={theme.isDark ? '#4a9eff' : '#007acc'} />
          <Text style={[styles.loadingText, { color: theme.subtext }]}>Rendering diagram...</Text>
        </View>
      )}
      <div
        ref={diagramRef}
        style={{
          width: '100%',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          padding: 16,
          minHeight: isRendering ? 100 : 'auto',
        }}
      />
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    borderRadius: 8,
    marginVertical: 8,
    borderWidth: 1,
    borderColor: '#e0e0e0',
    overflow: 'hidden',
  },
  actionBar: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#e0e0e0',
  },
  actionBarTitle: {
    fontSize: 12,
    fontWeight: '600',
  },
  actionButtons: {
    flexDirection: 'row',
    gap: 8,
  },
  downloadButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 6,
    borderWidth: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
  },
  downloadButtonText: {
    fontSize: 12,
    fontWeight: '600',
  },
  loadingContainer: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 10,
  },
  loadingText: {
    marginTop: 8,
    fontSize: 14,
  },
  errorContainer: {
    borderRadius: 8,
    padding: 16,
    marginVertical: 8,
    borderWidth: 1,
    borderColor: '#ff6b6b',
  },
  errorTitle: {
    fontSize: 16,
    fontWeight: 'bold',
    marginBottom: 8,
  },
  errorText: {
    fontSize: 14,
    marginBottom: 12,
  },
  retryButton: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 6,
    borderWidth: 1,
    alignSelf: 'flex-start',
  },
  retryButtonText: {
    fontSize: 13,
    fontWeight: '600',
  },
  codeContainer: {
    borderRadius: 6,
    padding: 12,
    marginTop: 8,
  },
  codeLabel: {
    fontSize: 12,
    fontWeight: 'bold',
    marginBottom: 4,
  },
  codeText: {
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
    fontSize: 12,
    lineHeight: 18,
  },
  fallbackContainer: {
    borderRadius: 8,
    padding: 16,
    marginVertical: 8,
    borderWidth: 1,
    borderColor: '#e0e0e0',
  },
  fallbackText: {
    fontSize: 14,
    marginBottom: 12,
    textAlign: 'center',
  },
});

export default MermaidDiagram;
