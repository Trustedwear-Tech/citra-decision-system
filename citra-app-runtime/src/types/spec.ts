// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * AppSpec / AgentSpec types — mirrors smart-app-service/schemas/*.schema.json (v0).
 * Keep in sync manually for now; later we can codegen from the JSON Schemas.
 */

export type SpecVersion = "v0";
export type ModelTier = "tier_a" | "tier_b" | "tier_c";
export type ClassificationLevel =
  | "public"
  | "internal"
  | "confidential"
  | "restricted";
export type AppStatus = "draft" | "published" | "archived";
export type AppKind = "app";
/**
 * Page purpose. 'standard' = ordinary app page. 'dashboard' = executive
 * treatment (KPI + chart grid + hero-brief copilot). A "dashboard" is just
 * an app whose primary page has kind='dashboard'.
 *
 * 'embed' = a decision card rendered INSIDE a customer's own application by
 * citra.js. It has no Citra screen: opened directly here it shows
 * EmbedPageNotice rather than empty panels, because its queue filters on a
 * host-supplied record id that a bare URL does not carry.
 *
 * Keep in step with PageKind in smart-app-service/models.py — they are the
 * same enum on two sides of the wire.
 */
export type PageKind = "standard" | "dashboard" | "embed";

// ---------------------------------------------------------------------------
// AgentSpec
// ---------------------------------------------------------------------------

export interface RagBinding {
  ref: string;
  doc_types?: string[];
  classification_max?: ClassificationLevel;
}

export interface SubAgent {
  id: string;
  role: string;
  system_prompt: string;
  model_tier?: ModelTier;
  tools?: string[];
  mcps?: string[];
  rag?: RagBinding[];
}

export interface AgentAction {
  name: string;
  description?: string;
  delegates_to?: string[];
  approval_required?: boolean;
  input_schema?: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
}

export interface AgentSpec {
  spec_version: SpecVersion;
  agent_id: string;
  name: string;
  description?: string;
  model_tier?: ModelTier;
  system_prompt: string;
  input_schema?: Record<string, unknown>;
  tools?: string[];
  mcps?: string[];
  rag?: RagBinding[];
  memory_namespace?: string;
  sub_agents?: SubAgent[];
  actions?: AgentAction[];
  hitl_policy?: Record<string, unknown>;
  version?: number;
}

// ---------------------------------------------------------------------------
// AppSpec
// ---------------------------------------------------------------------------

export interface Theme {
  primary?: string;
  /** Secondary highlight color (CTAs, accents). Falls back to a premium amber. */
  accent?: string;
  logo_url?: string;
  dark_mode?: boolean;
  /** BCP-47 locale for number/date formatting (e.g. "en-US", "en-IN"). Default en-US. */
  locale?: string;
  /** ISO-4217 currency for monetary KPIs (e.g. "USD", "INR"). Default USD. */
  currency?: string;
  // ── Theme v2 (runtime-ui-modernization-plan.md Track A). All optional;
  // unset reproduces the classic look. Closed enums mirrored from models.py.
  /** Customer display name — app header + browser title. Publish defaults it
   *  from the ontology's `organization` block. */
  company_name?: string;
  /** Font-stack preset (bundle-safe, no external fetch). */
  font?: "inter" | "source-sans" | "ibm-plex" | "system";
  /** Corner-radius scale → --citra-radius. */
  radius?: "sharp" | "soft" | "round";
  /** Spacing scale for data-dense vs airy apps. */
  density?: "comfortable" | "compact";
  /** Card surface treatment (shadow/backdrop). */
  surface?: "flat" | "elevated" | "glass";
  /** Color scheme; supersedes dark_mode when set. "auto" follows the OS. */
  mode?: "light" | "dark" | "auto";
  /** Named ECharts palette; "brand" derives a ramp from `primary`. */
  chart_palette?: "calm" | "vivid" | "mono" | "brand";
}

export interface DataSource {
  id: string;
  /**
   * - "mcp"               — dept-MCP query
   * - "rag"               — vector store
   * - "static"            — inline rows on the spec
   * - "workflow_staging"  — smartapp_workflow_staging Mongo collection (the
   *   recommendation inbox), fed by the app's agent on-demand or precomputed
   *   by an app trigger. Filters narrow by status / role / dept_id / max_age_days.
   */
  type: "mcp" | "rag" | "static" | "workflow_staging";
  ref: string;
  doc_types?: string[];
  filters?: Record<string, unknown>;
}

export interface ToolButton {
  /** Display label rendered on the button. */
  label: string;
  /** Must match a `tools_v2[].name` on the linked AgentSpec. */
  tool_name: string;
  /**
   * Static / pre-bound args, optionally with `{param.field}` templates the
   * renderer substitutes from the page params (e.g. a detail page's record
   * id) before POST. For user-entered values, use a form whose
   * `on_submit.tool_name` fires the write with the form fields as args.
   */
  args?: Record<string, unknown>;
  /** When set, renderer shows a confirm dialog with this text. */
  confirm?: string;
  /** Optional per-button role allowlist (enforced server-side). */
  roles?: string[];
}

interface PanelBase {
  id: string;
  title?: string;
  /** Lucide icon beside the panel title (closed set, publish rule I-01). */
  icon?: string;
  permissions?: string[];
  /**
   * UI buttons that POST to /apps/{slug}/tool/{tool_name} and invoke a
   * `tools_v2` entry directly (no LLM). Each button is gated server-
   * side against this panel's allowlist.
   */
  tool_buttons?: ToolButton[];
}

export interface FormOnSubmit {
  /** LLM path: run this agent action with the form fields as inputs. */
  agent_action?: string;
  /**
   * Direct (no-LLM) path: fire this tools_v2 tool with the form fields as
   * arguments via /apps/{slug}/tool/{tool_name}. Mutually exclusive with
   * agent_action. Use for classic write forms (add comment, assign, transfer).
   */
  tool_name?: string;
  /** Page to navigate to after the form submits (action runs first if both set). */
  navigate?: NavigateTarget;
}

/** One step of a multi-step (wizard) form. */
export interface FormStep {
  title: string;
  fields: string[];
  description?: string;
}

export interface FormPanel extends PanelBase {
  type: "form";
  schema_ref?: string;
  schema_inline?: Record<string, unknown>;
  accepts_files?: boolean;
  accepted_file_types?: string[];
  on_submit?: FormOnSubmit;
  /** Optional wizard: group fields into ordered steps with Back/Next. */
  steps?: FormStep[];
  /** "edit" prefills from an existing record (?id=) and saves changes back to
   *  it; "create" (default) is a blank insert form. */
  mode?: "create" | "edit";
  /** edit mode: data_source id to read the current record from. */
  prefill_source?: string;
  /** edit mode: the record key column (fetched by + re-sent on submit). */
  key_field?: string;
}

export interface NavigateTarget {
  /** Target page id. Must reference a Page declared in AppSpec.pages[]. */
  page: string;
  /**
   * Query string params to append. Values may use templates:
   * - `{row.field}` on a queue action substitutes the clicked row's field.
   * - `{result.field}` on a form on_submit substitutes the agent run output.
   * - `{form.field}` on a form on_submit substitutes a form field value.
   */
  params?: Record<string, string>;
}

export interface QueueAction {
  label: string;
  /** Lucide icon on the action button. */
  icon?: string;
  /** Agent action to invoke. May coexist with navigate (action first). */
  agent_action?: string;
  /** Page to navigate to when this action is clicked. */
  navigate?: NavigateTarget;
  /** Constant inputs merged onto the clicked row when invoking agent_action. */
  args?: Record<string, unknown>;
  /**
   * When true, the action also fires on plain row click (in addition to its
   * button). At most one action per queue may set this.
   */
  is_row_click?: boolean;
}

/** Semantic badge colors — never hex, so dark mode keeps working. */
export type BadgeColor = "green" | "amber" | "red" | "blue" | "slate";
/** Column display formats (C7). */
export type ColumnFormat =
  | "status_pill" | "currency" | "relative_time" | "progress" | "grade";

/** Sort spec for a queue panel's default ordering. */
export interface QueueSort {
  column: string;
  dir?: "asc" | "desc";
}

export interface QueuePanel extends PanelBase {
  type: "queue";
  data_source: string;
  query?: Record<string, unknown>;
  columns?: string[];
  actions?: QueueAction[];
  /**
   * Presentation options (P1/WS-A). All optional — the runtime applies
   * sensible defaults and auto-detection when these are absent, so existing
   * v0 specs render with the upgraded queue automatically.
   */
  /** Initial view. Default: "cards". "split" = master-detail two-pane. */
  view?: "cards" | "table" | "kanban" | "split";
  /** Rows per page. Default: 12. */
  page_size?: number;
  /** Columns the search box matches. Default: all columns. */
  searchable_columns?: string[];
  /** Columns to expose as faceted filters. Default: auto-detect low-cardinality columns. */
  filters?: string[];
  /** Column whose value renders as a status badge on cards. Default: auto-detect. */
  badge_column?: string;
  /** Semantic colors for badge_column VALUES, e.g. {pending:"amber"}. */
  badge_colors?: Record<string, BadgeColor>;
  /** Columns rendered de-emphasized (small, muted) after the main ones. */
  secondary_columns?: string[];
  /** Display formatting per column (status_pill/currency/relative_time/progress). */
  column_formats?: Record<string, ColumnFormat>;
  /** Column used as the card title. Default: auto-detect (name/title/id). */
  title_column?: string;
  /** Default sort applied on load. */
  default_sort?: QueueSort;
  /** view="kanban": column whose distinct values become the board columns. */
  group_by?: string;
}

export interface DetailSection {
  type:
    | "agent_timeline"
    | "agent_chat"
    | "approval"
    | "attachment"
    | "comments"
    | "documents"
    | "fields"
    | "markdown";
  title?: string;
  /** Lucide icon beside the section title. */
  icon?: string;
  agent_role?: string;
  data_source?: string;
  fields?: string[];
  roles?: string[];
  content?: string;
  /** When true, render inside a collapsible <details> disclosure (accordion). */
  collapsible?: boolean;
  /** With collapsible: start closed. */
  collapsed?: boolean;
}

/** A button on a detail panel that runs an agent action on THAT record.
 *  Deliberately narrower than QueueAction: no `is_row_click` (there are no
 *  rows) and `agent_action` is required (a detail panel IS the destination,
 *  so there is no navigate-only variant). */
export interface DetailAction {
  label: string;
  /** Lucide icon on the action button. */
  icon?: string;
  agent_action: string;
  /** Constant inputs merged onto the record when invoking agent_action. */
  args?: Record<string, unknown>;
}

export interface DetailPanel extends PanelBase {
  type: "detail";
  /** Buttons that run an agent action against this panel's record. On an embed
   *  page this is the trigger — it replaces the one-row queue that existed
   *  only to hold the button. */
  actions?: DetailAction[];
  /** Queue panel this detail reads its record from (officer clicks a row).
   *  Mutually exclusive with `data_source`. */
  linked_to?: string;
  /** Read the record DIRECTLY by id, with no queue to click — used on embed
   *  pages, where the host application passes the record id in. Mutually
   *  exclusive with `linked_to`. */
  data_source?: string;
  /** Column identifying a record, on the linked queue's source or on
   *  `data_source`; matched against `?id=`. */
  id_field?: string;
  /** "profile" tops the page with a header card (key facts + status pill). */
  layout?: "stack" | "profile";
  /** layout="profile": 2-5 columns shown large in the header card. */
  header_fields?: string[];
  /** layout="profile": column rendered as the header status pill. */
  status_field?: string;
  status_colors?: Record<string, BadgeColor>;
  sections?: DetailSection[];
}

/** One audit run in a detail panel's `agent_timeline` section. */
export interface DetailRun {
  correlation_id: string;
  created_at?: string;
  action?: string;
  status?: string;
  decision?: string;
  reasoning?: string;
  duration_ms?: number;
  model?: string;
  requested_by?: string;
  inputs?: Record<string, unknown>;
}

/** One pending-approval run in a detail panel's `approval` section. */
export interface DetailPendingRun {
  correlation_id: string;
  action?: string;
  requested_by?: string;
  created_at?: string;
  inputs?: Record<string, unknown>;
  approver_roles?: string[];
  /** Agent's recommended outcome from the plan phase. */
  decision?: string;
  reasoning?: string;
  /** Captured-but-not-yet-applied writes when the row came from a
   *  queue_action plan-then-apply run. Empty for legacy threshold-
   *  gated approvals where the LLM never ran. */
  planned_writes?: PlannedWrite[];
  /** Content hash of planned_writes — echoed on approve to verify display==commit. */
  plan_hash?: string;
}

export type ControlType =
  | "text" | "textarea" | "number" | "currency" | "date" | "datetime" | "time"
  | "select" | "multiselect" | "radio" | "lookup" | "checkbox" | "toggle" | "hidden";

export interface OptionItem {
  value: unknown;
  label?: string;
}

export interface OptionsSource {
  kind: "static" | "data_source" | "agent";
  values?: OptionItem[];
  data_source?: string;
  value_column?: string;
  label_column?: string;
  filter?: Record<string, unknown>;
  limit?: number;
  /** Typeahead mode (kind="data_source"): render a search-as-you-type combobox
   *  that debounces the query to /field-options?q=. */
  search?: boolean;
}

/** Composable, schema-driven input control (form fields + plan overrides). */
export interface FieldSpec {
  name: string;
  label?: string;
  control?: ControlType;
  required?: boolean;
  default?: unknown;
  placeholder?: string;
  help?: string;
  editable?: boolean;
  options?: OptionsSource;
  minimum?: number;
  maximum?: number;
  pattern?: string;
  editable_by_roles?: string[];
}

export interface PlannedWrite {
  tool?: string;
  kind?: string;
  source_id?: string;
  dataset_id?: string;
  action_id?: string;
  payload?: Record<string, unknown>;
  idempotency_key?: string;
  /** Officer-overridable fields for this write (from the action's editable_fields). */
  editable_fields?: FieldSpec[];
  /** Agent-proposed option candidates per field (OptionsSource kind="agent"). */
  _options?: Record<string, OptionItem[]>;
}

/** One human note/comment threaded to a record (the `comments` section). */
export interface RecordComment {
  id?: string;
  text: string;
  author?: string | null;
  created_at?: string | null;
}

/** A resolved detail section — the server fills in per-type payloads. */
export interface DetailSectionData {
  type: DetailSection["type"];
  title?: string;
  fields?: string[];
  content?: string;
  agent_role?: string;
  documents?: Record<string, unknown>[];
  runs?: DetailRun[];
  pending?: DetailPendingRun[];
  roles?: string[];
  /** Human notes/comments for the `comments` section. */
  comments?: RecordComment[];
  note?: string;
  /** The section's data_source id — lets a documents section sign an
   *  "Open original" URL (parity with the standalone document_view panel). */
  data_source?: string;
  /** The authoritative column the record was matched by (e.g. inspection_id) —
   *  an `attachment` section re-reads media through the MCP by this key rather
   *  than guessing from column names. */
  key_field?: string;
}

/** Response of GET /apps/{slug}/detail/{panel_id}. */
export interface DetailData {
  panel_id: string;
  /** Null when the panel binds via `data_source` rather than a queue. */
  linked_to?: string | null;
  record_id?: string;
  record?: Record<string, unknown> | null;
  record_columns?: string[];
  sections: DetailSectionData[];
  note?: string;
}

export interface DashboardMetric {
  name: string;
  agg: "count" | "sum" | "avg" | "min" | "max" | "ratio";
  field?: string;
  window?: string;
  data_source?: string;
  /** Predicate scoping the metric so the number matches its label. */
  filter?: Record<string, unknown>;
  /** Prior-period delta (▲/▼ chip) config — computed server-side. */
  compare?: { date_field: string; grain?: string; periods?: number };
  /** Sparkline series config — computed server-side. */
  trend?: { date_field: string; grain?: string; points?: number };
  /** Clean display subtitle (avoids leaking the raw field id). */
  label?: string;
  /** Progress-to-target: renders a progress bar of value against target. */
  target?: number;
  /** Ascending fraction-of-target cut points colouring the bar (e.g. [0.5,0.8]). */
  thresholds?: number[];
  /** Lucide icon on the KPI tile; auto-picked by semantics when absent. */
  icon?: string;
}

export interface DashboardPanel extends PanelBase {
  type: "dashboard";
  metrics: DashboardMetric[];
}

/** Page-header band (C1): icon + headline + optional live metric + ≤2
 *  navigation actions. */
export interface HeroPanel extends PanelBase {
  type: "hero";
  headline: string;
  subtitle?: string;
  metric?: DashboardMetric;
  actions?: QueueAction[];
}

/** Compact KPI band with delta arrows + sparklines (C2). */
export interface StatStripPanel extends PanelBase {
  type: "stat_strip";
  metrics: DashboardMetric[];
}

/** Vertical event feed bound to a tabular source (C3). */
export interface TimelinePanel extends PanelBase {
  type: "timeline";
  data_source: string;
  query?: Record<string, unknown>;
  date_field: string;
  title_field: string;
  subtitle_field?: string;
  /** Column whose VALUE is a lucide icon name per entry. */
  icon_field?: string;
  badge_field?: string;
  badge_colors?: Record<string, BadgeColor>;
  limit?: number;
}

export interface ChartPanel extends PanelBase {
  type: "chart";
  chart_type: "line" | "bar" | "pie" | "area" | "funnel" | "scatter";
  data_source: string;
  query?: Record<string, unknown>;
  x: string;
  y: string | string[];
  group_by?: string;
  limit?: number;
  stacked?: boolean;
}

export interface AgentChatPanel extends PanelBase {
  type: "agent_chat";
  agent_role?: string;
  starter_prompts?: string[];
}

export interface DocumentViewPanel extends PanelBase {
  type: "document_view";
  data_source: string;
  doc_types?: string[];
}

export interface MarkdownPanel extends PanelBase {
  type: "markdown";
  content: string;
}

/** A static, non-interactive callout band. No data binding — for a live
 *  number use a dashboard KPI tile. `content` is plain text / inline markdown. */
export interface NoticePanel extends PanelBase {
  type: "notice";
  tone?: "info" | "warn" | "error" | "success";
  content: string;
}

/** Month-grid calendar of records with a date column. Read-only. */
export interface CalendarPanel extends PanelBase {
  type: "calendar";
  data_source: string;
  query?: Record<string, unknown>;
  date_field: string;
  title_field: string;
  color_field?: string;
  limit?: number;
}

/** Geospatial marker map (Leaflet / OSM) of records with lat/lng columns. */
export interface MapPanel extends PanelBase {
  type: "map";
  data_source: string;
  query?: Record<string, unknown>;
  lat_field: string;
  lng_field: string;
  label_field?: string;
  limit?: number;
}

/** One control in a filter_bar — sets a URL param on change so every panel
 *  that references `{param.<param>}` re-queries. Options come from the same
 *  `/field-options` endpoint that form combos use. */
export interface FilterControl {
  param: string;
  label: string;
  control_type?: "dropdown" | "segment" | "daterange";
  options?: {
    kind: "static" | "data_source" | "agent";
    values?: { value: string; label?: string }[];
    data_source?: string;
    value_column?: string;
    label_column?: string;
    search?: boolean;
  };
  default?: string;
  all_label?: string;
}

/** A strip of filter controls bound to page params. Allowed on dashboard +
 *  standard pages; each control updates a URL param → the page re-renders and
 *  panels re-query. No per-app code; view-only (never writes). */
export interface FilterBarPanel extends PanelBase {
  type: "filter_bar";
  controls: FilterControl[];
}

/** One builder-defined feed in a notification centre. */
export interface NotificationFeed {
  /** group/badge label for this feed's items. */
  label: string;
  /** "data_source" (read `source` matching `filters`) or the built-in "approvals" inbox. */
  kind?: "data_source" | "approvals";
  /** data_source id (required when kind="data_source"). */
  source?: string;
  /** predicate selecting notable rows; supports {now}/{now-Nh}/… time tokens. */
  filters?: Record<string, unknown>;
  /** catalogue column to title each item. */
  title_field?: string;
  /** catalogue column for the item's secondary line. */
  sub_field?: string;
  /** badge colour for this feed's items. */
  tone?: "info" | "success" | "warning" | "danger" | "neutral";
  /** where a click on this feed's item routes (templated with {row.<column>}). */
  navigate?: NavigateTarget;
  limit?: number;
}

export interface NotificationsPanel extends PanelBase {
  type: "notifications";
  /** the feeds this centre aggregates — fully builder-defined. */
  feeds: NotificationFeed[];
}

/** One resolved notification item. */
export interface NotificationItem {
  type: "approval" | "feed";
  /** the feed's label (badge text). */
  label?: string;
  tone?: "info" | "success" | "warning" | "danger" | "neutral";
  id?: string | null;
  title: string;
  sub?: string | null;
  created_at?: string | null;
  correlation_id?: string;
  row?: Record<string, unknown>;
  /** this item's feed-level navigate target (per-feed routing). */
  navigate?: NavigateTarget;
}

export type Panel =
  | FormPanel
  | QueuePanel
  | DetailPanel
  | DashboardPanel
  | HeroPanel
  | StatStripPanel
  | TimelinePanel
  | ChartPanel
  | AgentChatPanel
  | DocumentViewPanel
  | MarkdownPanel
  | NoticePanel
  | CalendarPanel
  | MapPanel
  | FilterBarPanel
  | NotificationsPanel;

export type PageLayout = "grid" | "stack" | "split" | "tabs";

export interface PageParam {
  name: string;
  type?: "string" | "number" | "boolean";
  required?: boolean;
  description?: string;
}

export interface Page {
  id: string;
  /** Optional explicit URL path under /{slug}. Defaults to '/{id}'. */
  path?: string;
  title?: string;
  /** Lucide icon name rendered in the nav. */
  icon?: string;
  /** When true, page is reachable via navigate() but not shown in nav. */
  hide_in_nav?: boolean;
  permissions?: string[];
  /**
   * Page purpose. 'dashboard' renders the executive KPI/chart grid with the
   * ECharts exec theme + the hero-brief copilot at the top. Defaults to
   * 'standard'.
   */
  kind?: PageKind;
  /** How panels in this page are arranged. */
  layout?: PageLayout;
  panels: Panel[];
  params?: PageParam[];
}

export interface AppNavigation {
  style?: "sidebar" | "topbar" | "none";
  /** Page id that '/' resolves to. Defaults to the first page in pages[]. */
  default_page?: string;
  /** When true, an agent_chat panel becomes a floating, app-wide chat. */
  show_chat_globally?: boolean;
}

export type FactorSetMode = "composite" | "checklist";

/** What the SCREEN calls these things. The engine says "factor" forever. */
export interface FactorTerminology {
  panel: string;
  row: string;
  band: string;
  composite: string;
}

export interface FactorBand {
  label: string;
  /** Inclusive upper bound on the factor's SCORE. Absent on the catch-all, and
   *  absent on every band of a label-only factor. */
  max?: number | null;
  hint?: string | null;
}

export interface FactorSpec {
  id: string;
  label: string;
  /** Composite only — the factor's maximum score. A score is always out of it. */
  weight?: number | null;
  scope?: "entity" | "case";
  bands: FactorBand[];
}

export interface GateSpec {
  id: string;
  label: string;
}

export interface GradeStep {
  grade: string;
  /** Percentage of the attainable maximum, not a raw total. */
  min?: number | null;
  hint?: string | null;
}

/**
 * The customer's declared rubric. Optional, and absent is the common case.
 * `mode` is permanent for a published app version.
 */
export interface FactorSet {
  mode: FactorSetMode;
  terminology: FactorTerminology;
  factors: FactorSpec[];
  gates?: GateSpec[];
  grade_scale?: GradeStep[];
}

export interface GateResult {
  gate_id: string;
  label: string;
  /** 'flag' is what an unevaluated gate degrades to — never a silent pass. */
  status: "pass" | "fail" | "flag";
  rationale?: string;
  citations?: Record<string, unknown>[];
}

export interface FactorScoreRow {
  factor_id: string;
  label: string;
  scope?: "entity" | "case";
  /** The EFFECTIVE score — the officer's when overridden, else the model's. */
  score?: number | null;
  weight?: number | null;
  band?: string | null;
  rationale?: string;
  citations?: Record<string, unknown>[];
  /** Learned clauses that fired on THIS factor. They annotate the row; they
   *  never move the score. */
  clauses_fired?: Record<string, unknown>[];
  /** The model's self-reported certainty. NOT the score. */
  confidence?: number | null;
  unscored?: boolean;
  /** What the model scored before an officer changed it. Set only on override,
   *  and never overwritten by a second edit. */
  original_score?: number | null;
  override_reason?: string | null;
  overridden_by?: string | null;
  overridden_at?: string | null;
  /** The policy passage behind this factor changed since the rubric was
   *  extracted and confirmed. Advisory — scoring continues. */
  sop_drift?: boolean;
}

/**
 * Computed in code from the declared weights — the model scores one factor at
 * a time and never sees the arithmetic. When `gated` is true a hard policy gate
 * failed or could not be evaluated, and total/percent/grade are suppressed:
 * read the gate, not the score.
 */
export interface FactorScorecard {
  mode: FactorSetMode;
  terminology: FactorTerminology;
  gates?: GateResult[];
  gated?: boolean;
  rows: FactorScoreRow[];
  total?: number | null;
  max_total?: number | null;
  percent?: number | null;
  grade?: string | null;
  unscored_factor_ids?: string[];
  /** True when any row carries an officer's score. total/percent/grade above
   *  are the POST-override figures — what the officer is acting on, and so what
   *  the ledger records. The pre-override pair is kept beside them. */
  overridden?: boolean;
  grade_before_override?: string | null;
  percent_before_override?: number | null;
  /** Bumped on every accepted override. The server's write is conditional on
   *  the revision it was given, so a concurrent edit 409s instead of silently
   *  replacing someone else's correction. */
  revision?: number;
  /** Factors whose SOP passage moved since extraction — the app needs
   *  re-extraction and the officer should know before relying on the grade. */
  sop_drift_factor_ids?: string[];
}

export interface AppSpec {
  spec_version: SpecVersion;
  app_id?: string;
  slug: string;
  title: string;
  description?: string;
  /** SA / dept / org owning the app. Edit access flows through this. */
  owner_type?: "service_account" | "dept" | "org";
  owner_id?: string;
  /**
   * Who can SEE and RUN this app. Edit access is independent.
   *   'owner'          — only owner_sa_id members
   *   'team:<sa_id>'   — members of a specific team SA
   *   'dept:<dept_id>' — everyone in a department
   *   'org'            — everyone in the tenant
   */
  audience?: string;
  tenant_id?: string;
  /**
   * Per-item review gate for multimodal runs (image_analyze / doc_extract).
   * Only enforced when a run produces per-item findings (images/documents).
   *   'hard' (default) — record Apply is blocked until EVERY image AND document
   *                      is dispositioned (accept/reject/cancel).
   *   'soft'           — Apply allowed, but warns about un-reviewed items first.
   *   'none'           — items are reviewable but never gate the decision.
   */
  item_review_gate?: "hard" | "soft" | "none";
  /**
   * The customer's declared rubric (docs/factor-scorecard-plan.md). Absent on
   * most apps, which is correct — a scorecard panel without it is rejected at
   * publish (FS-03) rather than rendering an empty box.
   */
  factor_set?: FactorSet;
  /**
   * Clause-memory vocabulary (docs/clause-memory-graph-plan.md §2). The
   * runtime consumes ONE part of it: `reason_codes` — the closed list an
   * officer picks from when rejecting/correcting a recommendation. The picker
   * itself is a PLATFORM surface rendered here for every app; the builder only
   * declares the taxonomy. A free-text-only reason cannot cluster, so without
   * the code the correction can never become a learned judgement.
   */
  case_signature?: {
    version?: number;
    facets?: unknown[];
    reason_codes?: { code: string; label: string; hint?: string }[];
    learning?: Record<string, unknown>;
  };
  agent_id: string;
  /** Artefact kind. 'app' (default) — any page may set page.kind='dashboard' for the executive treatment. */
  kind?: AppKind;
  theme?: Theme;
  data_sources?: DataSource[];
  /** Single-page shorthand. Mutually exclusive with `pages`. */
  panels: Panel[];
  /** Multi-page layout. When non-empty, `panels` is empty. */
  pages?: Page[];
  navigation?: AppNavigation;
  permissions?: Record<string, string[]>;
  custom_modules?: { id: string; url: string; integrity?: string }[];
  version?: number;
  deployed_at?: string;
  status?: AppStatus;
}

export interface AppDetail {
  app_spec: AppSpec;
  agent_spec: AgentSpec;
  /** Which store served this app. "test" → render the TEST banner so a
   *  reviewer never mistakes a rehearsal for live work. Absent on older
   *  payloads → treated as "prod". */
  environment?: "test" | "prod";
}

export interface AppSummary {
  app_id: string;
  slug: string;
  title: string;
  description?: string;
  owner_type?: "service_account" | "dept" | "org";
  owner_id?: string;
  /** See AppSpec.audience for grammar. */
  audience?: string;
  tenant_id?: string;
  kind?: AppKind;
  /** A dashboard is an app with a page.kind='dashboard'; the list UI buckets on this, not kind. */
  has_dashboard_page?: boolean;
  status: AppStatus;
  version: number;
  deployed_at?: string;
  url: string;
}

export interface AppListResponse {
  apps: AppSummary[];
  total: number;
}

export interface PanelMetricValue {
  name: string;
  agg: string;
  field?: string | null;
  /** Clean display subtitle (avoids leaking the raw field id). */
  label?: string | null;
  /** Source-computed aggregate over the whole (filtered) table — not the capped rows. */
  value: number | null;
  /** Real prior-period delta (▲/▼ chip), computed server-side. */
  delta?: { dir: "up" | "down" | "flat"; pct: number; text: string } | null;
  /** Real grouped-by-time series for the sparkline, computed server-side. */
  trend?: number[] | null;
  /** Bucket labels ('YYYY-MM-DD') aligned 1:1 with `trend` — drives the
   * sparkline hover tooltip ("date: value"). */
  trend_labels?: string[] | null;
}

export interface PanelData {
  panel_id: string;
  data_source: string;
  columns: string[];
  rows: Record<string, unknown>[];
  total: number;
  truncated: boolean;
  source_kind: "static" | "mcp" | "rag" | "workflow_staging";
  /** Present for dashboard panels: true KPI aggregates computed at the source. */
  metrics?: PanelMetricValue[];
  note?: string;
}
