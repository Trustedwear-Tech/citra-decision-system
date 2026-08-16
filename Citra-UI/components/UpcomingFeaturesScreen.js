import React from 'react';
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { modernStyles, SPACING, BORDER_RADIUS } from '../styles/modernStyles';
import UI_TEXT from '../config/uiText';

const UpcomingFeaturesScreen = ({ theme }) => {
  const personaText = UI_TEXT;
  
  const features = [
    {
      icon: 'people-outline',
      title: 'Team Collaboration & Enterprise Data Sharing',
      description: `Collaborate seamlessly within your ${personaText?.upcomingTeamContext || 'team'} while maintaining strict data control.`,
      items: [
        `Team Workspaces: Create dedicated spaces for ${personaText?.upcomingWorkspaceType || 'teams or departments'}.`,
        `Shared Vaults: Work together on ${personaText?.upcomingDataType || 'project files'}, research drafts, and AI-generated documents.`,
        'Real-Time Sync: Updates and AI context growth are synchronized for all members instantly.',
        `Cross-Matter Intelligence: AI links shared materials across ${personaText?.upcomingMatterType || 'projects'} automatically for deeper insights.`,
      ],
    },
    {
      icon: 'shield-checkmark-outline',
      title: 'Enterprise Member Management & Access Control',
      description: 'Granular, permission-based access for complete administrative oversight.',
      items: [
        'Add Members: Invite team members and specialists with role-specific privileges.',
        'Permission Control: Assign access levels — view, edit, manage, or admin.',
        'Enterprise Entities: Manage multiple clients, cases, or divisions within one enterprise account.',
        'Audit Logs: Maintain full transparency of every file access, edit, and AI-generated output.',
      ],
    },
    {
      icon: 'folder-outline',
      title: 'Enterprise Folder & Data Sharing',
      description: `Centralized and secure collaboration built for large professional teams.`,
      items: [
        `Enterprise Folder: Private organization-wide repository for ${personaText?.upcomingRepositoryType || 'documents and internal archives'}.`,
        'Controlled Sharing: Share select folders or files with members or external parties securely.',
        'Version History: Track document changes, approvals, and rollbacks with ease.',
        'AI Context Growth: Every shared item enriches the enterprise AI context for faster research and drafting.',
      ],
    },
    {
      icon: 'mail-outline',
      title: 'Enterprise Email Login & OTP Authentication',
      description: 'Security and simplicity combined for enterprise-grade authentication.',
      items: [
        'Enterprise Email Login: Sign in using your official organization email.',
        'OTP Authentication: Receive secure one-time passwords for account verification.',
        'Multi-Level Encryption: Combines email authentication with encrypted sessions and activity validation.',
        'Seamless User Onboarding: Simplifies organization-wide setup without compromising security.',
      ],
    },
    {
      icon: 'headset-outline',
      title: 'Enterprise Support & Dedicated Assistance',
      description: 'Tailored support designed for professional organizations.',
      items: [
        'Enterprise Email Support: Priority 24/7 support via your organization\'s registered domain.',
        'Dedicated Account Manager: Personalized onboarding and workflow optimization.',
        'Compliance Assistance: Ensure your practice meets privacy and data governance standards.',
        'Custom AI Tuning: Configure AI for your organization\'s preferred drafting style, terminology, and framework.',
      ],
    },
    {
      icon: 'cog-outline',
      title: 'Advanced Collaboration & Workflow Automation',
      description: 'Next-generation features to accelerate research, drafting, and productivity.',
      items: [
        'Collaborative AI Drafting: Multiple users co-create documents with live AI assistance.',
        'AI Review Loops: Automatically detect missing citations or inconsistencies.',
        `Smart Notifications: Receive alerts for new relevant documents affecting your active ${personaText?.upcomingMatterType || 'projects'}.`,
        'Enterprise Dashboard: Visualize usage metrics, research productivity, and compliance status.',
      ],
    },
    {
      icon: 'lock-closed-outline',
      title: 'Security, Privacy & Governance Enhancements',
      description: 'Reinforcing trust through advanced enterprise-grade protection.',
      items: [
        'Role-Based Encryption: Secure access via user-specific encryption tokens.',
        'Confidential Mode: Prevent AI memory retention for sensitive matters.',
        'Data Residency Control: Choose secure storage regions (India, EU, or global cloud).',
        'Privacy Dashboard: Monitor data access, usage, and compliance in real time.',
      ],
    },
    {
      icon: 'phone-portrait-outline',
      title: 'Cross-Platform Access & Integrations',
      description: 'Expand your workflow across every platform and tool you use.',
      items: [
        'Mobile Integration: Sync notes, vaults, and research between desktop and mobile.',
        'Email Integration: Save correspondence and attachments directly into your Vault.',
        'Document Management APIs: Connect with your DMS or third-party tools effortlessly.',
        `Document Integration: Automatically link relevant documents and records to your ${personaText?.upcomingMatterType || 'projects'}.`,
      ],
    },
    {
      icon: 'bulb-outline',
      title: 'AI Evolution — Contextual Reasoning 2.0',
      description: 'The next leap in AI-driven professional understanding.',
      items: [
        `Dynamic Context: AI applies learned context from previous ${personaText?.upcomingMatterType || 'projects'} to current research.`,
        'Auto-Updating Citations: Keeps all references current as new documents are released.',
        'Relevance Analyzer: Identifies the most relevant parallels automatically.',
        'AI Co-Draft Mode: Experience real-time reasoning as your team drafts and reviews documents.',
      ],
    },
  ];

  const comingSoonFeatures = [
    { feature: 'Team Collaboration', description: 'Real-time shared workspaces and Vaults', status: '🟢 Coming Q1 2026' },
    { feature: 'Enterprise Member Management', description: 'Add users, assign permissions, track activity', status: '🟡 In Beta' },
    { feature: 'Enterprise Folder & Shared Vault', description: 'Centralized repository with permission control', status: '🟡 Testing Phase' },
    { feature: 'Enterprise Email & OTP Login', description: 'Secure organization-based authentication', status: '🟢 Coming Q1 2026' },
    { feature: 'Enterprise Email Support', description: 'Priority 24/7 assistance', status: '🟢 Live' },
    { feature: 'Collaborative AI Drafting', description: 'Multi-user live AI drafting', status: '🟣 Planned Q3 2026' },
    { feature: 'AI Review Loops', description: 'Automatic error and citation detection', status: '🟣 Planned Q3 2026' },
  ];

  const renderFeatureSection = (feature, index) => (
    <View
      key={index}
      style={[
        styles.featureCard,
        {
          backgroundColor: theme.surface || (theme.isDark ? '#1e293b' : '#ffffff'),
          borderColor: theme.border || (theme.isDark ? '#334155' : '#e2e8f0'),
        },
      ]}
    >
      <View style={styles.featureHeader}>
        <View style={styles.featureIconContainer}>
          <Ionicons
            name={feature.icon}
            size={24}
            color={theme.primary || '#3b82f7'}
          />
        </View>
        <View style={styles.featureTitleContainer}>
          <Text style={[styles.featureTitle, { color: theme.text || (theme.isDark ? '#ffffff' : '#1e293b') }]}>
            {feature.title}
          </Text>
          <Text style={[styles.featureDescription, { color: theme.textSecondary || (theme.isDark ? '#cbd5e1' : '#64748b') }]}>
            {feature.description}
          </Text>
        </View>
      </View>

      <View style={styles.featureItems}>
        {feature.items.map((item, itemIndex) => (
          <View key={itemIndex} style={styles.featureItem}>
            <Text style={[styles.bulletPoint, { color: theme.primary || '#3b82f7' }]}>•</Text>
            <Text style={[styles.featureItemText, { color: theme.text || (theme.isDark ? '#ffffff' : '#1e293b') }]}>
              {item}
            </Text>
          </View>
        ))}
      </View>
    </View>
  );

  const renderComingSoonTable = () => (
    <View
      style={[
        styles.tableCard,
        {
          backgroundColor: theme.surface || (theme.isDark ? '#1e293b' : '#ffffff'),
          borderColor: theme.border || (theme.isDark ? '#334155' : '#e2e8f0'),
        },
      ]}
    >
      <Text style={[styles.tableTitle, { color: theme.text || (theme.isDark ? '#ffffff' : '#1e293b') }]}>
        🔜 Coming Soon Highlights
      </Text>

      <View style={styles.tableHeader}>
        <Text style={[styles.tableHeaderText, { color: theme.textSecondary || (theme.isDark ? '#cbd5e1' : '#64748b') }]}>
          Feature
        </Text>
        <Text style={[styles.tableHeaderText, { color: theme.textSecondary || (theme.isDark ? '#cbd5e1' : '#64748b') }]}>
          Description
        </Text>
        <Text style={[styles.tableHeaderText, { color: theme.textSecondary || (theme.isDark ? '#cbd5e1' : '#64748b') }]}>
          Status
        </Text>
      </View>

      {comingSoonFeatures.map((item, index) => (
        <View key={index} style={[styles.tableRow, { borderTopColor: theme.border || (theme.isDark ? '#334155' : '#e2e8f0') }]}>
          <Text style={[styles.tableCell, styles.tableFeature, { color: theme.text || (theme.isDark ? '#ffffff' : '#1e293b') }]}>
            {item.feature}
          </Text>
          <Text style={[styles.tableCell, styles.tableDescription, { color: theme.text || (theme.isDark ? '#ffffff' : '#1e293b') }]}>
            {item.description}
          </Text>
          <Text style={[styles.tableCell, styles.tableStatus, { color: theme.text || (theme.isDark ? '#ffffff' : '#1e293b') }]}>
            {item.status}
          </Text>
        </View>
      ))}
    </View>
  );

  return (
    <ScrollView
      style={[styles.container, { backgroundColor: theme.background || (theme.isDark ? '#0f172a' : '#f8fafc') }]}
      contentContainerStyle={styles.contentContainer}
      showsVerticalScrollIndicator={false}
    >
      <View style={styles.header}>
        <Ionicons
          name="settings-outline"
          size={32}
          color={theme.primary || '#3b82f7'}
          style={styles.headerIcon}
        />
        <Text style={[styles.headerTitle, { color: theme.text || (theme.isDark ? '#ffffff' : '#1e293b') }]}>
          ⚙️ Upcoming & Future Features
        </Text>
        <Text style={[styles.headerSubtitle, { color: theme.textSecondary || (theme.isDark ? '#cbd5e1' : '#64748b') }]}>
          {personaText?.upcomingFeaturesSubtitle || 'Empowering Professionals with the Next Generation of AI-Powered Intelligence'}
        </Text>
      </View>

      <View style={styles.featuresContainer}>
        {features.map(renderFeatureSection)}
      </View>

      {renderComingSoonTable()}
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  contentContainer: {
    padding: SPACING.lg,
    maxWidth: 800,
    alignSelf: 'center',
    width: '100%',
  },
  header: {
    alignItems: 'center',
    marginBottom: SPACING.xl,
    paddingVertical: SPACING.xl,
  },
  headerIcon: {
    marginBottom: SPACING.md,
  },
  headerTitle: {
    fontSize: 24,
    fontWeight: '700',
    textAlign: 'center',
    marginBottom: SPACING.sm,
  },
  headerSubtitle: {
    fontSize: 16,
    textAlign: 'center',
    lineHeight: 24,
    opacity: 0.8,
  },
  featuresContainer: {
    marginBottom: SPACING.xl,
  },
  featureCard: {
    borderWidth: 1,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.lg,
    marginBottom: SPACING.lg,
    ...Platform.select({
      ios: {
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.1,
        shadowRadius: 4,
      },
      android: { elevation: 4 },
      web: { boxShadow: '0 2px 8px rgba(0, 0, 0, 0.1)' },
    }),
  },
  featureHeader: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: SPACING.lg,
  },
  featureIconContainer: {
    width: 48,
    height: 48,
    borderRadius: BORDER_RADIUS.md,
    backgroundColor: 'rgba(59, 130, 247, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: SPACING.md,
  },
  featureTitleContainer: {
    flex: 1,
  },
  featureTitle: {
    fontSize: 18,
    fontWeight: '600',
    marginBottom: SPACING.xs,
    lineHeight: 24,
  },
  featureDescription: {
    fontSize: 14,
    lineHeight: 20,
    opacity: 0.8,
  },
  featureItems: {
    paddingLeft: SPACING.md,
  },
  featureItem: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: SPACING.sm,
  },
  bulletPoint: {
    fontSize: 16,
    fontWeight: '600',
    marginRight: SPACING.sm,
    marginTop: -2,
  },
  featureItemText: {
    fontSize: 14,
    lineHeight: 20,
    flex: 1,
  },
  tableCard: {
    borderWidth: 1,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.lg,
    ...Platform.select({
      ios: {
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.1,
        shadowRadius: 4,
      },
      android: { elevation: 4 },
      web: { boxShadow: '0 2px 8px rgba(0, 0, 0, 0.1)' },
    }),
  },
  tableTitle: {
    fontSize: 20,
    fontWeight: '600',
    marginBottom: SPACING.lg,
    textAlign: 'center',
  },
  tableHeader: {
    flexDirection: 'row',
    paddingBottom: SPACING.md,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(0, 0, 0, 0.1)',
    marginBottom: SPACING.md,
  },
  tableHeaderText: {
    fontSize: 14,
    fontWeight: '600',
    flex: 1,
    textAlign: 'center',
  },
  tableRow: {
    flexDirection: 'row',
    paddingVertical: SPACING.sm,
    borderTopWidth: 1,
  },
  tableCell: {
    fontSize: 14,
    flex: 1,
    textAlign: 'center',
    paddingHorizontal: SPACING.xs,
  },
  tableFeature: {
    fontWeight: '600',
    textAlign: 'left',
  },
  tableDescription: {
    textAlign: 'left',
  },
  tableStatus: {
    fontWeight: '500',
  },
});

export default UpcomingFeaturesScreen;