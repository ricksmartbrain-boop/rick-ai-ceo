#!/usr/bin/env node
/**
 * DISABLED: unsafe PH auto-reply script.
 *
 * This script previously posted generic replies and had no reliable
 * duplicate prevention. It is intentionally disabled.
 *
 * Use instead:
 * - scripts/ph-comment-monitor.js for read-only inspection
 * - scripts/ph-reply-plan.js for reply drafting and duplicate spotting
 */

console.error('PH auto-reply is disabled. Use scripts/ph-comment-monitor.js or scripts/ph-reply-plan.js.');
process.exit(2);
