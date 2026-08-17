// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

import React, { useEffect, useRef, useState, useCallback, memo } from 'react';
import { View, Text, StyleSheet, Platform, ActivityIndicator, TouchableOpacity } from 'react-native';

/**
 * ChartJsDiagram Component
 * Renders Chart.js 4.x charts inline in chat messages with download functionality.
 * Supports all Chart.js chart types: bar, line, pie, doughnut, radar, polarArea, scatter, bubble.
 * Uses dynamic import to load Chart.js only when needed.
 */
const ChartJsDiagramImpl = ({ chartConfig, theme }) => {
  // Plain ref to the current canvas DOM node. Updated by the ref
  // callback below — never polled. The previous implementation used a
  // useRef + requestAnimationFrame loop that polled up to 20 times for
  // ``canvasRef.current``; that races with React's ref attachment when
  // ChartJsDiagram is wrapped by RichMessageRenderer (streamed message,
  // multiple re-renders during parsing). When the polls all returned
  // null the component fell into "Canvas element not available. Click
  // retry." even though clicking immediately succeeded — exactly the
  // first-time-fails / second-time-works symptom.
  const canvasRef = useRef(null);
  const chartInstanceRef = useRef(null);
  const [renderError, setRenderError] = useState(null);
  const [isRendering, setIsRendering] = useState(true);
  const [isDownloading, setIsDownloading] = useState(false);
  const mountedRef = useRef(true);
  // Latest chartConfig + theme captured for the canvas-attached
  // callback to read. We don't put these directly in deps of the
  // callback because doing so would create a new callback on every
  // re-render and detach/reattach the canvas (losing the rendered
  // chart). Instead, the callback always reads from these refs.
  const pendingConfigRef = useRef(chartConfig);
  const pendingDarkRef = useRef(theme?.isDark !== false);
  const pendingRenderRef = useRef(false);

  const isDark = theme?.isDark !== false;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (chartInstanceRef.current) {
        chartInstanceRef.current.destroy();
        chartInstanceRef.current = null;
      }
    };
  }, []);

  // Ref callback — fires synchronously when React attaches/detaches
  // the canvas DOM node. If a render is queued and the canvas just
  // attached, kick it off immediately. No polling, no retry threshold.
  const setCanvasRef = useCallback((node) => {
    canvasRef.current = node;
    if (Platform.OS !== 'web') return;
    if (node && pendingRenderRef.current && mountedRef.current) {
      pendingRenderRef.current = false;
      renderChart();
    }
  }, []);

  useEffect(() => {
    if (Platform.OS !== 'web') return;
    pendingConfigRef.current = chartConfig;
    pendingDarkRef.current = isDark;
    if (canvasRef.current && mountedRef.current) {
      // Canvas already attached — render now.
      pendingRenderRef.current = false;
      renderChart();
    } else {
      // Canvas not yet attached — flag pending; setCanvasRef will
      // pick it up when React attaches the DOM node.
      pendingRenderRef.current = true;
    }
    return () => {
      // Cancel any pending render queued for a future ref attach.
      // Note: do NOT destroy chartInstanceRef here. Effect re-runs are
      // typically caused by parent re-renders with the same chartConfig;
      // destroying mid-render causes a flash of "Chart Preview
      // Unavailable" until the user clicks retry. The unmount cleanup
      // handles destruction; renderChart() also destroys before
      // recreating.
      pendingRenderRef.current = false;
    };
  }, [chartConfig, isDark]);

  // Re-render chart when it becomes visible after being hidden (screen switch, tab switch)
  useEffect(() => {
    if (Platform.OS !== 'web' || isRendering || renderError) return;

    const canvas = canvasRef.current;
    if (!canvas || !chartInstanceRef.current) return;

    let wasHidden = false;

    // IntersectionObserver: fires when canvas enters/leaves viewport or parent hides it
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) {
          wasHidden = true;
        } else if (wasHidden) {
          wasHidden = false;
          // Canvas was hidden and is now visible — re-render to restore lost content
          if (mountedRef.current) renderChart();
        }
      },
      { threshold: 0.01 }
    );
    observer.observe(canvas);

    // visibilitychange: fires when user switches browser tabs
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible' && mountedRef.current && chartInstanceRef.current) {
        renderChart();
      }
    };
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      observer.disconnect();
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [isRendering, renderError, chartConfig, isDark]);

  const parseConfig = (config) => {
    if (typeof config === 'string') {
      try {
        return JSON.parse(config);
      } catch {
        throw new Error('Invalid Chart.js JSON configuration');
      }
    }
    return config;
  };

  const renderChart = async () => {
    try {
      setIsRendering(true);
      setRenderError(null);

      const config = parseConfig(chartConfig);

      if (!config || !config.type || !config.data) {
        throw new Error('Chart config must include "type" and "data" fields');
      }

      // Destroy previous instance
      if (chartInstanceRef.current) {
        chartInstanceRef.current.destroy();
        chartInstanceRef.current = null;
      }

      // Dynamic import Chart.js with all registerables
      const { Chart, registerables } = await import('chart.js');
      Chart.register(...registerables);

      // After async import, verify component is still mounted and canvas exists
      if (!mountedRef.current) return;

      const canvas = canvasRef.current;
      if (!canvas) {
        throw new Error('Canvas not available after Chart.js loaded');
      }

      // Apply dark theme defaults
      const defaultColors = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#06B6D4', '#84CC16'];
      const textColor = isDark ? '#f4f4f5' : '#1f2937';
      const gridColor = isDark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)';

      // Apply default backgroundColor to datasets if not provided
      if (config.data.datasets) {
        config.data.datasets.forEach((ds, i) => {
          if (!ds.backgroundColor) {
            const isPieType = ['pie', 'doughnut', 'polarArea'].includes(config.type);
            ds.backgroundColor = isPieType
              ? defaultColors.slice(0, (config.data.labels || []).length || defaultColors.length)
              : defaultColors[i % defaultColors.length];
          }
          if (!ds.borderColor && !['pie', 'doughnut', 'polarArea'].includes(config.type)) {
            ds.borderColor = ds.backgroundColor;
          }
        });
      }

      chartInstanceRef.current = new Chart(canvas, {
        type: config.type,
        data: config.data,
        options: {
          ...config.options,
          responsive: true,
          maintainAspectRatio: true,
          color: textColor,
          plugins: {
            ...config.options?.plugins,
            legend: {
              display: true,
              position: 'bottom',
              labels: { color: textColor, font: { size: 12 } },
              ...config.options?.plugins?.legend,
            },
            title: {
              display: !!config.options?.plugins?.title?.text,
              color: textColor,
              font: { size: 14, weight: '600' },
              ...config.options?.plugins?.title,
            },
          },
          scales: applyScaleDefaults(config, textColor, gridColor),
        },
      });

      setIsRendering(false);
    } catch (error) {
      console.error('❌ [CHARTJS_CHAT] Failed to render chart:', error.message);
      setRenderError(error.message);
      setIsRendering(false);
    }
  };

  /** Apply dark-theme-aware defaults to chart scales */
  const applyScaleDefaults = (config, textColor, gridColor) => {
    // Pie/doughnut/polarArea/radar don't use x/y axes the same way
    const noAxesTypes = ['pie', 'doughnut'];
    if (noAxesTypes.includes(config.type)) return config.options?.scales;

    const userScales = config.options?.scales || {};
    const result = { ...userScales };

    // For radial charts (radar, polarArea)
    if (['radar', 'polarArea'].includes(config.type)) {
      result.r = {
        ticks: { color: textColor, backdropColor: 'transparent' },
        grid: { color: gridColor },
        pointLabels: { color: textColor },
        ...userScales.r,
      };
      return result;
    }

    // For cartesian charts
    if (!result.x) result.x = {};
    if (!result.y) result.y = {};
    result.x = { ticks: { color: textColor }, grid: { color: gridColor }, ...userScales.x };
    result.y = { ticks: { color: textColor }, grid: { color: gridColor }, ...userScales.y };
    return result;
  };

  const handleRetry = useCallback(() => {
    renderChart();
  }, [chartConfig, theme]);

  // Download chart as PNG
  const handleDownloadPNG = async () => {
    try {
      setIsDownloading(true);
      const canvas = canvasRef.current;
      if (!canvas) throw new Error('Canvas not found');

      // Create a high-res export canvas
      const scale = 2;
      const exportCanvas = document.createElement('canvas');
      exportCanvas.width = canvas.width * scale;
      exportCanvas.height = canvas.height * scale;
      const ctx = exportCanvas.getContext('2d');
      ctx.scale(scale, scale);
      ctx.fillStyle = isDark ? '#1a1a2e' : '#ffffff';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(canvas, 0, 0, canvas.width, canvas.height);

      exportCanvas.toBlob((blob) => {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `chart-${Date.now()}.png`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
        setIsDownloading(false);
      }, 'image/png');
    } catch (error) {
      console.error('❌ [CHARTJS_CHAT] PNG download failed:', error.message);
      setIsDownloading(false);
    }
  };

  // Non-web fallback
  if (Platform.OS !== 'web') {
    return (
      <View style={[styles.fallbackContainer, { backgroundColor: isDark ? '#2a2a2a' : '#f8f9fa' }]}>
        <Text style={[styles.fallbackText, { color: theme?.text || '#666' }]}>
          📈 Chart view is only available on web platform
        </Text>
      </View>
    );
  }

  // Error UI overlays the canvas instead of replacing it via early
  // return. Keeping the canvas mounted means the ref-callback path
  // ``setCanvasRef`` already has a node when ``renderChart`` runs on
  // retry — no race between state-driven re-mount and the chart.js
  // import resolving.
  return (
    <View style={[styles.container, { backgroundColor: isDark ? '#1a1a2e' : '#ffffff' }]}>
      {/* Action Bar */}
      {!isRendering && !renderError && (
        <View style={[styles.actionBar, { backgroundColor: isDark ? '#16213e' : '#f8f9fa' }]}>
          <Text style={[styles.actionBarTitle, { color: theme?.subtext || '#888' }]}>📈 Chart</Text>
          <View style={styles.actionButtons}>
            <TouchableOpacity
              onPress={handleDownloadPNG}
              disabled={isDownloading}
              style={[
                styles.downloadButton,
                {
                  backgroundColor: isDark ? '#1a1a2e' : '#ffffff',
                  borderColor: isDark ? '#4a9eff' : '#007acc',
                  opacity: isDownloading ? 0.5 : 1
                }
              ]}
            >
              <Text style={[styles.downloadButtonText, { color: isDark ? '#4a9eff' : '#007acc' }]}>
                📥 PNG
              </Text>
            </TouchableOpacity>
          </View>
        </View>
      )}

      {isRendering && !renderError && (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="small" color={isDark ? '#4a9eff' : '#007acc'} />
          <Text style={[styles.loadingText, { color: theme?.subtext || '#888' }]}>Rendering chart...</Text>
        </View>
      )}

      {renderError && (
        <View style={[styles.errorContainer, { backgroundColor: isDark ? '#2a2a2a' : '#f8f9fa', borderColor: isDark ? '#4a4a4a' : '#e0e0e0' }]}>
          <Text style={[styles.errorTitle, { color: isDark ? '#ff9966' : '#ff6b35' }]}>
            📈 Chart Preview Unavailable
          </Text>
          <Text style={[styles.errorText, { color: theme?.subtext || '#888' }]}>
            {renderError || "The chart couldn't be rendered. The configuration may be invalid or incomplete."}
          </Text>
          <TouchableOpacity
            onPress={handleRetry}
            style={[styles.retryButton, { borderColor: isDark ? '#4a9eff' : '#007acc' }]}
          >
            <Text style={[styles.retryButtonText, { color: isDark ? '#4a9eff' : '#007acc' }]}>
              🔄 Retry Render
            </Text>
          </TouchableOpacity>
        </View>
      )}

      <View
        style={[
          styles.canvasWrapper,
          // Hide canvas wrapper when error UI is showing, but keep
          // the canvas mounted so retry's ref-callback path is ready.
          renderError && { height: 0, padding: 0, overflow: 'hidden' },
        ]}
      >
        <canvas
          ref={setCanvasRef}
          style={{
            width: '100%',
            maxHeight: 400,
            visibility: isRendering || renderError ? 'hidden' : 'visible',
          }}
        />
      </View>
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
  canvasWrapper: {
    padding: 16,
    alignItems: 'center',
    justifyContent: 'center',
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
    padding: 32,
    justifyContent: 'center',
    alignItems: 'center',
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
  fallbackContainer: {
    borderRadius: 8,
    padding: 16,
    marginVertical: 8,
    borderWidth: 1,
    borderColor: '#e0e0e0',
  },
  fallbackText: {
    fontSize: 14,
    textAlign: 'center',
  },
});

// Memoize on chartConfig only — theme object identity changes on every parent
// re-render and would otherwise cause repeated effect re-runs that destroyed
// the chart instance mid-render, leaving the user looking at the error UI
// until they clicked "Retry Render".
const ChartJsDiagram = memo(ChartJsDiagramImpl, (prev, next) => {
  if (prev.chartConfig !== next.chartConfig) return false;
  const prevDark = prev.theme?.isDark !== false;
  const nextDark = next.theme?.isDark !== false;
  return prevDark === nextDark;
});

export default ChartJsDiagram;
