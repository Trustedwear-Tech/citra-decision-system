// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * `react-dom` shim for the embed bundle — re-exports everything, but redirects
 * `createPortal(children, document.body)` into the embed's shadow root.
 *
 * WHY THIS IS NECESSARY
 * ---------------------
 * PanelRenderer renders modals through a portal into `document.body`
 * (`ModalPortal`, PanelRenderer.tsx:2314) so a fixed overlay escapes the
 * app-shell stacking context and the sticky header cannot paint over it. That
 * is correct in the full app.
 *
 * Inside an embed it breaks badly. The stylesheet lives INSIDE the shadow
 * root, so anything portalled to `document.body` lands in the host page's
 * light DOM with none of our CSS applied. And the thing rendered through that
 * portal is `RunResultModal` — the recommendation, the planned writes, and the
 * approve/reject with reason capture. In other words the entire decision card
 * would render unstyled in the bank's page.
 *
 * Redirecting the portal target keeps the modal inside the shadow root where
 * the styles are, without editing PanelRenderer — the embed build aliases
 * `react-dom` to this module; the app build is untouched.
 *
 * Only `document.body` targets are redirected. A portal with an explicit
 * container is honoured as written, since the caller meant that element.
 */
import * as ReactDOMOriginal from "react-dom";

export * from "react-dom";

/**
 * A STACK, not a single value, because a page may hold more than one card.
 *
 * With a single slot, mounting a second card would steal the portal target
 * from the first, and — worse — destroying the second would reset it to null,
 * sending the first card's modals back to `document.body` where none of our
 * CSS reaches. The stack keeps the most recent mount as the target and
 * restores the previous one on teardown.
 *
 * Known limitation: while two cards are live, a modal opened from the OLDER
 * one still renders in the NEWER one's shadow root. Both are fixed-position
 * overlays covering the viewport, so it is invisible to the officer — but it
 * is the reason this is a stack and not a map.
 */
const portalRoots: Element[] = [];

/** Push the mount's portal container; pass null to pop the most recent. */
export function setEmbedPortalRoot(el: Element | null) {
  if (el) portalRoots.push(el);
  else portalRoots.pop();
}

export function createPortal(
  children: React.ReactNode,
  container: Element | DocumentFragment,
  key?: string | null,
): React.ReactPortal {
  const isDocumentBody =
    typeof document !== "undefined" && container === document.body;
  const portalRoot = portalRoots[portalRoots.length - 1];
  const target = isDocumentBody && portalRoot ? portalRoot : container;
  return ReactDOMOriginal.createPortal(children, target as Element, key);
}
