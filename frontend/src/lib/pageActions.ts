/**
 * Declarative page-action helper. Each page calls `declareActions([...])`
 * once when building its `PageContext`. The shape is identical to the
 * backend `PageAction` schema; keeping them in sync is the page's
 * responsibility (Phase 3 will add a build-time check).
 */
import type { PageAction } from "../types";

export function declareActions(actions: PageAction[]): PageAction[] {
  return actions;
}
