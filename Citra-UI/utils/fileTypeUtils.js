// ========================= File Type Utilities =========================
// Purpose: File type detection and display utilities for multiple file formats
// Features: File extension mapping, MIME type detection, display formatting
// --------------------------------------------------------------------------

/**
 * Get file type information from filename or MIME type
 */
export const getFileTypeInfo = (filename, mimeType = '') => {
  const ext = filename ? filename.split('.').pop()?.toLowerCase() : '';
  
  // File type mappings
  const fileTypes = {
    // Documents
    pdf: {
      extensions: ['pdf'],
      mimeTypes: ['application/pdf'],
      category: 'document',
      displayName: 'PDF Document',
      color: '#FF6B6B',
      icon: 'document-text'
    },
    docx: {
      extensions: ['docx'],
      mimeTypes: ['application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
      category: 'document', 
      displayName: 'Word Document',
      color: '#2B579A',
      icon: 'document-text'
    },
    xlsx: {
      extensions: ['xlsx'],
      mimeTypes: ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
      category: 'spreadsheet',
      displayName: 'Excel Spreadsheet',
      color: '#217346',
      icon: 'grid'
    },
    xls: {
      extensions: ['xls'],
      mimeTypes: ['application/vnd.ms-excel'],
      category: 'spreadsheet',
      displayName: 'Excel Spreadsheet (Legacy)',
      color: '#217346',
      icon: 'grid'
    },
    pptx: {
      extensions: ['pptx'],
      mimeTypes: ['application/vnd.openxmlformats-officedocument.presentationml.presentation'],
      category: 'presentation',
      displayName: 'PowerPoint Presentation',
      color: '#D24726',
      icon: 'easel'
    },
    txt: {
      extensions: ['txt'],
      mimeTypes: ['text/plain'],
      category: 'text',
      displayName: 'Text File',
      color: '#6B7280',
      icon: 'document-text'
    },
    md: {
      extensions: ['md', 'markdown'],
      mimeTypes: ['text/markdown', 'text/x-markdown'],
      category: 'text',
      displayName: 'Markdown File',
      color: '#374151',
      icon: 'document-text'
    },
    // Google Docs formats
    gdoc: {
      extensions: ['gdoc'],
      mimeTypes: ['application/vnd.google-apps.document'],
      category: 'document',
      displayName: 'Google Docs',
      color: '#4285F4',
      icon: 'document-text'
    },
    gsheet: {
      extensions: ['gsheet'],
      mimeTypes: ['application/vnd.google-apps.spreadsheet'],
      category: 'spreadsheet',
      displayName: 'Google Sheets',
      color: '#0F9D58',
      icon: 'grid'
    },
    gslides: {
      extensions: ['gslides'],
      mimeTypes: ['application/vnd.google-apps.presentation'],
      category: 'presentation',
      displayName: 'Google Slides',
      color: '#F4B400',
      icon: 'easel'
    },
    // Images (existing)
    jpg: {
      extensions: ['jpg', 'jpeg'],
      mimeTypes: ['image/jpeg'],
      category: 'image',
      displayName: 'JPEG Image',
      color: '#10B981',
      icon: 'image'
    },
    png: {
      extensions: ['png'],
      mimeTypes: ['image/png'],
      category: 'image',
      displayName: 'PNG Image',
      color: '#10B981',
      icon: 'image'
    }
  };

  // Find file type by extension first
  for (const [type, info] of Object.entries(fileTypes)) {
    if (info.extensions.includes(ext)) {
      return { type, ...info };
    }
  }

  // Fallback to MIME type matching
  for (const [type, info] of Object.entries(fileTypes)) {
    if (info.mimeTypes.includes(mimeType.toLowerCase())) {
      return { type, ...info };
    }
  }

  // Default fallback
  return {
    type: 'unknown',
    category: 'unknown',
    displayName: 'Unknown File',
    color: '#9CA3AF',
    icon: 'document'
  };
};

/**
 * Get supported file types for document upload
 * Note: Google Docs formats (gdoc, gsheet, gslides) are only available through Google Drive picker
 */
export const getSupportedFileTypes = () => ({
  documents: [
    { ext: 'pdf', name: 'PDF Documents', mime: 'application/pdf' },
    { ext: 'docx', name: 'Word Documents', mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' },
    { ext: 'xlsx', name: 'Excel Spreadsheets', mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
    { ext: 'xls', name: 'Excel Spreadsheets (Legacy)', mime: 'application/vnd.ms-excel' },
    { ext: 'pptx', name: 'PowerPoint Presentations', mime: 'application/vnd.openxmlformats-officedocument.presentationml.presentation' },
    { ext: 'txt', name: 'Text Files', mime: 'text/plain' },
    { ext: 'md', name: 'Markdown Files', mime: 'text/markdown' },
    { ext: 'gdoc', name: 'Google Docs', mime: 'application/vnd.google-apps.document' },
    { ext: 'gsheet', name: 'Google Sheets', mime: 'application/vnd.google-apps.spreadsheet' },
    { ext: 'gslides', name: 'Google Slides', mime: 'application/vnd.google-apps.presentation' }
  ],
  images: [
    { ext: 'jpg', name: 'JPEG Images', mime: 'image/jpeg' },
    { ext: 'png', name: 'PNG Images', mime: 'image/png' },
    { ext: 'gif', name: 'GIF Images', mime: 'image/gif' },
    { ext: 'bmp', name: 'BMP Images', mime: 'image/bmp' },
    { ext: 'tiff', name: 'TIFF Images', mime: 'image/tiff' },
    { ext: 'webp', name: 'WebP Images', mime: 'image/webp' }
  ]
});

/**
 * Format file size for display
 */
export const formatFileSize = (bytes) => {
  if (bytes === 0) return '0 Bytes';
  
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

/**
 * Validate file type against supported types
 */
export const isFileTypeSupported = (filename, mimeType = '') => {
  const supportedTypes = getSupportedFileTypes();
  const allSupported = [...supportedTypes.documents, ...supportedTypes.images];
  
  const ext = filename ? filename.split('.').pop()?.toLowerCase() : '';
  
  return allSupported.some(type => 
    type.ext === ext || type.mime === mimeType.toLowerCase()
  );
};

/**
 * Get processing method for file type
 */
export const getProcessingMethod = (filename, mimeType = '') => {
  const fileInfo = getFileTypeInfo(filename, mimeType);
  
  switch (fileInfo.category) {
    case 'document':
      if (fileInfo.type === 'pdf') {
        return 'Enhanced PDF processing with Vision API';
      } else if (fileInfo.type === 'gdoc') {
        return 'Google Docs text extraction (exported as Word)';
      }
      return 'Direct text extraction';
    case 'spreadsheet':
      if (fileInfo.type === 'gsheet') {
        return 'Google Sheets data extraction (exported as Excel)';
      }
      return 'Table and data extraction';
    case 'presentation':
      if (fileInfo.type === 'gslides') {
        return 'Google Slides text extraction (exported as PowerPoint)';
      }
      return 'Slide-by-slide text extraction';
    case 'text':
      return 'Plain text processing';
    case 'image':
      return 'Vision API text extraction';
    default:
      return 'Standard processing';
  }
};

/**
 * Get estimated processing time based on file type and size
 */
export const getEstimatedProcessingTime = (filename, fileSize, mimeType = '') => {
  const fileInfo = getFileTypeInfo(filename, mimeType);
  const sizeMB = fileSize / (1024 * 1024);
  
  // Base processing time estimates (in seconds)
  const baseTimeByCategory = {
    document: fileInfo.type === 'pdf' ? 15 : 5,  // PDF needs more time for Vision API
    spreadsheet: 8,
    presentation: 10,
    text: 2,
    image: 10,
    unknown: 5
  };
  
  const baseTime = baseTimeByCategory[fileInfo.category] || 5;
  const sizeMultiplier = Math.max(1, sizeMB * 0.5); // 0.5 seconds per MB
  
  return Math.ceil(baseTime * sizeMultiplier);
};

export default {
  getFileTypeInfo,
  getSupportedFileTypes,
  formatFileSize,
  isFileTypeSupported,
  getProcessingMethod,
  getEstimatedProcessingTime
};
