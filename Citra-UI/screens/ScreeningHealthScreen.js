/**
 * ScreeningHealthScreen — admin "Screening Health" panel
 * (docs/fraud-screening-admin-panel-plan.md §1).
 *
 * NOT a fraud console: officers never work out of this screen and nothing here
 * alerts anyone — flags only ever ride the recommendation to the officer who
 * was already deciding the case. This panel answers the ADMIN's three
 * questions: is screening working, do officers trust it (false-alarm rate),
 * and when a check is noisy, exactly which knob turns it off (the advisory —
 * a sources.json/ontology or weights change, always a human action).
 *
 * Two views inside one modal:
 *   1. Org overview — five plain numbers + a per-app table (tap → drill-down).
 *   2. App drill-down — what was found (by check type), what officers said
 *      (confirmed vs dismissed + verbatim dismissal reasons), and advisories
 *      that only appear past the ≥5-verdicts / ≥70%-dismissed thresholds.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, TouchableOpacity, ActivityIndicator, ScrollView, Modal,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import ScreeningHealthService from '../services/ScreeningHealthService';

const RED = '#DC2626';
const GREEN = '#16A34A';
const AMBER = '#D97706';
// Per-app "needs attention" dot beyond this false-alarm rate (plan §1.1).
const ATTENTION_RATE = 0.3;

const PERIODS = [
  { id: 'week', label: 'Week' },
  { id: 'month', label: 'Month' },
  { id: 'all', label: 'All time' },
];

// ── "What we screen for" — the user-education catalogue ─────────────────────
// Every check the platform actually runs (keep in sync with
// docs/fraud-detection-coverage-matrix.md §3 — list only what is BUILT).
// Framing is part of the product: screening NEVER blocks and never
// auto-rejects; findings are evidence fed to the AI's recommendation, and the
// decision is always the officer's.
const SCREENING_CATALOGUE = [
  {
    group: 'Photo & image checks',
    icon: 'image-outline',
    items: [
      ['Reused photo — exact copy', 'The same photo file already appears on another case (double-dip / recycled proof).'],
      ['Reused photo — re-encoded or resized', 'Same picture, different bytes — survives messenger round-trips and re-saves.'],
      ['Reused photo — cropped or re-shot copy', 'A visual match to a photo on another case, even cropped, screenshotted or re-photographed.'],
      ['Photo predates the claimed incident', 'The photo’s own capture timestamp is earlier than the incident/report date on the record — it cannot show the claimed event.'],
      ['Photo taken away from the claimed site', 'The photo’s GPS is beyond the allowed distance from the site/premise coordinates on the record.'],
      ['Photo edited with software', 'The file carries an editing-tool signature (Photoshop, Snapseed, Canva…).'],
      ['Camera changes mid-photoset', 'Multiple camera models across one record’s evidence photos — supporting context only, never counted as an issue by itself.'],
    ],
  },
  {
    group: 'Document checks',
    icon: 'document-text-outline',
    items: [
      ['Reused document — identical file', 'The same PDF/document already appears on another case.'],
      ['Reused document — same content re-exported', 'The text is the same as a document on another case even though the file was re-saved to look different.'],
      ['Document modified after creation', 'The file’s metadata shows it was edited after its stated creation date.'],
      ['Document authored in an image editor', 'A "scanned" statement or invoice produced by Photoshop/Canva — a classic tamper tell.'],
    ],
  },
  {
    group: 'Record consistency checks',
    icon: 'git-compare-outline',
    items: [
      ['Claimed vs extracted mismatch', 'A value on the record (name, amount, date…) differs from what the submitted document or photo actually shows. Values are normalised first — "$123,456.00" equals "123456", "03/04/2026" is read in your locale — so only REAL differences flag.'],
      ['Fabricated ID numbers', 'Checksum-failing identifiers on either side: PAN, GSTIN, Aadhaar, VIN, IFSC, SSN, EIN, bank routing, ZIP and more (locale-aware).'],
      ['Invoice arithmetic', 'Line items that don’t add up to the stated total.'],
      ['Bank-statement reconciliation', 'The statement’s running balance is re-added row by row (each balance = prior balance ± that row’s transaction). Fabricated statements break the chain repeatedly; a single break is treated as OCR noise and never flagged.'],
      ['Date rules', 'Your ontology can declare simple date rules on a record — a claim filed days after the policy started, an inspection dated before its work order, a statement too old at application. Pure date arithmetic on values read from your source system.'],
      ['Photo metadata vs the record', 'The evidence photo’s own capture date and GPS are checked against the record’s claimed incident date and site — read directly from your source system, never taken from the AI.'],
      ['Photoset timing across cases', 'Photos on several different cases captured within minutes of each other — nobody genuinely inspects three sites in fifteen minutes. Corroboration only: this can never flag a case by itself.'],
      ['Cross-dataset verification', 'A submitted document (a purchase bill, a settlement letter, a serial number) is looked up in the REAL system of record your ontology names — registry, ledger, asset master. "Referenced record not found" is a fact; a full match shows as VERIFIED.'],
    ],
  },
  {
    group: 'Payment-proof checks',
    icon: 'card-outline',
    items: [
      ['Payment reference not found', '"I already paid — here’s the receipt": the reference on the submitted proof is looked up in your REAL payment ledger. If the ledger has no such payment, the proof cites a transaction that never happened — a fact, not a suspicion.'],
      ['Payment amount doesn’t match', 'The payment exists, but the amount on the proof differs from what the ledger recorded (beyond a small OCR tolerance) — a doctored receipt.'],
      ['Payment date doesn’t match', 'The payment exists, but the date on the proof is outside the allowed window of the ledger’s record.'],
      ['Genuine payment, wrong person', 'The receipt is real — but the ledger says the payment belongs to someone else. A genuine receipt reused by another party.'],
      ['Payment verified ✓', 'Most disputes are honest: when the reference, amount, date and party all match, the case shows "payment VERIFIED" and the AI recommends resolving in the customer’s favor — clearing honest customers in seconds is half the value.'],
    ],
  },
  {
    group: 'Cross-case patterns',
    icon: 'git-network-outline',
    items: [
      ['Same identifier on other cases', 'A phone, bank account, VIN, or reference number that also appears on other cases — ring and double-dip evidence.'],
      ['Resubmitted after a denial', 'A shared identifier links this case to a prior case that was DENIED — the finding quotes that prior decision and date verbatim, so the officer sees exactly what was refused before.'],
      ['One identifier, many names', 'The same phone/account used under several different names — a synthetic-identity tell.'],
      ['External registry match', 'A hit in an industry fraud registry, when your organisation subscribes to one.'],
    ],
  },
  {
    group: 'Deliberately NOT flagged (to protect trust)',
    icon: 'shield-outline',
    items: [
      ['Reused identity artifacts', 'A headshot or ID scan resubmitted by the same person is VERIFICATION, not fraud — it never counts against a case.'],
      ['Missing photo metadata', 'Messengers strip photo metadata routinely — its absence alone is never treated as suspicious.'],
    ],
  },
];

const pct = (r) => (r === null || r === undefined ? '—' : `${Math.round(r * 100)}%`);

// ── Domain badge + vertical-aware catalogue ordering (Phase 2) ───────────────
// The deployment's ontology declares WHAT line of business each source serves
// (domain: vertical / sub_vertical / country). The server surfaces the distinct
// triples on /org/screening-stats; here they render as a badge and pull the
// most relevant education groups to the top. ORDERING ONLY — every group stays
// visible regardless of vertical.
const SUB_VERTICAL_LABELS = {
  claims: 'Claims', underwriting: 'Underwriting',
  loan_origination: 'Loan origination', loan_recovery: 'Loan recovery',
  power_recovery: 'Power recovery', metering_inspection: 'Metering inspection',
  equipment_inspection: 'Equipment inspection',
};
const VERTICAL_LABELS = {
  insurance: 'Insurance', banking: 'Banking', utility: 'Utility',
  field_service: 'Field service',
};
// Which education group leads for each sub-vertical (its "killer check").
const SUB_VERTICAL_LEAD_GROUP = {
  loan_recovery: 'Payment-proof checks',
  power_recovery: 'Payment-proof checks',
  claims: 'Photo & image checks',
  metering_inspection: 'Photo & image checks',
  equipment_inspection: 'Photo & image checks',
  loan_origination: 'Document checks',
  underwriting: 'Document checks',
};

const domainLabel = (d) => [
  d?.country,
  VERTICAL_LABELS[d?.vertical] || d?.vertical,
  SUB_VERTICAL_LABELS[d?.sub_vertical] || d?.sub_vertical,
].filter(Boolean).join(' · ');

const orderedCatalogue = (domains) => {
  const leads = (domains || [])
    .map((d) => SUB_VERTICAL_LEAD_GROUP[d?.sub_vertical])
    .filter(Boolean);
  if (!leads.length) return SCREENING_CATALOGUE;
  return [...SCREENING_CATALOGUE].sort((a, b) => {
    const ia = leads.indexOf(a.group); const ib = leads.indexOf(b.group);
    return (ia === -1 ? leads.length : ia) - (ib === -1 ? leads.length : ib);
  });
};

export default function ScreeningHealthScreen({ visible, onClose, theme }) {
  const colors = theme || {};
  const bg = colors.background || '#FFFFFF';
  const surface = colors.surface || '#F9FAFB';
  const text = colors.text || '#111827';
  const sub = colors.textSecondary || '#6B7280';
  const border = colors.border || '#E5E7EB';
  const primary = colors.primary || '#3B82F6';

  const [period, setPeriod] = useState('month');
  const [showInfo, setShowInfo] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [org, setOrg] = useState(null);
  // Drill-down state — {slug, name} when an app row is open.
  const [app, setApp] = useState(null);
  const [appStats, setAppStats] = useState(null);
  const [appLoading, setAppLoading] = useState(false);
  const [appError, setAppError] = useState('');

  const loadOrg = useCallback(async (p) => {
    setLoading(true); setError('');
    try {
      setOrg(await ScreeningHealthService.getOrgScreeningStats(p));
    } catch (e) {
      setOrg(null);
      setError(e?.status === 403
        ? 'Screening stats are admin-only.'
        : (e?.message || 'Could not load screening stats.'));
    } finally { setLoading(false); }
  }, []);

  const loadApp = useCallback(async (slug, p) => {
    setAppLoading(true); setAppError(''); setAppStats(null);
    try {
      setAppStats(await ScreeningHealthService.getAppScreeningStats(slug, p));
    } catch (e) {
      setAppError(e?.message || 'Could not load app screening stats.');
    } finally { setAppLoading(false); }
  }, []);

  // Reset the navigation state ONLY when the modal opens — a period change
  // must never eject the user out of a drill-down.
  useEffect(() => {
    if (!visible) return;
    setApp(null); setAppStats(null); setShowInfo(false);
  }, [visible]);

  // Org stats load on open, on period change, and when returning from the
  // drill-down — but never WHILE the drill-down is showing (the result would
  // render nowhere; the app effect below owns that view's fetches).
  useEffect(() => {
    if (!visible || app) return;
    loadOrg(period);
  }, [visible, app, period, loadOrg]);

  useEffect(() => {
    if (app?.slug) loadApp(app.slug, period);
  }, [app, period, loadApp]);

  if (!visible) return null;

  const totals = org?.totals || {};
  const attention = (r) => r !== null && r !== undefined && r > ATTENTION_RATE;

  const StatTile = ({ label, value, hint, accent }) => (
    <View style={{ flexGrow: 1, flexBasis: 140, borderWidth: 1, borderColor: border, borderRadius: 12, padding: 12, backgroundColor: surface }}>
      <Text style={{ fontSize: 22, fontWeight: '800', color: accent || text }}>{value}</Text>
      <Text style={{ fontSize: 12, fontWeight: '700', color: text, marginTop: 2 }}>{label}</Text>
      {hint ? <Text style={{ fontSize: 10, color: sub, marginTop: 2 }}>{hint}</Text> : null}
    </View>
  );

  const SectionTitle = ({ icon, children }) => (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 18, marginBottom: 8 }}>
      <Ionicons name={icon} size={15} color={sub} />
      <Text style={{ fontSize: 13, fontWeight: '800', color: text }}>{children}</Text>
    </View>
  );

  const renderOrg = () => (
    <>
      {/* Domain badge — the ontology-declared line(s) of business this
          deployment screens for ("US · Utility · Metering inspection"). */}
      {(org?.domains || []).length > 0 && (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
          {org.domains.map((d) => (
            <View key={domainLabel(d)} style={{ flexDirection: 'row', alignItems: 'center', gap: 4, borderWidth: 1, borderColor: `${primary}55`, backgroundColor: `${primary}0D`, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 4 }}>
              <Ionicons name="business-outline" size={11} color={primary} />
              <Text style={{ fontSize: 11, fontWeight: '700', color: primary }}>{domainLabel(d)}</Text>
            </View>
          ))}
        </View>
      )}

      {/* The five card numbers (plan §1.1) */}
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
        <StatTile label="Cases screened" value={totals.screened ?? 0}
          hint="recommendations where fraud checks ran" />
        <StatTile label="With warnings" value={totals.warned ?? 0}
          hint="≥1 check raised a signal" />
        <StatTile label="Confirmed by officers" value={totals.confirmed ?? 0}
          hint="marked a real issue" accent={GREEN} />
        <StatTile label="False alarms" value={totals.dismissed ?? 0}
          hint="dismissed with a reason" accent={totals.dismissed ? AMBER : undefined} />
        <StatTile label="False-alarm rate" value={pct(totals.false_alarm_rate)}
          hint="false alarms ÷ warnings officers judged"
          accent={attention(totals.false_alarm_rate) ? RED : GREEN} />
      </View>

      <SectionTitle icon="apps-outline">By app — tap a row for detail</SectionTitle>
      {(org?.apps || []).length === 0 && (
        <Text style={{ fontSize: 13, color: sub, paddingVertical: 8 }}>
          No cases were screened in this period. Screening runs only for apps whose
          data source opted in (sources.json fraud_screening).
        </Text>
      )}
      {(org?.apps || []).map((a) => (
        <TouchableOpacity
          key={a.app_slug}
          onPress={() => setApp({ slug: a.app_slug, name: a.name || a.app_slug })}
          style={{ flexDirection: 'row', alignItems: 'center', gap: 8, borderWidth: 1, borderColor: border, borderRadius: 10, padding: 12, marginBottom: 8, backgroundColor: bg }}
        >
          {attention(a.false_alarm_rate) && (
            <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: RED }} />
          )}
          <Text style={{ flex: 1, fontSize: 13, fontWeight: '700', color: text }} numberOfLines={1}>
            {a.name || a.app_slug}
          </Text>
          <Text style={{ fontSize: 12, color: sub }}>{a.screened} screened</Text>
          <Text style={{ fontSize: 12, color: sub }}>{a.warned} warned</Text>
          <Text style={{ fontSize: 12, fontWeight: '700', color: attention(a.false_alarm_rate) ? RED : GREEN }}>
            {pct(a.false_alarm_rate)} false
          </Text>
          <Ionicons name="chevron-forward" size={16} color={sub} />
        </TouchableOpacity>
      ))}
      {org?.truncated_at ? (
        <Text style={{ fontSize: 11, color: AMBER }}>
          Showing the newest {org.truncated_at} screenings only — narrow the period for exact totals.
        </Text>
      ) : null}
    </>
  );

  // The user-education page behind the "What we screen for" button.
  const renderInfo = () => (
    <>
      <TouchableOpacity onPress={() => setShowInfo(false)} style={{ flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 10 }}>
        <Ionicons name="arrow-back" size={16} color={primary} />
        <Text style={{ fontSize: 13, fontWeight: '700', color: primary }}>Back to stats</Text>
      </TouchableOpacity>

      <Text style={{ fontSize: 16, fontWeight: '800', color: text }}>What we screen every case for</Text>

      {/* The doctrine, stated to the user in plain words — this framing is the
          product promise: evidence in, human decision out, nothing blocked. */}
      <View style={{ borderWidth: 1, borderColor: `${primary}55`, backgroundColor: `${primary}0D`, borderRadius: 10, padding: 12, marginTop: 10 }}>
        <Text style={{ fontSize: 12.5, color: text, lineHeight: 19 }}>
          Every case is screened automatically while the AI prepares its recommendation.
          {'\n'}• A finding <Text style={{ fontWeight: '800' }}>never blocks a case</Text> and nothing is ever auto-rejected.
          {'\n'}• Findings are fed to the AI as <Text style={{ fontWeight: '800' }}>evidence inside its recommendation</Text>, each with a plain-language explanation.
          {'\n'}• The decision is always at <Text style={{ fontWeight: '800' }}>your officer’s discretion</Text> — and their Confirm/Dismiss feedback teaches the screening what’s normal for your data.
        </Text>
      </View>

      {orderedCatalogue(org?.domains).map((g) => (
        <View key={g.group}>
          <SectionTitle icon={g.icon}>{g.group}</SectionTitle>
          {g.items.map(([title, desc]) => (
            <View key={title} style={{ borderWidth: 1, borderColor: border, borderRadius: 10, padding: 10, marginBottom: 6, backgroundColor: surface }}>
              <Text style={{ fontSize: 13, fontWeight: '700', color: text }}>{title}</Text>
              <Text style={{ fontSize: 11.5, color: sub, marginTop: 2, lineHeight: 16 }}>{desc}</Text>
            </View>
          ))}
        </View>
      ))}

      <Text style={{ fontSize: 11, color: sub, marginTop: 12, lineHeight: 16 }}>
        Which checks run on which records is declared by your data source’s ontology
        (sources.json) — screening only runs where your organisation opted a dataset in,
        and it can be tuned or turned off per dataset at any time. Cases with enough
        combined signals additionally get one AI cross-examination pass, whose output
        is also just evidence for the officer.
      </Text>
    </>
  );

  const renderApp = () => (
    <>
      <TouchableOpacity onPress={() => setApp(null)} style={{ flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 8 }}>
        <Ionicons name="arrow-back" size={16} color={primary} />
        <Text style={{ fontSize: 13, fontWeight: '700', color: primary }}>All apps</Text>
      </TouchableOpacity>
      <Text style={{ fontSize: 16, fontWeight: '800', color: text }}>{app.name}</Text>

      {appLoading && <ActivityIndicator style={{ marginTop: 20 }} color={primary} />}
      {!!appError && <Text style={{ fontSize: 13, color: RED, marginTop: 10 }}>{appError}</Text>}

      {appStats && (
        <>
          {appStats.fraud_enabled === false && (
            <Text style={{ fontSize: 12, color: AMBER, marginTop: 6 }}>
              Screening is currently OFF for this app — showing historical data only.
              It turns on when the app is (re)published with a data source that opts in
              via sources.json fraud_screening.
            </Text>
          )}
          <SectionTitle icon="search-outline">What was found</SectionTitle>
          {Object.keys(appStats.signals || {}).length === 0 && (
            <Text style={{ fontSize: 13, color: sub }}>No signals in this period.</Text>
          )}
          {Object.entries(appStats.signals || {}).map(([key, s]) => (
            <View key={key} style={{ borderWidth: 1, borderColor: border, borderRadius: 10, padding: 10, marginBottom: 6, backgroundColor: surface }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Text style={{ flex: 1, fontSize: 13, fontWeight: '700', color: text }}>{s.label}</Text>
                <Text style={{ fontSize: 12, color: sub }}>{s.cases} case{s.cases === 1 ? '' : 's'}</Text>
              </View>
              <Text style={{ fontSize: 11, color: sub, marginTop: 2 }}>
                Officers: {s.confirmed} confirmed · {s.dismissed} dismissed
                {s.false_alarm_rate !== null && s.false_alarm_rate !== undefined
                  ? ` · ${pct(s.false_alarm_rate)} false alarms` : ' · not yet judged'}
              </Text>
            </View>
          ))}

          <SectionTitle icon="chatbox-ellipses-outline">Why officers dismissed flags</SectionTitle>
          {(appStats.dismissal_reasons || []).length === 0 && (
            <Text style={{ fontSize: 13, color: sub }}>No dismissals in this period.</Text>
          )}
          {(appStats.dismissal_reasons || []).map((r, i) => (
            <View key={`${r.record_id}-${i}`} style={{ borderLeftWidth: 3, borderLeftColor: AMBER, paddingLeft: 10, paddingVertical: 4, marginBottom: 6 }}>
              <Text style={{ fontSize: 12, color: text }}>&ldquo;{r.reason}&rdquo;</Text>
              <Text style={{ fontSize: 10, color: sub, marginTop: 2 }}>case {r.record_id}{r.by ? ` · ${r.by}` : ''}</Text>
            </View>
          ))}

          <SectionTitle icon="bulb-outline">Advisories</SectionTitle>
          {(appStats.advisories || []).length === 0 && (
            <Text style={{ fontSize: 13, color: sub }}>
              None — no check is being dismissed often enough to recommend a change
              (advisories appear after ≥5 officer verdicts with ≥70% dismissed).
            </Text>
          )}
          {(appStats.advisories || []).map((adv) => (
            <View key={adv.signal} style={{ borderWidth: 1, borderColor: '#FDE68A', backgroundColor: '#FFFBEB', borderRadius: 10, padding: 12, marginBottom: 8 }}>
              <Text style={{ fontSize: 13, fontWeight: '800', color: '#92400E' }}>
                {adv.label} — {pct(adv.dismissal_rate)} dismissed ({adv.dismissed}/{adv.judged})
              </Text>
              <Text style={{ fontSize: 12, color: '#78350F', marginTop: 4 }}>{adv.advisory}</Text>
              <Text style={{ fontSize: 11, color: '#92400E', marginTop: 6 }}>
                Ontology changes take effect in 3 steps: edit sources.json → re-crawl the
                catalogue → republish the app. Nothing is changed automatically.
              </Text>
            </View>
          ))}
        </>
      )}
    </>
  );

  return (
    <Modal visible transparent={false} animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: bg }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 20, paddingTop: 18, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: border }}>
          <Ionicons name="shield-checkmark-outline" size={22} color={primary} />
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 18, fontWeight: '800', color: text }}>Screening Health</Text>
            <Text style={{ fontSize: 12, color: sub }}>
              How fraud checks perform inside recommendations — and what to tune when a check is noisy.
            </Text>
          </View>
          {/* The education entry point: everything we screen for, and the
              never-blocks / officer-discretion framing. */}
          <TouchableOpacity
            onPress={() => setShowInfo(true)}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 5, borderWidth: 1, borderColor: primary, borderRadius: 14, paddingHorizontal: 10, paddingVertical: 5 }}
          >
            <Ionicons name="information-circle-outline" size={15} color={primary} />
            <Text style={{ fontSize: 12, fontWeight: '700', color: primary }}>What we screen for</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={onClose} hitSlop={12}>
            <Ionicons name="close" size={24} color={text} />
          </TouchableOpacity>
        </View>

        {/* Period selector (stats views only — meaningless on the info page) */}
        {!showInfo && (
        <View style={{ flexDirection: 'row', gap: 8, paddingHorizontal: 20, paddingVertical: 10 }}>
          {PERIODS.map((p) => (
            <TouchableOpacity
              key={p.id}
              onPress={() => setPeriod(p.id)}
              style={{ paddingHorizontal: 12, paddingVertical: 6, borderRadius: 14, borderWidth: 1, borderColor: period === p.id ? primary : border, backgroundColor: period === p.id ? `${primary}18` : bg }}
            >
              <Text style={{ fontSize: 12, fontWeight: '700', color: period === p.id ? primary : sub }}>{p.label}</Text>
            </TouchableOpacity>
          ))}
        </View>
        )}

        <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 32 }}>
          {/* Education page is reachable regardless of stats loading/errors —
              "what do we screen for" must not depend on the data being up. */}
          {showInfo ? renderInfo() : (
            <>
              {loading && <ActivityIndicator style={{ marginTop: 30 }} color={primary} />}
              {!!error && !loading && (
                <Text style={{ fontSize: 13, color: RED, marginTop: 16 }}>{error}</Text>
              )}
              {!loading && !error && (app ? renderApp() : renderOrg())}
            </>
          )}
        </ScrollView>
      </View>
    </Modal>
  );
}
