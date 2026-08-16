/**
 * `next/dynamic` shim for the embed bundle.
 *
 * Aliased in at build time; the runtime's source is untouched. PanelRenderer
 * uses `dynamic()` once (LeafletMap, PanelRenderer.tsx:4928). React.lazy plus
 * a Suspense boundary reproduces the contract.
 *
 * NOTE ON CODE SPLITTING: esbuild's `iife` output format cannot code-split, so
 * a dynamic import here is inlined into the single bundle rather than fetched
 * as a chunk. That is exactly why leaflet and echarts are aliased to stubs in
 * scripts/build-embed.mjs — without those aliases the "lazy" module would ship
 * inside the bundle anyway and the size win would be imaginary.
 */
import { Suspense, lazy, type ComponentType } from "react";
import * as React from "react";

type Loader<P> = () => Promise<ComponentType<P> | { default: ComponentType<P> }>;

interface DynamicOptions {
  /** Rendered while the lazy module resolves. */
  loading?: ComponentType<Record<string, never>>;
  /** Irrelevant here — an embed never server-renders. Accepted and ignored. */
  ssr?: boolean;
}

export default function dynamic<P extends object>(
  loader: Loader<P>,
  options: DynamicOptions = {},
): ComponentType<P> {
  const Lazy = lazy(async () => {
    const mod = await loader();
    // next/dynamic accepts both a bare component and a module namespace.
    return "default" in mod
      ? (mod as { default: ComponentType<P> })
      : { default: mod as ComponentType<P> };
  });

  const Loading = options.loading;
  // React.lazy's type is expressed in terms of PropsWithRef, which does not
  // unify with an unconstrained generic P. The runtime behaviour is correct;
  // only the type needs restating.
  const LazyComponent = Lazy as unknown as ComponentType<P>;

  return function DynamicShim(props: P) {
    return (
      <Suspense fallback={Loading ? <Loading /> : null}>
        <LazyComponent {...props} />
      </Suspense>
    );
  };
}
