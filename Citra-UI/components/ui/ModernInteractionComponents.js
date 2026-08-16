import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  Animated,
  Platform,
  ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { modernStyles, SPACING } from '../../styles/modernStyles';
import { ModernToggleSwitch } from './ModernComponents';
import { PERSONAL_VAULT_ENABLED } from '../../config/featureFlags';

// Re-export RibbonMenu for convenience
export { RibbonMenu } from './RibbonMenu';

// ========== WEB VERSION - QUERY ENHANCEMENT TOGGLES ==========
// This component is used for the WEB version in App.js around line 18040
// For Mobile/Native version, see inline toggle buttons in App.js around line 1823
// Features: How to use, General Query, Vault, Reader, Link Enterprise Data
export const ModernQueryToggles = ({
  theme,
  personaText,
  // Deep Research props removed - now handled automatically by LLM orchestrator
  // deepResearch = false,
  // onDeepResearchChange,
  useUploadedData = true,
  onUseUploadedDataChange,
  // New: Enterprise toggle
  useEnterprise = false,
  onUseEnterpriseChange,
  showDeepResearch = false, // Deep Research now hidden by default - auto-decided by LLM
  isModelOnlyMode = false,
  onModelOnlyModeChange,
  disabled = false,
  // Draft controls - NEW
  // Reader controls
  onShowReader,
  // Knowledge Graph controls - NEW
  onShowKnowledgeGraph,
  // How to use callback
  onShowHowToUse,
}) => {
  const slideAnim = useRef(new Animated.Value(0)).current;
  const isWeb = Platform.OS === 'web';

  // Ribbon already exposes these controls on web; hide this bar to avoid duplication.
  if (isWeb) return null;

  useEffect(() => {
    Animated.spring(slideAnim, {
      toValue: 1,
      useNativeDriver: Platform.OS !== 'web',
      tension: 120,
      friction: 8,
    }).start();
  }, [slideAnim]);

  // Enhanced handlers that automatically turn off AI Model mode when needed
  const createEnhancedHandler = (originalHandler, featureValue) => {
    return (newValue) => {
      // If trying to enable this feature while AI Model mode is active, turn it off first
      if (newValue && isModelOnlyMode) {
        console.log('🔄 Automatically disabling General Query (AI Model) mode to enable other feature');
        onModelOnlyModeChange(false);
      }
      // Then call the original handler
      originalHandler(newValue);
    };
  };

  const toggles = [
    // How to use Citra AI - Info button (kept for native layouts)
    ...(!isWeb && onShowHowToUse ? [{
      id: 'howToUse',
      label: 'How to use Citra AI',
      icon: 'help-circle-outline',
      value: false,
      onChange: () => {
        if (onShowHowToUse) {
          onShowHowToUse();
        }
      },
      color: theme.primary, // Use theme primary color like other buttons
      description: 'Learn how to use Citra AI effectively',
    }] : []),
    // Knowledge Graph button - NEW
    ...(isWeb ? [{
      id: 'knowledgeGraph',
      label: 'KG',
      icon: 'git-network-outline',
      value: false,
      onChange: () => {
        if (onShowKnowledgeGraph) {
          onShowKnowledgeGraph();
        }
      },
      color: '#3B82F6', // Blue highlight color
      description: 'View knowledge graph for legal documents',
      disabled: false, // Always enabled - supports both folder and all documents
    }] : []),
    // AI Model toggle - HIDDEN (handled server-side in query.py)
    // Server automatically enables General Query when neither Vault nor Enterprise is active
    // Deep Research toggle removed - now automatically decided by LLM orchestrator
    // ...(showDeepResearch ? [{
    //   id: 'deepResearch',
    //   label: 'Deep Research',
    //   icon: deepResearch ? 'flask' : 'flask-outline',
    //   value: deepResearch,
    //   onChange: createEnhancedHandler(onDeepResearchChange, deepResearch),
    //   color: theme.accent,
    //   description: 'Enhanced analysis with comprehensive insights',
    // }] : []),
    // Personal Vault source toggle + the Reader that browses those same
    // personal documents. Both are dropped while PERSONAL_VAULT_ENABLED is
    // false — chat searches dept MCP sources and the enterprise SOP library
    // only, on mobile web exactly as on desktop.
    ...(PERSONAL_VAULT_ENABLED ? [
      {
        id: 'uploadedData',
        label: personaText?.menuPersonalDrive || 'Vault',
        icon: useUploadedData ? 'cloud-done' : 'cloud-outline',
        value: useUploadedData,
        onChange: createEnhancedHandler(onUseUploadedDataChange, useUploadedData),
        color: theme.primary,
        description: 'Blend your uploaded documents and memories with AI reasoning',
      },
      // Reader button - positioned next to Vault
      {
        id: 'reader',
        label: 'Reader',
        icon: 'book-outline',
        value: false,
        onChange: onShowReader || (() => { }),
        color: '#10B981', // Green highlight color
        description: 'Browse and read documents from selected folder or all documents',
      },
    ] : []),
    // Enterprise sources are ALWAYS ON — there is nothing to toggle. The row
    // was native-only anyway (web uses the header control), and it is now gone
    // on every platform to match the "Always On" treatment in the Connection
    // Scope modal and the server-side override in query.py.
  ];

  return (
    <Animated.View
      style={[
        modernStyles.modernToggleContainer,
        {
          opacity: slideAnim,
          marginBottom: 6, // Keep toggles close to the input without overlapping
          transform: [
            {
              translateY: slideAnim.interpolate({
                inputRange: [0, 1],
                outputRange: [-20, 0],
              }),
            },
          ],
        },
      ]}
    >
      {toggles.map((toggle, index) => (
        <Animated.View
          key={toggle.id}
          style={{
            opacity: slideAnim,
            transform: [
              {
                translateY: slideAnim.interpolate({
                  inputRange: [0, 1],
                  outputRange: [20 * (index + 1), 0],
                }),
              },
            ],
          }}
        >
          <TouchableOpacity
            style={[
              modernStyles.modernToggleButton,
              {
                backgroundColor: toggle.value ? toggle.color : 'transparent',
                borderColor: (toggle.id === 'mindmap' || toggle.id === 'diagram') ? '#000000' : (toggle.value ? toggle.color : theme.border),
                opacity: (disabled || toggle.disabled) ? 0.5 : 1,
              }
            ]}
            onPress={() => {
              if (disabled || toggle.disabled) {
                return;
              }
              if (toggle.id === 'howToUse') {
                // How to use button - just trigger the action
                toggle.onChange();
              } else if (typeof toggle.onPress === 'function') {
                toggle.onPress();
              } else if (typeof toggle.onChange === 'function') {
                toggle.onChange(!toggle.value);
              }
            }}
            disabled={disabled || toggle.disabled}
            activeOpacity={0.8}
            accessibilityRole={toggle.id === 'howToUse' ? "button" : "switch"}
            accessibilityState={toggle.id === 'howToUse' ? {} : { checked: toggle.value }}
            accessibilityLabel={toggle.id === 'howToUse' ? toggle.label : `Toggle ${toggle.label}`}
            accessibilityHint={toggle.description}
          >
            {toggle.isLoading ? (
              <ActivityIndicator size={16} color={theme.textOnPrimary} />
            ) : (
              <Ionicons
                name={toggle.icon}
                size={16}
                color={toggle.value ? theme.textOnPrimary : theme.text}
              />
            )}
            <Text
              style={[
                modernStyles.modernToggleText,
                {
                  color: toggle.value ? theme.textOnPrimary : theme.text,
                  fontWeight: toggle.value ? '600' : '500',
                }
              ]}
            >
              {toggle.label}
            </Text>
          </TouchableOpacity>
        </Animated.View>
      ))}
    </Animated.View>
  );
};

export const ModernAttachmentPreview = ({
  attachments = [],
  onRemoveAttachment,
  theme,
  style,
}) => {
  if (!attachments || attachments.length === 0) return null;

  return (
    <View
      style={[
        modernStyles.modernAttachmentContainer,
        {
          backgroundColor: theme.surface,
          borderColor: theme.border,
        },
        style,
      ]}
    >
      <Text
        style={[
          { fontSize: 12, fontWeight: '600', marginBottom: SPACING.sm },
          { color: theme.textSecondary }
        ]}
      >
        Attachments ({attachments.length})
      </Text>

      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        {attachments.map((attachment, index) => {
          const getIconName = (type) => {
            switch (type) {
              case 'image': return 'image';
              case 'audio': return 'musical-note';
              case 'document': return 'document-text';
              default: return 'attach';
            }
          };

          return (
            <View
              key={attachment.id || index}
              style={[
                modernStyles.modernAttachmentItem,
                {
                  backgroundColor: theme.surfaceVariant,
                  borderColor: theme.primary,
                }
              ]}
            >
              <Ionicons
                name={getIconName(attachment.type)}
                size={20}
                color={theme.primary}
              />

              {onRemoveAttachment && (
                <TouchableOpacity
                  style={[
                    modernStyles.modernAttachmentRemove,
                    {
                      backgroundColor: theme.error,
                      borderColor: theme.background,
                    }
                  ]}
                  onPress={() => onRemoveAttachment(attachment.id)}
                  accessibilityLabel={`Remove ${attachment.type} attachment`}
                >
                  <Ionicons name="close" size={12} color={theme.background} />
                </TouchableOpacity>
              )}
            </View>
          );
        })}
      </ScrollView>
    </View>
  );
};

export const ModernProgressIndicator = ({
  visible = false,
  progress = 0,
  status = 'Processing...',
  theme,
  style,
}) => {
  const progressAnim = useRef(new Animated.Value(0)).current;
  const fadeAnim = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (visible) {
      Animated.timing(fadeAnim, {
        toValue: 1,
        duration: 300,
        useNativeDriver: Platform.OS !== 'web',
      }).start();
    } else {
      Animated.timing(fadeAnim, {
        toValue: 0,
        duration: 300,
        useNativeDriver: Platform.OS !== 'web',
      }).start();
    }
  }, [visible, fadeAnim]);

  useEffect(() => {
    Animated.timing(progressAnim, {
      toValue: progress,
      duration: 500,
      useNativeDriver: false,
    }).start();
  }, [progress, progressAnim]);

  if (!visible) return null;

  const progressWidth = progressAnim.interpolate({
    inputRange: [0, 1],
    outputRange: ['0%', '100%'],
  });

  return (
    <Animated.View
      style={[
        {
          backgroundColor: theme.surface,
          borderColor: theme.border,
          borderWidth: 1,
          borderRadius: 12,
          padding: SPACING.md,
          marginHorizontal: SPACING.md,
          marginVertical: SPACING.sm,
          opacity: fadeAnim,
        },
        style,
      ]}
    >
      <View style={{
        flexDirection: 'row',
        alignItems: 'center',
        marginBottom: SPACING.sm,
      }}>
        <Ionicons
          name="hourglass-outline"
          size={16}
          color={theme.primary}
          style={{ marginRight: SPACING.sm }}
        />
        <Text style={{
          fontSize: 14,
          fontWeight: '500',
          color: theme.text,
          flex: 1,
        }}>
          {status}
        </Text>
        <Text style={{
          fontSize: 12,
          color: theme.textSecondary,
        }}>
          {Math.round(progress * 100)}%
        </Text>
      </View>

      <View style={{
        height: 4,
        backgroundColor: theme.borderLight,
        borderRadius: 2,
        overflow: 'hidden',
      }}>
        <Animated.View
          style={{
            height: '100%',
            backgroundColor: theme.primary,
            width: progressWidth,
            borderRadius: 2,
          }}
        />
      </View>
    </Animated.View>
  );
};

export const ModernActionSheet = ({
  visible = false,
  onClose,
  options = [],
  title,
  theme,
}) => {
  const slideAnim = useRef(new Animated.Value(300)).current;
  const overlayOpacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (visible) {
      Animated.parallel([
        Animated.timing(slideAnim, {
          toValue: 0,
          duration: 300,
          useNativeDriver: Platform.OS !== 'web',
        }),
        Animated.timing(overlayOpacity, {
          toValue: 1,
          duration: 300,
          useNativeDriver: Platform.OS !== 'web',
        }),
      ]).start();
    } else {
      Animated.parallel([
        Animated.timing(slideAnim, {
          toValue: 300,
          duration: 250,
          useNativeDriver: Platform.OS !== 'web',
        }),
        Animated.timing(overlayOpacity, {
          toValue: 0,
          duration: 250,
          useNativeDriver: Platform.OS !== 'web',
        }),
      ]).start();
    }
  }, [visible, slideAnim, overlayOpacity]);

  if (!visible) return null;

  return (
    <>
      {/* Overlay */}
      <Animated.View
        style={[
          modernStyles.modernSideMenuOverlay,
          { opacity: overlayOpacity }
        ]}
      >
        <TouchableOpacity
          style={{ flex: 1 }}
          onPress={onClose}
          activeOpacity={1}
        />
      </Animated.View>

      {/* Action Sheet */}
      <Animated.View
        style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          right: 0,
          backgroundColor: theme.card,
          borderTopLeftRadius: 20,
          borderTopRightRadius: 20,
          paddingBottom: Platform.OS === 'ios' ? 34 : 20,
          transform: [{ translateY: slideAnim }],
          shadowColor: '#000',
          shadowOffset: { width: 0, height: -2 },
          shadowOpacity: 0.1,
          shadowRadius: 8,
          elevation: 8,
        }}
      >
        {/* Handle */}
        <View style={{
          width: 40,
          height: 4,
          backgroundColor: theme.borderLight,
          borderRadius: 2,
          alignSelf: 'center',
          marginTop: 8,
          marginBottom: 16,
        }} />

        {/* Title */}
        {title && (
          <Text style={{
            fontSize: 18,
            fontWeight: '600',
            color: theme.text,
            textAlign: 'center',
            marginBottom: 20,
            paddingHorizontal: 20,
          }}>
            {title}
          </Text>
        )}

        {/* Options */}
        {options.map((option, index) => (
          <TouchableOpacity
            key={option.id || index}
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              paddingVertical: 16,
              paddingHorizontal: 20,
              borderBottomWidth: index < options.length - 1 ? 1 : 0,
              borderBottomColor: theme.borderLight,
            }}
            onPress={() => {
              option.onPress && option.onPress();
              onClose();
            }}
            activeOpacity={0.7}
          >
            {option.icon && (
              <Ionicons
                name={option.icon}
                size={24}
                color={option.destructive ? theme.error : theme.text}
                style={{ marginRight: 16 }}
              />
            )}
            <Text style={{
              fontSize: 16,
              color: option.destructive ? theme.error : theme.text,
              fontWeight: option.destructive ? '600' : '400',
            }}>
              {option.title}
            </Text>
          </TouchableOpacity>
        ))}
      </Animated.View>
    </>
  );
};

// Helper function to generate "How to use Citra AI" instructions
export const getHowToUseMessage = () => {
  return `**How to Use Citra AI** 📚

**Quick Start:**
By default, **Project Vault** is enabled to give you answers using your uploaded data.

If you want to skip your data to answer then deselect it.

**Working with Project Vaults (Primary):**
Enable **Project Vault** to query your uploaded documents and organize your projects.

**File Organization:**
• Files without a selected vault go to **General**
• Meeting recordings go to **Meetings** under a selected Vault 
• Notes you create go to **Notes** under a selected vault

**Best Practice for Projects:**
1. Create a **new vault** for each project or initiative
2. Select that vault before uploading
3. All files will be organized in that vault
4. Vault search auto-enables when you select a vault

**Vault Search Behavior:**
• **Project Vault enabled + Vault selected** = Search that specific vault
• **Project Vault enabled + No vault selected** = Search across ALL vaults

**Mindmap Feature:**
Creates a visual map with auto-generated questions to help you understand your project files and do quick research.

**How to Use Mindmap:**
1. Select a vault from your vaults
2. You'll see the Mindmap button is now enabled
3. Click on **Mindmap** button
4. Wait while the system analyzes your data
5. A mindmap will appear with entities and relationships from your documents

**Working with Enterprise (Optional):**
Enterprise helps you organize team, department, or organizational data in a structured way. **Not required** - use only if you need shared data organization!

**Setting Up Enterprise:**
1. Go to **Menu → Enterprise** screen
2. Create an entity (client/department/team)
3. Upload files to specific entities or general enterprise
4. Manage all shared data in one organized location

**Using Enterprise Data:**
• Enable **Link Enterprise Data** button to search enterprise
• Select a specific entity in the top search bar for targeted search
• **No entity selected?** Search across entire enterprise data
• Link enterprise data to your vaults for combined research

**Enterprise Search Behavior:**
• **Enterprise enabled + Entity selected** = Search that specific entity
• **Enterprise enabled + No entity selected** = Search across ALL enterprise data

**Combined Search Options:**
• **Both Vault + Enterprise enabled** = Search both together
• **Only Vault enabled** = Search only your vaults
• **Only Enterprise enabled** = Search only enterprise data
• **Mix & match** = Any combination you need

**How It Works:**
1. Upload data to your vault or enterprise
2. Select the vault/enterprise you want to query
3. Run your AI-powered queries

**Data Management:**
• Data is stored permanently until you delete it
• You can replace files with new versions
• Open any vault to view and manage all files

**Supported Formats:**
We support all data types including:
• Handwritten notes (take a photo or scan)
• Images, PDFs, documents
• Use OCR for scanned PDFs

**Verify OCR Quality:**
Check OCR results in:
• Folder view
• Uploaded files section  
• Menu history
This ensures scans are accurate for AI queries.

Ready to start? Try uploading a document and selecting a vault! 🚀`;
};

export default {
  ModernQueryToggles,
  ModernAttachmentPreview,
  ModernProgressIndicator,
  ModernActionSheet,
  getHowToUseMessage,
};
