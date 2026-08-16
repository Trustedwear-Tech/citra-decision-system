/**
 * Reader Service
 * Handles API calls for document reading with LLM-extracted metadata
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import authService from './authService';
import CONFIG from '../config/config';

// Normalize service base so missing /citra-ai in prod env vars does not break proxy URLs
const normalizeServiceBase = (url) => {
  if (!url || typeof url !== 'string') return null;
  const trimmed = url.replace(/\/$/, '');
  // If the configured base already includes the path segment, keep as-is
  if (/\/citra-ai(\b|\/)/.test(trimmed)) return trimmed;
  // Otherwise append the expected prefix (prod envs occasionally omit it)
  return `${trimmed}/citra-ai`;
};

const buildApiBaseUrl = () => {
  try {
    const configuredBase = normalizeServiceBase(CONFIG?.CITRA_SERVICE_URL);
    if (configuredBase) {
      return `${configuredBase}/api`;
    }
  } catch (err) {
    console.warn('⚠️ ReaderService: Unable to resolve CONFIG.CITRA_SERVICE_URL, falling back to localhost.', err);
  }
  return 'http://localhost:8085/api';
};

const API_BASE_URL = buildApiBaseUrl();

class ReaderService {
  constructor() {
    this.baseUrl = API_BASE_URL;
  }

  /**
   * Get documents with metadata for a folder
   * @param {string} folderId - Folder ID (null for all documents)
   * @param {boolean} forceRefresh - If true, bypass cache and re-extract metadata
   * @param {number} page - Page number for pagination (default: 1)
   * @param {number} perPage - Documents per page (default: 10, max: 50)
   */
  async getDocumentsWithMetadata(folderId = null, forceRefresh = false, page = 1, perPage = 10) {
    try {
      const folderDesc = folderId ? `folder: ${folderId}` : 'ALL documents';
      console.log(`📚 Reader Service: Fetching documents for ${folderDesc}, forceRefresh: ${forceRefresh}, page: ${page}, perPage: ${perPage}`);

      // Get auth token
      const token = await authService.getToken();
      if (!token) {
        throw new Error('Authentication token not found');
      }

      // Build URL with pagination parameters
      let url = `${this.baseUrl}/reader/extract-metadata?force_refresh=${forceRefresh}&page=${page}&per_page=${perPage}`;
      if (folderId) {
        url += `&folder_id=${encodeURIComponent(folderId)}`;
      }

      console.log(`📡 Calling: ${url}`);

      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        }
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || `HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();

      if (!data.success) {
        throw new Error(data.error || 'Failed to fetch documents');
      }

      console.log(`✅ Reader Service: Retrieved ${data.documents?.length || 0} documents (page ${data.page}/${Math.ceil(data.total / data.per_page)}, total: ${data.total}, has_more: ${data.has_more})`);

      return {
        success: true,
        documents: data.documents || [],
        total: data.total || 0,
        page: data.page || 1,
        perPage: data.per_page || perPage,
        hasMore: data.has_more || false,
        cached: data.cached || 0,
        newly_extracted: data.newly_extracted || 0,
        warning: data.warning || null
      };

    } catch (error) {
      console.error('❌ Reader Service Error (getDocumentsWithMetadata):', error);
      return {
        success: false,
        error: error.message,
        documents: []
      };
    }
  }

  // Proxy a PDF through the backend to avoid S3 CORS/download issues
  async getPdfProxyUrl(documentId) {
    try {
      const serviceBaseUrl = normalizeServiceBase(CONFIG.CITRA_SERVICE_URL) || 'http://localhost:8085/citra-ai';
      const token = await authService.getToken();
      const proxyEndpoint = `${serviceBaseUrl}/api/pdfstreaming/${documentId}/proxy${token ? `?token=${encodeURIComponent(token)}` : ''}`;
      const resp = await fetch(proxyEndpoint, {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });
      if (!resp.ok) {
        throw new Error(`Proxy request failed: ${resp.status}`);
      }
      const data = await resp.json();
      const baseUrl = data.download_url || data.proxy_url || proxyEndpoint;

      if (!baseUrl) return null;

      // Append JWT as query param for downstream stream requests (iframe fetch does not send headers)
      try {
        const urlObj = new URL(baseUrl);
        if (token) {
          urlObj.searchParams.set('token', token);
        }
        return urlObj.toString();
      } catch (urlErr) {
        // Fallback safe concatenation
        if (token) {
          const joiner = baseUrl.includes('?') ? '&' : '?';
          return `${baseUrl}${joiner}token=${encodeURIComponent(token)}`;
        }
        return baseUrl;
      }
    } catch (err) {
      console.error('❌ ReaderService: getPdfProxyUrl failed', err);
      return null;
    }
  }

  /**
   * Get complete document content with all chunks
   */
  async getDocument(documentId) {
    try {
      console.log(`📖 Reader Service: Fetching document content: ${documentId}`);

      // Get auth token
      const token = await authService.getToken();
      if (!token) {
        throw new Error('Authentication token not found');
      }

      const url = `${this.baseUrl}/reader/document/${encodeURIComponent(documentId)}`;

      console.log(`📡 Calling: ${url}`);

      const response = await fetch(url, {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        }
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || `HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();

      if (!data.success) {
        throw new Error('Failed to fetch document content');
      }

      console.log(`✅ Reader Service: Retrieved document with ${data.document.total_chunks} chunks`);

      return {
        success: true,
        document: data.document
      };

    } catch (error) {
      console.error('❌ Reader Service Error (getDocument):', error);
      return {
        success: false,
        error: error.message,
        document: null
      };
    }
  }

  /**
   * Clear reader metadata cache
   * @param {string} folderId - Optional: Clear cache for specific folder only
   */
  async clearCache(folderId = null) {
    try {
      console.log(`🗑️ Reader Service: Clearing cache ${folderId ? `for folder ${folderId}` : 'for all folders'}`);

      // Get auth token
      const token = await authService.getToken();
      if (!token) {
        throw new Error('Authentication token not found');
      }

      let url = `${this.baseUrl}/reader/clear-cache`;
      if (folderId) {
        url += `?folder_id=${encodeURIComponent(folderId)}`;
      }

      console.log(`📡 Calling: ${url}`);

      const response = await fetch(url, {
        method: 'DELETE',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        }
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || `HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();

      console.log(`✅ Reader Service: Cache cleared for ${data.documents_updated} documents`);

      return {
        success: true,
        documents_updated: data.documents_updated,
        message: data.message
      };

    } catch (error) {
      console.error('❌ Reader Service Error (clearCache):', error);
      return {
        success: false,
        error: error.message
      };
    }
  }

  /**
   * Health check for reader service
   */
  async healthCheck() {
    try {
      const url = `${this.baseUrl}/reader/health`;
      const response = await fetch(url);

      if (!response.ok) {
        return { healthy: false, error: `HTTP ${response.status}` };
      }

      const data = await response.json();
      return {
        healthy: data.status === 'healthy',
        ...data
      };

    } catch (error) {
      console.error('❌ Reader Service health check failed:', error);
      return { healthy: false, error: error.message };
    }
  }
}

// Export singleton instance
const readerService = new ReaderService();
export default readerService;
