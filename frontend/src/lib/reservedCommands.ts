// Composer slash-commands handled by their own logic, NOT the workflow picker.
// Keep in sync with the backend RESERVED_WORKFLOW_SLUGS. `goal` is owned by the
// goal-mode feature; the workflow picker must never intercept it.
export const RESERVED_COMPOSER_COMMANDS = new Set<string>(['goal']);
