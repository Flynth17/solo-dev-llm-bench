/* Solo Dev LLM Bench — Shared Results status/presentation layer (RM-26-AA Results UX P1).
 *
 * ONE authoritative place that maps a benchmark's semantic state (as exposed by the
 * read-only read models) to presentation metadata: a human-facing uppercase label, a
 * tone, a shared chip CSS class and a concise explanation. Every Results surface renders
 * status through this helper so there is a single vocabulary instead of five ad-hoc maps.
 *
 * Contract (presentation only -- never benchmark logic):
 *   - This layer NEVER scores, ranks, computes eligibility or invents a value. It only
 *     translates an already-authoritative state string into presentation metadata.
 *   - N/A stays N/A: missing/absent data is rendered as an em-dash by the callers' value
 *     formatters (na()/fmt()), never here and never coerced to 0. A null/empty STATE falls
 *     back to "UNKNOWN" -- a safe, distinct state that is never treated as completed.
 *   - Mandatory distinctions are preserved verbatim: unknown != not_stored, incomplete !=
 *     failed, diagnostic != failed (diagnostic is non-scoreable), partial != completed,
 *     unsupported == a gap (neutral), ambiguous != unavailable.
 *   - Colour is NEVER the sole signal: every chip shows its uppercase word; the tone only
 *     drives a background/edge treatment via the shared .rs-chip-{tone} classes.
 *
 * Exposed as a global (`window.statusPresentation`) to match the existing no-framework
 * classic-script convention (e.g. escapeHtml from results-utils.js). Load this file before
 * any Results surface that consumes it.
 */
(function () {
    "use strict";

    // Semantic state -> presentation metadata. Labels are uppercase and stable; tones map to
    // the shared .rs-chip-{tone} palette in results-shell.css. Explanations are concise,
    // human-language and truthful -- they never imply a score for non-scoreable states.
    var STATE_PRESENTATION = {
        "completed": {
            tone: "positive",
            label: "COMPLETED",
            explanation: "Ran to completion; all expected measurements present and valid."
        },
        "partial": {
            tone: "warning",
            label: "PARTIAL",
            explanation: "Ran but a required measurement is missing or aborted midway."
        },
        "failed": {
            tone: "negative",
            label: "FAILED",
            explanation: "Terminated with an observable failure."
        },
        "incomplete": {
            tone: "warning",
            label: "INCOMPLETE",
            explanation: "Valid but not fully scored -- inspectable diagnostic evidence, not a canonical result."
        },
        "diagnostic": {
            tone: "info",
            label: "DIAGNOSTIC",
            explanation: "Collected for inspection only; never contributes to a score or ranking."
        },
        "ambiguous": {
            tone: "warning",
            label: "AMBIGUOUS",
            explanation: "Identity cannot be uniquely attributed to one configuration for this pairing."
        },
        "unsupported": {
            tone: "neutral",
            label: "UNSUPPORTED",
            explanation: "Contract point unhostable for this config -- shown as a gap, never scored or rescaled."
        },
        "unavailable": {
            tone: "neutral",
            label: "UNAVAILABLE",
            explanation: "No eligible result available for this dimension."
        },
        "unknown": {
            tone: "neutral",
            label: "UNKNOWN",
            explanation: "Status could not be determined; distinct from a stored value (never coerced to zero)."
        },
        "not_stored": {
            tone: "neutral",
            label: "NOT STORED",
            explanation: "Nothing was recorded for this field; never coerced to zero (N/A stays N/A)."
        }
    };

    // Safe default for null / empty / unrecognized states. Never reads as completed, so a
    // state the backend does not name is surfaced honestly as UNKNOWN rather than guessed.
    var DEFAULT_PRESENTATION = STATE_PRESENTATION["unknown"];

    function normalize(state) {
        if (state === undefined || state === null) { return ""; }
        return String(state).trim().toLowerCase();
    }

    /**
     * Map a semantic benchmark state to presentation metadata.
     * @param {string|null|undefined} state - authoritative state from a read model.
     * @returns {{state: (*), label: string, tone: string, className: string, explanation: (string|null)}}
     */
    function statusPresentation(state) {
        var key = normalize(state);
        var def = STATE_PRESENTATION[key] || DEFAULT_PRESENTATION;
        return {
            // Original semantic state, unchanged (may be null/undefined for absent data).
            state: state,
            label: def.label,
            tone: def.tone,
            // Shared chip class -- the single visual vocabulary across every Results surface.
            className: "rs-chip-" + def.tone,
            explanation: def.explanation || null
        };
    }

    // Expose globally for the classic Result surfaces (results.js, v2-result.js, ...).
    if (typeof window !== "undefined") {
        window.statusPresentation = statusPresentation;
    } else if (typeof module !== "undefined" && module.exports) {
        module.exports = { statusPresentation: statusPresentation, STATE_PRESENTATION: STATE_PRESENTATION };
    }
})();
