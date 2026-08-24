// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

import React, { useState, useEffect, useRef } from 'react';
import { View, Text, Animated, TouchableOpacity, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

/**
 * Chat Upload Bubble Component
 * Shows upload progress as a chat bubble in the message area
 */
const ChatUploadBubble = ({ 
  stage, 
  progress, 
  documentId,
  filename,
  uploadType = 'document', // Add uploadType prop with default
  queuePosition,
  queueStatus,
  error,
  transcriptionPreview,
  transcriptId,
  topic,
  folderId,
  onDismiss,
  style 
}) => {
  const [isVisible, setIsVisible] = useState(true);
  const [autoRemoveCountdown, setAutoRemoveCountdown] = useState(5);
  const slideAnimation = useRef(new Animated.Value(0)).current;
  const progressAnimation = useRef(new Animated.Value(0)).current;
  const fadeAnimation = useRef(new Animated.Value(1)).current;
  const maxTimeoutRef = useRef(null); // For maximum 5-minute timeout

  // Maximum timeout - force close after 5 minutes regardless of state
  useEffect(() => {
    const MAX_BUBBLE_LIFETIME = 5 * 60 * 1000; // 5 minutes
    
    maxTimeoutRef.current = setTimeout(() => {
      // Reduced verbosity
      // console.log('🕐 ChatUploadBubble: Maximum lifetime reached (5 minutes), force closing');
      handleAutoRemoval();
    }, MAX_BUBBLE_LIFETIME);

    return () => {
      if (maxTimeoutRef.current) {
        clearTimeout(maxTimeoutRef.current);
      }
    };
  }, []); // Only run once when component mounts

  useEffect(() => {
    if (isVisible) {
      // Slide in animation
      Animated.spring(slideAnimation, {
        toValue: 1,
        useNativeDriver: true,
        tension: 100,
        friction: 8
      }).start();
    }
  }, [isVisible]);

  useEffect(() => {
    // Animate progress bar
    Animated.timing(progressAnimation, {
      toValue: progress / 100,
      duration: 300,
      useNativeDriver: false
    }).start();
  }, [progress]);

  useEffect(() => {
    // Auto-remove after specified duration based on stage
    if (stage === 'complete' || stage === 'error' || stage === 'redis_down') {
      // Clear the maximum timeout since stage-based removal will handle it
      if (maxTimeoutRef.current) {
        clearTimeout(maxTimeoutRef.current);
      }
      
      // Set countdown duration based on stage - 5 seconds for success, 15 for error, 8 for redis_down
      const countdownDuration = stage === 'error' ? 15 : (stage === 'redis_down' ? 8 : 5);
      let countdown = countdownDuration;
      setAutoRemoveCountdown(countdown);
      
      const countdownInterval = setInterval(() => {
        countdown -= 1;
        setAutoRemoveCountdown(countdown);
        
        if (countdown <= 0) {
          clearInterval(countdownInterval);
        }
      }, 1000);
      
      const timer = setTimeout(() => {
        handleAutoRemoval();
      }, countdownDuration * 1000);
      
      return () => {
        clearTimeout(timer);
        clearInterval(countdownInterval);
      };
    }
  }, [stage]);

  const handleAutoRemoval = () => {
    // Clear the maximum timeout since we're removing the bubble
    if (maxTimeoutRef.current) {
      clearTimeout(maxTimeoutRef.current);
    }
    
    // Fade out animation before removal
    Animated.sequence([
      Animated.timing(fadeAnimation, {
        toValue: 0.5,
        duration: 1000,
        useNativeDriver: true
      }),
      Animated.timing(slideAnimation, {
        toValue: 0,
        duration: 500,
        useNativeDriver: true
      })
    ]).start(() => {
      setIsVisible(false);
      // Reduced verbosity
      // console.log('🧹 ChatUploadBubble: Auto-removal completed for:', filename);
      if (onDismiss) {
        onDismiss();
      }
    });
  };

  const handleManualDismiss = () => {
    // Clear the maximum timeout since we're removing the bubble
    if (maxTimeoutRef.current) {
      clearTimeout(maxTimeoutRef.current);
    }
    
    Animated.timing(slideAnimation, {
      toValue: 0,
      duration: 200,
      useNativeDriver: true
    }).start(() => {
      setIsVisible(false);
      // Reduced verbosity
      // console.log('🧹 ChatUploadBubble: Manual dismiss completed for:', filename);
      if (onDismiss) {
        onDismiss();
      }
    });
  };

  const getStageInfo = (stage) => {
    const isVideo = uploadType === 'video';
    
    const stageMap = {
      queued: {
        title: queuePosition ? `⏳ Queued (#${queuePosition})` : '⏳ Queued for upload...',
        color: '#9E9E9E',
        icon: 'time-outline'
      },
      initializing: {
        title: isVideo ? '🔄 Initializing video upload...' : '🔄 Initializing upload...',
        color: '#2196F3',
        icon: 'sync-outline'
      },
      starting: {
        title: isVideo ? '🚀 Starting video upload...' : '🚀 Starting upload...',
        color: '#2196F3',
        icon: 'cloud-upload-outline'
      },
      analyzing: {
        title: isVideo ? '🔍 Analyzing video content...' : '🔍 Analyzing document...',
        color: '#2196F3',
        icon: isVideo ? 'videocam-outline' : 'document-text-outline'
      },
      extracting: {
        title: isVideo ? '🎬 Extracting video transcription...' : '📝 Extracting text content...',
        color: '#FF9800',
        icon: isVideo ? 'mic-outline' : 'text-outline'
      },
      processing: {
        title: isVideo ? '⚙️ Processing video content...' : '⚙️ Processing content...',
        color: '#FF9800',
        icon: 'cog-outline'
      },
      embedding: {
        title: isVideo ? '🧠 Building video knowledge base...' : '🧠 Building knowledge base...',
        color: '#9C27B0',
        icon: 'layers-outline'
      },
      finalizing: {
        title: isVideo ? '🔄 Finalizing video processing...' : '🔄 Finalizing upload...',
        color: '#FF9800',
        icon: 'sync-outline'
      },
      complete: {
        title: isVideo ? '✅ Video processed successfully!' : '✅ Upload complete!',
        color: '#4CAF50',
        icon: 'checkmark-circle'
      },
      error: {
        title: isVideo ? '❌ Video upload failed' : '❌ Upload failed',
        color: '#F44336',
        icon: 'alert-circle'
      },
      redis_down: {
        title: '⚠️ Monitoring unavailable',
        color: '#FF9800',
        icon: 'warning-outline'
      }
    };
    return stageMap[stage] || stageMap.analyzing;
  };

  const stageInfo = getStageInfo(stage);

  if (!isVisible) {
    return null;
  }

  return (
    <Animated.View 
      style={[
        styles.container,
        style,
        {
          transform: [{
            translateX: slideAnimation.interpolate({
              inputRange: [0, 1],
              outputRange: [300, 0]
            })
          }],
          opacity: Animated.multiply(slideAnimation, fadeAnimation)
        }
      ]}
    >
      <View style={styles.bubble}>
        {/* Header */}
        <View style={styles.header}>
          <View style={styles.titleContainer}>
            <Ionicons 
              name={stageInfo.icon} 
              size={16} 
              color={stageInfo.color} 
            />
            <Text style={styles.title}>{stageInfo.title}</Text>
          </View>
          {(stage === 'complete' || stage === 'error' || stage === 'redis_down') && (
            <TouchableOpacity 
              onPress={handleManualDismiss}
              style={styles.dismissButton}
            >
              <Ionicons name="close" size={14} color="#666" />
            </TouchableOpacity>
          )}
        </View>

        {/* File info */}
        {filename && (
          <View style={styles.fileInfo}>
            <Ionicons name="document" size={12} color="#666" />
            <Text style={styles.filename} numberOfLines={1}>
              {filename}
            </Text>
          </View>
        )}

        {/* Queue Status - Show only when there's queue information */}
        {queueStatus && queueStatus.size > 0 && (
          <View style={styles.queueStatusContainer}>
            {(() => {
              const queuedCount = Array.from(queueStatus.values()).filter(p => p.stage === 'queued').length;
              const processingCount = Array.from(queueStatus.values()).filter(p => 
                p.stage === 'processing' || p.stage === 'analyzing' || p.stage === 'embedding' || p.stage === 'uploading'
              ).length;
              
              return (
                <View style={styles.queueBadges}>
                  {processingCount > 0 && (
                    <View style={[styles.queueBadge, styles.processingBadge]}>
                      <Text style={styles.queueBadgeText}>⚙️ {processingCount} processing</Text>
                    </View>
                  )}
                  {queuedCount > 0 && (
                    <View style={[styles.queueBadge, styles.queuedBadge]}>
                      <Text style={styles.queueBadgeText}>⏳ {queuedCount} in queue</Text>
                    </View>
                  )}
                </View>
              );
            })()}
          </View>
        )}

        {/* Progress bar (only show if not complete) */}
        {stage !== 'complete' && stage !== 'error' && stage !== 'redis_down' && (
          <View style={styles.progressContainer}>
            <View style={styles.progressBar}>
              <Animated.View 
                style={[
                  styles.progressFill, 
                  { 
                    width: progressAnimation.interpolate({
                      inputRange: [0, 1],
                      outputRange: ['0%', '100%']
                    }),
                    backgroundColor: stageInfo.color
                  }
                ]} 
              />
            </View>
            <Text style={styles.progressText}>
              {stage === 'queued' ? 'In Queue' : `${Math.round(progress)}%`}
            </Text>
          </View>
        )}

        {/* Success message */}
        {stage === 'complete' && (
          <View>
            <Text style={styles.successMessage}>
              {uploadType === 'video' 
                ? '✅ Video recording uploaded to Citra AI!' 
                : uploadType === 'audio'
                ? '✅ Audio recording uploaded to Citra AI!'
                : '✅ Document successfully added to your memory database'}
            </Text>
            
            {/* Transcription Preview */}
            {(uploadType === 'video' || uploadType === 'audio') && transcriptionPreview && (
              <View style={styles.transcriptionPreviewContainer}>
                <Text style={styles.transcriptionLabel}>
                  📝 Transcription Preview:
                </Text>
                <Text style={styles.transcriptionPreviewText} numberOfLines={4}>
                  {transcriptionPreview}
                </Text>
              </View>
            )}
            
            {/* Where to find full transcription */}
            {(uploadType === 'video' || uploadType === 'audio') && (
              <View style={styles.locationHint}>
                <Ionicons name="folder-outline" size={14} color="#2196F3" />
                <Text style={styles.locationHintText}>
                  View full transcription in Data Store → Meetings folder
                </Text>
              </View>
            )}
            
            <Text style={styles.countdownMessage}>
              Auto-removing in {autoRemoveCountdown}s
            </Text>
          </View>
        )}

        {/* Redis down message */}
        {stage === 'redis_down' && (
          <View>
            <Text style={styles.warningMessage}>
              Progress tracking temporarily unavailable. Your upload is processing in the background.
            </Text>
            <Text style={styles.countdownMessage}>
              Auto-removing in {autoRemoveCountdown}s
            </Text>
          </View>
        )}

        {/* Error message */}
        {stage === 'error' && (
          <View>
            <Text style={styles.errorMessage}>
              {error || 'Failed to upload document. Please try again.'}
            </Text>
            <Text style={styles.countdownMessage}>
              Auto-removing in {autoRemoveCountdown}s
            </Text>
          </View>
        )}
      </View>
    </Animated.View>
  );
};

const styles = StyleSheet.create({
  container: {
    alignSelf: 'flex-end',
    marginVertical: 4,
    marginHorizontal: 10,
    maxWidth: '80%',
  },
  bubble: {
    backgroundColor: '#f0f8ff',
    borderRadius: 16,
    padding: 12,
    borderWidth: 1,
    borderColor: '#e3f2fd',
    shadowColor: '#000',
    shadowOffset: {
      width: 0,
      height: 2,
    },
    shadowOpacity: 0.1,
    shadowRadius: 3.84,
    elevation: 5,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  titleContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
  },
  title: {
    fontSize: 14,
    fontWeight: '600',
    color: '#333',
    marginLeft: 6,
  },
  dismissButton: {
    padding: 4,
    borderRadius: 12,
    backgroundColor: '#f5f5f5',
  },
  fileInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 8,
  },
  filename: {
    fontSize: 12,
    color: '#666',
    marginLeft: 4,
    flex: 1,
  },
  progressContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 4,
  },
  progressBar: {
    flex: 1,
    height: 4,
    backgroundColor: '#e0e0e0',
    borderRadius: 2,
    overflow: 'hidden',
    marginRight: 8,
  },
  progressFill: {
    height: '100%',
    borderRadius: 2,
  },
  progressText: {
    fontSize: 11,
    color: '#666',
    fontWeight: '500',
    minWidth: 30,
    textAlign: 'right',
  },
  successMessage: {
    fontSize: 12,
    color: '#4CAF50',
    marginTop: 4,
    fontWeight: '600',
  },
  transcriptionPreviewContainer: {
    marginTop: 8,
    padding: 8,
    backgroundColor: '#f8f9fa',
    borderRadius: 8,
    borderLeftWidth: 3,
    borderLeftColor: '#2196F3',
  },
  transcriptionLabel: {
    fontSize: 11,
    fontWeight: '600',
    color: '#333',
    marginBottom: 4,
  },
  transcriptionPreviewText: {
    fontSize: 11,
    color: '#555',
    lineHeight: 16,
    fontStyle: 'italic',
  },
  locationHint: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 8,
    paddingVertical: 4,
    paddingHorizontal: 8,
    backgroundColor: '#E3F2FD',
    borderRadius: 6,
  },
  locationHintText: {
    fontSize: 11,
    color: '#1565C0',
    marginLeft: 4,
    fontWeight: '500',
  },
  warningMessage: {
    fontSize: 12,
    color: '#FF9800',
    marginTop: 4,
    fontStyle: 'italic',
  },
  countdownMessage: {
    fontSize: 10,
    color: '#999',
    marginTop: 2,
    textAlign: 'center',
    fontStyle: 'italic',
  },
  errorMessage: {
    fontSize: 12,
    color: '#F44336',
    marginTop: 4,
    fontStyle: 'italic',
  },
  queueStatusContainer: {
    marginTop: 6,
    paddingTop: 6,
    borderTopWidth: 1,
    borderTopColor: '#E5E5EA',
  },
  queueBadges: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 4,
  },
  queueBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 8,
    borderWidth: 1,
  },
  processingBadge: {
    backgroundColor: '#D1ECF1',
    borderColor: '#74C0FC',
  },
  queuedBadge: {
    backgroundColor: '#FFF3CD',
    borderColor: '#FFEAA7',
  },
  queueBadgeText: {
    fontSize: 9,
    fontWeight: '600',
    color: '#495057',
  },
});

export default ChatUploadBubble;
