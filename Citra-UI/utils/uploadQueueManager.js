import { v4 as uuidv4 } from 'uuid';

/**
 * Upload Queue Manager
 * Handles parallel uploads without blocking and processes them one by one
 */
export class UploadQueueManager {
  constructor() {
    this.queue = [];
    this.processing = false;
    this.activeUploads = new Map();
    this.maxConcurrent = 3; // Process up to three uploads in parallel
    this.listeners = new Set();
    this.duplicateListeners = new Set(); // Listeners for duplicate detection
  }

  // Add listener for queue updates
  addListener(listener) {
    this.listeners.add(listener);
  }

  removeListener(listener) {
    this.listeners.delete(listener);
  }

  // Add listener for duplicate notifications
  addDuplicateListener(listener) {
    this.duplicateListeners.add(listener);
  }

  removeDuplicateListener(listener) {
    this.duplicateListeners.delete(listener);
  }

  // Notify duplicate listeners
  notifyDuplicateDetected(topicName, existingItem) {
    this.duplicateListeners.forEach(listener => {
      try {
        listener({
          type: 'duplicate_detected',
          topicName: topicName,
          existingItem: existingItem,
          timestamp: Date.now()
        });
      } catch (error) {
        console.error('Duplicate listener error:', error);
      }
    });
  }

  // Notify all listeners about queue changes
  notifyListeners() {
    const activeProcessingUploads = Array.from(this.activeUploads.values()).filter(
      upload => upload.status === 'processing'
    ).length;

    this.listeners.forEach(listener => {
      try {
        listener({
          queue: [...this.queue],
          activeUploads: new Map(this.activeUploads),
          processing: this.processing,
          totalInQueue: this.queue.length,
          totalActive: this.activeUploads.size,
          activeProcessing: activeProcessingUploads
        });
      } catch (error) {
        console.error('Upload queue listener error:', error);
      }
    });
  }

  // Add upload to queue
  addToQueue(uploadItem) {
    const queueItem = {
      id: uploadItem.id || uuidv4(),
      ...uploadItem,
      status: 'queued',
      queuedAt: Date.now()
    };

    // Check for duplicate topic names in queue and active uploads
    const topicName = queueItem.title || queueItem.fileName;
    if (topicName) {
      const existingQueueItem = this.queue.find(item => 
        (item.title || item.fileName) === topicName
      );
      
      const existingActiveItem = Array.from(this.activeUploads.values()).find(item => 
        (item.title || item.fileName) === topicName && 
        (item.status === 'processing' || item.status === 'queued')
      );

      if (existingQueueItem || existingActiveItem) {
        const existingItem = existingQueueItem || existingActiveItem;
        console.log(`⚠️ Duplicate upload detected for topic "${topicName}". Ignoring request.`);
        
        // Notify duplicate listeners for UI feedback
        this.notifyDuplicateDetected(topicName, existingItem);
        
        // Notify regular listeners about the queue state
        this.notifyListeners();
        
        // Return existing item ID
        return existingItem.id;
      }
    }

    this.queue.push(queueItem);
    this.notifyListeners();
    this.processNext();
    return queueItem.id;
  }

  // Add multiple files to queue (for preload upload)
  addMultipleToQueue(uploadItems) {
    console.log('🔍 DEBUG: addMultipleToQueue ENTRY - items count:', uploadItems.length, 'timestamp:', Date.now());
    const queueIds = [];
    const duplicates = [];
    
    uploadItems.forEach(item => {
      const queueItem = {
        id: item.id || uuidv4(),
        ...item,
        status: 'queued',
        queuedAt: Date.now()
      };
      
      // Check for duplicate topic names
      const topicName = queueItem.title || queueItem.fileName;
      if (topicName) {
        const existingQueueItem = this.queue.find(queuedItem => 
          (queuedItem.title || queuedItem.fileName) === topicName
        );
        
        const existingActiveItem = Array.from(this.activeUploads.values()).find(activeItem => 
          (activeItem.title || activeItem.fileName) === topicName && 
          (activeItem.status === 'processing' || activeItem.status === 'queued')
        );

        // Check for duplicates within the current batch
        const isDuplicateInBatch = this.queue.some(q => 
          queueIds.includes(q.id) && (q.title || q.fileName) === topicName
        );

        if (existingQueueItem || existingActiveItem || isDuplicateInBatch) {
          const existingItem = existingQueueItem || existingActiveItem;
          console.log(`⚠️ Duplicate upload detected in batch for topic "${topicName}". Skipping.`);
          duplicates.push({ topicName, existingItem });
          
          // Notify duplicate listeners for each duplicate found
          if (existingItem) {
            this.notifyDuplicateDetected(topicName, existingItem);
          }
          
          return; // Skip this item
        }
      }
      
      this.queue.push(queueItem);
      queueIds.push(queueItem.id);
      console.log('🔍 DEBUG: Added item to queue - id:', queueItem.id, 'title:', queueItem.title);
    });

    if (duplicates.length > 0) {
      console.log(`📥 Added ${uploadItems.length - duplicates.length} items to upload queue. Skipped ${duplicates.length} duplicates: ${duplicates.map(d => d.topicName).join(', ')}`);
    } else {
      console.log('📥 Added multiple items to upload queue:', uploadItems.length, 'items');
    }

    this.notifyListeners();
    this.processNext();
    return queueIds;
  }

  // Process next item in queue
  async processNext() {
    // Count only processing uploads, not completed ones
    const activeProcessingUploads = Array.from(this.activeUploads.values()).filter(
      upload => upload.status === 'processing'
    ).length;

    // Debug log only in development - reduced verbosity
    // if (process.env.NODE_ENV === 'development') {
    //   console.log('🔍 processNext called:', {
    //     processing: this.processing,
    //     queueLength: this.queue.length,
    //     totalActiveUploads: this.activeUploads.size,
    //     activeProcessingUploads,
    //     maxConcurrent: this.maxConcurrent
    //   });
    // }

    // Don't process if we're at max concurrent uploads or queue is empty
    if (this.queue.length === 0 || activeProcessingUploads >= this.maxConcurrent) {
      // Reduced verbosity - only log errors
      // if (process.env.NODE_ENV === 'development') {
      //   console.log('🚫 processNext early return:', {
      //     queueEmpty: this.queue.length === 0,
      //     atMaxConcurrent: activeProcessingUploads >= this.maxConcurrent,
      //     activeProcessingCount: activeProcessingUploads,
      //     maxConcurrent: this.maxConcurrent
      //   });
      // }
      return;
    }

    const nextItem = this.queue.shift();
    
    if (!nextItem) {
      return;
    }

    // Reduced verbosity - only show important status
    if (process.env.NODE_ENV === 'development') {
      console.log(`🔄 Processing: ${nextItem.title.substring(0, 30)}...`);
    }

    // Move to active uploads
    this.activeUploads.set(nextItem.id, {
      ...nextItem,
      status: 'processing',
      startedAt: Date.now()
    });

    this.notifyListeners();

    // Start processing this upload in parallel
    this.processUpload(nextItem);

    // Schedule next upload immediately if we're not at max concurrent
    if (activeProcessingUploads + 1 < this.maxConcurrent && this.queue.length > 0) {
      setTimeout(() => this.processNext(), 100);
    }
  }

  // Process a single upload item
  async processUpload(item) {
    try {
      // Execute the upload function - reduced logging
      // console.log('🚀 Starting upload for:', item.title);
      // console.log('🔍 DEBUG: About to call uploadFunction - id:', item.id, 'title:', item.title, 'timestamp:', Date.now());
      
      // For enhanced uploads, we need to wait for completion differently
      if (item.type === 'document' || item.type === 'pdf') {
        // Start the enhanced upload with folder information and OCR flag
        await item.uploadFunction(item.document, item.title, item.id, item.folderId, item.useOCR);
        // console.log('🔍 DEBUG: uploadFunction call completed - id:', item.id, 'title:', item.title);
        
        // Reduced verbosity
        // if (process.env.NODE_ENV === 'development') {
        //   console.log('📊 Enhanced upload started, waiting for completion...');
        // }
        
        // Wait for the progress to reach completion
        // We'll poll until we see the progress is no longer being tracked
        await new Promise((resolve) => {
          const checkCompletion = () => {
            // Wait a minimum of 3 seconds for processing
            setTimeout(() => {
              // Reduced verbosity
              // if (process.env.NODE_ENV === 'development') {
              //   console.log('🏁 Enhanced upload processing time elapsed');
              // }
              resolve();
            }, 3000);
          };
          checkCompletion();
        });
      } else {
        // For non-PDF uploads (including images), also pass folderId and useOCR
        await item.uploadFunction(item.document, item.title, item.id, item.folderId, item.useOCR, item.isEnterprise, item.entityId, item.documentDetails);
      }
      
      // Reduced verbosity
      // if (process.env.NODE_ENV === 'development') {
      //   console.log('🏁 Upload function completed for:', item.title);
      // }
      
      // Mark as completed
      this.activeUploads.set(item.id, {
        ...item,
        status: 'completed',
        completedAt: Date.now()
      });

      if (process.env.NODE_ENV === 'development') {
        console.log('✅ Upload completed:', item.title.substring(0, 30) + '...');
      }

      // Remove from active uploads after a brief delay
      setTimeout(() => {
        this.activeUploads.delete(item.id);
        this.notifyListeners();
        
        // Try to process next item in queue after this one completes
        this.processNext();
      }, 5000);

    } catch (error) {
      console.error('❌ Upload failed:', {
        id: item.id,
        title: item.title,
        error: error.message
      });

      // Mark as failed
      this.activeUploads.set(item.id, {
        ...item,
        status: 'failed',
        error: error.message,
        failedAt: Date.now()
      });

      // Remove from active uploads after a delay
      setTimeout(() => {
        this.activeUploads.delete(item.id);
        this.notifyListeners();
        
        // Try to process next item in queue even after failure
        this.processNext();
      }, 10000);
    }

    this.notifyListeners();

    // Reduced verbosity
    // if (process.env.NODE_ENV === 'development') {
    //   console.log('🔄 processUpload completed for:', item.title);
    // }
  }

  // Remove item from queue
  removeFromQueue(id) {
    const queueIndex = this.queue.findIndex(item => item.id === id);
    if (queueIndex !== -1) {
      const removed = this.queue.splice(queueIndex, 1)[0];
      // Reduced verbosity
      // console.log('🗑️ Removed from queue:', { id, title: removed.title });
      this.notifyListeners();
      return true;
    }
    return false;
  }

  // Cancel active upload
  cancelActiveUpload(id) {
    if (this.activeUploads.has(id)) {
      this.activeUploads.delete(id);
      // Reduced verbosity
      // console.log('❌ Cancelled active upload:', { id });
      this.notifyListeners();
      return true;
    }
    return false;
  }

  // Get queue status
  getStatus() {
    const activeProcessingUploads = Array.from(this.activeUploads.values()).filter(
      upload => upload.status === 'processing'
    ).length;

    return {
      queue: [...this.queue],
      activeUploads: new Map(this.activeUploads),
      processing: this.processing,
      totalInQueue: this.queue.length,
      totalActive: this.activeUploads.size,
      activeProcessing: activeProcessingUploads
    };
  }

  // Clear all queued items
  clearQueue() {
    this.queue = [];
    console.log('🧹 Cleared upload queue');
    this.notifyListeners();
  }

  // Get estimated wait time for new uploads
  getEstimatedWaitTime() {
    const averageUploadTime = 30000; // 30 seconds average per upload
    const totalAhead = this.queue.length + this.activeUploads.size;
    return totalAhead * averageUploadTime;
  }

  // Check if a topic name is already in queue or being processed
  isDuplicateTopicName(topicName) {
    if (!topicName) return false;

    const isInQueue = this.queue.some(item => 
      (item.title || item.fileName) === topicName
    );
    
    const isInActive = Array.from(this.activeUploads.values()).some(item => 
      (item.title || item.fileName) === topicName && 
      (item.status === 'processing' || item.status === 'queued')
    );

    return isInQueue || isInActive;
  }

  // Get all topic names currently in queue or being processed
  getActiveTopicNames() {
    const queueTopics = this.queue.map(item => item.title || item.fileName).filter(Boolean);
    const activeTopics = Array.from(this.activeUploads.values())
      .filter(item => item.status === 'processing' || item.status === 'queued')
      .map(item => item.title || item.fileName)
      .filter(Boolean);
    
    return [...new Set([...queueTopics, ...activeTopics])];
  }
}

// Create singleton instance
export const uploadQueueManager = new UploadQueueManager();
