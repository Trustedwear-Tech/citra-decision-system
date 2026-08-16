import React, { useState } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  Animated,
  Platform,
  ActivityIndicator,
  ScrollView,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { modernStyles, SPACING } from '../../styles/modernStyles';
import { TourButton } from '../ProductTour';

// Get the AI description shown in the ribbon.
const getAIDescription = () => 'Citra AI model';

/**
 * Microsoft Word-style Ribbon Menu Component
 * Organizes buttons into tabs for better organization and user experience
 */
export const RibbonMenu = ({
  theme,
  personaText,
  // User profession for conditional text
  userProfession = 'legal',
  // Research Tools
  // Productivity Tools
  // Page Builder
  onShowIntegrations,
  // Enterprise
  useEnterprise = false,
  onUseEnterpriseChange,
  renderEnterpriseSearch,
  // History
  onOpenHistory,
  // Internet
  enableInternetSearch = false,
  onEnableInternetSearchChange,
  // Help
  onShowHowToUse,
  // Customize UI
  isDarkMode = false,
  onToggleTheme,
  // Vault
  // Vault Selector
  selectedFolders = [],
  folders = [],
  onSelectFolder,
  // Drive (Vault / Firm Knowledge)
  // Additional props
  disabled = false,
  isModelOnlyMode = false,
  onModelOnlyModeChange,
}) => {
  const [activeTab, setActiveTab] = useState(null); // No tab selected by default
  const [isRibbonCollapsed, setIsRibbonCollapsed] = useState(true); // Start collapsed as requested

  // Expose tab control for product tour (globally accessible)
  React.useEffect(() => {
    if (Platform.OS === 'web') {
      window.__ribbonMenuControl = {
        openTab: (tabId) => {
          console.log('🎯 [TOUR] Opening tab via global control:', tabId);
          setActiveTab(tabId);
          setIsRibbonCollapsed(false);
        },
        closeRibbon: () => {
          setIsRibbonCollapsed(true);
        },
        getActiveTab: () => activeTab,
      };
    }
    return () => {
      if (Platform.OS === 'web') {
        delete window.__ribbonMenuControl;
      }
    };
  }, [activeTab]);

  // Enhanced handlers that automatically turn off AI Model mode when needed
  const createEnhancedHandler = (originalHandler, featureValue) => {
    return (newValue) => {
      if (newValue && isModelOnlyMode) {
        console.log('🔄 Automatically disabling General Query (AI Model) mode to enable other feature');
        onModelOnlyModeChange(false);
      }
      originalHandler(newValue);
    };
  };

  // Tab definitions
  const tabs = [
    // {
    //   id: 'research',
    //   label: 'Research Tools',
    //   icon: 'flask-outline',
    // },
    // {
    //   id: 'productivity',
    //   label: 'Productivity',
    //   icon: 'create-outline',
    // },
    // {
    //   id: 'pages',
    //   label: 'Pages',
    //   icon: 'documents-outline',
    // },
    // {
    //   id: 'connection',
    //   label: 'Connection',
    //   icon: 'link-outline',
    // },
    {
      id: 'help',
      label: 'Help',
      icon: 'help-circle-outline',
    },
    // {
    //   id: 'customize',
    //   label: 'Customize UI',
    //   icon: 'settings-outline',
    // },
    // {
    //   id: 'history',
    //   label: 'My Files',
    //   icon: 'time-outline',
    // },
  ];

  // Only the Help tab is live (see `tabs` above), and it has no toggle inside
  // it, so no tab ever shows the "something is on" dot.
  const selectionState = { help: false };

  const selectionColor = '#10B981';

  // Render button component
  const RibbonButton = ({ icon, label, onPress, isActive, color, isLoading, disabled: buttonDisabled, description, dataTour }) => {
    const buttonElement = (
      <TouchableOpacity
        style={[
          styles.ribbonButton,
          {
            backgroundColor: isActive ? color : 'transparent',
            borderColor: isActive ? color : theme.border,
            opacity: (disabled || buttonDisabled) ? 0.5 : 1,
          }
        ]}
        onPress={onPress}
        disabled={disabled || buttonDisabled}
        activeOpacity={0.8}
        accessibilityRole="button"
        accessibilityLabel={label}
        accessibilityHint={description}
      >
        <View style={styles.ribbonButtonIcon}>
          {isLoading ? (
            <ActivityIndicator size={24} color={isActive ? theme.textOnPrimary : theme.text} />
          ) : (
            <Ionicons
              name={icon}
              size={24}
              color={isActive ? theme.textOnPrimary : theme.text}
            />
          )}
        </View>
        <Text
          style={[
            styles.ribbonButtonLabel,
            {
              color: isActive ? theme.textOnPrimary : theme.text,
              fontWeight: isActive ? '600' : '500',
            }
          ]}
          numberOfLines={2}
        >
          {label}
        </Text>
      </TouchableOpacity>
    );

    // Wrap in View with dataSet for proper data-tour attribute on web
    return dataTour && Platform.OS === 'web' ? (
      <View dataSet={{ tour: dataTour }}>
        {buttonElement}
      </View>
    ) : buttonElement;
  };

  // Render toggle button component
  const RibbonToggleButton = ({ icon, label, value, onChange, color, description, dataTour }) => {
    const buttonElement = (
      <TouchableOpacity
        style={[
          styles.ribbonButton,
          {
            backgroundColor: value ? color : 'transparent',
            borderColor: value ? color : theme.border,
            opacity: disabled ? 0.5 : 1,
          }
        ]}
        onPress={() => onChange(!value)}
        disabled={disabled}
        activeOpacity={0.8}
        accessibilityRole="switch"
        accessibilityState={{ checked: value }}
        accessibilityLabel={label}
        accessibilityHint={description}
      >
        <View style={styles.ribbonButtonIcon}>
          <Ionicons
            name={icon}
            size={24}
            color={value ? theme.textOnPrimary : theme.text}
          />
        </View>
        <Text
          style={[
            styles.ribbonButtonLabel,
            {
              color: value ? theme.textOnPrimary : theme.text,
              fontWeight: value ? '600' : '500',
            }
          ]}
          numberOfLines={2}
        >
          {label}
        </Text>
      </TouchableOpacity>
    );

    // Wrap in View with dataSet for proper data-tour attribute on web
    return dataTour && Platform.OS === 'web' ? (
      <View dataSet={{ tour: dataTour }}>
        {buttonElement}
      </View>
    ) : buttonElement;
  };

  // Render info button component (non-toggleable, always-on indicator)
  const RibbonInfoButton = ({ icon, label, description, color, badge }) => (
    <View
      style={[
        styles.ribbonButton,
        {
          backgroundColor: color,
          borderColor: color,
        }
      ]}
      accessibilityRole="text"
      accessibilityLabel={`${label} - ${badge || 'Always On'}`}
      accessibilityHint={description}
    >
      <View style={styles.ribbonButtonIcon}>
        <Ionicons
          name={icon}
          size={24}
          color={theme.textOnPrimary}
        />
      </View>
      <Text
        style={[
          styles.ribbonButtonLabel,
          {
            color: theme.textOnPrimary,
            fontWeight: '600',
          }
        ]}
        numberOfLines={2}
      >
        {label}
      </Text>
      {badge && (
        <View style={styles.alwaysOnBadge}>
          <Text style={styles.alwaysOnBadgeText}>{badge}</Text>
        </View>
      )}
    </View>
  );

  // Render content based on active tab
  const renderTabContent = () => {
    switch (activeTab) {
      case 'help':
        return (
          <View style={styles.ribbonContent}>
            <RibbonButton
              icon="help-circle-outline"
              label="How to use"
              onPress={onShowHowToUse}
              isActive={false}
              color={theme.primary}
              description="Learn how to use Citra AI effectively"
            />
            {/* Product Tour Button */}
            {Platform.OS === 'web' && (
              <TourButton theme={theme} />
            )}
            <View style={styles.ribbonDivider} />
            <Text style={[styles.ribbonHelperText, { color: theme.textSecondary }]}>
              Get started with Citra AI
            </Text>
          </View>
        );

      default:
        return null;
    }
  };

  return (
    <View style={[styles.ribbonContainer, { backgroundColor: theme.inputBackground, borderBottomColor: theme.border }]}>
      {/* Tab Bar */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.tabBar}
        contentContainerStyle={styles.tabBarContent}
      >
        {tabs.map((tab) => {
          const tourId = tab.id === 'research' ? 'research-tab' : tab.id === 'productivity' ? 'productivity-tab' : tab.id === 'help' ? 'help-tab' : `${tab.id}-tab`;
          const tabContent = (
            <TouchableOpacity
              key={tab.id}
              style={[
                styles.tab,
                {
                  backgroundColor: activeTab === tab.id ? theme.primary + '20' : 'transparent',
                  borderBottomWidth: activeTab === tab.id ? 3 : 0,
                  borderBottomColor: theme.primary,
                }
              ]}
              onPress={() => {
                if (activeTab === tab.id) {
                  // If clicking the same tab, toggle the ribbon collapse state
                  setIsRibbonCollapsed(!isRibbonCollapsed);
                } else {
                  // If clicking a different tab, switch to it and expand the ribbon
                  setActiveTab(tab.id);
                  setIsRibbonCollapsed(false);
                }
              }}
              activeOpacity={0.7}
            >
              <Ionicons
                name={tab.icon}
                size={18}
                color={activeTab === tab.id ? theme.primary : theme.text}
                style={{ marginRight: 6 }}
              />
              <Text
                style={[
                  styles.tabLabel,
                  {
                    color: activeTab === tab.id ? theme.primary : theme.text,
                    fontWeight: activeTab === tab.id ? '600' : '500',
                  }
                ]}
              >
                {tab.label}
              </Text>
              {/* Green circle indicator when items are selected */}
              {selectionState[tab.id] && (
                <View style={[
                  styles.tabBadge,
                  { backgroundColor: selectionColor }
                ]} />
              )}
            </TouchableOpacity>
          );

          // Wrap in View with dataSet for proper data-tour attribute on web
          return Platform.OS === 'web' ? (
            <View key={tab.id} dataSet={{ tour: tourId }}>
              {tabContent}
            </View>
          ) : tabContent;
        })}
      </ScrollView>

      {/* Tab Content */}
      {!isRibbonCollapsed && (
        <View style={[styles.ribbonContentContainer, { backgroundColor: theme.surface }]}>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.ribbonContentScrollView}
          >
            {renderTabContent()}
          </ScrollView>
        </View>
      )}

      {/* Collapse/Expand Button */}
      <TouchableOpacity
        style={[styles.collapseButton, { backgroundColor: theme.surface, borderColor: theme.border }]}
        onPress={() => {
          if (isRibbonCollapsed) {
            // If expanding and no tab selected, default to the first tab that
            // still renders. 'research' was removed with its three buttons.
            if (!activeTab) {
              setActiveTab('help');
            }
            setIsRibbonCollapsed(false);
          } else {
            setIsRibbonCollapsed(true);
          }
        }}
        activeOpacity={0.7}
      >
        <Ionicons
          name={isRibbonCollapsed ? 'chevron-down' : 'chevron-up'}
          size={16}
          color={theme.text}
        />
      </TouchableOpacity>
    </View>
  );
};

// Styles for the Ribbon Menu
const styles = {
  ribbonContainer: {
    width: '100%',
    borderBottomWidth: 1,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 4,
  },
  tabBar: {
    flexDirection: 'row',
    paddingHorizontal: 8,
    paddingTop: 4,
  },
  tabBarContent: {
    alignItems: 'center',
  },
  tab: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 10,
    marginHorizontal: 2,
    borderRadius: 6,
    minHeight: 44,
    position: 'relative',
  },
  tabLabel: {
    fontSize: 14,
  },
  tabBadge: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginLeft: 6,
  },
  ribbonContentContainer: {
    paddingVertical: 12,
    paddingHorizontal: 8,
    borderTopWidth: 1,
    borderTopColor: 'rgba(0,0,0,0.05)',
  },
  ribbonContentScrollView: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    paddingHorizontal: 4,
  },
  ribbonContent: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 12,
    flexWrap: 'wrap',
  },
  ribbonContentColumn: {
    flexDirection: 'column',
    alignItems: 'flex-start',
    gap: 12,
  },
  ribbonButton: {
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 8,
    borderWidth: 1.5,
    minWidth: 110,
    maxWidth: 140,
    minHeight: 85,
    maxHeight: 85,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.1,
    shadowRadius: 2,
    elevation: 2,
  },
  ribbonButtonIcon: {
    marginBottom: 6,
  },
  ribbonButtonLabel: {
    fontSize: 12,
    textAlign: 'center',
    lineHeight: 16,
  },
  ribbonDivider: {
    width: 1,
    height: 60,
    backgroundColor: 'rgba(0,0,0,0.1)',
    marginHorizontal: 8,
  },
  ribbonHelperText: {
    fontSize: 11,
    fontStyle: 'italic',
    maxWidth: 150,
    textAlign: 'center',
    lineHeight: 14,
    alignSelf: 'center',
  },
  collapseButton: {
    position: 'absolute',
    right: 8,
    top: 8,
    width: 28,
    height: 28,
    borderRadius: 14,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.15,
    shadowRadius: 2,
    elevation: 3,
  },
  alwaysOnBadge: {
    position: 'absolute',
    top: 4,
    right: 4,
    backgroundColor: '#10b981',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 8,
  },
  alwaysOnBadgeText: {
    color: '#ffffff',
    fontSize: 8,
    fontWeight: '700',
    textTransform: 'uppercase',
  },
  ribbonGroup: {
    flexDirection: 'column',
    alignItems: 'flex-start',
    gap: 6,
  },
  ribbonGroupTitle: {
    fontSize: 10,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginLeft: 4,
  },
  ribbonGroupRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
};

export default RibbonMenu;
