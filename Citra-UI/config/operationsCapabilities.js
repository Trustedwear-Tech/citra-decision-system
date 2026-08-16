// operationsCapabilities.js
//
// SINGLE SOURCE OF TRUTH for Citra's capability vocabulary, shared by:
//   - IntroScreen.js            (pre-login marketing / landing page, dark theme)
//   - components/HomePanel.js   (post-login home, light theme)
//
// THESIS: One engine. Operations is the spine; every other capability is the
// SAME governed engine expressed for a different operational WORK AREA — chat,
// analytics, reporting, workflow / data-movement. Decision Apps are the wedge;
// the rest is reassurance that the platform is deep, not four separate products.
//
// Renaming or recoloring a capability HERE changes both surfaces at once, so the
// landing page and the post-login home can never drift apart again. Before this
// file existed, every label lived inline in each screen — which is exactly why
// "Citra Chat" / "Enterprise Chat" and "Deep Analytics" / "Deep Research" had
// diverged.
//
// Field notes:
//   - `icon`        : Ionicons solid name        (IntroScreen marketing cards)
//   - `iconOutline` : Ionicons outline name      (HomePanel feature cards)
//   - `handlerKey`  : the onOpen* prop HomePanel calls for this capability
//   - `introPage`   : the product-page key IntroScreen's onNavigate routes to
//   - `gate`        : null | 'paid' | 'workflow' | 'admin' | 'superAdmin'
//   - `group`       : OPERATIONS | WORK_AREAS | DATA_FOUNDATION
//   - `hidden`      : drop from BOTH surfaces (capability is off entirely)

// One canonical thesis line. Rendered on BOTH hero areas so a prospect reads the
// same sentence before and after login.
export const CITRA_THESIS =
  'The operational platform where your enterprise runs its decisions — on your ' +
  'data, under your governance, audited on every action.';

// Short framing line for the consolidated "everything else" section — the
// reassurance-not-headline treatment the sales plan calls for.
//
// INTROSCREEN ONLY. HomePanel used to render this above its work-areas section
// but no longer does: that section is now just enterprise chat (+ the SOP
// Library for dept members) and carries its own literal heading.
//
// KEEP THIS IN SYNC WITH THE GRID IT SITS ABOVE. IntroScreen renders
// marketingCapabilities() minus the flagship, which today is exactly three
// tiles — Operations Workflow, Operations Chat, Operations Dashboard. Operations
// Analytics, Quick Chat and the Presentation / Visual Report / Doc Creator tiles
// were DELETED in the 2026-08-08 Phase 0 OSS split, not hidden. Name only what
// the grid actually shows.
export const WORK_AREAS_FRAME = {
  badge: 'ONE ENGINE · SAME GOVERNANCE',
  title: 'The same engine, wherever operations needs it.',
  subtitle:
    'Where Decision Apps carry the complex, high-stakes calls with memory, ' +
    'workflows carry the thousands of simple ones — high-volume batch & event ' +
    'pipelines on the same governed engine. Live dashboards let your team watch ' +
    'it all in real time. And when you want to track what changed or pull a ' +
    'quick stat — "how many loans did I approve today?" — just ask the chat. ' +
    'One deployment, one security model, one audit trail.',
};

export const GROUPS = {
  OPERATIONS: 'OPERATIONS',       // the agent-driven flagship (Decision Apps)
  WORK_AREAS: 'WORK_AREAS',       // the same engine, other operational surfaces
  DATA_FOUNDATION: 'DATA_FOUNDATION',
};

// NOTE ON EXECUTION MODELS: Citra runs TWO complementary execution models on
// one governed engine, split by STAKES-PER-DECISION vs VOLUME — (1) Decision
// Apps & API: memory-backed agent judgment on complex, high-stakes calls, one
// case at a time (insurance claim, fraud case); (2) Operations Workflow:
// high-frequency, low-complexity-per-item work at volume — batch or
// event-driven pipelines that preprocess data, AI-evaluate each item against
// fixed rules, then move data or produce reports, mainly automated end to end
// (human checkpoints are OPTIONAL, added only where the author chooses).
// They are kept in SEPARATE groups/sections on purpose so the agentic wedge
// stands alone as the hero. Be precise: Decision Apps = deep judgment + memory,
// high stakes; workflow = AI-in-a-pipeline, high volume, low stakes per item.

// ---------------------------------------------------------------------------
// Capabilities. Order within each group is the canonical display order.
// ---------------------------------------------------------------------------
export const CAPABILITIES = [
  // ---- OPERATIONS (the spine) ---------------------------------------------
  {
    key: 'decision-apps',
    group: GROUPS.OPERATIONS,
    title: 'Self-Improving Decision Apps & APIs',
    // Home-only label. This card is the BUILD entry (gate: 'builder'), so on
    // the post-login home it says "Builder" to distinguish it from the "My
    // Decision Apps" consumer card sitting right beside it. IntroScreen keeps
    // `title` — pre-login there is no builder/consumer split to disambiguate.
    homeTitle: 'Self-Improving Decision Apps & API Builder',
    // HomePanel subtitle (operational, concrete). Kept to ONE line — this is a
    // card subtitle, not a pitch; the full story lives in `blurb` (IntroScreen)
    // and in the section subtitle above the card on home.
    tagline:
      'Agent-driven decisions that recommend, act, and learn — authored in plain ' +
      'English, shipped as an app or a headless API',
    // IntroScreen marketing blurb
    blurb:
      'Describe a decision in plain English; Citra ships an agent-driven app — an ' +
      'agent recommends the action for your team to approve, or, where you opt in, ' +
      'auto-processes routine low-risk cases within your policy gate — and every ' +
      'outcome feeds back so the call gets sharper each week. Take the built-in ' +
      'UI, or run it headless as a Decision API inside your own front-end.',
    icon: 'apps',
    iconOutline: 'apps-outline',
    color: '#8B5CF6',
    gradient: ['#8B5CF6', '#EC4899'],
    handlerKey: 'onOpenPowerApps',
    introPage: 'powerapps',
    tourId: 'smart-app-card',
    // Builder-only surface: the pink flagship is the BUILD entry. Gated to
    // admins + the decision-app-builder role (HomePanel: canBuildApps). Every
    // other user gets the consumer list cards below instead.
    gate: 'builder',
    flagship: true,
  },
  // Consumer entry — every app this user can open: their own, those published
  // to them, and (for admins) those they oversee, via the surface's scope tabs.
  // Opens the Decision Apps surface in list-only 'consumer' mode filtered to
  // kind='app' (no Build / edit / API). Home-only (no introPage), everyone.
  {
    key: 'decision-apps-list',
    group: GROUPS.OPERATIONS,
    title: 'My Decision Apps',
    tagline: 'Open your Decision Apps — the screens your team works in',
    blurb: '',
    icon: 'grid',
    iconOutline: 'grid-outline',
    color: '#6366F1',
    gradient: null,
    handlerKey: 'onOpenDecisionApps',
    introPage: null,
    tourId: 'my-decision-apps-card',
    gate: null,
  },
  // REMOVED 2026-08-08 (OSS split): `operations-workflow`. The workflow engine
  // (citra-workflow + Citra-Worker) left the Decision System and is republished
  // from its own repo. The ORCHESTRATION group went with it.

  // ---- WORK AREAS (same engine, different operational surface) -------------
  {
    key: 'operations-chat',
    group: GROUPS.WORK_AREAS,
    title: 'Operations Chat',
    tagline: 'Ask your operational data anything — governed answers in seconds',
    blurb:
      'Ask anything across your operational systems and get grounded, cited ' +
      'answers — no ETL, under your governance.',
    icon: 'chatbubbles',
    iconOutline: 'chatbubbles-outline',
    color: '#6366F1',
    gradient: null,
    handlerKey: 'onOpenChat',
    introPage: 'chatquery',
    tourId: 'enterprise-chat-card',
    gate: null,
  },
  // REMOVED 2026-08-08 (Phase 0 OSS split): `operations-analytics`. The backing
  // action-chat-service was DELETED, not parked — this is not coming back.
  {
    key: 'operations-dashboard',
    // Consumer dashboards live under "Run your operations" (OPERATIONS) beside
    // "My Decision Apps". On home it opens the Decision Apps surface in
    // list-only 'consumer' mode filtered to dashboards (onOpenDashboards). The
    // marketing surface (IntroScreen) still shows it as "Operations Dashboard"
    // via `title` + introPage; `homeTitle` overrides the label on home only.
    group: GROUPS.OPERATIONS,
    title: 'Operations Dashboard',
    homeTitle: 'My Dashboards',
    tagline: 'Open your dashboards — live KPI + chart views on your data',
    blurb:
      'A live dashboard page inside your Decision App — how the operator and their ' +
      'manager watch the decisions and KPIs in real time, on your data, under your governance.',
    icon: 'speedometer',
    iconOutline: 'speedometer-outline',
    color: '#14B8A6',
    gradient: ['#14B8A6', '#22D3EE'],
    // A dashboard is a page WITHIN a Decision App (kind='app' +
    // page.kind='dashboard'); the consumer card lists those dashboards.
    handlerKey: 'onOpenDashboards',
    introPage: 'smartapp',
    tourId: 'operations-dashboard-card',
    gate: null,
  },
  // REMOVED 2026-08-08 (Phase 0 OSS split): `quick-chat`. Citra-Service's
  // api/quick_chat.py and the QuickChat* components are deleted. The
  // quick-chat-sandbox IMAGE survives — services/code_executor.py still uses
  // that pool for smart-app tools_v2 kind=code_exec.

  // ---- DATA FOUNDATION (what powers it) -----------------------------------
  {
    key: 'operations-data-flow',
    group: GROUPS.DATA_FOUNDATION,
    title: 'Operational Data Flow Audit',
    tagline: 'Audit and monitor governed data access across SQL, Mongo, S3 and REST APIs — via MCP',
    blurb:
      'Source-agnostic, governed access to your operational systems via MCP — no ' +
      'copy, no ETL, every read and write audited.',
    icon: 'server',
    iconOutline: 'server-outline',
    color: '#0EA5E9',
    gradient: ['#0EA5E9', '#6366F1'],
    handlerKey: 'onOpenDeptSources',
    introPage: null,
    tourId: 'dept-sources-card',
    gate: null,
  },
];

// ---------------------------------------------------------------------------
// Selectors
// ---------------------------------------------------------------------------
export const getCapability = (key) =>
  CAPABILITIES.find((c) => c.key === key) || null;

// Capabilities in a group, excluding any gated off with `hidden: true`.
export const capabilitiesInGroup = (group) =>
  CAPABILITIES.filter((c) => c.group === group && !c.hidden);

// Marketing surface: capabilities with a product page to route to, minus hidden.
export const marketingCapabilities = () =>
  CAPABILITIES.filter((c) => c.introPage && !c.hidden);

export default CAPABILITIES;
