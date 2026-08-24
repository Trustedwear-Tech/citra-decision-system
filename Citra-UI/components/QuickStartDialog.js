// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * QuickStartDialog.js - Streamlined first-time user onboarding
 * 
 * Single-step onboarding flow: 
 * - Click Presentation / Report / Diagram to launch directly
 * 
 * (Vault upload is available inside each tool's UI)
 */

import React, { useState, useEffect } from 'react';
import {
    View,
    Text,
    TouchableOpacity,
    StyleSheet,
    Modal,
    Dimensions,
    Platform,
    ScrollView,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Animated, {
    FadeIn,
    SlideInRight,
} from 'react-native-reanimated';

// Intent options
const INTENT_OPTIONS = [
    {
        id: 'chat',
        title: 'Enterprise Chat',
        subtitle: 'AI Chat on Data Store and Internet',
        icon: 'chatbubbles-outline',
        color: '#6366F1',
    },
    {
        id: 'quickchat',
        title: 'Quick Chat',
        subtitle: 'Upload files & ask',
        icon: 'flash-outline',
        color: '#F59E0B',
    },
    {
        id: 'presentation',
        title: 'Presentation',
        subtitle: 'Create slides from your data',
        icon: 'easel-outline',
        color: '#8B5CF6',
    },
    {
        id: 'printable',
        title: 'Dashboard',
        subtitle: 'Design A4 documents & flyers',
        icon: 'print-outline',
        color: '#F59E0B',
    },
    {
        id: 'report',
        title: 'Report',
        subtitle: 'Generate detailed analysis',
        icon: 'document-text-outline',
        color: '#3B82F6',
    },
];

// IntentCard component
const IntentCard = ({ option, isSelected, onSelect, theme, cardWidth }) => {
    const isWeb = Platform.OS === 'web';

    return (
        <TouchableOpacity
            onPress={() => onSelect(option.id)}
            activeOpacity={0.7}
            style={[
                styles.intentCard,
                {
                    backgroundColor: isSelected ? `${option.color}15` : theme.surface,
                    borderColor: isSelected ? option.color : theme.borderColor,
                    borderWidth: isSelected ? 2 : 1,
                    width: cardWidth || 140,
                },
                isWeb && styles.intentCardWeb,
            ]}
        >
            <View style={[styles.intentIcon, { backgroundColor: `${option.color}20` }]}>
                <Ionicons name={option.icon} size={32} color={option.color} />
            </View>
            <Text style={[styles.intentTitle, { color: theme.text }]}>{option.title}</Text>
            <Text style={[styles.intentSubtitle, { color: theme.textSecondary }]}>
                {option.subtitle}
            </Text>
            {isSelected && (
                <View style={[styles.checkMark, { backgroundColor: option.color }]}>
                    <Ionicons name="checkmark" size={16} color="#fff" />
                </View>
            )}
        </TouchableOpacity>
    );
};

const QuickStartDialog = ({
    visible,
    onClose,
    onOpenChat,
    onOpenQuickChat,
    onOpenPresentation,
    onOpenPrintable,
    onOpenReport,
    onOpenDiagram,
    onOpenPages,
    onCreateDefaultVault,
    theme,
    userName,
}) => {
    // Reset selected intent when dialog opens
    const [selectedIntent, setSelectedIntent] = useState(null);
    const [vaultCreated, setVaultCreated] = useState(false);

    // Dynamic dimensions for responsiveness
    const [dimensions, setDimensions] = useState(() => Dimensions.get('window'));
    useEffect(() => {
        const sub = Dimensions.addEventListener('change', ({ window }) => setDimensions(window));
        return () => sub?.remove();
    }, []);
    const isMobile = dimensions.width < 768;
    const cardWidth = isMobile ? (dimensions.width - 80) / 2 : 140;

    useEffect(() => {
        if (visible) {
            setSelectedIntent(null);
            // Create default vault for new user when dialog opens
            if (onCreateDefaultVault && !vaultCreated) {
                onCreateDefaultVault().then(() => {
                    setVaultCreated(true);
                }).catch(err => {
                    console.error('[QuickStart] Vault creation error:', err);
                });
            }
        }
    }, [visible]);

    // Navigate to the selected output type directly
    const handleIntentSelect = (intent) => {
        setSelectedIntent(intent);
        // Small delay to show selection animation before navigating
        setTimeout(() => {
            onClose();
            switch (intent) {
                case 'chat':
                    onOpenChat?.();
                    break;
                case 'quickchat':
                    onOpenQuickChat?.();
                    break;
                case 'presentation':
                    onOpenPresentation?.();
                    break;
                case 'printable':
                    onOpenPrintable?.();
                    break;
                case 'report':
                    onOpenReport?.();
                    break;
                case 'diagram':
                    onOpenDiagram?.();
                    break;
                case 'pages':
                    onOpenPages?.();
                    break;
            }
        }, 150);
    };

    const safeTheme = theme || {
        background: '#FFFFFF',
        text: '#1F2937',
        textSecondary: '#6B7280',
        surface: '#F9FAFB',
        borderColor: '#E5E7EB',
        primary: '#6366F1',
    };

    return (
        <Modal
            visible={visible}
            animationType="fade"
            transparent={true}
            statusBarTranslucent={true}
            onRequestClose={onClose}
        >
            <View style={[styles.overlay, isMobile && styles.overlayMobile]}>
                <Animated.View
                    entering={FadeIn.duration(200)}
                    style={[
                        styles.dialogContainer,
                        { backgroundColor: safeTheme.background },
                        isMobile && {
                            maxWidth: '100%',
                            width: '100%',
                            maxHeight: dimensions.height,
                            borderRadius: 0,
                        },
                    ]}
                >
                    {/* Header */}
                    <View style={styles.header}>
                        <View style={styles.headerLeft} />
                        <Text style={[styles.headerTitle, { color: safeTheme.text }]}>
                            Welcome! 🎉
                        </Text>
                        <TouchableOpacity onPress={onClose} style={styles.closeButton}>
                            <Ionicons name="close" size={24} color={safeTheme.textSecondary} />
                        </TouchableOpacity>
                    </View>

                    {/* Scrollable Content */}
                    <ScrollView
                        style={styles.scrollContent}
                        showsVerticalScrollIndicator={false}
                        contentContainerStyle={styles.scrollContentContainer}
                    >
                        <Text style={[styles.stepTitle, { color: safeTheme.text }]}>
                            Your Vault is Ready.
                        </Text>
                        <Text style={[styles.stepSubtitle, { color: safeTheme.textSecondary }]}>
                            What would you like to create? You'll add project files in the next step.
                        </Text>

                        <View style={[styles.intentGrid, isMobile && { gap: 10 }]}>
                            {INTENT_OPTIONS.map((option) => (
                                <IntentCard
                                    key={option.id}
                                    option={option}
                                    isSelected={selectedIntent === option.id}
                                    onSelect={handleIntentSelect}
                                    theme={safeTheme}
                                    cardWidth={cardWidth}
                                />
                            ))}
                        </View>

                        {/* Explore More */}
                        <View style={styles.exploreSection}>
                            <Text style={[styles.exploreSectionTitle, { color: safeTheme.text }]}>
                                💡 Keep Exploring
                            </Text>
                            <Text style={[styles.exploreSectionText, { color: safeTheme.textSecondary }]}>
                                Chat with your Vault, research using Mindmaps and Knowledge Graphs, and keep creating new designs!
                            </Text>
                        </View>
                    </ScrollView>
                </Animated.View>
            </View>
        </Modal>
    );
};

const styles = StyleSheet.create({
    overlay: {
        flex: 1,
        backgroundColor: 'rgba(0, 0, 0, 0.5)',
        justifyContent: 'center',
        alignItems: 'center',
        padding: 20,
    },
    overlayMobile: {
        padding: 0,
        justifyContent: 'flex-end',
    },
    dialogContainer: {
        width: '100%',
        maxWidth: 500,
        maxHeight: '85%',
        borderRadius: 20,
        overflow: 'hidden',
        ...Platform.select({
            web: {
                boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.25)',
            },
            default: {
                shadowColor: '#000',
                shadowOffset: { width: 0, height: 10 },
                shadowOpacity: 0.25,
                shadowRadius: 25,
                elevation: 10,
            },
        }),
    },
    header: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
        paddingHorizontal: 20,
        paddingVertical: 16,
        borderBottomWidth: 1,
        borderBottomColor: '#E5E7EB',
    },
    headerLeft: {
        width: 40,
    },
    headerTitle: {
        fontSize: 18,
        fontWeight: '600',
        flex: 1,
        textAlign: 'center',
    },
    backButton: {
        padding: 4,
    },
    closeButton: {
        padding: 4,
    },
    content: {
        padding: 24,
    },
    stepTitle: {
        fontSize: 22,
        fontWeight: '700',
        textAlign: 'center',
        marginBottom: 8,
    },
    stepSubtitle: {
        fontSize: 14,
        textAlign: 'center',
        marginBottom: 20,
        lineHeight: 20,
    },
    // Welcome Banner Styles
    welcomeBanner: {
        flexDirection: 'row',
        alignItems: 'center',
        backgroundColor: '#6366F1',
        padding: 16,
        borderRadius: 16,
        marginBottom: 24,
        gap: 12,
        ...Platform.select({
            web: {
                backgroundImage: 'linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%)',
            },
        }),
    },
    welcomeBannerIcon: {
        width: 48,
        height: 48,
        borderRadius: 24,
        backgroundColor: 'rgba(255, 255, 255, 0.2)',
        alignItems: 'center',
        justifyContent: 'center',
    },
    welcomeBannerContent: {
        flex: 1,
    },
    welcomeBannerTitle: {
        fontSize: 18,
        fontWeight: 'bold',
        color: '#FFFFFF',
        marginBottom: 2,
    },
    welcomeBannerSubtitle: {
        fontSize: 13,
        color: 'rgba(255, 255, 255, 0.9)',
    },
    welcomeBannerBadge: {
        width: 28,
        height: 28,
        borderRadius: 14,
        backgroundColor: '#FFFFFF',
        alignItems: 'center',
        justifyContent: 'center',
    },
    referralNudge: {
        flexDirection: 'row',
        alignItems: 'center',
        gap: 8,
        backgroundColor: 'rgba(99, 102, 241, 0.08)',
        paddingVertical: 10,
        paddingHorizontal: 14,
        borderRadius: 10,
        marginBottom: 20,
        marginTop: -16,
    },
    referralNudgeText: {
        fontSize: 13,
        fontWeight: '500',
        color: '#6366F1',
        flex: 1,
    },
    tipText: {
        fontSize: 13,
        textAlign: 'center',
        lineHeight: 18,
        fontStyle: 'italic',
    },
    // ScrollView Styles
    scrollContent: {
        flex: 1,
    },
    scrollContentContainer: {
        padding: 24,
        paddingBottom: 32,
    },
    // Workspace Info Card Styles
    workspaceInfoCard: {
        backgroundColor: 'rgba(139, 92, 246, 0.08)',
        borderRadius: 12,
        padding: 16,
        marginBottom: 16,
        borderLeftWidth: 3,
        borderLeftColor: '#8B5CF6',
    },
    workspaceInfoHeader: {
        flexDirection: 'row',
        alignItems: 'center',
        gap: 8,
        marginBottom: 8,
    },
    workspaceInfoTitle: {
        fontSize: 15,
        fontWeight: '600',
    },
    workspaceInfoText: {
        fontSize: 13,
        lineHeight: 20,
        marginBottom: 12,
    },
    workspaceActions: {
        flexDirection: 'row',
        gap: 12,
    },
    workspaceActionButton: {
        flexDirection: 'row',
        alignItems: 'center',
        gap: 6,
        paddingVertical: 8,
        paddingHorizontal: 12,
        borderRadius: 8,
        backgroundColor: 'rgba(139, 92, 246, 0.1)',
    },
    workspaceActionText: {
        fontSize: 13,
        fontWeight: '600',
        color: '#8B5CF6',
    },
    // Vault Info Card Styles
    vaultInfoCard: {
        backgroundColor: 'rgba(245, 158, 11, 0.08)',
        borderRadius: 12,
        padding: 16,
        marginBottom: 20,
        borderLeftWidth: 3,
        borderLeftColor: '#F59E0B',
    },
    vaultInfoHeader: {
        flexDirection: 'row',
        alignItems: 'center',
        gap: 8,
        marginBottom: 8,
    },
    vaultInfoTitle: {
        fontSize: 15,
        fontWeight: '600',
    },
    vaultInfoText: {
        fontSize: 13,
        lineHeight: 20,
    },
    // Explore Section Styles
    exploreSection: {
        backgroundColor: 'rgba(99, 102, 241, 0.06)',
        borderRadius: 12,
        padding: 16,
        marginTop: 8,
    },
    exploreSectionTitle: {
        fontSize: 14,
        fontWeight: '600',
        marginBottom: 6,
    },
    exploreSectionText: {
        fontSize: 13,
        lineHeight: 19,
    },
    intentGrid: {
        flexDirection: 'row',
        flexWrap: 'wrap',
        justifyContent: 'center',
        gap: 12,
        marginBottom: 24,
    },
    intentCard: {
        padding: 16,
        borderRadius: 16,
        alignItems: 'center',
        position: 'relative',
    },
    intentCardWeb: {
        cursor: 'pointer',
        transition: 'transform 0.15s, box-shadow 0.15s',
    },
    intentIcon: {
        width: 60,
        height: 60,
        borderRadius: 16,
        alignItems: 'center',
        justifyContent: 'center',
        marginBottom: 12,
    },
    intentTitle: {
        fontSize: 15,
        fontWeight: '600',
        marginBottom: 4,
        textAlign: 'center',
    },
    intentSubtitle: {
        fontSize: 11,
        textAlign: 'center',
        lineHeight: 14,
    },
    checkMark: {
        position: 'absolute',
        top: 8,
        right: 8,
        width: 24,
        height: 24,
        borderRadius: 12,
        alignItems: 'center',
        justifyContent: 'center',
    },
    primaryButton: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'center',
        paddingVertical: 14,
        paddingHorizontal: 28,
        borderRadius: 12,
        gap: 8,
    },
    primaryButtonText: {
        fontSize: 16,
        fontWeight: '600',
    },
    dataChoiceCard: {
        padding: 20,
        borderRadius: 16,
        borderWidth: 2,
        marginBottom: 16,
    },
    recommendedCard: {
        backgroundColor: 'rgba(16, 185, 129, 0.05)',
    },
    dataChoiceHeader: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: 12,
    },
    dataChoiceIcon: {
        width: 52,
        height: 52,
        borderRadius: 14,
        alignItems: 'center',
        justifyContent: 'center',
    },
    recommendedBadge: {
        flexDirection: 'row',
        alignItems: 'center',
        backgroundColor: '#10B981',
        paddingHorizontal: 10,
        paddingVertical: 4,
        borderRadius: 12,
        gap: 4,
    },
    recommendedText: {
        color: '#fff',
        fontSize: 11,
        fontWeight: '600',
    },
    dataChoiceTitle: {
        fontSize: 17,
        fontWeight: '600',
        marginBottom: 6,
    },
    dataChoiceDesc: {
        fontSize: 13,
        lineHeight: 18,
    },
    loader: {
        marginTop: 12,
    },
    progressDots: {
        flexDirection: 'row',
        justifyContent: 'center',
        gap: 8,
        paddingBottom: 20,
    },
    dot: {
        width: 8,
        height: 8,
        borderRadius: 4,
    },
    dotActive: {
        width: 24,
    },
});

export default QuickStartDialog;
