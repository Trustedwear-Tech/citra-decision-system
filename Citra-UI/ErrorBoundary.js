import React from 'react';
import { View, Text, ScrollView, TouchableOpacity, Platform } from 'react-native';
import { API_CONFIG } from './config/config';
import authService from './services/authService';

// Rate-limit: max 1 error report email per 5 minutes
let _lastErrorReportTime = 0;
const ERROR_REPORT_COOLDOWN = 5 * 60 * 1000; // 5 minutes

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null, errorCount: 0 };
    
    // Listen for JavaScript loading errors on web
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      window.addEventListener('error', this.handleGlobalError);
      window.addEventListener('unhandledrejection', this.handleUnhandledRejection);
    }
  }

  componentWillUnmount() {
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      window.removeEventListener('error', this.handleGlobalError);
      window.removeEventListener('unhandledrejection', this.handleUnhandledRejection);
    }
  }

  reportErrorToSupport = (error, extra = {}) => {
    if (Platform.OS !== 'web') return;
    const now = Date.now();
    if (now - _lastErrorReportTime < ERROR_REPORT_COOLDOWN) return;
    _lastErrorReportTime = now;

    const payload = {
      error: error?.message || String(error),
      stack: error?.stack || '',
      url: typeof window !== 'undefined' ? window.location.href : '',
      userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
      timestamp: new Date().toISOString(),
      componentStack: extra.componentStack || '',
    };

    authService.getToken().then(token => {
      const headers = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;
      fetch(`${API_CONFIG.CITRA_SERVICE_URL}/api/report-error`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
      }).catch(() => {});
    }).catch(() => {});
  };

  handleGlobalError = (event) => {
    // Ignore the benign "ResizeObserver loop" notification. Browsers fire a
    // window 'error' event for it (message is set, but event.error === null)
    // when a resizable container settles — e.g. the react-flow workflow canvas
    // while the user drags the node-panel splitter. It is NOT an app error, so
    // it must not trip the boundary (which would blank the app into the crash UI).
    const _msg = (event && (event.message || (event.error && event.error.message))) || '';
    if (typeof _msg === 'string' && _msg.indexOf('ResizeObserver loop') !== -1) {
      return;
    }

    // Filter out image loading errors
    if (event.target && event.target.tagName === 'IMG') {
      console.warn('Image failed to load:', event.target.src);
      return;
    }
    
    console.error('Global error caught:', event.error);
    
    // Check if this is the specific JavaScript loading error
    if (event.error && event.error.message && 
        (event.error.message.includes('Unexpected token') || 
         event.error.message.includes('SyntaxError'))) {
      
      console.error('JavaScript loading error detected - likely cache issue');
      
      // Clear all caches and reload
      this.clearCachesAndReload();
      return;
    }

    this.reportErrorToSupport(event.error || new Error('Global error'));
    
    // Handle as regular error
    this.setState(prevState => ({
      hasError: true,
      error: event.error || new Error('Global error'),
      errorCount: prevState.errorCount + 1
    }));
  };

  handleUnhandledRejection = (event) => {
    // Filter out image loading errors (benign - missing resources)
    if (event.reason && typeof event.reason === 'object' && 
        (event.reason.target === 'img' || event.reason.type === 'error')) {
      console.warn('Image loading failed (non-critical):', event.reason);
      return;
    }
    
    console.error('Unhandled promise rejection:', event.reason);

    const err = event.reason instanceof Error ? event.reason : new Error(`Promise rejection: ${event.reason}`);
    this.reportErrorToSupport(err);

    this.setState(prevState => ({
      hasError: true,
      error: err,
      errorCount: prevState.errorCount + 1
    }));
  };

  clearCachesAndReload = async () => {
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      try {
        // Clear all possible caches
        if ('caches' in window) {
          const cacheNames = await caches.keys();
          await Promise.all(
            cacheNames.map(cacheName => caches.delete(cacheName))
          );
          console.log('Service worker caches cleared');
        }
        
        // Clear localStorage and sessionStorage
        if (window.localStorage) {
          window.localStorage.clear();
        }
        if (window.sessionStorage) {
          window.sessionStorage.clear();
        }
        
        // Force reload with cache bypass
        window.location.reload(true);
      } catch (error) {
        console.error('Error clearing caches:', error);
        // Fallback to simple reload
        window.location.reload();
      }
    }
  };

  static getDerivedStateFromError(error) {
    // Update state so the next render will show the fallback UI
    return { hasError: true };
  }

  componentDidCatch(error, errorInfo) {
    // Log error details
    console.error('Error Boundary caught an error:', error);
    console.error('Error Info:', errorInfo);

    this.reportErrorToSupport(error, { componentStack: errorInfo?.componentStack });
    
    // Check if this is a JavaScript syntax error (cache issue)
    if (error && error.message && 
        (error.message.includes('Unexpected token') || 
         error.message.includes('SyntaxError'))) {
      
      console.error('JavaScript syntax error detected - attempting cache clear and reload');
      setTimeout(() => {
        this.clearCachesAndReload();
      }, 1000);
      return;
    }
    
    // Update state with error details
    this.setState(prevState => ({
      error: error,
      errorInfo: errorInfo,
      errorCount: prevState.errorCount + 1
    }));

    // If we're in development, also log to console for debugging
    if (__DEV__) {
      console.group('🚨 Error Boundary Details');
      console.error('Error:', error);
      console.error('Error Info:', errorInfo);
      console.error('Component Stack:', errorInfo.componentStack);
      console.groupEnd();
    }
  }

  handleReload = () => {
    // Reset error state
    this.setState({ hasError: false, error: null, errorInfo: null });
    
    // Clear caches and reload the page in web environment
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      this.clearCachesAndReload();
    }
  };

  handleRetry = () => {
    // Reset error state to try rendering again
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  render() {
    if (this.state.hasError) {
      // Check if this looks like a JavaScript loading error
      const isJSLoadingError = this.state.error && 
        this.state.error.message && 
        (this.state.error.message.includes('Unexpected token') || 
         this.state.error.message.includes('SyntaxError'));
      
      // Render fallback UI
      return (
        <View style={{
          flex: 1,
          justifyContent: 'center',
          alignItems: 'center',
          padding: 20,
          backgroundColor: '#f5f5f5'
        }}>
          <Text style={{
            fontSize: 24,
            fontWeight: 'bold',
            color: '#e74c3c',
            marginBottom: 20,
            textAlign: 'center'
          }}>
            {isJSLoadingError ? 'Loading Issue Detected' : 'Something went wrong'}
          </Text>
          
          <Text style={{
            fontSize: 16,
            color: '#666',
            marginBottom: 30,
            textAlign: 'center'
          }}>
            {isJSLoadingError 
              ? 'This appears to be a cache issue after a new deployment. Clearing cache and reloading should fix this.'
              : 'The app encountered an unexpected error. This sometimes happens with complex responses.'
            }
          </Text>

          <View style={{
            flexDirection: Platform.OS === 'web' ? 'row' : 'column',
            alignItems: 'center',
            gap: 10
          }}>
            <TouchableOpacity
              style={{
                backgroundColor: '#27ae60',
                paddingHorizontal: 30,
                paddingVertical: 15,
                borderRadius: 8,
                marginBottom: Platform.OS === 'web' ? 0 : 10
              }}
              onPress={this.handleRetry}
            >
              <Text style={{
                color: 'white',
                fontSize: 16,
                fontWeight: 'bold'
              }}>
                Try Again
              </Text>
            </TouchableOpacity>

            {Platform.OS === 'web' && (
              <TouchableOpacity
                style={{
                  backgroundColor: isJSLoadingError ? '#e74c3c' : '#3498db',
                  paddingHorizontal: 30,
                  paddingVertical: 15,
                  borderRadius: 8,
                  marginBottom: 20
                }}
                onPress={this.handleReload}
              >
                <Text style={{
                  color: 'white',
                  fontSize: 16,
                  fontWeight: 'bold'
                }}>
                  {isJSLoadingError ? 'Clear Cache & Reload' : 'Reload Page'}
                </Text>
              </TouchableOpacity>
            )}
          </View>
          
          {this.state.errorCount > 1 && (
            <Text style={{
              fontSize: 14,
              color: '#e67e22',
              textAlign: 'center',
              marginBottom: 20
            }}>
              Repeated errors detected. Consider switching to a different AI model.
            </Text>
          )}

          {__DEV__ && (
            <ScrollView style={{
              maxHeight: 300,
              backgroundColor: '#f8f9fa',
              padding: 15,
              borderRadius: 8,
              width: '100%'
            }}>
              <Text style={{
                fontSize: 14,
                color: '#666',
                fontWeight: 'bold',
                marginBottom: 10
              }}>
                Error Details (Dev Mode):
              </Text>
              <Text style={{
                fontSize: 12,
                color: '#333',
                fontFamily: Platform.OS === 'web' ? 'monospace' : 'Courier New'
              }}>
                {this.state.error && this.state.error.toString()}
              </Text>
              {this.state.errorInfo && (
                <Text style={{
                  fontSize: 12,
                  color: '#666',
                  fontFamily: Platform.OS === 'web' ? 'monospace' : 'Courier New',
                  marginTop: 10
                }}>
                  {this.state.errorInfo.componentStack}
                </Text>
              )}
            </ScrollView>
          )}
        </View>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
