// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

// HomePanel.js - Home Dashboard with Feature Cards
// Renders in place of Chat UI when in Home mode

import React, { useRef, useState, useMemo, useEffect } from 'react';
import {
    View,
    Text,
    TouchableOpacity,
    ScrollView,
    StyleSheet,
    Platform,
    Modal,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { authService } from '../services/authService';
import { useUser } from './UserProvider';
import PWAInstallPrompt from './PWAInstallPrompt';
import OperationsControlScreen from '../screens/OperationsControlScreen';
import ScreeningHealthScreen from '../screens/ScreeningHealthScreen';
import SuccessRateScreen from '../screens/SuccessRateScreen';
import LearningBatchScreen from '../screens/LearningBatchScreen';
import MoneyImpactScreen from '../screens/MoneyImpactScreen';
import SmartAppService from '../services/SmartAppService';
import {
    GROUPS,
    CITRA_THESIS,
    capabilitiesInGroup,
} from '../config/operationsCapabilities';


// Banner shown at the top of HomePanel when the signed-in user is missing
// their Personal SA and/or Work SA. Without these, every create / save
// path (skills, smart-apps, workflows, presentations, printables, reports,
// diagrams) will be rejected by the backend. The user is told to refresh
// (re-login) or, if that doesn't fix it, contact their admin.
const ServiceAccountWarningBanner = ({ theme }) => {
    // Read SA ids from the live JWT in UserContext. We can't memo with [] —
    // on cold start authService.getCurrentUser() returns null because the
    // token hasn't hydrated from AsyncStorage yet; an empty-deps memo would
    // then cache hasWork=false forever and the banner would stick. Re-deriving
    // from authToken every time it changes is cheap (one base64 + JSON parse).
    const { authToken, isAuthenticated } = useUser();
    const { hasPersonal, hasWork } = useMemo(() => {
        const u = authService.getCurrentUser?.() || {};
        return {
            hasPersonal: !!u.personal_sa_id,
            hasWork: !!u.work_sa_id,
        };
    }, [authToken, isAuthenticated]);

    // Until auth is ready, don't show a "missing SA" warning — the JWT
    // simply hasn't loaded yet.
    if (!isAuthenticated || !authToken) return null;

    if (hasPersonal && hasWork) return null;

    const missing = [];
    if (!hasPersonal) missing.push('Personal SA (presentations / printables / reports / diagrams)');
    if (!hasWork)     missing.push('Work SA (skills / smart-apps / workflows)');

    return (
        <View
            style={{
                marginHorizontal: 16,
                marginTop: 12,
                marginBottom: 4,
                padding: 12,
                borderRadius: 10,
                borderWidth: 1,
                borderColor: '#F59E0B',
                backgroundColor: '#FFFBEB',
                flexDirection: 'row',
                alignItems: 'flex-start',
            }}
        >
            <Ionicons name="alert-circle" size={20} color="#B45309" style={{ marginRight: 10, marginTop: 1 }} />
            <View style={{ flex: 1 }}>
                <Text style={{ color: '#92400E', fontSize: 13, fontWeight: '600', marginBottom: 4 }}>
                    Service Accounts not provisioned
                </Text>
                <Text style={{ color: '#92400E', fontSize: 12, lineHeight: 18 }}>
                    Your user is missing: {missing.join(' · ')}.
                    {'\n\n'}
                    Without these, you cannot create or save these resources — every attempt will be rejected.
                    {'\n'}
                    Try signing out and signing back in to refresh. If it persists, ask an org admin to open
                    your user record and click <Text style={{ fontWeight: '600' }}>Fix Service Accounts</Text>.
                </Text>
            </View>
        </View>
    );
};

// Banner shown at the top of HomePanel when the current session is an
// impersonation (super_admin "Login as" flow). Reads from UserProvider's
// impersonationContext which is derived from the `act` claim on the JWT
// — single source of truth, can't drift from a stale flag.
//
// Action is "Logout" (not "End session" / "Stop impersonating") because
// in the clean impersonation model the admin's original session is NOT
// preserved; returning requires a fresh sign-in. This eliminates the
// dual-source-of-truth bugs around impersonate-in / end-impersonation
// and removes the re-elevation surface that an in-browser stash created.
const ImpersonationBanner = () => {
    const { impersonationContext, clearAuthenticationState } = useUser();
    const [ending, setEnding] = useState(false);

    if (!impersonationContext) return null;

    const { actor, target, expires_at } = impersonationContext;
    const expiresLabel = (() => {
        if (!expires_at) return '';
        const remainingMs = new Date(expires_at).getTime() - Date.now();
        if (remainingMs <= 0) return 'expired';
        const mins = Math.floor(remainingMs / 60000);
        return mins >= 60 ? `~${Math.floor(mins / 60)}h${mins % 60}m left` : `${mins}m left`;
    })();

    const handleEnd = async () => {
        setEnding(true);
        try {
            await clearAuthenticationState();
        } finally {
            // Reload so every component bootstraps from a clean, signed-out
            // state and routes to the login screen. Same rationale as the
            // reload after impersonate-in.
            if (typeof window !== 'undefined' && window.location && window.location.reload) {
                setTimeout(() => window.location.reload(), 50);
            } else {
                setEnding(false);
            }
        }
    };

    return (
        <View
            style={{
                marginHorizontal: 16,
                marginTop: 12,
                marginBottom: 4,
                padding: 12,
                borderRadius: 10,
                borderWidth: 1,
                borderColor: '#F59E0B',
                backgroundColor: '#FEF3C7',
                flexDirection: 'row',
                alignItems: 'center',
            }}
        >
            <Ionicons name="person-circle" size={22} color="#B45309" style={{ marginRight: 10 }} />
            <View style={{ flex: 1 }}>
                <Text style={{ color: '#92400E', fontSize: 13, fontWeight: '600' }}>
                    Impersonating {target || '(unknown)'}
                </Text>
                <Text style={{ color: '#92400E', fontSize: 12, marginTop: 2 }}>
                    Acting as the user above · audited as {actor}
                    {expiresLabel ? ` · ${expiresLabel}` : ''}
                </Text>
            </View>
            <TouchableOpacity
                onPress={handleEnd}
                disabled={ending}
                style={{
                    paddingHorizontal: 12,
                    paddingVertical: 6,
                    borderRadius: 6,
                    backgroundColor: '#B45309',
                    opacity: ending ? 0.6 : 1,
                }}
            >
                <Text style={{ color: '#fff', fontSize: 12, fontWeight: '600' }}>
                    {ending ? 'Logging out…' : 'Logout'}
                </Text>
            </TouchableOpacity>
        </View>
    );
};


// Non-admin automation halt banner. Any user sees this when automation is
// frozen for their org/dept (global/org/dept scope), so a BA who can't reach
// the admin console still knows why their apps aren't running. Public status
// endpoint (/automation-status) — no role needed.
const AutomationHaltBanner = () => {
    const [halt, setHalt] = useState(null);
    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const r = await SmartAppService.automationStatus();
                if (!cancelled) setHalt(r && r.halted ? r : null);
            } catch { /* service down / not reachable — no banner */ }
        })();
        return () => { cancelled = true; };
    }, []);
    if (!halt) return null;
    const scopeLabel = halt.scope === 'global' ? 'Deployment-wide'
        : halt.scope === 'org' ? 'Organization'
        : halt.scope === 'dept' ? 'Department' : 'Automation';
    return (
        <View style={{ marginHorizontal: 16, marginTop: 12, marginBottom: 4, padding: 12, borderRadius: 10, borderWidth: 1, borderColor: '#FCA5A5', backgroundColor: '#FEF2F2', flexDirection: 'row', alignItems: 'center' }}>
            <Ionicons name="warning" size={20} color="#B91C1C" style={{ marginRight: 10 }} />
            <View style={{ flex: 1 }}>
                <Text style={{ color: '#B91C1C', fontSize: 13, fontWeight: '700' }}>{scopeLabel} automation is HALTED</Text>
                <Text style={{ color: '#991B1B', fontSize: 12, marginTop: 2 }}>
                    Runs, autonomous writes, approvals and triggers are frozen{halt.actor ? ` (by ${halt.actor})` : ''}{halt.reason ? ` · ${halt.reason}` : ''}. Reads still work.
                </Text>
            </View>
        </View>
    );
};

// Feature Card Component - Premium Styled
const FeatureCard = ({ icon, title, subtitle, color, onPress, size = 'medium', tourId, gradientColors, badge, badgeLabel }) => {
    const isLarge = size === 'large';
    const isSmall = size === 'small';
    const textColor = gradientColors ? '#FFFFFF' : '#1F2937';
    const subtitleColor = gradientColors ? 'rgba(255, 255, 255, 0.85)' : '#6B7280';
    const iconColor = gradientColors ? '#FFFFFF' : color;
    const iconBg = gradientColors ? 'rgba(255, 255, 255, 0.25)' : color + '18';

    const minWidth = isLarge ? 180 : isSmall ? 95 : 150;
    const minHeight = isLarge ? 140 : isSmall ? 72 : 120;
    const iconSize = isLarge ? 32 : isSmall ? 20 : 28;

    return (
        <TouchableOpacity
            style={[
                styles.featureCard,
                {
                    backgroundColor: gradientColors ? 'transparent' : '#FFFFFF',
                    borderColor: gradientColors ? 'transparent' : color + '25',
                    minWidth,
                    minHeight,
                    overflow: 'hidden',
                    borderWidth: gradientColors ? 0 : 1,
                },
                isSmall && styles.featureCardSmall,
            ]}
            onPress={onPress}
            activeOpacity={0.85}
            {...(Platform.OS === 'web' && tourId ? { dataSet: { tour: tourId } } : {})}
        >
            {gradientColors && (
                <LinearGradient
                    colors={gradientColors}
                    start={{ x: 0, y: 0 }}
                    end={{ x: 1, y: 1 }}
                    style={StyleSheet.absoluteFill}
                />
            )}
            {/* Glassmorphism overlay for gradient cards */}
            {gradientColors && (
                <View style={{
                    ...StyleSheet.absoluteFillObject,
                    backgroundColor: 'rgba(255,255,255,0.08)',
                    zIndex: 0,
                }} />
            )}
            <View style={[
                styles.cardIconContainer,
                {
                    backgroundColor: iconBg,
                    zIndex: 1,
                    borderWidth: gradientColors ? 1 : 0,
                    borderColor: 'rgba(255,255,255,0.2)',
                },
                isSmall && styles.cardIconContainerSmall,
            ]}>
                <Ionicons name={icon} size={iconSize} color={iconColor} />
            </View>
            {/* Attention badge — a COUNT OF THINGS WAITING ON A PERSON, never
                a decoration. Rendered only for a positive number: an unknown
                count (the server could not read the store) must leave the card
                dark rather than imply an all-clear. */}
            {typeof badge === 'number' && badge > 0 && (
                <View
                    style={styles.cardBadge}
                    accessibilityLabel={badgeLabel || `${badge} items need attention`}
                >
                    <Text style={styles.cardBadgeText}>{badge > 99 ? '99+' : badge}</Text>
                </View>
            )}
            <Text style={[styles.cardTitle, isSmall && styles.cardTitleSmall, { color: textColor, zIndex: 1 }]}>{title}</Text>
            {subtitle && (
                <Text style={[styles.cardSubtitle, isSmall && styles.cardSubtitleSmall, { color: subtitleColor, zIndex: 1 }]}>{subtitle}</Text>
            )}
        </TouchableOpacity>
    );
};



// Category Section Component - Enhanced Premium
const CategorySection = ({ title, subtitle, icon, iconColor, children, tourId }) => {
    // Extract emoji and title for backwards compatibility
    const hasEmoji = title.match(/^[\p{Emoji}]/u);
    const displayTitle = hasEmoji ? title.slice(2).trim() : title;
    const accentColor = iconColor || '#3B82F6';

    return (
        <View style={styles.categorySection} {...(Platform.OS === 'web' && tourId ? { dataSet: { tour: tourId } } : {})}>
            <View style={styles.categoryHeaderContainer}>
                <View style={[styles.categoryAccent, { backgroundColor: accentColor }]} />
                <View style={{ flex: 1 }}>
                    <Text style={styles.categoryTitle}>{title}</Text>
                    {subtitle && (
                        <Text style={styles.categorySubtitle}>{subtitle}</Text>
                    )}
                </View>
            </View>
            <View style={styles.cardRow}>
                {children}
            </View>
        </View>
    );
};


const HomePanel = ({
    theme,
    // NOTE: the Operations Presentation / Visual Reports / Doc Creator surfaces
    // were REMOVED from the product 2026-08-08 (Phase 0 OSS split), along with
    // Reader & Review, Knowledge Graph, Mindmap, Diagram, Pages and Project
    // Management. Their capability entries, components and services are deleted
    // — not hidden. See docs/open-source-release-plan.md.
    onOpenChat,
    onOpenProjectManagement,
    onOpenPages,
    onVideoRecording,
    onOpenCredits,
    onOpenSupport,
    onOpenDeptSources,
    onOpenPowerApps,
    onOpenDecisionApps,
    onOpenDashboards,
    onOpenAdminUsers,
    onOpenMemory,
    onOpenDeptLibrary,
    onOpenDepartures,
    onOpenAdminResources,
    onOpenImpersonateUser,
    userType,
    isAdmin,
    isSuperAdmin,
    // Who may BUILD Decision Apps — gates the pink flagship builder card. Every
    // other user sees only the consumer list cards (My Decision Apps / My
    // Dashboards). Mirrors the backend /build gate.
    canBuildApps,
}) => {
    const safeTheme = theme || {
        background: '#FFFFFF',
        text: '#1F2937',
        textSecondary: '#6B7280',
        primary: '#3B82F6',
        surface: '#F9FAFB',
    };

    // Paid feature modal state
    const [showPaidFeatureModal, setShowPaidFeatureModal] = useState(false);
    const [paidFeatureTitle, setPaidFeatureTitle] = useState('');

    // Automation kill-switch modal (scoped halt) — same component the Decision
    // Apps console uses; opened from the Admin section here.
    const [showHaltModal, setShowHaltModal] = useState(false);
    // Workflow Automation Control console (IT / workflow engine).
    const [showScreeningHealth, setShowScreeningHealth] = useState(false);
    const [showSuccessRate, setShowSuccessRate] = useState(false);
    const [showLearningBatch, setShowLearningBatch] = useState(false);
    // One-line proof the learned memory is worth something (plan §19.2). The
    // lift is SUPPRESSED server-side when either cohort is under-powered, so
    // this renders the asset size alone rather than an unearned percentage —
    // this is the card someone screenshots.
    const [memoryImpact, setMemoryImpact] = useState(null);

    useEffect(() => {
        let cancelled = false;
        SmartAppService.orgMemoryImpact()
            .then((d) => { if (!cancelled) setMemoryImpact(d); })
            .catch(() => { /* card falls back to its static subtitle */ });
        return () => { cancelled = true; };
    }, []);

    // Judgements STUCK until a supervisor rules on them (SOP conflicts and
    // officer disagreements). They render nowhere else in the app, so without
    // this count nobody learns they are waiting. `null` = the server could not
    // read the store — that must stay dark, never render as an all-clear.
    const memoryAttention = React.useMemo(() => {
        const n = memoryImpact?.needs_attention;
        return typeof n === 'number' && n > 0 ? n : undefined;
    }, [memoryImpact]);

    // Plain words, no jargon, and never a number the server refused to publish.
    const memorySubtitle = React.useMemo(() => {
        if (!memoryImpact) return 'Judgements & past decisions';
        const judgements = memoryImpact.clauses_active || 0;
        const corrections = memoryImpact.corrections || 0;
        // The waiting decision outranks the asset size — lead with it.
        if (memoryAttention) {
            const where = (memoryImpact.attention_apps || []).length;
            return `${memoryAttention} awaiting your call`
                + (where > 1 ? ` across ${where} apps` : '');
        }
        if (!judgements && !corrections) return 'Nothing learned yet';
        const base = `${judgements} judgement${judgements === 1 ? '' : 's'} from ${corrections} correction${corrections === 1 ? '' : 's'}`;
        const lift = memoryImpact.lift;
        if (lift === null || lift === undefined) return base;
        const pct = Math.round(lift * 100);
        // A negative lift is shown, not hidden — if the judgements are hurting,
        // that is the single most important thing on this screen.
        return `${base} · ${pct >= 0 ? '+' : ''}${pct}% accepted when they apply`;
    }, [memoryImpact, memoryAttention]);
    const [showMoneyImpact, setShowMoneyImpact] = useState(false);

    // Action Chat tier picker removed 2026-08-08 (Phase 0 OSS split) —
    // Operations Analytics left the product.

    const handlePaidFeaturePress = (featureName) => {
        if (userType !== 'paid') {
            setPaidFeatureTitle(featureName);
            setShowPaidFeatureModal(true);
            return true;
        }
        return false;
    };

    // Impersonation-aware hero. When a super_admin is "Logged in as" a
    // user from another org, the hero greets them with that org + the
    // dept they're acting in, so it's obvious which tenant context every
    // action will land in. Reads from useUser() — same source of truth
    // as the ImpersonationBanner above.
    const { user, impersonationContext } = useUser();
    const impersonationHero = useMemo(() => {
        if (!impersonationContext || !user) return null;
        const formatId = (raw) =>
            String(raw || '')
                .split(/[-_]/)
                .filter(Boolean)
                .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
                .join(' ');
        const orgDisplay = formatId(user.org_id) || user.org_id || '(unknown org)';
        const deptIds = Array.isArray(user.dept_ids) ? user.dept_ids : [];
        const deptDisplay = deptIds.length
            ? deptIds.map(formatId).join(' · ')
            : null;
        return { orgDisplay, deptDisplay };
    }, [user, impersonationContext]);

    // Maps a capability's `handlerKey` (from the shared operationsCapabilities
    // config) to the live handler on this component.
    const capabilityHandlers = {
        onOpenPowerApps,
        onOpenDecisionApps,
        onOpenDashboards,
        onOpenChat,
        onOpenDeptSources,
    };

    // A single calm hue for the entire supporting suite. Enterprise buyers read
    // restraint as trust, so only the flagship (Decision Apps) carries a bold
    // accent gradient; every other tile renders in this one uniform tone with no
    // gradient and at a smaller size, making the hierarchy obvious at a glance.
    const SUITE_HUE = '#6366F1';

    // Renders one capability card from the shared config so HomePanel and
    // IntroScreen can never drift on names / icons / colors. Returns null when a
    // capability is gated off for this user (e.g. workflow access).
    const renderCapabilityCard = (cap) => {
        // Builder-gated (the pink flagship): only admins / decision-app-builder
        // see the BUILD entry. Consumers get the list cards instead.
        if (cap.gate === 'builder' && !canBuildApps) return null;
        const isFlagship = !!cap.flagship;
        return (
            <FeatureCard
                key={cap.key}
                icon={cap.iconOutline}
                title={cap.homeTitle || cap.title}
                subtitle={cap.tagline}
                color={isFlagship ? cap.color : SUITE_HUE}
                size={isFlagship ? 'large' : 'medium'}
                onPress={capabilityHandlers[cap.handlerKey]}
                tourId={cap.tourId}
                gradientColors={isFlagship ? cap.gradient : null}
            />
        );
    };

    // Cards for a group, with `hidden` capabilities and role-gated nulls
    // already dropped. Sections render only when this comes back non-empty —
    // hiding every card in a group (as with presentations / visual reports /
    // doc creator) must hide the section heading with them, not leave an empty
    // titled block behind.
    const homeCardsForGroup = (group) =>
        capabilitiesInGroup(group)
            .map((cap) => renderCapabilityCard(cap))
            .filter(Boolean);

    const operationsCards = homeCardsForGroup(GROUPS.OPERATIONS);
    const workAreaCards = homeCardsForGroup(GROUPS.WORK_AREAS);
    // Operational Data Flow Audit. Rendered inside the Admin section — the
    // "Runs on your data" section that used to hold it was removed, and audit
    // tooling belongs with the rest of the governance chrome.
    const dataFoundationCards = homeCardsForGroup(GROUPS.DATA_FOUNDATION);
    // Read-only SOP Library entry for dept MEMBERS. Admins get the managed
    // version inside the Admin section instead.
    const showMemberSopLibrary =
        !isAdmin && Array.isArray(user?.dept_ids) && user.dept_ids.length > 0;

    return (
        <>
            <ScrollView
                style={[styles.container, { backgroundColor: safeTheme.background }]}
                contentContainerStyle={styles.contentContainer}
                showsVerticalScrollIndicator={false}
            >
                {/* SA-provisioning warning — only renders when SAs are missing. */}
                <ServiceAccountWarningBanner theme={safeTheme} />

                {/* Impersonation banner — only renders when the JWT carries
                    an `act` claim (super_admin "Login as" flow). */}
                <ImpersonationBanner />

                {/* Automation halt banner — front-and-centre for EVERY user when
                    automation is frozen for their org/dept. */}
                <AutomationHaltBanner />

                {/* Hero Section - Restored and Simplified.
                    When impersonating, swap in the impersonated org's name +
                    dept so the super_admin sees at a glance which tenant
                    context every action will land in. */}
                <View style={[styles.heroSection, { marginTop: 16 }]}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', width: '100%', paddingHorizontal: 16 }}>
                        <View style={{ flex: 1 }} />
                        <Text style={[styles.heroTitle, { color: safeTheme.text, fontSize: 24, lineHeight: 32 }]}>
                            {impersonationHero
                                ? `Welcome to ${impersonationHero.orgDisplay}`
                                : 'Welcome To Citra Enterprise'}
                        </Text>
                        <View style={{ flex: 1 }} />
                    </View>
                    {impersonationHero && impersonationHero.deptDisplay && (
                        <Text
                            style={{
                                textAlign: 'center',
                                marginTop: 6,
                                fontSize: 14,
                                fontWeight: '500',
                                color: '#B45309',
                            }}
                        >
                            Department: {impersonationHero.deptDisplay}
                        </Text>
                    )}
                    <Text style={[styles.heroTagline, { color: safeTheme.textSecondary }]}>
                        {impersonationHero
                            ? `Acting on behalf of a ${impersonationHero.deptDisplay ? impersonationHero.deptDisplay + ' ' : ''}user in ${impersonationHero.orgDisplay}. Every action here is audited and scoped to this user's permissions.`
                            : CITRA_THESIS}
                    </Text>
                </View>

                {/* 1. RUN HIGH-STAKES OPERATIONS — the spine: Decision Apps & API
                    (BA-authored, memory-backed agent judgment on complex,
                    high-stakes cases). The volume counterpart (Operations
                    Workflow) has its own section below. Driven by the shared
                    operationsCapabilities config so names/colors match IntroScreen. */}
                {operationsCards.length > 0 && (
                    <CategorySection
                        title="🚀 Run your high-stakes, complex operations"
                        subtitle="For the decisions where real money rides on every call — turn a stuck, repeated decision into a memory-backed Decision App or API: an agent recommends the call with evidence, acts within your policy gate, and gets sharper with every outcome. Governed and audited end to end, authored by your business team in plain English."
                        tourId="productivity-section"
                    >
                        {operationsCards}
                    </CategorySection>
                )}

                {/* 2. OPERATIONS CHAT — enterprise chat over your operational
                    data. This section no longer uses WORK_AREAS_FRAME ("The same
                    engine, wherever operations needs it" / "…runs the chat,
                    analytics and live views around them"): that framing described
                    a four-pillar breadth that no longer exists here. Analytics is
                    hidden (service parked) and Presentation / Visual Reports / Doc
                    Creator were removed, so the section is enterprise chat plus —
                    for dept members — the SOP Library. The heading now says that.
                    WORK_AREAS_FRAME is untouched; IntroScreen still uses it. */}
                {(workAreaCards.length > 0 || showMemberSopLibrary) && (
                    <CategorySection
                        title="💬 Ask your operational data"
                        tourId="work-areas-section"
                    >
                        {workAreaCards}
                        {/* SOP Library — READ entry for dept MEMBERS (admins manage
                            it from the Admin section, so this is shown only to
                            non-admins who belong to a department). Dept-owned
                            reference docs the RAG reader draws on; members can
                            browse / view / download. Rehomed here when the
                            "Runs on your data" section was removed — Admin is
                            isAdmin-gated, so it could not go there. */}
                        {showMemberSopLibrary && (
                            <FeatureCard
                                icon="library-outline"
                                title="SOP Library"
                                subtitle="Department documents"
                                color={SUITE_HUE}
                                onPress={onOpenDeptLibrary}
                                tourId="sop-library-read-card"
                            />
                        )}
                    </CategorySection>
                )}

                {/* OPERATIONS WORKFLOW removed 2026-08-08 (OSS split). The
                    workflow engine (citra-workflow + Citra-Worker) left the
                    Decision System and is republished from its own repo. This
                    is a deletion, not a park — there is nothing to uncomment. */}

                {/* VISUALIZE DATA category removed 2026-08-08 (Phase 0 OSS
                    split). Reader & Review, Knowledge Graph, Mindmap and Diagram
                    were deleted from the product along with their services and
                    components — this is no longer a re-enableable block.
                    See docs/open-source-release-plan.md. */}

                {/* 4. ORGANIZE Category — DELETED. It was gated `false` and held
                    the personal knowledge-base cards (open / create / upload).
                    The personal data store is gone, so the cards and their
                    handlers went with it rather than staying wired behind a
                    flag that will never be flipped back on.
                    See docs/open-source-release-plan.md §7.4.7. */}

                {/* 3. DATA FOUNDATION section REMOVED — "🔌 Runs on your data"
                    held a single card (Operational Data Flow Audit), which now
                    lives in the Admin section below where the rest of the
                    governance tooling sits. The SOP Library read-entry it also
                    hosted moved up into the work-areas section, because Admin is
                    isAdmin-gated and that card is for dept MEMBERS. */}

                {/* Tools Category removed — Teams is gone ("no use of this,
                    remove fully"); this section existed only to host the
                    team/workspace card. */}

                {/* Admin Category — only visible to org_admin / dept_admin / super_admin.
                    Role-gated, so a regular Head of Ops never sees this setup chrome.
                    These functions (Manage Users, Departures, Managed Resources, Login
                    as User) live ONLY here — they are not in the sidebar — so this
                    section stays on the landing grid for admins. */}
                {isAdmin && (
                    <CategorySection title="🛡️ Admin" tourId="admin-section">
                        {/* Admin cards use the same calm, white, single-hue treatment
                            as the rest of the grid — no bold gradient fills — so the
                            screen reads as one uniform enterprise surface. */}
                        <FeatureCard
                            icon="people-outline"
                            title="Manage Users"
                            subtitle="Deactivate & handoff"
                            color={SUITE_HUE}
                            onPress={onOpenAdminUsers}
                            tourId="admin-users-card"
                        />
                        <FeatureCard
                            icon="archive-outline"
                            title="Departures"
                            subtitle="Handoff reports"
                            color={SUITE_HUE}
                            onPress={onOpenDepartures}
                            tourId="admin-departures-card"
                        />
                        <FeatureCard
                            icon="layers-outline"
                            title="Managed Resources"
                            subtitle="Decision Apps"
                            color={SUITE_HUE}
                            onPress={onOpenAdminResources}
                            tourId="admin-resources-card"
                        />
                        {/* Operational Data Flow Audit — moved here from the
                            removed "Runs on your data" section. Audit and
                            monitor governed data access (SQL / Mongo / S3 /
                            REST via MCP); it reads as governance tooling, so it
                            sits with the other admin cards. Still config-driven
                            (GROUPS.DATA_FOUNDATION) so its label and icon stay
                            in sync with operationsCapabilities.js. */}
                        {dataFoundationCards}
                        {/* App Memory — the per-app memory asset (learned
                            judgements, reviewed precedents, stats). Curation is
                            role-gated server-side; this entry is admin-only. */}
                        <FeatureCard
                            icon="school-outline"
                            title="App Memory"
                            subtitle={memorySubtitle}
                            color={SUITE_HUE}
                            onPress={onOpenMemory}
                            tourId="admin-memory-card"
                            badge={memoryAttention}
                            badgeLabel={memoryAttention
                                ? `${memoryAttention} judgement(s) awaiting your decision`
                                : undefined}
                        />
                        {/* Learning Batch — the job that folds officer
                            corrections into learned judgements. It replaced a
                            summarizer that ran inside every approve request, so
                            it needs somewhere to be visible. Pausing it stops
                            LEARNING, not operations — never the red button. */}
                        <FeatureCard
                            icon="school-outline"
                            title="Learning Batch"
                            subtitle="Fold officer feedback"
                            color={SUITE_HUE}
                            onPress={() => setShowLearningBatch(true)}
                            tourId="admin-learning-batch-card"
                        />
                        {/* Department SOP Library — dept-owned reference docs
                            for RAG. Admin-only entry; server enforces curation. */}
                        <FeatureCard
                            icon="library-outline"
                            title="SOP Library"
                            subtitle="Department documents"
                            color={SUITE_HUE}
                            onPress={onOpenDeptLibrary}
                            tourId="admin-sop-library-card"
                        />
                        {/* Success Rate — org-wide AI-recommendation adoption:
                            accepted / accepted-with-changes / rejected /
                            pending per app, plus the memory-lift sentence.
                            The wedge's "show the curve" card (adoption plan §1). */}
                        <FeatureCard
                            icon="trending-up-outline"
                            title="Success Rate"
                            subtitle="AI recommendations accepted"
                            color={SUITE_HUE}
                            onPress={() => setShowSuccessRate(true)}
                            tourId="success-rate-card"
                        />
                        {/* Money impact — value recovered / protected through
                            decisions, summed from poller-stamped outcome.value
                            per the ontology's frozen value_semantics
                            (money-saved-roi-plan.md V4). Read-only; the UI
                            never recomputes money. */}
                        <FeatureCard
                            icon="cash-outline"
                            title="Money Impact"
                            subtitle="Value recovered & protected"
                            color={SUITE_HUE}
                            onPress={() => setShowMoneyImpact(true)}
                            tourId="money-impact-card"
                        />
                        {/* Screening Health — how fraud checks perform inside
                            recommendations (confirmed vs false alarms) and the
                            turn-off advisory per noisy check. Read-only
                            aggregation; NOT an alarm queue (fraud is evidence
                            on recommendations, officers decide in-case). */}
                        <FeatureCard
                            icon="shield-checkmark-outline"
                            title="Screening Health"
                            subtitle="Fraud checks & false alarms"
                            color={SUITE_HUE}
                            onPress={() => setShowScreeningHealth(true)}
                            tourId="screening-health-card"
                        />
                        {/* Automation kill switch — scoped halt (org/dept).
                            The prominent red accent keeps this high-stakes
                            control visible, not buried. */}
                        <FeatureCard
                            icon="stop-circle-outline"
                            title="Automation Control"
                            subtitle="Halt runs & writes"
                            color="#DC2626"
                            onPress={() => setShowHaltModal(true)}
                            tourId="automation-control-card"
                        />
                        {isSuperAdmin && (
                            <FeatureCard
                                icon="person-circle-outline"
                                title="Login as User"
                                subtitle="Impersonate (audited)"
                                color={SUITE_HUE}
                                onPress={onOpenImpersonateUser}
                                tourId="admin-impersonate-card"
                            />
                        )}
                    </CategorySection>
                )}

                {/* Payment & Support removed from the landing grid — Credits and
                    Support both live in the left sidebar, so they no longer clutter
                    the operational front door. Handlers (onOpenCredits / onOpenSupport)
                    stay wired via the sidebar and the paid-feature modal. */}

                {/* Bottom Spacing */}
                <View style={{ height: 40 }} />
            </ScrollView>

            {/* PWA Install Prompt - Only shows on web when installable */}
            <PWAInstallPrompt />

            {/* Full-screen automation console — same component as the Decision
                Apps surface (kill switches + schedules). */}
            <OperationsControlScreen
                visible={showHaltModal}
                theme={safeTheme}
                onClose={() => setShowHaltModal(false)}
            />

            {/* Success Rate — org-wide AI-recommendation adoption. */}
            {/* Learning Batch — clause consolidation control. */}
            <LearningBatchScreen
                visible={showLearningBatch}
                theme={safeTheme}
                onClose={() => setShowLearningBatch(false)}
            />

            <SuccessRateScreen
                visible={showSuccessRate}
                theme={safeTheme}
                onClose={() => setShowSuccessRate(false)}
            />

            {/* Money impact — canonical ROI spine rollup. */}
            <MoneyImpactScreen
                visible={showMoneyImpact}
                theme={safeTheme}
                onClose={() => setShowMoneyImpact(false)}
            />

            {/* Screening Health — admin view of fraud-check performance. */}
            <ScreeningHealthScreen
                visible={showScreeningHealth}
                theme={safeTheme}
                onClose={() => setShowScreeningHealth(false)}
            />


            {/* Paid Feature Modal */}
            <Modal
                visible={showPaidFeatureModal}
                transparent={true}
                animationType="fade"
                onRequestClose={() => setShowPaidFeatureModal(false)}
            >
                <View style={styles.modalOverlay}>
                    <View style={styles.modalContainer}>
                        <View style={styles.modalIconCircle}>
                            <Ionicons name="lock-closed" size={32} color="#F59E0B" />
                        </View>
                        <Text style={styles.modalTitle}>Paid Feature</Text>
                        <Text style={styles.modalMessage}>
                            {paidFeatureTitle} is only available for paid users. Purchase credits to unlock this feature.
                        </Text>
                        <View style={styles.modalButtonRow}>
                            <TouchableOpacity
                                style={styles.modalCancelButton}
                                onPress={() => setShowPaidFeatureModal(false)}
                            >
                                <Text style={styles.modalCancelText}>Cancel</Text>
                            </TouchableOpacity>
                            <TouchableOpacity
                                style={styles.modalBuyButton}
                                onPress={() => {
                                    setShowPaidFeatureModal(false);
                                    onOpenCredits?.();
                                }}
                            >
                                <LinearGradient
                                    colors={['#F59E0B', '#EF4444']}
                                    start={{ x: 0, y: 0 }}
                                    end={{ x: 1, y: 0 }}
                                    style={styles.modalBuyGradient}
                                >
                                    <Ionicons name="card-outline" size={18} color="#fff" style={{ marginRight: 6 }} />
                                    <Text style={styles.modalBuyText}>Buy Credits</Text>
                                </LinearGradient>
                            </TouchableOpacity>
                        </View>
                    </View>
                </View>
            </Modal>

        </>
    );
};

const styles = StyleSheet.create({
    container: {
        flex: 1,
    },
    contentContainer: {
        paddingHorizontal: 24,
        paddingBottom: 24,
        paddingTop: 16, // Adjusted for spacing after removing hero
    },
    heroSection: {
        marginBottom: 16,
        alignItems: 'center',
        marginTop: 8,
        paddingBottom: 16,
        borderBottomWidth: 1,
        borderBottomColor: 'rgba(0,0,0,0.06)',
    },
    heroTitle: {
        fontSize: 26,
        fontWeight: '800',
        textAlign: 'center',
        lineHeight: 36,
        letterSpacing: -0.3,
    },
    heroTagline: {
        fontSize: 13,
        lineHeight: 20,
        textAlign: 'center',
        fontStyle: 'italic',
        marginTop: 10,
        paddingHorizontal: 24,
        maxWidth: 820,
        alignSelf: 'center',
    },
    categorySection: {
        marginBottom: 32,
    },
    categoryHeaderContainer: {
        flexDirection: 'row',
        alignItems: 'center',
        marginBottom: 18,
        gap: 10,
    },
    categoryAccent: {
        width: 4,
        height: 24,
        borderRadius: 2,
    },
    categoryTitle: {
        fontSize: 17,
        fontWeight: '700',
        color: '#1F2937',
        letterSpacing: 0.3,
    },
    categorySubtitle: {
        fontSize: 13,
        fontWeight: '500',
        color: '#6B7280',
        marginTop: 2,
        letterSpacing: 0.2,
    },
    cardRow: {
        flexDirection: 'row',
        flexWrap: 'wrap',
        gap: 14,
    },
    featureCard: {
        borderRadius: 18,
        borderWidth: 1,
        padding: 18,
        alignItems: 'center',
        justifyContent: 'center',
        ...Platform.select({
            web: {
                cursor: 'pointer',
                transition: 'transform 0.2s ease, box-shadow 0.25s ease',
                boxShadow: '0 4px 16px rgba(0,0,0,0.08)',
            },
        }),
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 4 },
        shadowOpacity: 0.10,
        shadowRadius: 12,
        elevation: 4,
    },
    cardIconContainer: {
        width: 58,
        height: 58,
        borderRadius: 16,
        alignItems: 'center',
        justifyContent: 'center',
        marginBottom: 14,
    },
    cardTitle: {
        fontSize: 14,
        fontWeight: '700',
        textAlign: 'center',
        marginBottom: 4,
        letterSpacing: 0.2,
    },
    // Amber, not red: these are decisions waiting for a person, not failures.
    cardBadge: {
        position: 'absolute',
        top: 8,
        right: 8,
        minWidth: 20,
        height: 20,
        borderRadius: 10,
        paddingHorizontal: 6,
        backgroundColor: '#D97706',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 2,
    },
    cardBadgeText: {
        color: '#FFFFFF',
        fontSize: 11,
        fontWeight: '700',
        lineHeight: 14,
    },
    cardSubtitle: {
        fontSize: 11,
        textAlign: 'center',
        opacity: 0.85,
    },
    featureCardSmall: {
        padding: 10,
        borderRadius: 12,
    },
    cardIconContainerSmall: {
        width: 36,
        height: 36,
        borderRadius: 10,
        marginBottom: 6,
    },
    cardTitleSmall: {
        fontSize: 11,
        marginBottom: 2,
    },
    cardSubtitleSmall: {
        fontSize: 9,
    },
    // Workspace styles - Premium
    workspaceCard: {
        borderRadius: 18,
        padding: 18,
        marginBottom: 12,
        borderWidth: 1,
        borderColor: 'rgba(59, 130, 246, 0.15)',
        backgroundColor: '#FFFFFF',
        shadowColor: '#3B82F6',
        shadowOffset: { width: 0, height: 4 },
        shadowOpacity: 0.08,
        shadowRadius: 12,
        elevation: 4,
        ...Platform.select({
            web: {
                boxShadow: '0 4px 20px rgba(59, 130, 246, 0.12)',
            },
        }),
    },
    workspaceCardContent: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
    },
    workspaceInfo: {
        flexDirection: 'row',
        alignItems: 'center',
        flex: 1,
    },
    workspaceIcon: {
        width: 46,
        height: 46,
        borderRadius: 14,
        alignItems: 'center',
        justifyContent: 'center',
        marginRight: 14,
        ...Platform.select({
            web: {
                boxShadow: '0 3px 12px rgba(59, 130, 246, 0.25)',
            },
        }),
    },
    workspaceDetails: {
        flex: 1,
    },
    workspaceLabel: {
        fontSize: 11,
        fontWeight: '600',
        letterSpacing: 0.8,
        textTransform: 'uppercase',
        marginBottom: 3,
        opacity: 0.7,
    },
    workspaceName: {
        fontSize: 17,
        fontWeight: '700',
        letterSpacing: 0.2,
    },
    workspaceRole: {
        fontSize: 12,
        marginTop: 2,
    },
    workspaceActions: {
        flexDirection: 'row',
        alignItems: 'center',
        gap: 8,
    },
    invitationBadge: {
        backgroundColor: '#EF4444',
        borderRadius: 12,
        minWidth: 24,
        height: 24,
        alignItems: 'center',
        justifyContent: 'center',
        paddingHorizontal: 8,
    },
    toolsBadge: {
        position: 'absolute',
        top: 8,
        right: 8,
        backgroundColor: '#EF4444',
        borderRadius: 12,
        minWidth: 24,
        height: 24,
        alignItems: 'center',
        justifyContent: 'center',
        paddingHorizontal: 8,
        zIndex: 10,
    },
    invitationBadgeText: {
        color: '#FFFFFF',
        fontSize: 12,
        fontWeight: '600',
    },
    quickWorkspaceSwitcher: {
        flexDirection: 'row',
        alignItems: 'center',
        paddingVertical: 8,
    },
    quickSwitcherLabel: {
        fontSize: 12,
        marginRight: 8,
    },
    quickSwitcherScroll: {
        flex: 1,
    },
    quickSwitchChip: {
        flexDirection: 'row',
        alignItems: 'center',
        paddingHorizontal: 12,
        paddingVertical: 6,
        borderRadius: 20,
        marginRight: 8,
        borderWidth: 1,
        gap: 6,
    },
    quickSwitchText: {
        fontSize: 13,
        fontWeight: '500',
        maxWidth: 100,
    },
    // Paid Feature Modal Styles
    modalOverlay: {
        flex: 1,
        backgroundColor: 'rgba(0, 0, 0, 0.5)',
        justifyContent: 'center',
        alignItems: 'center',
        padding: 24,
    },
    modalContainer: {
        backgroundColor: '#FFFFFF',
        borderRadius: 20,
        padding: 28,
        width: '100%',
        maxWidth: 400,
        alignItems: 'center',
        ...Platform.select({
            web: {
                boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
            },
            default: {
                elevation: 10,
                shadowColor: '#000',
                shadowOffset: { width: 0, height: 10 },
                shadowOpacity: 0.3,
                shadowRadius: 20,
            },
        }),
    },
    modalIconCircle: {
        width: 64,
        height: 64,
        borderRadius: 32,
        backgroundColor: '#FEF3C7',
        justifyContent: 'center',
        alignItems: 'center',
        marginBottom: 16,
    },
    modalTitle: {
        fontSize: 20,
        fontWeight: '700',
        color: '#1F2937',
        marginBottom: 8,
    },
    modalMessage: {
        fontSize: 15,
        color: '#6B7280',
        textAlign: 'center',
        lineHeight: 22,
        marginBottom: 24,
    },
    modalButtonRow: {
        flexDirection: 'row',
        gap: 12,
        width: '100%',
    },
    modalCancelButton: {
        flex: 1,
        paddingVertical: 14,
        borderRadius: 12,
        backgroundColor: '#F3F4F6',
        alignItems: 'center',
    },
    modalCancelText: {
        fontSize: 15,
        fontWeight: '600',
        color: '#6B7280',
    },
    modalBuyButton: {
        flex: 1,
        borderRadius: 12,
        overflow: 'hidden',
    },
    modalBuyGradient: {
        paddingVertical: 14,
        alignItems: 'center',
        justifyContent: 'center',
        flexDirection: 'row',
    },
    modalBuyText: {
        fontSize: 15,
        fontWeight: '700',
        color: '#FFFFFF',
    },
});

export default HomePanel;
