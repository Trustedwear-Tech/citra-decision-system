/**
 * Empty module — the alias target for side-effect-only imports the embed does
 * not need, e.g. `import "leaflet/dist/leaflet.css"` inside LeafletMap.
 *
 * Resolving those to nothing keeps the leaflet package out of the bundle
 * entirely rather than pulling it in for a stylesheet no map will use.
 */
export {};
