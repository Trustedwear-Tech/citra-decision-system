/**
 * Resource taxonomy — frontend mirror of the platform's two-tier model.
 *
 * MANAGED resources are admin-visible in Workflow / SmartApp admin
 * pages. PERSONAL resources are user-only; admin pages never surface
 * them.
 *
 * KEEP IN LOCKSTEP WITH:
 *   - Citra-User-Service/src/config/resourceTaxonomy.js
 *   - Citra-Service/config/resource_taxonomy.py
 */

export const MANAGED_RESOURCES = Object.freeze([
  'workflow',
  'smart_app',
]);

export const PERSONAL_RESOURCES = Object.freeze([
  'presentation',
  'report',      // composer_reports
  'printable',
  'diagram',     // shared with mindmaps
  'note',        // Notes
  'page',
  'vault',       // folders
  'project',
]);

export const ALL_RESOURCES = Object.freeze([
  ...MANAGED_RESOURCES,
  ...PERSONAL_RESOURCES,
]);

export function isManaged(resourceType) {
  return MANAGED_RESOURCES.includes(resourceType);
}

export function isPersonal(resourceType) {
  return PERSONAL_RESOURCES.includes(resourceType);
}
