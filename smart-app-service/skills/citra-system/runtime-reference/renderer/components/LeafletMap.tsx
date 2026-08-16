"use client";

/**
 * Leaflet marker map for the MapPanel. Loaded via next/dynamic with ssr:false
 * (Leaflet touches `window`). Uses CircleMarker rather than the default pin so
 * there is no marker-icon image asset to resolve through the bundler.
 */

import { useMemo } from "react";
import { MapContainer, TileLayer, CircleMarker, Popup, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";

export interface MapPoint {
  lat: number;
  lng: number;
  label?: string;
}

export default function LeafletMap({ points }: { points: MapPoint[] }) {
  // Center on the centroid of the points; pick a zoom that isn't absurd for a
  // single point vs a spread.
  const { center, zoom } = useMemo(() => {
    const lats = points.map((p) => p.lat);
    const lngs = points.map((p) => p.lng);
    const cLat = lats.reduce((a, b) => a + b, 0) / points.length;
    const cLng = lngs.reduce((a, b) => a + b, 0) / points.length;
    const spread = Math.max(
      Math.max(...lats) - Math.min(...lats),
      Math.max(...lngs) - Math.min(...lngs),
    );
    const z = spread > 8 ? 4 : spread > 2 ? 6 : spread > 0.5 ? 9 : spread > 0.05 ? 12 : 14;
    return { center: [cLat, cLng] as [number, number], zoom: z };
  }, [points]);

  return (
    <MapContainer
      center={center}
      zoom={zoom}
      scrollWheelZoom={false}
      style={{ height: 360, width: "100%", borderRadius: 8 }}
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {points.map((p, i) => (
        <CircleMarker
          key={i}
          center={[p.lat, p.lng]}
          radius={7}
          pathOptions={{ color: "#2563eb", fillColor: "#3b82f6", fillOpacity: 0.7, weight: 2 }}
        >
          {p.label && <Tooltip>{p.label}</Tooltip>}
          {p.label && <Popup>{p.label}</Popup>}
        </CircleMarker>
      ))}
    </MapContainer>
  );
}
