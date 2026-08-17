// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * ReaderPanel Component
 * Full-screen modal for reading personal documents
 * Includes AI chat functionality
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  View,
  Text,
  Modal,
  TouchableOpacity,
  ScrollView,
  TextInput,
  ActivityIndicator,
  Platform,
  Dimensions,
  Linking
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import readerService from '../services/ReaderService';
import authService from '../services/authService';
import CONFIG from '../config/config';
import WebViewer from './WebViewer';


const ReaderPanel = ({ isVisible, onClose, folderId, folderName, theme, userId, initialDocumentId = null, onEmbedToVault = null }) => {
  // Personal documents state
  const [loading, setLoading] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [documents, setDocuments] = useState([]);
  const [selectedDocument, setSelectedDocument] = useState(null);
  const [documentContent, setDocumentContent] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [error, setError] = useState(null);
  const [extractionStatus, setExtractionStatus] = useState(null);

  // Pagination state
  const [currentPage, setCurrentPage] = useState(1);
  const [hasMoreDocuments, setHasMoreDocuments] = useState(false);
  const [totalDocuments, setTotalDocuments] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const DOCS_PER_PAGE = 10;

  // Web viewer state (web citations opened as documents)
  const [showWebViewer, setShowWebViewer] = useState(false);
  const [currentWebPage, setCurrentWebPage] = useState(null);

  // Theme colors
  // Theme colors - Professional Palette
  const isDarkMode = theme?.isDark ?? theme === 'dark';
  const bgColor = theme?.background || (isDarkMode ? '#0f172a' : '#f8fafc'); // Slate 900 / Slate 50
  const panelBg = theme?.surface || (isDarkMode ? '#1e293b' : '#ffffff'); // Slate 800 / White
  const textColor = theme?.text || (isDarkMode ? '#f1f5f9' : '#0f172a'); // Slate 100 / Slate 900
  const textSecondary = theme?.textSecondary || (isDarkMode ? '#94a3b8' : '#64748b'); // Slate 400 / Slate 500
  const borderColor = theme?.border || (isDarkMode ? '#334155' : '#e2e8f0'); // Slate 700 / Slate 200
  const highlightColor = '#10b981'; // Emerald 500 (Personal)
  const errorColor = '#ef4444'; // Red 500

  const { width } = Dimensions.get('window');
  const isMobile = width < 768;

  const prevVisibleRef = useRef(false);
  const lastInitialDocIdRef = useRef(null);

  // Load personal documents when the panel opens (but not if opening specific document)
  useEffect(() => {
    // Skip loading document list if we have an initialDocumentId - just show that document
    if (isVisible && documents.length === 0 && !initialDocumentId) {
      loadDocuments();
    }
  }, [isVisible, initialDocumentId]);

  // Auto-load a specific document when opened from outside (e.g., folder view)
  useEffect(() => {
    // Detect open transition
    const justOpened = isVisible && !prevVisibleRef.current;
    prevVisibleRef.current = isVisible;

    console.log('📖 READER_PANEL: useEffect triggered', {
      isVisible,
      justOpened,
      initialDocumentId,
      lastInitialDocIdRef: lastInitialDocIdRef.current,
      selectedDocument
    });

    if (!isVisible) return;

    // If this open uses a new initial document id, load it and remember
    if (initialDocumentId && initialDocumentId !== lastInitialDocIdRef.current) {
      console.log('📖 READER_PANEL: Loading initial document:', initialDocumentId);
      lastInitialDocIdRef.current = initialDocumentId;
      if (selectedDocument !== initialDocumentId) {
        loadDocument(initialDocumentId);
      }
      return;
    }

    // If opened without a new initial doc (e.g., Reader button), show the document list
    if (justOpened && !initialDocumentId) {
      console.log('📖 READER_PANEL: No initial doc, showing document list');
      setSelectedDocument(null);
      setDocumentContent(null);
      lastInitialDocIdRef.current = null;
    }
  }, [isVisible, initialDocumentId]);

  // Reset state when closing
  useEffect(() => {
    if (!isVisible) {
      // Cleanup blob URL if exists (for xAI files)
      if (documentContent?.blobUrl) {
        console.log('🧹 READER: Cleaning up blob URL');
        URL.revokeObjectURL(documentContent.blobUrl);
      }
      setDocuments([]);
      setSelectedDocument(null);
      setDocumentContent(null);
      setSearchQuery('');
      setError(null);
      setExtractionStatus(null);
      setShowWebViewer(false);
      setCurrentWebPage(null);
      // Reset pagination state
      setCurrentPage(1);
      setHasMoreDocuments(false);
      setTotalDocuments(0);
      setLoadingMore(false);
      // Reset ref so same document can be loaded again
      lastInitialDocIdRef.current = null;
    }
  }, [isVisible]);

  // Load documents with metadata extraction (paginated)
  const loadDocuments = async (page = 1, append = false) => {
    try {
      if (append) {
        setLoadingMore(true);
      } else {
        setLoading(true);
        setExtracting(true);
        setExtractionStatus('Analyzing documents...');
      }
      setError(null);

      const folderDesc = folderId ? `folder: ${folderId}` : 'all documents';
      console.log(`📚 Loading documents page ${page} for ${folderDesc}`);

      const result = await readerService.getDocumentsWithMetadata(folderId, false, page, DOCS_PER_PAGE);

      if (!result.success) {
        throw new Error(result.error || 'Failed to load documents');
      }

      // Update pagination state
      setCurrentPage(page);
      setHasMoreDocuments(result.hasMore);
      setTotalDocuments(result.total);

      // Update status
      const loadedCount = append ? documents.length + (result.documents?.length || 0) : (result.documents?.length || 0);
      setExtractionStatus(`✅ Loaded ${loadedCount} of ${result.total} document(s)`);

      if (append) {
        // Append new documents to existing list
        setDocuments(prev => [...prev, ...(result.documents || [])]);
      } else {
        setDocuments(result.documents || []);
      }

      console.log(`✅ Loaded page ${page}: ${result.documents?.length || 0} docs, hasMore: ${result.hasMore}, total: ${result.total}`);

    } catch (err) {
      console.error('❌ Error loading documents:', err);
      setError(err.message);
      setExtractionStatus(null);
    } finally {
      setLoading(false);
      setLoadingMore(false);
      // Clear extraction status after 3 seconds
      setTimeout(() => setExtracting(false), 3000);
    }
  };

  // Load more documents (next page)
  const loadMoreDocuments = () => {
    if (hasMoreDocuments && !loadingMore) {
      loadDocuments(currentPage + 1, true);
    }
  };

  // Load document content
  const loadDocument = async (documentId) => {
    try {
      setLoading(true);
      setError(null);

      console.log(`📖 READER: Loading document input:`, documentId);

      // Normalize documentId if it's an object (coming from citation click)
      let docIdStr = documentId;
      let docTitle = 'Web Source';

      if (typeof documentId === 'object' && documentId !== null) {
        docIdStr = documentId.documentId || documentId.id || documentId.url;
        docTitle = documentId.friendlyName || documentId.title || documentId.topic || 'Web Source';
        console.log(`📖 READER: Normalized object input to: ${docIdStr} (${docTitle})`);
      }

      // Check for URL (Web Citation)
      if (typeof docIdStr === 'string' && (docIdStr.startsWith('http://') || docIdStr.startsWith('https://'))) {
        console.log('🌐 READER: Web URL detected, opening WebViewer');
        setCurrentWebPage({
          url: docIdStr,
          title: docTitle,
          content: '',
          description: ''
        });
        setShowWebViewer(true);
        setLoading(false);
        return;
      }

      // Check if this is an xAI collection file (file_ prefix)
      const isXAIFile = docIdStr && typeof docIdStr === 'string' && docIdStr.startsWith('file_');

      if (isXAIFile) {
        console.log('🔮 READER: xAI collection file detected, fetching with authentication');

        const serviceBaseUrl = CONFIG.CITRA_SERVICE_URL || 'http://localhost:8085';
        const xaiDownloadUrl = `${serviceBaseUrl}/api/xai-files/download/${docIdStr}`;

        try {
          // Fetch the file with authentication
          const token = await authService.getToken();
          console.log('🔮 READER: Fetching xAI file with auth token');

          const response = await fetch(xaiDownloadUrl, {
            method: 'GET',
            headers: {
              'Authorization': `Bearer ${token}`
            }
          });

          if (!response.ok) {
            throw new Error(`Failed to download xAI file: ${response.status} ${response.statusText}`);
          }

          // Get the content type from the response
          const contentType = response.headers.get('content-type') || 'application/pdf';
          console.log('🔮 READER: xAI file content type:', contentType);

          // Convert to blob and create blob URL
          const blob = await response.blob();
          const blobUrl = URL.createObjectURL(blob);
          console.log('✅ READER: Created blob URL for xAI file');

          // Set up as a PDF-like document for WebViewer
          const xaiDocument = {
            id: docIdStr,
            topic_or_filename: docIdStr,
            file_type: contentType,
            download_url: blobUrl,
            isPDF: contentType.includes('pdf'),
            isXAIFile: true,
            blobUrl: blobUrl, // Store for cleanup
            text: null,
            total_chunks: 0
          };

          setDocumentContent(xaiDocument);
          setSelectedDocument(docIdStr);
          console.log('✅ READER: xAI file loaded and ready for viewing');
          setLoading(false);
          return;

        } catch (xaiError) {
          console.error('❌ READER: Error loading xAI file:', xaiError);
          setError(`Failed to load xAI file: ${xaiError.message}`);
          setLoading(false);
          return;
        }
      }

      // Regular document flow
      const result = await readerService.getDocument(docIdStr);

      if (!result.success) {
        throw new Error(result.error || 'Failed to load document');
      }

      // Check if this is a PDF document (fallback to filename/content type as needed)
      const fileType = (result.document.file_type || '').toLowerCase();
      const fileName = (result.document.topic_or_filename || '').toLowerCase();
      console.log('📑 READER: Incoming document metadata', {
        file_type: result.document.file_type,
        topic_or_filename: result.document.topic_or_filename,
        has_download_url: Boolean(result.document.download_url)
      });

      let isPDF = fileType.includes('pdf') || fileName.endsWith('.pdf');

      if (isPDF) {
        console.log('📄 READER: Detected PDF document, fetching download URL');

        // Prefer proxy to avoid CORS/download; fallback to direct download
        try {
          const proxyUrl = await readerService.getPdfProxyUrl(docIdStr);
          if (proxyUrl) {
            result.document.download_url = proxyUrl;
            result.document.isPDF = true;
            result.document.isProxied = true;
            console.log('✅ READER: PDF proxy URL obtained');
          } else {
            const serviceBaseUrl = CONFIG.CITRA_SERVICE_URL || 'http://localhost:8085';
            const downloadEndpoint = `${serviceBaseUrl}/api/documents/${docIdStr}/download`;

            const token = await authService.getToken();
            const downloadResponse = await fetch(downloadEndpoint, {
              method: 'GET',
              headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
              }
            });

            if (downloadResponse.ok) {
              const downloadData = await downloadResponse.json();
              result.document.download_url = downloadData.download_url;
              result.document.isPDF = true;
              console.log('✅ READER: PDF download URL obtained');
            } else {
              console.warn('⚠️ READER: Failed to get PDF download URL');
            }
          }
        } catch (pdfError) {
          console.error('❌ READER: Error fetching PDF URL:', pdfError);
        }
      }

      // If API already provided a download URL, treat as PDF even if file_type was missing
      if (!isPDF && result.document.download_url) {
        isPDF = true;
        result.document.isPDF = true;
      }

      setDocumentContent(result.document);
      setSelectedDocument(docIdStr);
      // Reduced verbosity
      // console.log(`✅ Loaded document with ${result.document.total_chunks} chunks`);

    } catch (err) {
      console.error('❌ Error loading document:', err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Go back to document list
  const handleBackToList = () => {
    setSelectedDocument(null);
    setDocumentContent(null);
    setError(null);
  };

  // Handle back from web viewer (web citations opened as documents)
  const handleBackFromWebViewer = () => {
    setShowWebViewer(false);
    setCurrentWebPage(null);
  };

  // Handle back from document viewer to show web viewer with AI chat
  const handleDocumentViewWithChat = (documentId) => {
    loadDocument(documentId);
    // Document viewer will now include AI chat
  };

  // Filter documents based on search
  const filteredDocuments = documents.filter((doc) => {
    if (!searchQuery) return true;

    const query = searchQuery.toLowerCase();
    const metadata = doc.reader_metadata || {};

    return (
      (metadata.extracted_title || '').toLowerCase().includes(query) ||
      (metadata.document_type || '').toLowerCase().includes(query) ||
      (metadata.case_number || '').toLowerCase().includes(query) ||
      (metadata.preview || '').toLowerCase().includes(query) ||
      (doc.original_filename || '').toLowerCase().includes(query)
    );
  });

  // Render document list item
  const renderDocumentItem = (doc, index) => {
    const metadata = doc.reader_metadata || {};
    const title = metadata.extracted_title || doc.original_filename || 'Unknown Document';
    const docType = metadata.document_type || 'Document';
    const caseNumber = metadata.case_number || '';
    const preview = metadata.preview || 'No preview available';
    const isSelected = selectedDocument === doc.document_id;

    return (
      <TouchableOpacity
        key={doc.document_id || index}
        onPress={() => loadDocument(doc.document_id)}
        style={{
          padding: 16,
          marginHorizontal: 16,
          marginBottom: 8,
          backgroundColor: isSelected ? (isDarkMode ? '#0f172a' : '#f0fdf4') : panelBg, // Slate 900 / Green 50
          borderRadius: 12,
          borderWidth: 1,
          borderColor: isSelected ? highlightColor : borderColor,
          shadowColor: '#000',
          shadowOffset: { width: 0, height: 1 },
          shadowOpacity: 0.05,
          shadowRadius: 2,
          elevation: 1
        }}
      >
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <View style={{ flex: 1, marginRight: 12 }}>
            {/* Document Type Badge */}
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 6 }}>
              <View
                style={{
                  backgroundColor: isDarkMode ? '#1e3a28' : '#dcfce7', // Green 900 / Green 100
                  paddingHorizontal: 8,
                  paddingVertical: 2,
                  borderRadius: 4,
                  marginRight: 8
                }}
              >
                <Text style={{ fontSize: 11, color: isDarkMode ? '#86efac' : '#166534', fontWeight: '700', textTransform: 'uppercase' }}>
                  {docType}
                </Text>
              </View>

              {caseNumber && (
                <Text style={{ fontSize: 12, color: textSecondary, fontWeight: '500' }}>
                  #{caseNumber}
                </Text>
              )}
            </View>

            {/* Title */}
            <Text
              style={{
                fontSize: 16,
                fontWeight: '600',
                color: textColor,
                marginBottom: 4,
                lineHeight: 22
              }}
              numberOfLines={2}
            >
              {title}
            </Text>

            {/* Preview */}
            <Text
              style={{
                fontSize: 13,
                color: textSecondary,
                marginTop: 2,
                lineHeight: 18
              }}
              numberOfLines={2}
            >
              {preview}
            </Text>

            {/* Parties */}
            {metadata.parties && metadata.parties.length > 0 && (
              <View style={{ flexDirection: 'row', marginTop: 8, alignItems: 'center' }}>
                <Ionicons name="people-outline" size={14} color={textSecondary} style={{ marginRight: 4 }} />
                <Text
                  style={{
                    fontSize: 12,
                    color: textSecondary,
                    fontStyle: 'italic'
                  }}
                  numberOfLines={1}
                >
                  {metadata.parties.join(', ')}
                </Text>
              </View>
            )}
          </View>

          {/* Chevron/Selected Indicator */}
          <Ionicons
            name={isSelected ? "checkmark-circle" : "chevron-forward"}
            size={isSelected ? 20 : 16}
            color={isSelected ? highlightColor : textSecondary}
          />
        </View>
      </TouchableOpacity>
    );

  };

  // Render document viewer with AI chat
  const renderDocumentViewer = () => {
    if (!documentContent) return null;

    const metadata = documentContent.reader_metadata || {};
    const title = metadata.extracted_title || documentContent.topic_or_filename || 'Document';
    const isPDF = documentContent.isPDF || false;
    const pdfUrl = documentContent.download_url || null;
    const isXAIFile = documentContent.isXAIFile || false;

    // Detect mobile web browser — iframes can't display PDFs on Android/iOS browsers
    const { width } = Dimensions.get('window');
    const isMobileWebBrowser = Platform.OS === 'web' && width < 768 && (() => {
      if (typeof navigator === 'undefined') return false;
      const ua = navigator.userAgent || navigator.vendor || '';
      return /android|webos|iphone|ipad|ipod|blackberry|iemobile|opera mini|mobile/i.test(ua);
    })();

    // For PDFs on desktop web: render via iframe in WebViewer
    // For PDFs on mobile web: fall through to extracted text (chunks from MongoDB) to avoid blank iframe
    if (isPDF && pdfUrl && !isMobileWebBrowser) {
      return (
        <WebViewer
          url={pdfUrl}
          title={title}
          content=""
          onBack={handleBackToList}
          onClose={onClose}
          theme={theme}
          type="document"
          documentId={selectedDocument}
          userId={userId}
          isPDF={true}
          isXAIFile={isXAIFile}
          onEmbedToVault={onEmbedToVault}
          folderId={folderId}
          folderName={folderName}
        />
      );
    }

    // Sort chunks and combine for text documents (also used for PDFs on mobile web)
    const chunks = documentContent.chunks || [];
    const sortedChunks = [...chunks].sort((a, b) =>
      (a.chunk_index || 0) - (b.chunk_index || 0)
    );

    const fullText = sortedChunks
      .map((chunk) => chunk.text)
      .join('\n\n');

    return (
      <WebViewer
        url={null}
        title={title}
        content={fullText}
        onBack={handleBackToList}
        onClose={onClose}
        theme={theme}
        type="document"
        documentId={selectedDocument}
        userId={userId}
        isPDF={false}
        onEmbedToVault={onEmbedToVault}
        folderId={folderId}
        folderName={folderName}
      />
    );
  };


  // Render document list
  const renderDocumentList = () => {
    return (
      <View style={{ flex: 1, backgroundColor: bgColor }}>
        {/* Extraction status */}
        {extracting && extractionStatus && (
          <View
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              padding: 12,
              marginHorizontal: 16,
              marginTop: 16,
              borderRadius: 8,
              backgroundColor: isDarkMode ? '#1e3a28' : '#e6f7ed',
              borderWidth: 1,
              borderColor: highlightColor
            }}
          >
            <ActivityIndicator size="small" color={highlightColor} style={{ marginRight: 12 }} />
            <Text style={{ fontSize: 13, color: isDarkMode ? '#86efac' : '#166534', fontWeight: '500', flex: 1 }}>
              {extractionStatus}
            </Text>
          </View>
        )}

        {/* Search bar */}
        <View style={{ padding: 16, paddingBottom: 8 }}>
          <View
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              backgroundColor: panelBg,
              borderRadius: 12,
              paddingHorizontal: 12,
              height: 48,
              borderWidth: 1,
              borderColor: borderColor,
              shadowColor: '#000',
              shadowOffset: { width: 0, height: 2 },
              shadowOpacity: 0.05,
              shadowRadius: 4,
              elevation: 2
            }}
          >
            <Ionicons name="search" size={20} color={textSecondary} />
            <TextInput
              value={searchQuery}
              onChangeText={setSearchQuery}
              placeholder="Search your documents..."
              placeholderTextColor={textSecondary}
              style={{
                flex: 1,
                marginLeft: 12,
                fontSize: 16,
                color: textColor
              }}
            />
            {searchQuery.length > 0 && (
              <TouchableOpacity onPress={() => setSearchQuery('')} style={{ padding: 4 }}>
                <Ionicons name="close-circle" size={18} color={textSecondary} />
              </TouchableOpacity>
            )}
          </View>
        </View>

        {/* Toolbar / Count */}
        <View
          style={{
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'space-between',
            paddingHorizontal: 16,
            paddingBottom: 8,
          }}
        >
          <Text style={{ fontSize: 13, fontWeight: '600', color: textSecondary, textTransform: 'uppercase', letterSpacing: 0.5 }}>
            {filteredDocuments.length} document{filteredDocuments.length !== 1 ? 's' : ''} found
          </Text>
        </View>

        {/* Error message */}
        {error && (
          <View
            style={{
              padding: 12,
              marginHorizontal: 16,
              marginBottom: 8,
              backgroundColor: '#fef2f2',
              borderRadius: 8,
              borderWidth: 1,
              borderColor: '#fecaca'
            }}
          >
            <Text style={{ fontSize: 13, color: errorColor }}>
              ⚠️ {error}
            </Text>
          </View>
        )}

        {/* Document list */}
        {loading && documents.length === 0 ? (
          <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}>
            <ActivityIndicator size="large" color={highlightColor} />
            <Text style={{ marginTop: 16, fontSize: 15, fontWeight: '500', color: textSecondary }}>
              Loading documents...
            </Text>
          </View>
        ) : filteredDocuments.length === 0 ? (
          <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', padding: 24 }}>
            <View style={{
              width: 80,
              height: 80,
              borderRadius: 40,
              backgroundColor: isDarkMode ? '#1e3a28' : '#f0fdf4',
              justifyContent: 'center',
              alignItems: 'center',
              marginBottom: 20
            }}>
              <Ionicons name="document-text" size={40} color={highlightColor} />
            </View>
            <Text style={{ fontSize: 20, fontWeight: '800', color: textColor, textAlign: 'center', marginBottom: 8 }}>
              {searchQuery
                ? 'No matches found'
                : folderId
                  ? 'Folder is empty'
                  : 'Your Documents'}
            </Text>
            <Text style={{ fontSize: 15, color: textSecondary, textAlign: 'center', maxWidth: 400, lineHeight: 22 }}>
              {searchQuery
                ? `We couldn't find any documents matching "${searchQuery}"`
                : 'Upload documents to your data store to analyze, read, and chat with them using AI.'}
            </Text>
          </View>
        ) : (
          <ScrollView style={{ flex: 1 }}>
            {filteredDocuments.map(renderDocumentItem)}

            {/* Load More Button */}
            {hasMoreDocuments && !searchQuery && (
              <TouchableOpacity
                onPress={loadMoreDocuments}
                disabled={loadingMore}
                style={{
                  margin: 16,
                  padding: 14,
                  backgroundColor: loadingMore ? borderColor : highlightColor,
                  borderRadius: 12,
                  alignItems: 'center',
                  flexDirection: 'row',
                  justifyContent: 'center',
                  gap: 8,
                  shadowColor: highlightColor,
                  shadowOffset: { width: 0, height: 4 },
                  shadowOpacity: 0.2,
                  shadowRadius: 8,
                  elevation: 4
                }}
              >
                {loadingMore ? (
                  <>
                    <ActivityIndicator size="small" color="#fff" />
                    <Text style={{ color: '#fff', fontWeight: '600' }}>Loading more...</Text>
                  </>
                ) : (
                  <>
                    <Ionicons name="arrow-down-circle-outline" size={20} color="#fff" />
                    <Text style={{ color: '#fff', fontWeight: '600', fontSize: 15 }}>
                      Load More Documents ({documents.length} of {totalDocuments})
                    </Text>
                  </>
                )}
              </TouchableOpacity>
            )}

            {/* Document count footer */}
            {!hasMoreDocuments && documents.length > 0 && !searchQuery && (
              <View style={{ padding: 24, alignItems: 'center' }}>
                <Text style={{ color: textSecondary, fontSize: 13, fontWeight: '500' }}>
                  All {documents.length} documents loaded
                </Text>
              </View>
            )}
          </ScrollView>
        )}
      </View>
    );
  };

  return (
    <Modal
      visible={isVisible}
      animationType="slide"
      presentationStyle="fullScreen"
      onRequestClose={onClose}
    >
      <View style={{ flex: 1, backgroundColor: bgColor }}>
        {/* Web Viewer (for documents that are web citations / URLs) */}
        {showWebViewer && currentWebPage ? (
          <WebViewer
            url={currentWebPage.url}
            title={currentWebPage.title}
            content={currentWebPage.content}
            onBack={handleBackFromWebViewer}
            onClose={onClose}
            theme={theme}
            type="internet"
            userId={userId}
            onEmbedToVault={onEmbedToVault}
            folderId={folderId}
            folderName={folderName}
          />
        ) : selectedDocument && documentContent ? (
          /* Document Viewer with AI Chat (for personal documents) */
          renderDocumentViewer()
        ) : (
          /* Main View — document list */
          <View style={{ flex: 1, backgroundColor: bgColor }}>
            {/* Header */}
            <View
              style={{
                flexDirection: 'row',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: isMobile ? 12 : 16,
                borderBottomWidth: 1,
                borderBottomColor: borderColor,
                backgroundColor: panelBg
              }}
            >
              <View style={{ flex: 1 }}>
                <Text style={{ fontSize: isMobile ? 18 : 20, fontWeight: '800', color: textColor, letterSpacing: -0.5 }}>
                  Reader
                </Text>
                <Text style={{ fontSize: isMobile ? 12 : 13, color: textSecondary, marginTop: 2 }}>
                  {folderId ? (folderName || 'Selected Folder') : 'All Documents'}
                </Text>
              </View>

              <TouchableOpacity onPress={onClose} style={{ padding: 4 }}>
                <Ionicons name="close" size={isMobile ? 22 : 24} color={textSecondary} />
              </TouchableOpacity>
            </View>

            {/* Document list */}
            {renderDocumentList()}
          </View>
        )}
      </View>
    </Modal>
  );
};

export default ReaderPanel;
