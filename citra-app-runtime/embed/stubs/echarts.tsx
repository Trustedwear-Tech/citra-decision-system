/**
 * `echarts-for-react` stub — aliased in for the EMBED build only.
 *
 * Why an alias and not a lazy import in the renderer: echarts is reached by
 * two STATIC imports in shared code (PanelRenderer.tsx:20 for the chart panel,
 * and KpiSparkline.tsx:6 for dashboard/stat_strip tiles), so a bundler cannot
 * tree-shake it. Converting those to lazy boundaries would have changed the
 * file the live app compiles — and `e2e/runtime.spec.ts` has no chart or
 * sparkline assertions, so a regression there would ship unnoticed. Aliasing
 * leaves the app build byte-identical and moves all the risk into the embed.
 *
 * The prop type mirrors the call sites loosely on purpose: this renders a
 * notice and never reads them.
 */
import { UnsupportedInEmbed } from "./unsupported";

export interface EChartsStubProps {
  option?: unknown;
  style?: React.CSSProperties;
  theme?: string;
  notMerge?: boolean;
  lazyUpdate?: boolean;
  opts?: unknown;
  onEvents?: unknown;
}

export default function ReactEChartsStub(_props: EChartsStubProps) {
  return <UnsupportedInEmbed what="chart" />;
}
