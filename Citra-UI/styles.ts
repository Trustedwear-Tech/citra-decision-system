import { StyleSheet, Platform, Dimensions, StatusBar } from 'react-native';

const { width } = Dimensions.get('window');
const APPBAR_HEIGHT = Platform.OS === 'ios' ? 44 : 56;
const STATUS_BAR_HEIGHT = Platform.OS === 'android' ? StatusBar.currentHeight || 24 : 0;

// Web-specific constants
const WEB_SIDEBAR_WIDTH = Platform.OS === 'web' ? 300 : 0;

export const styles = StyleSheet.create({
  container: {
    flex: 1,
    // Remove paddingTop since SafeAreaView handles it
  },
  safeArea: {
    flex: 1,
  },
  keyboardAvoidingView: {
    flex: 1,
    ...Platform.select({
      android: {
        // Android-specific keyboard behavior
        backgroundColor: 'transparent',
      },
      ios: {
        // iOS-specific keyboard behavior  
        backgroundColor: 'transparent',
      },
    }),
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    height: 55,
    paddingHorizontal: 15,
    borderBottomWidth: 1,
  },
  headerContainer: {
    height: APPBAR_HEIGHT,
    justifyContent: 'center',
  },
  headerTitleContainer: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    marginHorizontal: 10,
  },
  headerButton: {
    padding: 8,
  },
  headerText: {
    fontSize: 18,
    fontWeight: 'bold',
  },
  themeToggle: {
    width: 56,
    height: 28,
    borderRadius: 14,
    backgroundColor: '#E0E0E0',
    justifyContent: 'center',
    padding: 2,
  },
  toggleButton: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: '#FFFFFF',
    justifyContent: 'center',
    alignItems: 'center',
    ...Platform.select({
      ios: {
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.25,
        shadowRadius: 3.84,
      },
      android: {
        elevation: 5,
      },
      web: {
        boxShadow: '0 2px 4px rgba(0, 0, 0, 0.25)',
      },
    }),
  },
  logoContainer: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 100,
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 1,
    pointerEvents: 'none',
  },
  logo: {
    width: 120,
    height: 120,
    opacity: 1,
  },
  chatContainer: {
    flex: 1,
  },
  chatContentContainer: {
    padding: 10,
    paddingBottom: 15,
  },
  messageWrapper: {
    flexDirection: 'row',
    marginBottom: 12,
    alignItems: 'flex-start',
  },
  userMessageWrapper: {
    justifyContent: 'flex-end',
  },
  botLogoContainer: {
    width: 32,
    height: 32,
    marginRight: 10,
    alignSelf: 'flex-start',
    marginTop: 10,
    justifyContent: 'center',
    alignItems: 'center',
  },
  botLogo: {
    width: '100%',
    height: '100%',
    borderRadius: 15,
  },
  messageBubble: {
    maxWidth: '85%',
    padding: 14,
    borderRadius: 20,
    marginHorizontal: 6,
    marginVertical: 3,
    ...Platform.select({
      ios: {
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 1 },
        shadowOpacity: 0.15,
        shadowRadius: 6,
      },
      android: {
        elevation: 2,
      },
      web: {
        boxShadow: '0 1px 6px rgba(0, 0, 0, 0.15)',
      },
    }),
  },
  userMessage: {
    alignSelf: 'flex-end',
    borderBottomRightRadius: 6,
    marginLeft: 20,
    marginRight: 8,
  },
  botMessage: {
    alignSelf: 'flex-start',
    borderBottomLeftRadius: 6,
    marginRight: 20,
    marginLeft: 8,
  },
  messageText: {
    fontSize: 15,
    lineHeight: 20,
    letterSpacing: 0.2,
  },
  messageImage: {
    width: 200,
    height: 200,
    borderRadius: 10,
    marginTop: 10,
  },
  streamingIndicator: {
    marginTop: 8,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
    backgroundColor: 'rgba(0, 122, 255, 0.1)',
  },
  streamingText: {
    fontSize: 12,
    fontWeight: '500',
    letterSpacing: 0.3,
  },
  documentText: {
    fontSize: 14,
    marginTop: 5,
  },
  inputContainer: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 20, // Shift up by 20 pixels
    // bottom will be controlled by Animated.View inline
    flexDirection: 'column', // Changed from 'row' to 'column' to stack toggles above input
    paddingHorizontal: 12,
    paddingTop: 10,
    // paddingBottom gets dynamic safe-area injected inline
    minHeight: 60,
    backgroundColor: 'rgba(0,0,0,0.4)', // adjust or theme color; keep semi-transparent if desired
  },
  plusButton: {
    padding: 10,
    marginRight: 10,
  },
  input: {
    flex: 1,
    fontSize: 16, // Must be 16 to prevent iOS Safari auto-zoom on focus
    maxHeight: 100,
    minHeight: 40,
    paddingVertical: 8,
    paddingHorizontal: 10,
    textAlignVertical: 'center',
  },
  micButton: {
    padding: 10,
    marginLeft: 10,
  },
  sendButton: {
    marginLeft: 10,
    width: 24,
    height: 24,
    justifyContent: 'center',
    alignItems: 'center',
  },
  optionsContainer: {
    flexDirection: 'row',
    position: 'absolute',
    left: 10,
    bottom: 60,
    borderRadius: 20,
    padding: 10,
    justifyContent: 'space-around',
    width: 250,
    ...Platform.select({
      ios: {
        shadowColor: '#000',
        shadowOffset: { width: 2, height: 0 },
        shadowOpacity: 0.3,
        shadowRadius: 10,
      },
      android: {
        elevation: 10,
      },
      web: {
        boxShadow: '2px 0 10px rgba(0, 0, 0, 0.3)',
      },
    }),
  },
  option: {
    marginHorizontal: 10,
  },
  historyContainer: {
    flex: 1,
    ...Platform.select({
      web: {
        overflowY: 'auto',
      },
    }),
  },
  historyHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 15,
  },
  historyHeaderText: {
    fontSize: 18,
    fontWeight: 'bold',
  },
  clearAllButton: {
    padding: 5,
  },
  clearAllButtonText: {
    fontSize: 16,
  },
  historyList: {
    paddingHorizontal: 15,
  },
  historyItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 15,
  },
  historyLogoContainer: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: '#E0E0E0',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 10,
  },
  historyItemText: {
    flex: 1,
    fontSize: 16,
    fontWeight: 'bold',
  },
  historyItemSummary: {
    fontSize: 14,
    marginTop: 4,
    lineHeight: 18,
  },
  historyItemTimestamp: {
    fontSize: 12,
    marginTop: 4,
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
  },
  loadingText: {
    marginTop: 10,
    fontSize: 16,
    textAlign: 'center',
  },
  emptyContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 40,
  },
  emptyText: {
    fontSize: 16,
    textAlign: 'center',
    lineHeight: 24,
  },
  menuOverlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    zIndex: 1000,
  },
  menuCurtain: {
    position: 'absolute',
    top: 0,
    left: 0,
    bottom: 0,
    width: width * 0.8,
    borderTopRightRadius: 20,
    borderBottomRightRadius: 20,
    paddingTop: 0,
    zIndex: 1001,
    ...Platform.select({
      ios: {
        shadowColor: '#000',
        shadowOffset: { width: 2, height: 0 },
        shadowOpacity: 0.3,
        shadowRadius: 10,
      },
      android: {
        elevation: 10,
      },
      web: {
        boxShadow: '2px 0 10px rgba(0, 0, 0, 0.3)',
      },
    }),
  },
  menuHeader: {
    paddingHorizontal: 20,
    paddingVertical: 15,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255, 255, 255, 0.1)',
  },
  menuHeaderText: {
    fontSize: 24,
    fontWeight: 'bold',
  },
  menuContent: {
    flex: 1,
    paddingTop: 20,
    paddingHorizontal: 20,
  },
  menuItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 15,
    paddingHorizontal: 10,
    borderRadius: 10,
    marginBottom: 10,
  },
  activeMenuItem: {
    backgroundColor: 'rgba(255, 255, 255, 0.1)',
  },
  menuItemText: {
    fontSize: 18,
    marginLeft: 15,
    fontWeight: '500',
  },
  userDetailModal: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
  },
  userDetailContent: {
    width: '90%',
    maxHeight: '80%',
    borderRadius: 15,
    padding: 25,
  },
  userDetailHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 20,
    paddingBottom: 15,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(0, 0, 0, 0.1)',
  },
  userDetailTitle: {
    fontSize: 22,
    fontWeight: 'bold',
  },
  userDetailInput: {
    minHeight: 120,
    maxHeight: 300,
    borderRadius: 10,
    padding: 15,
    textAlignVertical: 'top',
    borderWidth: 1,
    fontSize: 16,
    lineHeight: 22,
  },
  userDetailButtons: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    marginTop: 20,
    gap: 15,
  },
  userDetailButton: {
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 8,
    minWidth: 100,
    alignItems: 'center',
  },
  userDetailButtonText: {
    fontSize: 16,
    fontWeight: '600',
  },
  userDetailViewText: {
    fontSize: 16,
    lineHeight: 24,
    marginVertical: 10,
  },
  inputWrapper: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 20,
    paddingHorizontal: 15,
    paddingVertical: 5,
    minHeight: 50,
    maxHeight: 120, // Limit max height to prevent excessive growth
    ...Platform.select({
      android: {
        // Android-specific styling
        borderRadius: 25,
        paddingHorizontal: 12,
        paddingVertical: 8,
      },
      ios: {
        // iOS-specific styling  
        borderRadius: 20,
        paddingHorizontal: 15,
        paddingVertical: 5,
      },
    }),
  },
  modalContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  modalContent: {
    width: '80%',
    // Height is managed by child content; keep responsive defaults
    borderRadius: 10,
    padding: 20,
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 15,
  },
  modalHeaderActions: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  modalHeaderIconButton: {
    padding: 8,
    borderRadius: 18,
    marginLeft: 8,
  },
  modalTitle: {
    fontSize: 18,
    fontWeight: 'bold',
  },
  noteInput: {
    height: 200,
    borderRadius: 5,
    padding: 10,
    textAlignVertical: 'top',
  },
  modalFooter: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    marginTop: 20,
  },
  modalButton: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 5,
    marginLeft: 10,
  },
  saveButton: {
    backgroundColor: '#007AFF',
  },
  modalButtonText: {
    fontSize: 16,
    fontWeight: 'bold',
  },
  addNoteButton: {
    position: 'absolute',
    right: 10,
    top: 550,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#075E54',
    paddingHorizontal: 15,
    paddingVertical: 10,
    borderRadius: 25,
  },
  addNoteButtonText: {
    color: '#FFFFFF',
    marginLeft: 5,
    fontWeight: 'bold',
  },
  noteButton: {
    padding: 10,
    marginLeft: 10,
  },
  notesHeaderText: {
    fontSize: 18,
    fontWeight: 'bold',
    textAlign: 'center',
  },
  notesContainer: {
    flex: 1,
    width: '100%',
    ...Platform.select({
      web: {
        overflowY: 'auto',
      },
    }),
  },
  webScrollContainer: {
    flex: 1,
    width: '100%',
    ...Platform.select({
      web: {
        overflowY: 'auto',
      },
    }),
  },
  notesList: {
    paddingHorizontal: 16,
    paddingTop: 16,
    paddingBottom: 100,
  },
  notesHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 15,
  },
  noteItem: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    paddingVertical: 12,
    paddingHorizontal: 5,
    borderRadius: 8,
    marginVertical: 4,
  },
  noteText: {
    fontSize: 16,
    lineHeight: 20,
    flex: 1,
  },
  notePreview: {
    fontSize: 14,
    marginTop: 4,
    lineHeight: 18,
  },
  noteTimestamp: {
    fontSize: 12,
    marginTop: 5,
  },
  deleteNoteButton: {
    padding: 8,
  },
  noteViewScrollContainer: {
    maxHeight: 300,
    marginVertical: 10,
  },
  noteViewText: {
    fontSize: 16,
    lineHeight: 24,
  },
  noteViewTitle: {
    fontSize: 18,
    fontWeight: '600',
    marginTop: 8,
    marginBottom: 6,
  },
  noteViewTimestamp: {
    fontSize: 12,
    textAlign: 'center',
    marginTop: 10,
  },
  clearHistoryButton: {
    padding: 5,
    marginLeft: 10,
  },
  separator: {
    height: 1,
    marginLeft: 50,
  },
  // Enhanced TranscriptItem styles to match EnhancedDocumentItem
  enhancedContainer: {
    padding: 16,
    marginVertical: 4,
    marginHorizontal: 8,
    borderRadius: 12,
    borderWidth: 1,
    elevation: 2,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.1,
    shadowRadius: 2,
  },
  enhancedHeader: {
    marginBottom: 8,
  },
  titleContainer: {
    flex: 1,
  },
  enhancedTitle: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 4,
  },
  indicatorsContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 4,
    flexWrap: 'wrap',
    gap: 8,
  },
  typeIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  entityIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  entityText: {
    fontSize: 12,
    marginLeft: 4,
    fontWeight: '600',
  },
  typeText: {
    fontSize: 12,
    marginLeft: 4,
    fontWeight: '600',
  },
  storageIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  storageText: {
    fontSize: 12,
    marginLeft: 4,
    fontWeight: '500',
  },
  contentPreview: {
    fontSize: 14,
    marginBottom: 8,
    lineHeight: 20,
  },
  enhancedMetadata: {
    marginBottom: 12,
  },
  metadataText: {
    fontSize: 12,
    marginBottom: 2,
  },
  enhancedActions: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    paddingTop: 8,
    borderTopWidth: 1,
    borderTopColor: '#E5E5EA',
  },
  actionButton: {
    alignItems: 'center',
    padding: 8,
    borderRadius: 8,
    minWidth: 60,
  },
  actionText: {
    fontSize: 12,
    marginTop: 4,
    fontWeight: '500',
  },
  stopGeneratingButton: {
    position: 'absolute',
    right: 15,
    bottom: 100,
    width: 40,
    height: 40,
    borderRadius: 24,
    backgroundColor: '#FF6B6B',
    justifyContent: 'center',
    alignItems: 'center',
    ...Platform.select({
      ios: {
        shadowColor: '#FF6B6B',
        shadowOffset: { width: 0, height: 4 },
        shadowOpacity: 0.4,
        shadowRadius: 8,
      },
      android: {
        elevation: 8,
      },
      web: {
        boxShadow: '0 4px 8px rgba(255, 107, 107, 0.4)',
      },
    }),
  },
  messageActionsContainer: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    marginTop: 8,
    paddingHorizontal: 5,
  },
  messageActionButton: {
    padding: 6,
    marginLeft: 8,
    borderRadius: 15,
    backgroundColor: 'rgba(0, 0, 0, 0.1)',
  },
  editMessageModal: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
  },
  editMessageContent: {
    width: '90%',
    maxHeight: '80%',
    borderRadius: 10,
    padding: 20,
  },
  editMessageInput: {
    minHeight: 100,
    maxHeight: 200,
    borderRadius: 8,
    padding: 12,
    marginVertical: 15,
    textAlignVertical: 'top',
  },
  editMessageButtons: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: 10,
  },
  editMessageButton: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 6,
    minWidth: 80,
    alignItems: 'center',
  },
  editMessageButtonText: {
    fontSize: 16,
    fontWeight: '600',
  },
  typingContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 8,
  },
  typingText: {
    fontSize: 16,
    marginRight: 8,
  },
  dotsContainer: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    marginHorizontal: 2,
  },
  // Essential transcript styles only - minimal additions
  transcriptPreview: {
    fontSize: 14,
    marginTop: 4,
    lineHeight: 18,
  },

  transcriptMeta: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 8,
  },

  transcriptDuration: {
    fontSize: 12,
    fontWeight: '500',
  },

  transcriptViewTopic: {
    fontSize: 18,
    fontWeight: '600',
    marginBottom: 12,
    textAlign: 'center',
  },

  transcriptViewMeta: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
    paddingHorizontal: 4,
  },

  // Modal label style for transcript editing
  modalLabel: {
    fontSize: 16,
    fontWeight: '500',
    marginBottom: 8,
  },

  // Document-specific styles
  documentPreview: {
    fontSize: 14,
    marginTop: 4,
    lineHeight: 18,
  },

  documentMeta: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 8,
  },

  documentFileType: {
    fontSize: 12,
    fontWeight: '500',
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 10,
    textTransform: 'uppercase',
  },

  documentViewTitle: {
    fontSize: 18,
    fontWeight: '600',
    marginBottom: 12,
    textAlign: 'center',
  },

  documentViewMeta: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
    paddingHorizontal: 4,
  },

  documentViewFilename: {
    fontSize: 12,
    fontStyle: 'italic',
    textAlign: 'center',
    marginBottom: 8,
  },
  modalOverlay: {
    flex: 1, justifyContent: 'center', alignItems: 'center',
    backgroundColor: 'rgba(0,0,0,0.5)'
  },

  modalButtons: {
    flexDirection: 'row',
    justifyContent: 'flex-end'
  },

  // Action sheet styles for Android safe area handling
  actionSheetContainer: {
    paddingBottom: Platform.OS === 'android' ? 20 : 0,
  },

  actionSheetTitle: {
    fontSize: 16,
    fontWeight: '600',
    textAlign: 'center',
    paddingVertical: 12,
  },

  actionSheetButton: {
    fontSize: 18,
    textAlign: 'center',
    paddingVertical: 16,
  },

  // iOS-specific safe area styles
  iosContainer: {
    ...Platform.select({
      ios: {
        paddingTop: 0, // Let SafeAreaView handle this
      },
    }),
  },

  // iOS action sheet styles
  iosActionSheetContainer: {
    ...Platform.select({
      ios: {
        paddingBottom: 20,
      },
    }),
  },

  // Header adjustments for iOS
  iosHeader: {
    ...Platform.select({
      ios: {
        backgroundColor: 'transparent',
      },
    }),
  },

  // iPad specific styles
  ipadContainer: {
    maxWidth: 800,
    alignSelf: 'center',
    width: '100%',
  },

  // iOS safe area adjustments
  iosSafeAreaTop: {
    paddingTop: Platform.OS === 'ios' ? 0 : 10,
  },

  // Enhanced input wrapper for iOS
  iosInputWrapper: {},

  // Android-specific input wrapper for better keyboard handling
  androidInputWrapper: {},

  // Android container adjustments
  androidContainer: {
    ...Platform.select({
      android: {
        paddingBottom: 25,
      },
    }),
  },

  // Android keyboard spacer for extra clearance
  androidKeyboardSpacer: {
    ...Platform.select({
      android: {
        height: 20,
        backgroundColor: 'transparent',
      },
      ios: {
        height: 0,
      },
    }),
  },

  // Web-specific container styles
  webContainer: {
    ...Platform.select({
      web: {
        flexDirection: 'row' as const,
        flex: 1,
        backgroundColor: '#1e1e1e', // VS Code dark background
        height: '100%' as any, // Use 100% to scale properly with browser zoom
        width: '100%' as any, // Use 100% to scale properly with browser zoom
        overflow: 'hidden' as any, // Prevent scrollbars on main container
      },
      default: {
        flex: 1,
      },
    }),
  },

  webSidebar: {
    ...Platform.select({
      web: {
        width: '280px' as any, // Responsive sidebar width
        minWidth: '250px' as any, // Minimum width
        maxWidth: '350px' as any, // Maximum width
        backgroundColor: '#252526', // VS Code sidebar background
        borderRightWidth: 1,
        borderRightColor: '#3e3e42', // VS Code border color
        boxShadow: '2px 0 8px rgba(0, 0, 0, 0.1)' as any,
        height: '100%' as any,
        overflow: 'auto' as any, // Allow scrolling if content overflows
      },
      default: {
        width: 0,
        overflow: 'hidden',
      },
    }),
  },

  webSidebarHeader: {
    ...Platform.select({
      web: {
        paddingHorizontal: 20,
        paddingVertical: 24,
        borderBottomWidth: 1,
        // Colors will be set dynamically via style prop
      },
      default: {},
    }),
  },

  webSidebarTitle: {
    ...Platform.select({
      web: {
        fontSize: 20,
        fontWeight: 'bold',
        textAlign: 'center',
        // Color will be set dynamically via style prop
      },
      default: {},
    }),
  },

  webSidebarContent: {
    ...Platform.select({
      web: {
        flex: 1,
        paddingTop: 20,
      },
      default: {},
    }),
  },

  webSidebarMenuItem: {
    ...Platform.select({
      web: {
        flexDirection: 'row',
        alignItems: 'center',
        paddingVertical: 16,
        paddingHorizontal: 20,
        marginHorizontal: 8,
        marginVertical: 2,
        borderRadius: 8,
      },
      default: {},
    }),
  },

  webSidebarMenuItemActive: {
    ...Platform.select({
      web: {
        backgroundColor: '#094771', // VS Code active item background
      },
      default: {},
    }),
  },

  webSidebarMenuItemText: {
    ...Platform.select({
      web: {
        fontSize: 16,
        marginLeft: 12,
        fontWeight: '500',
        // Color will be set dynamically via style prop
      },
      default: {},
    }),
  },

  webProgressPanel: {
    ...Platform.select({
      web: {
        width: 320,
        backgroundColor: '#252526', // VS Code sidebar background
        borderRightWidth: 1,
        borderRightColor: '#3e3e42', // VS Code border color
        padding: 16,
        flexDirection: 'column',
        justifyContent: 'flex-start',
      },
      default: {
        width: 0,
        overflow: 'hidden',
      },
    }),
  },

  webMainContent: {
    ...Platform.select({
      web: {
        flex: 1,
        flexDirection: 'column' as const,
        height: '100%' as any, // Use 100% instead of 100vh to avoid mobile viewport issues
        overflow: 'hidden' as any, // Prevent scrolling on main content
        // Background color will be set dynamically via style prop
      },
      default: {
        flex: 1,
      },
    }),
  },

  webChatContainer: {
    ...Platform.select({
      web: {
        maxWidth: '100%' as any, // Use 100% to work correctly with browser zoom
        width: '100%' as any,
        alignSelf: 'center',
        flex: 1,
        flexDirection: 'column' as const,
        height: '100%' as any, // Use 100% instead of 100vh to fill parent properly on mobile
        overflow: 'hidden' as any, // Prevent main container scroll
        paddingHorizontal: 20, // React Native compatible padding
        // Background color will be set dynamically via style prop
      },
      default: {},
    }),
  },

  webHeader: {
    ...Platform.select({
      web: {
        flexDirection: 'row' as const,
        justifyContent: 'space-between',
        alignItems: 'center',
        height: 60, // Slightly reduced height for smaller screens
        paddingHorizontal: 'max(20px, min(40px, 3vw))' as any, // Responsive padding
        borderBottomWidth: 1,
        minHeight: 50, // Minimum height for very small screens
        // Colors will be set dynamically via style prop
      },
      default: {},
    }),
  },

  webHeaderTitle: {
    ...Platform.select({
      web: {
        fontSize: 24,
        fontWeight: '600',
        textAlign: 'center',
        flex: 1,
        // Color will be set dynamically via style prop
      },
      default: {},
    }),
  },

  webChatContent: {
    ...Platform.select({
      web: {
        flex: 1,
        minHeight: 0, // Required for flex children to not overflow
        overflow: 'hidden' as any,
        // Background color will be set dynamically via style prop
      },
      default: {},
    }),
  },

  webInputContainer: {
    ...Platform.select({
      web: {
        padding: 20,
        borderTopWidth: 1,
        // Colors will be set dynamically via style prop
      },
      default: {},
    }),
  },

  // Query Enhancement Toggle Container and Buttons
  toggleContainer: {
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 12,
    paddingHorizontal: 16,
    paddingVertical: 8,
    gap: 12,
    backgroundColor: 'rgba(255, 255, 255, 0.1)', // Add slight background for visibility
    borderRadius: 20, // Make it rounded
    marginHorizontal: 8, // Add side margins
  },

  queryToggleButton: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: '#E0E0E0',
    backgroundColor: 'transparent',
    gap: 6,
    minWidth: 120,
    justifyContent: 'center',
    ...Platform.select({
      web: {
        cursor: 'pointer',
        transition: 'all 0.2s ease',
      },
      default: {},
    }),
  },

  queryToggleButtonActive: {
    backgroundColor: '#007AFF',
    borderColor: '#007AFF',
    ...Platform.select({
      web: {
        boxShadow: '0 2px 6px rgba(0, 122, 255, 0.3)',
      },
      default: {
        shadowColor: '#007AFF',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.3,
        shadowRadius: 4,
        elevation: 4,
      },
    }),
  },

  queryToggleButtonText: {
    fontSize: 13,
    fontWeight: '500',
  },

  queryToggleButtonTextActive: {
    color: '#FFFFFF',
    fontWeight: '600',
  },

  webInputWrapper: {
    ...Platform.select({
      web: {
        flexDirection: 'row',
        alignItems: 'center',
        borderRadius: 25,
        paddingHorizontal: 16,
        paddingVertical: 8,
        borderWidth: 1,
        minHeight: 50,
        // Colors will be set dynamically via style prop
      },
      default: {},
    }),
  },

  webButton: {
    padding: 8,
    marginHorizontal: 4,
    borderRadius: 6,
    backgroundColor: 'transparent',
    alignItems: 'center',
    justifyContent: 'center',
  },

  webMessageContainer: {
    ...Platform.select({
      web: {
        padding: 20,
      },
      default: {},
    }),
  },

  webUserMessage: {
    ...Platform.select({
      web: {
        alignSelf: 'flex-end',
        backgroundColor: '#3498db',
        borderRadius: 18,
        borderBottomRightRadius: 4,
        paddingHorizontal: 16,
        paddingVertical: 12,
        marginVertical: 4,
        maxWidth: '85%',
      },
      default: {},
    }),
  },

  webBotMessage: {
    ...Platform.select({
      web: {
        alignSelf: 'flex-start',
        backgroundColor: '#ffffff',
        borderRadius: 18,
        borderBottomLeftRadius: 4,
        paddingHorizontal: 16,
        paddingVertical: 12,
        marginVertical: 4,
        maxWidth: '85%',
        borderWidth: 1,
        borderColor: '#e1e8ed',
      },
      default: {},
    }),
  },

  webThemeToggle: {
    ...Platform.select({
      web: {
        padding: 8,
        borderRadius: 4,
        backgroundColor: '#3e3e42', // VS Code button background
        borderWidth: 1,
        borderColor: '#6e6e70', // VS Code button border
      },
      default: {},
    }),
  },

  webDropdownContainer: {
    ...Platform.select({
      web: {
        borderRadius: 8,
        marginHorizontal: 8,
        marginTop: 4,
        borderWidth: 1,
        // Colors will be set dynamically via style prop
      },
      default: {},
    }),
  },

  webDropdownItem: {
    ...Platform.select({
      web: {
        paddingVertical: 12,
        paddingHorizontal: 16,
        borderBottomWidth: 1,
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
        // Colors will be set dynamically via style prop
      },
      default: {},
    }),
  },

  // Mobile dropdown styles (missing styles that were referenced in mobile menu)
  dropdownContainer: {
    backgroundColor: 'rgba(255, 255, 255, 0.1)',
    borderRadius: 8,
    marginHorizontal: 10,
    marginVertical: 5,
    overflow: 'hidden',
    ...Platform.select({
      ios: {
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.25,
        shadowRadius: 3.84,
      },
      android: {
        elevation: 5,
      },
    }),
  },

  dropdownItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: 'rgba(255, 255, 255, 0.1)',
  },

  selectedDropdownItem: {
    backgroundColor: 'rgba(255, 255, 255, 0.15)',
  },

  dropdownItemText: {
    flex: 1,
    fontSize: 16,
    fontWeight: '400',
  },

  dropdownItemDescription: {
    fontSize: 12,
    fontWeight: '300',
    marginTop: 2,
    lineHeight: 16,
  },

  selectedDropdownItemText: {
    fontWeight: '600',
  },

  // Additional web-specific styles for enhanced UX
  webSidebarSeparator: {
    ...Platform.select({
      web: {
        height: 1,
        backgroundColor: '#34495e',
        marginVertical: 8,
        marginHorizontal: 16,
      },
      default: {},
    }),
  },

  webSidebarSection: {
    ...Platform.select({
      web: {
        paddingVertical: 8,
      },
      default: {},
    }),
  },

  webSidebarSectionTitle: {
    ...Platform.select({
      web: {
        fontSize: 12,
        fontWeight: '600',
        color: '#95a5a6',
        paddingHorizontal: 20,
        paddingVertical: 8,
        textTransform: 'uppercase',
        letterSpacing: 1,
      },
      default: {},
    }),
  },

  webTooltip: {
    ...Platform.select({
      web: {
        position: 'absolute',
        backgroundColor: '#2c3e50',
        padding: 8,
        borderRadius: 4,
        zIndex: 9999,
      },
      default: {},
    }),
  },

  webTooltipText: {
    ...Platform.select({
      web: {
        color: '#ecf0f1',
        fontSize: 12,
        whiteSpace: 'nowrap',
      },
      default: {},
    }),
  },

  webLoadingSpinner: {
    ...Platform.select({
      web: {
        borderRadius: '50%',
        borderWidth: 2,
        borderStyle: 'solid',
        borderColor: '#e1e8ed',
        borderTopColor: '#3498db',
      },
      default: {},
    }),
  },

  // Enhanced message styling for web
  webMessageText: {
    ...Platform.select({
      web: {
        fontSize: 15,
        lineHeight: 22,
        letterSpacing: 0.3,
        wordWrap: 'break-word',
      },
      default: {},
    }),
  },

  webCodeBlock: {
    ...Platform.select({
      web: {
        backgroundColor: '#f8f9fa',
        borderRadius: 6,
        padding: 12,
        marginVertical: 8,
        fontFamily: 'monospace',
        fontSize: 14,
        borderWidth: 1,
        borderColor: '#e1e8ed',
      },
      default: {},
    }),
  },

  // Code Block Styles
  codeBlock: {
    ...Platform.select({
      web: {
        borderRadius: 8,
        marginVertical: 8,
        borderWidth: 1,
        overflow: 'hidden',
      },
      default: {
        borderRadius: 8,
        marginVertical: 8,
        borderWidth: 1,
        overflow: 'hidden',
      },
    }),
  },

  codeBlockHeader: {
    ...Platform.select({
      web: {
        flexDirection: 'row',
        justifyContent: 'space-between',
        alignItems: 'center',
        paddingHorizontal: 12,
        paddingVertical: 8,
        borderBottomWidth: 1,
      },
      default: {
        flexDirection: 'row',
        justifyContent: 'space-between',
        alignItems: 'center',
        paddingHorizontal: 12,
        paddingVertical: 8,
        borderBottomWidth: 1,
      },
    }),
  },

  codeBlockLanguage: {
    ...Platform.select({
      web: {
        fontSize: 12,
        fontWeight: '600',
        textTransform: 'uppercase',
      },
      default: {
        fontSize: 12,
        fontWeight: '600',
        textTransform: 'uppercase',
      },
    }),
  },

  copyButton: {
    ...Platform.select({
      web: {
        flexDirection: 'row',
        alignItems: 'center',
        paddingHorizontal: 8,
        paddingVertical: 4,
        borderRadius: 4,
        borderWidth: 1,
      },
      default: {
        flexDirection: 'row',
        alignItems: 'center',
        paddingHorizontal: 8,
        paddingVertical: 4,
        borderRadius: 4,
        borderWidth: 1,
      },
    }),
  },

  copyButtonText: {
    ...Platform.select({
      web: {
        fontSize: 12,
        fontWeight: '500',
        marginLeft: 4,
      },
      default: {
        fontSize: 12,
        fontWeight: '500',
        marginLeft: 4,
      },
    }),
  },

  codeBlockContent: {
    ...Platform.select({
      web: {
        padding: 12,
      },
      default: {
        padding: 12,
      },
    }),
  },

  codeText: {
    ...Platform.select({
      web: {
        fontSize: 14,
        lineHeight: 20,
        fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
      },
      default: {
        fontSize: 14,
        lineHeight: 20,
        fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
      },
    }),
  },

  inlineCode: {
    ...Platform.select({
      web: {
        fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
        fontSize: 13,
        paddingHorizontal: 4,
        paddingVertical: 2,
        borderRadius: 3,
        fontWeight: '500',
      },
      default: {
        fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
        fontSize: 13,
        paddingHorizontal: 4,
        paddingVertical: 2,
        borderRadius: 3,
        fontWeight: '500',
      },
    }),
  },

  formattedMessageContainer: {
    ...Platform.select({
      web: {
        flex: 1,
      },
      default: {
        flex: 1,
      },
    }),
  },

  // Enhanced RAG Features Styles
  toggleSwitch: {
    width: 40,
    height: 20,
    borderRadius: 10,
    justifyContent: 'center',
    marginLeft: 8,
  },
  toggleThumb: {
    width: 16,
    height: 16,
    borderRadius: 8,
    position: 'absolute',
  },
  intentIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 8,
    marginHorizontal: 16,
    marginVertical: 4,
    borderRadius: 8,
    borderWidth: 1,
  },
  intentText: {
    fontSize: 12,
    fontWeight: '600',
    marginLeft: 6,
  },
  confidenceText: {
    fontSize: 10,
    marginLeft: 8,
    opacity: 0.7,
  },
  suggestionChip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    marginRight: 8,
    marginBottom: 8,
    borderRadius: 16,
    borderWidth: 1,
  },
  suggestionText: {
    fontSize: 12,
    fontWeight: '500',
  },
  suggestionsContainer: {
    padding: 8,
    borderTopWidth: 1,
  },
  suggestionsTitle: {
    fontSize: 12,
    fontWeight: '600',
    marginBottom: 8,
  },
  suggestionsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
  },
  enhancedInputContainer: {
    borderTopWidth: 1,
    paddingVertical: 8,
  },
  intentPreview: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 6,
    marginHorizontal: 16,
    marginBottom: 8,
    borderRadius: 6,
    borderWidth: 1,
  },
  intentPreviewText: {
    fontSize: 12,
    marginLeft: 6,
    fontStyle: 'italic',
  },
  enhancedInputWrapper: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    marginHorizontal: 16,
    marginBottom: 8,
    borderRadius: 12,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  enhancedTextInput: {
    flex: 1,
    fontSize: 16,
    lineHeight: 20,
    maxHeight: 100,
    paddingVertical: 4,
  },
  inputActions: {
    flexDirection: 'row',
    alignItems: 'center',
    marginLeft: 8,
  },
  suggestionsButton: {
    padding: 8,
    borderRadius: 20,
    marginRight: 8,
  },
  enhancedSendButton: {
    padding: 8,
    borderRadius: 20,
    justifyContent: 'center',
    alignItems: 'center',
  },
  smartSuggestions: {
    marginHorizontal: 16,
    marginBottom: 8,
    borderRadius: 12,
    borderWidth: 1,
    padding: 12,
  },
  suggestionsHeader: {
    fontSize: 14,
    fontWeight: '600',
    marginBottom: 8,
  },
  categoryTabs: {
    marginBottom: 8,
  },
  categoryTab: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    borderWidth: 1,
    marginRight: 8,
  },
  categoryTabText: {
    fontSize: 12,
    marginLeft: 4,
    fontWeight: '500',
  },
  suggestionsList: {
    gap: 4,
  },
  suggestionItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 8,
    paddingHorizontal: 4,
    borderBottomWidth: 1,
  },
  smartSuggestionText: {
    fontSize: 13,
    flex: 1,
  },
  mobileIntentToggle: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 6,
    marginHorizontal: 16,
    marginBottom: 8,
    borderRadius: 16,
    borderWidth: 1,
  },
  mobileIntentText: {
    fontSize: 12,
    marginLeft: 6,
    fontWeight: '500',
  },
  mobileToggleSwitch: {
    width: 32,
    height: 18,
    borderRadius: 12,
    justifyContent: 'center',
    marginRight: 8,
  },
  mobileToggleThumb: {
    width: 14,
    height: 14,
    borderRadius: 7,
  },
  searchMetadataContainer: {
    padding: 8,
    marginHorizontal: 16,
    marginVertical: 4,
    borderRadius: 8,
    borderWidth: 1,
  },
  metadataRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginVertical: 2,
  },
  metadataLabel: {
    fontSize: 11,
    fontWeight: '600',
  },
  metadataValue: {
    fontSize: 11,
    opacity: 0.8,
  },

  // Quick Actions Styles
  quickActionsContainer: {
    flexDirection: 'column',
    marginTop: 12,
    gap: 8,
  },
  quickActionButton: {
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 8,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 44,
    ...Platform.select({
      web: {
        cursor: 'pointer',
        transition: 'all 0.2s ease',
      },
    }),
  },
  quickActionButtonPrimary: {
    backgroundColor: '#007AFF',
    borderColor: '#007AFF',
  },
  quickActionButtonSecondary: {
    backgroundColor: 'transparent',
    borderColor: '#8E8E93',
  },
  quickActionText: {
    fontSize: 16,
    fontWeight: '600',
    textAlign: 'center',
  },
  quickActionTextPrimary: {
    color: '#FFFFFF',
  },
  quickActionTextSecondary: {
    color: '#007AFF',
  },

  // Comprehensive Modal Styles
  comprehensiveModalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
  },
  comprehensiveModalContent: {
    width: '95%',
    maxWidth: 800, // Increased from 500 to 800 to prevent horizontal cramping
    maxHeight: Platform.OS === 'web' ? '85%' : '90%', // Slightly increased height for web
    minHeight: Platform.OS === 'web' ? 'auto' : 500,  // Increased minimum height
    borderRadius: 16,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 8,
    ...Platform.select({
      android: {
        minHeight: 550, // Increased for Android
      },
      ios: {
        minHeight: 500, // Increased for iOS
      },
    }),
  },
  comprehensiveModalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 20,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(0, 0, 0, 0.1)',
  },
  comprehensiveModalTitle: {
    fontSize: 20,
    fontWeight: '600',
  },
  closeButton: {
    padding: 8,
    borderRadius: 20,
    backgroundColor: 'rgba(0, 0, 0, 0.1)',
  },
  comprehensiveModalScroll: {
    flex: 1,
    padding: 20,
    ...Platform.select({
      ios: {
        paddingBottom: 30,
      },
      android: {
        paddingBottom: 25,
      },
    }),
  },
  categoriesContainer: {
    gap: 20,
  },
  categoryDescription: {
    fontSize: 14,
    textAlign: 'center',
    marginBottom: 10,
  },
  categoryCard: {
    padding: 20,
    borderRadius: 12,
    borderWidth: 1,
  },
  categoryHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 8,
    gap: 12,
  },
  categoryTitle: {
    fontSize: 18,
    fontWeight: '600',
  },
  categorySubtitle: {
    fontSize: 14,
    marginBottom: 12,
    lineHeight: 20,
  },
  categoryPreview: {
    flexDirection: 'row',
    gap: 16,
  },
  previewItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  previewText: {
    fontSize: 12,
  },
  modalOptionsContainer: {
    gap: 16,
  },
  backButton: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 20,
  },
  backText: {
    fontSize: 16,
    fontWeight: '500',
  },
  optionsTitle: {
    fontSize: 24,
    fontWeight: '700',
    marginBottom: 8,
  },
  optionsDescription: {
    fontSize: 14,
    marginBottom: 20,
    lineHeight: 20,
  },
  memoryUploadTip: {
    fontSize: 13,
    fontWeight: '600',
    marginTop: 4,
    marginBottom: 16,
  },
  optionButton: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 16,
    borderRadius: 12,
    borderWidth: 1,
    gap: 16,
  },
  optionContent: {
    flex: 1,
  },
  optionTitle: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 4,
  },
  optionDescription: {
    fontSize: 13,
    lineHeight: 18,
  },

  // Direct Action Buttons for Ask Question
  directActionsContainer: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
    marginTop: 16,
    ...Platform.select({
      web: {
        justifyContent: 'space-between',
      },
      default: {
        justifyContent: 'space-around',
      },
    }),
  },
  directActionButton: {
    padding: 12,
    borderRadius: 12,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 75,
    ...Platform.select({
      web: {
        flex: 1,
        minWidth: 140,
        maxWidth: 180,
        cursor: 'pointer',
        transition: 'all 0.2s ease',
      },
      default: {
        width: '30%',
        minWidth: 80,
      },
    }),
  },
  directActionText: {
    fontWeight: '600',
    marginTop: 8,
    textAlign: 'center',
    ...Platform.select({
      web: {
        fontSize: 14,
      },
      default: {
        fontSize: 12,
      },
    }),
  },
  directActionSubtext: {
    marginTop: 4,
    textAlign: 'center',
    lineHeight: 16,
    ...Platform.select({
      web: {
        fontSize: 12,
      },
      default: {
        fontSize: 10,
      },
    }),
  },

  // Attachment UI Styles
  attachmentsContainer: {
    backgroundColor: '#f8f9fa',
    padding: 12,
    marginHorizontal: 16,
    marginBottom: 8,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#e9ecef',
  },
  attachmentsTitle: {
    fontSize: 14,
    fontWeight: '600',
    color: '#495057',
    marginBottom: 8,
  },
  attachmentsScroll: {
    flexDirection: 'row',
  },
  attachmentItem: {
    marginRight: 12,
    alignItems: 'center',
    position: 'relative',
  },
  attachmentPreview: {
    width: 60,
    height: 60,
    borderRadius: 8,
    backgroundColor: '#f8f9fa',
  },
  audioPreview: {
    width: 60,
    height: 60,
    borderRadius: 8,
    backgroundColor: '#e9ecef',
    justifyContent: 'center',
    alignItems: 'center',
  },
  audioIcon: {
    fontSize: 24,
  },
  attachmentType: {
    fontSize: 10,
    color: '#6c757d',
    marginTop: 4,
    textAlign: 'center',
  },
  removeAttachment: {
    position: 'absolute',
    top: -6,
    right: -6,
    width: 20,
    height: 20,
    borderRadius: 10,
    backgroundColor: '#dc3545',
    justifyContent: 'center',
    alignItems: 'center',
  },
  removeAttachmentText: {
    color: '#ffffff',
    fontSize: 12,
    fontWeight: 'bold',
  },

  // Preload Content Styles
  preloadContainer: {
    flex: 1,
    padding: 20,
  },
  manageEntitiesContainer: {
    flex: 1,
    paddingBottom: 20,
    ...Platform.select({
      web: {},
      default: {
        paddingHorizontal: 16,
        paddingTop: 16,
      },
    }),
  },
  preloadDescription: {
    fontSize: 16,
    lineHeight: 24,
    textAlign: 'center',
  },
  preloadOptions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'center',
    gap: Platform.OS === 'web' ? 16 : 12,
    paddingHorizontal: Platform.OS === 'web' ? 0 : 16,
  },
  preloadOptionCard: {
    flex: Platform.OS === 'web' ? 1 : undefined,
    width: Platform.OS === 'web' ? '31%' : '100%',
    minWidth: Platform.OS === 'web' ? 280 : undefined,
    maxWidth: Platform.OS === 'web' ? 320 : undefined,
    minHeight: 80,
    padding: Platform.OS === 'web' ? 18 : 16,
    borderRadius: 16,
    borderWidth: 0,
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 16,
    ...Platform.select({
      ios: {
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 4 },
        shadowOpacity: 0.25,
        shadowRadius: 8,
      },
      android: {
        elevation: 6,
      },
      web: {
        boxShadow: '0 8px 32px rgba(0, 0, 0, 0.12), 0 4px 16px rgba(0, 0, 0, 0.08)',
        transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
        cursor: 'pointer',
        ':hover': {
          transform: 'translateY(-2px)',
          boxShadow: '0 12px 40px rgba(0, 0, 0, 0.15), 0 6px 20px rgba(0, 0, 0, 0.1)',
        },
        ':active': {
          transform: 'translateY(0px)',
          boxShadow: '0 4px 16px rgba(0, 0, 0, 0.12), 0 2px 8px rgba(0, 0, 0, 0.08)',
        },
        '@media (max-width: 768px)': {
          width: '100%',
          maxWidth: '100%',
          minWidth: 'auto',
        },
        '@media (max-width: 480px)': {
          padding: 14,
          minHeight: 70,
        },
      },
    }),
  },
  preloadOptionIcon: {
    width: Platform.OS === 'web' ? 48 : 44,
    height: Platform.OS === 'web' ? 48 : 44,
    borderRadius: 12,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: Platform.OS === 'web' ? 14 : 12,
    ...Platform.select({
      ios: {
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.15,
        shadowRadius: 4,
      },
      android: {
        elevation: 3,
      },
      web: {
        boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
        '@media (max-width: 480px)': {
          width: 40,
          height: 40,
        },
      },
    }),
  },
  preloadOptionContent: {
    flex: 1,
    flexDirection: 'column',
  },
  preloadOptionTitle: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 4,
    textAlign: 'left',
    flex: 1,
  },
  preloadOptionDescription: {
    fontSize: 13,
    lineHeight: 18,
    textAlign: 'left',
    opacity: 0.8,
    flex: 1,
  },

  // Mobile Browser Redirect Styles - REMOVED (MobileBrowserRedirect removed)
  // Mobile web now uses MobileHomeScreen + MobileWebHeader + mobileViewOnly composers

  // Load More Button Styles
  loadMoreContainer: {
    paddingHorizontal: 20,
    paddingVertical: 15,
    alignItems: 'center',
  },
  loadMoreButton: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 25,
    shadowColor: '#000',
    shadowOffset: {
      width: 0,
      height: 2,
    },
    shadowOpacity: 0.1,
    shadowRadius: 3.84,
    elevation: 5,
  },
  loadMoreButtonText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '600',
    marginLeft: 8,
  },

  // Content Loading Modal Styles
  loadingModalContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
  },
  loadingModalContent: {
    borderRadius: 10,
    padding: 30,
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: {
      width: 0,
      height: 2,
    },
    shadowOpacity: 0.25,
    shadowRadius: 3.84,
    elevation: 5,
    minWidth: 200,
  },
  loadingModalText: {
    marginTop: 15,
    fontSize: 16,
    fontWeight: '500',
    textAlign: 'center',
  },

  // Rich Content Styles for Tables and Advanced Formatting
  richContentContainer: {
    flex: 1,
  },
  tableContainer: {
    marginVertical: 16,
    borderRadius: 8,
    overflow: 'hidden',
  },
  tableRow: {
    flexDirection: 'row',
    minHeight: 44,
  },
  tableCell: {
    flex: 1,
    padding: 12,
    justifyContent: 'center',
  },
  tableCellText: {
    fontSize: 13,
    lineHeight: 18,
    textAlign: 'left',
  },
  listItem: {
    flexDirection: 'row',
    marginVertical: 2,
  },
  listBullet: {
    fontSize: 14,
    minWidth: 20,
    marginRight: 8,
  },
  listContent: {
    fontSize: 14,
    lineHeight: 20,
    flex: 1,
  },
  sectionHeader: {
    fontWeight: 'bold',
    lineHeight: 24,
  },
  richCodeBlock: {
    borderRadius: 8,
    marginVertical: 12,
    overflow: 'hidden',
  },
  richCodeHeader: {
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  richCodeLanguage: {
    fontSize: 12,
    fontWeight: 'bold',
    opacity: 0.8,
  },
  richCodeContent: {
    fontSize: 13,
    padding: 12,
    lineHeight: 18,
  },
  quoteBlock: {
    borderLeftWidth: 4,
    marginVertical: 12,
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderRadius: 4,
  },
  quoteContent: {
    fontSize: 14,
    fontStyle: 'italic',
    lineHeight: 20,
  },

  // Citation Styles
  citationsContainer: {
    // marginTop, paddingTop, borderTopWidth, borderTopColor are set inline for theme compatibility
  },
  citationsHeader: {
    // color, fontSize, fontWeight, marginBottom are set inline for theme compatibility
  },
  citationItem: {
    // backgroundColor, borderColor, borderWidth, borderRadius, padding, marginBottom are set inline
  },
  citationTitle: {
    // color, fontSize, fontWeight, marginBottom are set inline for theme compatibility
  },
  citationSource: {
    // color, fontSize, marginBottom are set inline for theme compatibility
  },
  citationScore: {
    // color, fontSize are set inline for theme compatibility
  },

  // Folder Content Styles
  folderContentHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 20,
    borderBottomWidth: 1,
  },
  folderHeaderInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
  },
  folderTitleContainer: {
    marginLeft: 12,
    flex: 1,
  },
  folderContentTitle: {
    fontSize: 22,
    fontWeight: 'bold',
  },
  folderContentSubtitle: {
    fontSize: 14,
    marginTop: 2,
  },
  documentsBackButton: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1,
  },
  backButtonText: {
    marginLeft: 8,
    fontSize: 14,
    fontWeight: '500',
  },
  documentsList: {
    padding: 16,
  },
  documentItem: {
    borderRadius: 8,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
  },
  documentHeader: {
    flexDirection: 'row',
    alignItems: 'flex-start',
  },
  documentIcon: {
    marginRight: 12,
    marginTop: 2,
  },
  documentInfo: {
    flex: 1,
  },
  documentTitle: {
    fontSize: 16,
    fontWeight: '600',
    lineHeight: 20,
  },
  documentDate: {
    fontSize: 12,
    marginTop: 4,
  },
  documentsPreview: {
    marginTop: 12,
    fontSize: 14,
    lineHeight: 18,
  },
  retryButton: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
    alignSelf: 'center',
  },
  retryButtonText: {
    fontSize: 14,
    fontWeight: '600',
  },

  // ==================== ATTACHMENT PROGRESS STYLES ====================
  attachmentProgressContainer: {
    margin: 8,
    padding: 12,
    borderRadius: 8,
    borderWidth: 1,
    marginBottom: 8,
  },
  attachmentProgressTitle: {
    fontSize: 14,
    fontWeight: '600',
    marginBottom: 8,
  },
  attachmentProgressItem: {
    marginBottom: 8,
  },
  attachmentProgressInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 4,
  },
  attachmentProgressName: {
    fontSize: 13,
    marginLeft: 8,
    flex: 1,
  },
  attachmentProgressStatus: {
    marginLeft: 24,
  },
  attachmentProgressMessage: {
    fontSize: 12,
    marginBottom: 4,
  },
  progressBar: {
    height: 4,
    borderRadius: 2,
    overflow: 'hidden',
  },
  progressBarFill: {
    height: '100%',
    borderRadius: 2,
  },
  attachmentProgressResult: {
    fontSize: 12,
    marginLeft: 24,
    fontWeight: '500',
  },
  attachmentReadyIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 8,
    padding: 8,
    borderRadius: 6,
    backgroundColor: 'rgba(68, 255, 68, 0.1)',
  },
  attachmentReadyText: {
    fontSize: 12,
    marginLeft: 6,
    fontWeight: '500',
  },

  // Progress Circle Styles for Attachments
  attachmentProgressOverlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    borderRadius: 8,
    zIndex: 10,
  },
  progressCircleContainer: {
    width: 50,
    height: 50,
    position: 'relative',
    justifyContent: 'center',
    alignItems: 'center',
  },
  progressCircle: {
    position: 'absolute',
    width: 50,
    height: 50,
    borderRadius: 25,
    borderWidth: 3,
    borderColor: 'rgba(255, 255, 255, 0.3)',
    borderTopColor: '#007AFF',
    transformOrigin: 'center',
  },
  progressInner: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(255, 255, 255, 0.9)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  progressText: {
    fontSize: 10,
    fontWeight: 'bold',
    color: '#007AFF',
  },

  // Success overlay for processed attachments
  attachmentSuccessOverlay: {
    position: 'absolute',
    top: 4,
    right: 4,
    zIndex: 11,
  },
  successCheckContainer: {
    backgroundColor: 'rgba(255, 255, 255, 0.9)',
    borderRadius: 12,
    padding: 2,
  },

  // Error overlay for failed attachment processing
  attachmentErrorOverlay: {
    position: 'absolute',
    top: 4,
    right: 4,
    zIndex: 11,
  },
  errorIconContainer: {
    backgroundColor: 'rgba(255, 255, 255, 0.9)',
    borderRadius: 12,
    padding: 2,
  },

  // Subfolder Grid Styles
  subfoldersContainer: {
    flex: 1,
    padding: 20,
  },
  subfoldersGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 16,
    justifyContent: 'flex-start',
  },
  subfolderCard: {
    width: Platform.OS === 'web' ? 'calc(33.33% - 12px)' as any : '100%',
    minWidth: 250,
    padding: 24,
    borderRadius: 12,
    borderWidth: 1,
    alignItems: 'center',
    ...Platform.select({
      ios: {
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.1,
        shadowRadius: 4,
      },
      android: {
        elevation: 3,
      },
      web: {
        boxShadow: '0 2px 8px rgba(0, 0, 0, 0.1)',
        cursor: 'pointer',
        transition: 'all 0.2s ease',
      },
    }),
  },
  subfolderIconContainer: {
    width: 64,
    height: 64,
    borderRadius: 32,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 16,
  },
  subfolderName: {
    fontSize: 18,
    fontWeight: '600',
    marginBottom: 8,
    textAlign: 'center',
  },
  subfolderDescription: {
    fontSize: 14,
    textAlign: 'center',
    lineHeight: 20,
  },
  folderContentHeader: {
    padding: 20,
    borderBottomWidth: 1,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  folderHeaderInfo: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  folderTitleContainer: {
    flex: 1,
  },
  folderContentTitle: {
    fontSize: 20,
    fontWeight: '600',
    marginBottom: 4,
  },
  folderContentSubtitle: {
    fontSize: 14,
  },
  backButton: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1,
    gap: 8,
  },
  backButtonText: {
    fontSize: 14,
    fontWeight: '500',
  },
  retryButton: {
    paddingHorizontal: 24,
    paddingVertical: 12,
    borderRadius: 8,
  },
  retryButtonText: {
    fontSize: 16,
    fontWeight: '600',
  },

});