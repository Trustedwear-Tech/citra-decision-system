import { CONFIG as API_CONFIG } from '../config/config';
import { authService } from './authService';

/**
 * API Endpoints and Utilities for Clean Architecture
 */
export const API_ENDPOINTS = {
  base: API_CONFIG.CITRA_SERVICE_URL,
  note: '/note',
  chunkedDocuments: '/api/v2/documents/chunked',
  audioTranscripts: '/v2/transcripts',
  videoTranscripts: '/v2/transcripts'
};

/**
 * Centralized HTTP request utility with authentication
 */
export const makeRequest = async (url, options = {}) => {
  try {
    // Use authentication service for all requests
    const response = await authService.authenticatedFetch(url, options);
    return response;
  } catch (error) {
    console.error('API Request Error:', error);
    throw error;
  }
};

/**
 * Make authenticated request to Citra AI Service
 */
export const makeCitraAIRequest = async (endpoint, options = {}) => {
  if (endpoint.startsWith('http://') || endpoint.startsWith('https://')) {
    return makeRequest(endpoint, options);
  }
  const url = `${API_ENDPOINTS.base}${endpoint}`;
  return makeRequest(url, options);
};

/**
 * API Response Transformer Utilities
 */
export const transformers = {
  /**
   * Transform note API response to uniform document format
   */
  noteToDocument: (note) => ({
    document_id: note._id || note.id,
    id: note._id || note.id, // Ensure both formats are available
    filename: `${note.title || 'Untitled Note'}.txt`,
    file_type: 'note',
    content_type: 'note', // Mark as note for proper handling
    total_pages: 1,
    total_chunks: note.total_chunks || 1,
    chunk_size: 1,
    processing_status: 'completed',
    created_at: note.created_at,
    updated_at: note.updated_at || note.created_at,
    topic: note.title || 'Untitled Note',
    title: note.title || 'Untitled Note', // Keep title for compatibility
    file_size: (note.text || '').length,
    fileUrl: null, // Notes don't have file URLs
    // Note-specific fields for proper note handling
    text: note.text, // Keep note text (might be truncated in list view)
    note_id: note.note_id,
    folder_id: note.folder_id || 'notes',
    user_id: note.user_id,
    // Mark this as a note for proper UI routing
    isNote: true
  }),

  /**
   * Transform transcript to uniform document format
   */
  transcriptToDocument: (transcript, type = 'audio') => {
    const isVideo = type === 'video' || transcript.full_transcription;

    // Handle different ID field names: transcript_id (audio API), _id (MongoDB), id (general)
    const transcriptId = transcript.transcript_id || transcript._id || transcript.id;

    // FIXED: Use topic as filename if it already has an extension, otherwise append default extension
    let filename = transcript.topic_or_filename || (isVideo ? 'Video Recording' : 'Audio Recording');

    const fileType = transcript.topic_or_filename?.split('.').pop();

    // Check if filename already has an extension (contains a dot and no spaces after last dot)
    const hasExtension = filename.includes('.') && !filename.split('.').pop().includes(' ');

    if (!hasExtension) {
      // Append default extension if topic doesn't have one
      filename = `${filename}.${isVideo ? 'mp4' : 'mp3'}`;
    }
    // If topic already has extension, use it as-is

    return {
      document_id: transcriptId,
      id: transcriptId, // Ensure id is available for API calls
      transcript_id: transcriptId, // Add transcript_id for viewTranscript compatibility
      _id: transcriptId, // Add _id for viewTranscript compatibility
      filename: filename,
      file_type: fileType,
      total_pages: 1,
      total_chunks: transcript.total_chunks || 0,
      chunk_size: 1,
      processing_status: 'completed',
      created_at: transcript.utc_date || transcript.created_at,
      updated_at: transcript.utc_date || transcript.created_at,
      topic: transcript.topic_or_filename || (isVideo ? 'Video Recording' : 'Audio Recording'),
      topic_or_filename: transcript.topic_or_filename || (isVideo ? 'Video Recording' : 'Audio Recording'),
      file_size: (transcript.transcript || transcript.full_transcription || '').length,
      fileUrl: transcript.audio_url || transcript.video_url,
      // FIXED: Preserve all URL fields for download functionality and viewTranscript detection
      audioUrl: transcript.audio_url || transcript.audioUrl,
      videoUrl: transcript.video_url || transcript.videoUrl,
      audio_url: transcript.audio_url,
      video_url: transcript.video_url,
      original_filename: transcript.original_filename, // Preserve for video transcript detection
      // Preserve transcript type and source for download handlers and viewTranscript detection
      type: transcript.type || (isVideo ? 'video' : 'audio'),
      source: transcript.source || (isVideo ? 'video_transcripts' : 'audio_transcripts')
    };
  },

  /**
   * Create pagination response
   */
  createPaginationResponse: (items, page, perPage, total = null) => {
    const totalCount = total !== null ? total : items.length;
    const totalPages = Math.ceil(totalCount / perPage);

    return {
      documents: items,
      pagination: {
        current_page: page,
        per_page: perPage,
        total_documents: totalCount,
        total_pages: totalPages,
        has_next: page < totalPages,
        has_prev: page > 1,
        next_page: page < totalPages ? page + 1 : null,
        prev_page: page > 1 ? page - 1 : null
      }
    };
  }
};
