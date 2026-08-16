/**
 * Human-readable byte sizes.
 *
 * Extracted from utils/audioCompression.js when the meeting-recording stack was
 * removed. It has nothing to do with audio — it was only living there — and
 * four call sites in App.js still use it.
 */
export const formatFileSize = (bytes) => {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB';
};

export default { formatFileSize };
