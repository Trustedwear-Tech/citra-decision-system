// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * `react-leaflet` stub — aliased in for the EMBED build only.
 *
 * LeafletMap is already behind `next/dynamic` in the renderer, but esbuild's
 * `iife` output cannot code-split, so a dynamic import is INLINED into the
 * single bundle. Without this alias leaflet would ship inside citra.js despite
 * looking lazily loaded — the size win would be imaginary.
 *
 * Exports mirror what LeafletMap.tsx imports: MapContainer, TileLayer,
 * CircleMarker, Popup, Tooltip. Only MapContainer renders anything; the rest
 * are inert so the component tree still type-checks and mounts.
 */
import type { ReactNode } from "react";
import { UnsupportedInEmbed } from "./unsupported";

export function MapContainer(_props: {
  children?: ReactNode;
  center?: unknown;
  zoom?: number;
  style?: React.CSSProperties;
  scrollWheelZoom?: boolean;
}) {
  return <UnsupportedInEmbed what="map" />;
}

/** Inert: never mounted, because MapContainer does not render its children. */
export function TileLayer(_props: Record<string, unknown>) {
  return null;
}
export function CircleMarker(_props: { children?: ReactNode } & Record<string, unknown>) {
  return null;
}
export function Popup(_props: { children?: ReactNode }) {
  return null;
}
export function Tooltip(_props: { children?: ReactNode }) {
  return null;
}
