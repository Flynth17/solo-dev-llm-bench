/** Solo Dev LLM Bench — Compare view (RM-26-AA-0013, ST-003).
 *
 * Establishes the visible Compare workspace: two subject selectors populated from
 * the authoritative comparison subject catalogue (`/api/comparison/subjects`) and a
 * model/config identity header projected by `/api/comparison?a=&b=`.
 *
 * Scope discipline (ST-003 = identity only):
 *  - This view renders identities, family-specific configuration truth and per-family
 *    dimension availability. It displays NO benchmark metrics: no throughput values,
 *    no time-to-first-token figures, no Workflow correctness results, no Context
 *    degradation curves, no deltas or percentages. Metric comparison begins at ST-004
 *    onward.
 *  - No frontend reconstruction of backend identity: subject keys come verbatim from
 *    the catalogue; run selection and state semantics come verbatim from the resolved
 *    comparison projection. This script formats only -- it never joins Speed /
 *    Workflow / Context APIs independently or re-derives any identity.
 *  - Family-specific configuration truth is preserved: each family section renders
 *    ONLY that family's authoritative fields. Workflow does not record quantization,
 *    so its section says "Not recorded" -- it never copies another family's value.
 *    N/A stays N/A; AMBIGUOUS stays explicit with an explanation of the evidence
 *    limitation (it is not an application error).
 *
 * URL persistence: `#compare?a=<subject-key>&b=<subject-key>` (fragment only, no
 * routing framework). Unknown / malformed keys fail gracefully (selection cleared +
 * inline notice); the fragment never carries raw artifacts beyond short subject keys.
 */

(function () {
    "use strict";

    // Canonical family order and labels (matches the comparison read model's FAMILIES).
    var FAMILIES = ["speed", "workflow", "context"];
    var FAMILY_LABELS = { speed: "Speed", workflow: "Workflow", context: "Context" };

    // The ONLY state vocabulary this view renders -- emitted verbatim by the ST-002
    // comparison read model. Availability is never derived from metric presence.
    var STATE_LABELS = {
        available: "Available",
        ambiguous: "Ambiguous",
        missing: "Not available",
        in_progress: "In progress",
        failed: "Failed",
        unsupported: "Unsupported",
        unavailable: "Unavailable"
    };
    var STATE_BADGE = {
        available: "rs-badge-ok",
        ambiguous: "rs-badge-warn",
        missing: "rs-badge-unavailable",
        in_progress: "rs-badge-accent",
        failed: "rs-badge-error",
        unsupported: "rs-badge-unavailable",
        unavailable: "rs-badge-unavailable"
    };

    // AMBIGUOUS is an evidence limitation, not an error. Per-family explanation of WHY
    // ownership cannot be established (honest cross-family identity, ST-002 contract).
    var AMBIGUOUS_NOTES = {
        workflow: "Workflow configuration identity omits quantization, so runs of the same base model cannot be attributed to a specific configuration.",
        context: "Context evidence for this subject pair cannot be attributed to one specific configuration with authority.",
        speed: "Speed evidence for this subject pair cannot be attributed to one specific configuration with authority."
    };

    var state = {
        subjects: [],      // catalogue entries, in authoritative order
        byKey: {},        // subject_key -> catalogue entry
        a: "",            // selected Subject A key ("" = unselected)
        b: ""             // selected Subject B key
    };
    var initialized = false;
    var fetchSeq = 0;     // monotonic guard: stale responses never mutate state

    function esc(t) {
        return typeof escapeHtml === "function" ? escapeHtml(String(t)) : String(t);
    }

    // -----------------------------------------------------------------------
    // URL persistence (#compare?a=<key>&b=<key>) -- fragment only.
    // -----------------------------------------------------------------------

    /** Parse the current fragment into {a, b} (empty strings when absent). */
    function parseHash() {
        var out = { a: "", b: "" };
        var raw = location.hash.replace(/^#/, "");
        if (raw.split("?")[0] !== "compare") { return out; }
        var query = raw.split("?")[1] || "";
        query.split("&").forEach(function (pair) {
            var kv = pair.split("=");
            var k = decodeURIComponent(kv[0] || "");
            var v = decodeURIComponent((kv.slice(1).join("=")) || "");
            if ((k === "a" || k === "b") && v) { out[k] = v; }
        });
        return out;
    }

    /** Write the current selection into the fragment (replaceState: no history spam). */
    function writeUrl() {
        var params = [];
        if (state.a) { params.push("a=" + encodeURIComponent(state.a)); }
        if (state.b) { params.push("b=" + encodeURIComponent(state.b)); }
        var hash = "#compare" + (params.length ? "?" + params.join("&") : "");
        history.replaceState(null, "", location.pathname + hash);
    }

    // -----------------------------------------------------------------------
    // Subject selectors -- values are authoritative backend subject keys.
    // -----------------------------------------------------------------------

    /** Short configuration fingerprint for the representative family. Prefers the
     *  catalogue's per-family fingerprint; when several distinct configurations exist
     *  for one base model the catalogue exposes no single fingerprint, so fall back to
     *  the short signature that the subject key itself carries (display-only -- the
     *  option VALUE remains the full authoritative key). */
    function repFingerprint(s) {
        var rep = s.representative_family;
        if (rep !== "speed" && rep !== "context") { return null; }
        var fp = (s.config_fingerprints || {})[rep];
        if (!fp) {
            var markerPrefix = "|" + rep + ":";
            var idx = s.subject_key.indexOf(markerPrefix);
            if (idx !== -1) { fp = s.subject_key.slice(idx + markerPrefix.length); }
        }
        return fp || null;
    }

    /** Human-readable option label: display name + a short configuration marker where
     *  one can be established. The VALUE stays the raw subject key; the label is never
     *  used as identity anywhere in this view. */
    function optionLabel(s) {
        var label = s.display_name || s.model_version;
        var rep = s.representative_family;
        var fp = repFingerprint(s);
        if (fp) { label += " · " + FAMILY_LABELS[rep] + " config " + String(fp).slice(0, 8); }
        else if (rep === "base") {
            // Workflow-only evidence cannot encode a distinguishing configuration.
            label += ((s.config_fingerprints || {}).workflow ? " · base model only" : "");
        }
        return label;
    }

    function populateSelects() {
        ["a", "b"].forEach(function (slot) {
            var sel = document.getElementById("compare-subject-" + slot);
            if (!sel) { return; }
            var current = state[slot];
            sel.innerHTML = "<option value=''>Select…</option>";
            state.subjects.forEach(function (s) {
                var opt = document.createElement("option");
                opt.value = s.subject_key; // authoritative backend key, never the label
                opt.textContent = optionLabel(s);
                if (s.subject_key === current) { opt.selected = true; }
                sel.appendChild(opt);
            });
        });
    }

    /** A subject cannot be compared with itself: mirror-disable the exact same key in
     *  the opposite selector. Same-model/different-config subjects are DISTINCT keys
     *  and remain independently selectable. */
    function syncDisabledOptions() {
        ["a", "b"].forEach(function (slot) {
            var sel = document.getElementById("compare-subject-" + slot);
            if (!sel) { return; }
            var otherKey = state[slot === "a" ? "b" : "a"];
            Array.prototype.forEach.call(sel.options, function (opt) {
                opt.disabled = !!otherKey && opt.value === otherKey;
            });
        });
    }

    // -----------------------------------------------------------------------
    // Catalogue load + URL restoration.
    // -----------------------------------------------------------------------

    function setNotice(text) {
        var el = document.getElementById("compare-notice");
        if (!el) { return; }
        el.textContent = text || "";
        el.hidden = !text;
    }

    function loadCatalogue() {
        fetch("/api/comparison/subjects")
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
            .then(function (data) {
                state.subjects = data.subjects || [];
                state.byKey = {};
                state.subjects.forEach(function (s) { state.byKey[s.subject_key] = s; });

                // Restore selection from the URL fragment; unknown / malformed keys
                // fail gracefully -- cleared with a notice, never guessed at.
                var fromHash = parseHash();
                ["a", "b"].forEach(function (slot) {
                    var key = fromHash[slot];
                    if (!key) { return; }
                    if (state.byKey[key]) {
                        state[slot] = key;
                    } else {
                        setNotice("Subject \u201C" + key + "\u201D is not in the current catalogue; selection cleared.");
                    }
                });

                populateSelects();
                syncDisabledOptions();
                if (state.a && state.b) { fetchComparison(); } else { renderIdentity(null); }
            })
            .catch(function () {
                setNotice("The comparison subject catalogue could not be loaded. Try refreshing the page.");
            });
    }

    // -----------------------------------------------------------------------
    // Resolved comparison projection (authoritative; fetched only when both valid).
    // -----------------------------------------------------------------------

    function fetchComparison() {
        var container = document.getElementById("compare-identity");
        if (!state.a || !state.b) { renderIdentity(null); return; }
        if (state.a === state.b) {
            // Defense in depth: the selectors already mirror-disable identical keys,
            // and the backend rejects this with 400 -- but never fetch a self-comparison.
            setNotice("A subject cannot be compared with itself; select two distinct subjects.");
            renderIdentity(null);
            return;
        }
        var seq = ++fetchSeq;
        if (container) {
            container.innerHTML = '<div class="rs-state"><span class="rs-state-spinner" aria-hidden="true"></span><span>Loading&hellip;</span></div>';
        }
        fetch("/api/comparison?a=" + encodeURIComponent(state.a) + "&b=" + encodeURIComponent(state.b))
            .then(function (r) {
                if (!r.ok) { return r.json().catch(function () { return {}; }).then(function (body) { throw new Error(body.detail || ("HTTP " + r.status)); }); }
                return r.json();
            })
            .then(function (data) {
                if (seq !== fetchSeq) { return; } // stale response: a newer selection won
                setNotice("");
                renderIdentity(data);
            })
            .catch(function (err) {
                if (seq !== fetchSeq) { return; }
                renderError(err && err.message ? err.message : "The comparison could not be loaded. Try refreshing the page.");
            });
    }

    // -----------------------------------------------------------------------
    // Rendering -- identity header only, never metrics.
    // -----------------------------------------------------------------------

    function valOrDash(value) {
        if (value === null || value === undefined || value === "") { return "<span class='cmp-na'>&mdash;</span>"; }
        return esc(String(value));
    }

    /** Configuration identity row: the representative family whose signature defines
     *  this subject key, with its short fingerprint. Never a display-name guess. */
    function configIdentityText(entry) {
        var rep = entry.representative_family;
        if (rep === "speed" || rep === "context") {
            var fp = repFingerprint(entry);
            return FAMILY_LABELS[rep] + " configuration " +
                (fp ? "<code class='cmp-fp' title='" + esc(fp) + "'>" + esc(String(fp)) + "</code>" : valOrDash(null));
        }
        // Representative family is the base model itself: no distinguishing
        // configuration is recorded for this subject (Workflow-only evidence).
        return "No distinguishing configuration recorded";
    }

    /** One family section inside a subject column. Renders ONLY that family's
     *  authoritative fields -- cross-family values are never copied across. */
    function renderFamilySection(family, dim) {
        var label = FAMILY_LABELS[family];
        var out = '<div class="cmp-family">';
        out += "<h4 class='cmp-family-title'>" + label + "</h4>";

        if (!dim || !STATE_LABELS[dim.state]) {
            // No authoritative state at all -> unavailable, never fabricated.
            out += "<span class='rs-badge " + STATE_BADGE.unavailable + "'>Unavailable</span></div>";
            return out;
        }

        var st = dim.state;
        out += "<span class='rs-badge " + (STATE_BADGE[st] || STATE_BADGE.unavailable) + "'>" + esc(STATE_LABELS[st]) + "</span>";

        if (st === "ambiguous") {
            // Evidence limitation, not an application error: explain it in text.
            out += "<p class='cmp-ambiguous-note'>" + esc(AMBIGUOUS_NOTES[family] ||
                "Configuration-specific evidence ownership cannot be established for this subject pair.") + "</p>";
        } else if (st === "available" && dim.config) {
            var cfg = dim.config;
            out += "<dl class='cmp-config'>";
            if (family === "speed") {
                // Speed's authoritative configuration identity (ST-002 contract).
                out += "<dt>Quantization</dt><dd>" + valOrDash(cfg.quantization) + "</dd>";
                out += "<dt>Loaded context</dt><dd>" + (cfg.loaded_context_tokens != null ? esc(String(cfg.loaded_context_tokens)) + " tokens" : "<span class='cmp-na'>&mdash;</span>") + "</dd>";
                out += "<dt>Hardware / environment</dt><dd>" + valOrDash(cfg.hardware_label) + "</dd>";
            } else if (family === "workflow") {
                // Workflow deliberately does NOT record quantization: say so honestly.
                // Never copy another family's quantization into this section.
                out += "<dt>Quantization</dt><dd>" + (cfg.quantization != null ? esc(String(cfg.quantization)) : "<span class='cmp-na'>Not recorded</span>") + "</dd>";
                if (cfg.configuration_fingerprint) {
                    out += "<dt>Fingerprint</dt><dd><code class='cmp-fp' title='" + esc(cfg.configuration_fingerprint) + "'>" + esc(String(cfg.configuration_fingerprint).slice(0, 16)) + "&hellip;</code></dd>";
                }
            } else if (family === "context") {
                // Context's authoritative configuration identity.
                out += "<dt>Quantization</dt><dd>" + valOrDash(cfg.quantization) + "</dd>";
                out += "<dt>Effective capacity</dt><dd>" + valOrDash(cfg.effective_capacity) + "</dd>";
                out += "<dt>Baseline context point</dt><dd>" + valOrDash(cfg.baseline_context_point) + "</dd>";
                if (cfg.configuration_fingerprint) {
                    out += "<dt>Fingerprint</dt><dd><code class='cmp-fp' title='" + esc(cfg.configuration_fingerprint) + "'>" + esc(String(cfg.configuration_fingerprint).slice(0, 16)) + "&hellip;</code></dd>";
                }
            }
            out += "</dl>";
        }

        // Evidence traceability: the authoritative run behind this state (L0 -> L2).
        if (dim.run_id && dim.deep_link) {
            out += "<a class='rs-view-link cmp-evidence' href='" + esc(dim.deep_link) + "'>View " + label + " evidence (" + esc(String(dim.run_id)) + ") &rarr;</a>";
        } else if (st === "available" && dim.classification) {
            out += "<span class='cmp-classification' title='Authoritative run classification'>" + esc(String(dim.classification)) + "</span>";
        }

        out += "</div>";
        return out;
    }

    /** One subject column: identity rows (catalogue) + per-family availability and
     *  family-specific configuration truth (resolved projection). */
    function renderSubjectColumn(slot, entry, dimsForSlot) {
        var isA = slot === "a";
        var titleId = "cmp-subject-" + slot + "-title";
        var out = '<section class="rs-panel cmp-col" aria-labelledby="' + titleId + '">';
        out += "<h3 id='" + titleId + "' class='cmp-col-title'>Subject " + (isA ? "A" : "B") + "</h3>";

        if (!entry) {
            // Key no longer in the catalogue (e.g. evidence changed): honest gap.
            out += "<p class='cmp-na'>Subject is not in the current catalogue.</p></section>";
            return out;
        }

        var arch = entry.architecture || "";
        out += '<dl class="cmp-identity">';
        out += "<dt>Model / version</dt><dd>" + esc(entry.model_version || entry.display_name) + "</dd>";
        // Architecture is not persisted per-run by the authoritative sources: N/A stays N/A.
        out += "<dt>Architecture</dt><dd>" + (arch ? esc(arch) : "<span class='cmp-na'>Not recorded</span>") + "</dd>";
        out += "<dt>Configuration identity</dt><dd>" + configIdentityText(entry) + "</dd>";
        var dimChips = "";
        (entry.available_dimensions || []).forEach(function (f) {
            dimChips += "<span class='rs-badge rs-badge-accent'>" + esc(FAMILY_LABELS[f] || f) + "</span> ";
        });
        out += "<dt>Available dimensions</dt><dd>" + (dimChips || "<span class='cmp-na'>&mdash;</span>") + "</dd>";
        out += "</dl>";

        FAMILIES.forEach(function (f) {
            var dim = dimsForSlot ? dimsForSlot[f] : null;
            out += renderFamilySection(f, dim);
        });

        out += "</section>";
        return out;
    }

    function renderIdentity(data) {
        var container = document.getElementById("compare-identity");
        if (!container) { return; }
        if (!data || !data.comparison) {
            container.innerHTML = '<div class="rs-panel cmp-empty"><p class="cmp-hint">Identity and configuration details appear once two distinct subjects are selected.</p></div>';
            return;
        }

        var cmp = data.comparison;
        var entryA = state.byKey[cmp.subject_a.subject_key] || state.byKey[state.a];
        var entryB = state.byKey[cmp.subject_b.subject_key] || state.byKey[state.b];
        var dims = cmp.dimensions || {};

        // The projection nests each family as {subject_a, subject_b}; hand each column
        // ONLY its own slot so a shared run is never rendered into both columns.
        function slotDims(slot) {
            var key = slot === "a" ? "subject_a" : "subject_b";
            var out = {};
            FAMILIES.forEach(function (f) { out[f] = (dims[f] || {})[key]; });
            return out;
        }

        var html = "";
        if (cmp.same_model_configuration) {
            // Honest framing for the same-model case: only the representative family's
            // identity distinguishes the two configurations; other families are
            // reported Ambiguous rather than duplicating shared evidence.
            html += '<div class="rs-banner rs-banner-info"><span class="rs-banner-icon" aria-hidden="true">\u2139</span>' +
                "<div class='rs-banner-text'><span class='rs-banner-title'>Same base model</span>" +
                "Both subjects are <code>" + esc(cmp.subject_a.model_version) + "</code> at different configurations. Only the family whose identity distinguishes them is pinned per subject; families that cannot attribute evidence to a specific configuration are reported as Ambiguous, never duplicated into both columns.</div></div>";
        }

        html += '<div class="cmp-grid">';
        html += renderSubjectColumn("a", entryA, slotDims("a"));
        html += renderSubjectColumn("b", entryB, slotDims("b"));
        html += "</div>";

        container.innerHTML = html;
    }

    function renderError(message) {
        var container = document.getElementById("compare-identity");
        if (!container) { return; }
        container.innerHTML = '<div class="rs-banner rs-banner-error"><span class="rs-banner-icon" aria-hidden="true">\u2716</span>' +
            "<div class='rs-banner-text'><span class='rs-banner-title'>Comparison unavailable</span>" + esc(message) + "</div></div>";
    }

    // -----------------------------------------------------------------------
    // Controls: two labelled selectors + accessible Swap button.
    // -----------------------------------------------------------------------

    function renderControls() {
        var container = document.getElementById("compare-container");
        if (!container) { return; }
        container.innerHTML =
            '<div class="rs-panel">' +
            '  <div class="rs-panel-title">Compare</div>' +
            "  <p class='cmp-hint'>Select two model configurations to compare. Identities, configuration truth and dimension availability are shown first; metric comparison arrives in a later release.</p>" +
            "  <div class='cmp-controls'>" +
            "    <div class='cmp-field'>" +
            "      <label for='compare-subject-a'>Subject A</label>" +
            "      <select id='compare-subject-a' class='cmp-select' aria-label='Comparison subject A'></select>" +
            "    </div>" +
            "    <button type='button' id='compare-swap' class='btn-secondary cmp-swap' aria-label='Swap Subject A and B'>Swap A &harr; B</button>" +
            "    <div class='cmp-field'>" +
            "      <label for='compare-subject-b'>Subject B</label>" +
            "      <select id='compare-subject-b' class='cmp-select' aria-label='Comparison subject B'></select>" +
            "    </div>" +
            "  </div>" +
            "  <p id='compare-notice' role='status' aria-live='polite' hidden></p>" +
            "</div>" +
            '<div id="compare-identity"></div>';

        ["a", "b"].forEach(function (slot) {
            var sel = document.getElementById("compare-subject-" + slot);
            if (!sel) { return; }
            sel.addEventListener("change", function () {
                state[slot] = sel.value;
                setNotice("");
                syncDisabledOptions();
                writeUrl();
                fetchComparison(); // no-op render when only one side is selected
            });
        });

        var swapBtn = document.getElementById("compare-swap");
        if (swapBtn) {
            swapBtn.addEventListener("click", function () {
                // Only column order changes: backend keys are preserved verbatim.
                var tmp = state.a;
                state.a = state.b;
                state.b = tmp;
                ["a", "b"].forEach(function (slot) {
                    var sel = document.getElementById("compare-subject-" + slot);
                    if (sel) { sel.value = state[slot]; }
                });
                setNotice("");
                syncDisabledOptions();
                writeUrl();
                fetchComparison(); // normal refetch with swapped subject order
            });
        }
    }

    // -----------------------------------------------------------------------
    // Initialization -- idempotent; results-shell.js's lazy-load bridge calls this
    // when the Compare nav item is clicked, and it self-initializes when the page
    // opens directly at #compare (before that script existed to call us).
    // -----------------------------------------------------------------------

    function init() {
        if (initialized) { return; }
        initialized = true;
        renderControls();
        loadCatalogue();
    }

    window.__compareInit = init;

    var hashView = location.hash.replace(/^#/, "").split("?")[0];
    if (hashView === "compare") { init(); }
})();
