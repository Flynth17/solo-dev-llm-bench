/** Solo Dev LLM Bench — Compare view (RM-26-AA-0013, ST-003).
 *
 * Establishes the visible Compare workspace: two subject selectors populated from
 * the authoritative comparison subject catalogue (`/api/comparison/subjects`) and a
 * model/config identity header projected by `/api/comparison?a=&b=`.
 *
 * Scope discipline (ST-003 = identity header, ST-004 = Speed metrics):
 *  - The view renders identities, family-specific configuration truth and per-family
 *    dimension availability. Metric comparison is limited to the SPEED dimension:
 *    generation / TTFT / prefill throughput at canonical points plus a presentation-only
 *    average -- no other-dimension metric values (Workflow / Context belong to later
 *    subtasks), no deltas or percentages, and no overall-verdict language of any kind.
 *  - No frontend reconstruction of backend identity: subject keys come verbatim from
 *    the catalogue; run selection and state semantics come verbatim from the resolved
 *    comparison projection. This script formats only -- it never joins Speed /
 *    Workflow / Context APIs independently or re-derives any identity.
 *  - Metric-version compatibility (ST-004): only corrected-baseline evidence (metric v2/v3)
 *    renders numeric values. LEGACY evidence is shown as an explicit not-comparable state
 *    with its run traceability -- never silently compared against current values; N/A stays
 *    N/A, missing/unsupported points stay gaps and are never zeroed.
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
        unavailable: "Unavailable",
        legacy: "Legacy metric"
    };
    var STATE_BADGE = {
        available: "rs-badge-ok",
        ambiguous: "rs-badge-warn",
        missing: "rs-badge-unavailable",
        in_progress: "rs-badge-accent",
        failed: "rs-badge-error",
        unsupported: "rs-badge-unavailable",
        unavailable: "rs-badge-unavailable",
        legacy: "rs-badge-legacy"
    };

    // AMBIGUOUS is an evidence limitation, not an error. Per-family explanation of WHY
    // ownership cannot be established (honest cross-family identity, ST-002 contract).
    var AMBIGUOUS_NOTES = {
        workflow: "Workflow configuration identity omits quantization, so runs of the same base model cannot be attributed to a specific configuration.",
        context: "Context evidence for this subject pair cannot be attributed to one specific configuration with authority.",
        speed: "Speed evidence for this subject pair cannot be attributed to one specific configuration with authority."
    };

    // Speed-specific state explanations (ST-004). Every non-available state renders its
    // own text -- never an empty numeric column, never a fabricated zero.
    var SPEED_STATE_NOTES = {
        failed: "The selected run failed; no comparable values are shown.",
        unsupported: "Speed is not supported for this configuration.",
        missing: "No Speed evidence recorded for this subject.",
        in_progress: "The Speed benchmark is still running.",
        ambiguous: AMBIGUOUS_NOTES.speed,
        legacy: "Pre-metric-v2 measurement \u2014 not comparable with current-metric evidence. Values are withheld rather than silently compared.",
        unavailable: "Speed evidence exists but cannot be represented on this surface; open the run for details."
    };

    // Workflow (v2 Quality) state explanations. Every non-available state renders its
    // own text -- never an empty numeric column, never a fabricated pass rate.
    var WORKFLOW_STATE_NOTES = {
        failed: "The selected run failed; no comparable pass rates are shown.",
        unsupported: "Workflow is not supported for this configuration.",
        missing: "No Workflow evidence recorded for this subject.",
        in_progress: "The Workflow benchmark is still running.",
        ambiguous: AMBIGUOUS_NOTES.workflow,
        unavailable: "Workflow evidence exists but cannot be represented on this surface; open the run for details."
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
        if (!state.a || !state.b) { renderIdentity(null); clearSpeed(); return; }
        if (state.a === state.b) {
            // Defense in depth: the selectors already mirror-disable identical keys,
            // and the backend rejects this with 400 -- but never fetch a self-comparison.
            setNotice("A subject cannot be compared with itself; select two distinct subjects.");
            renderIdentity(null);
            clearSpeed();
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
                renderSpeed(data && data.comparison); // ST-004: Speed metrics beneath the identity header
                renderWorkflow(data && data.comparison); // Workflow (v2 Quality) pass-rate + per-suite breakdown
            })
            .catch(function (err) {
                if (seq !== fetchSeq) { return; }
                clearSpeed();
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
    // Speed side-by-side comparison (ST-004).
    //
    // Renders ONLY the authoritative speed dimension projection: canonical points
    // aligned by label (never array index), a presentation-only average, and honest
    // per-point / per-run states. Only the speed dimension is read from the projection;
    // no overall-verdict language exists anywhere in this view.
    // -----------------------------------------------------------------------

    var SPEED_CANONICAL_POINTS = ["8K", "16K", "32K"];

    // Metric rows per group. ``kind`` selects the point field; "average" reads the
    // read-model presentation aggregate (never recomputed here).
    var SPEED_GROUPS = [
        { title: "Generation throughput (tok/s)", unit: "tok/s", fmt: "rate",
          metrics: [["8K", "generation"], ["16K", "generation"], ["32K", "generation"], ["avg", "average"]] },
        { title: "Time to first token (TTFT)", unit: "", fmt: "ttft",
          metrics: [["8K", "ttft"], ["16K", "ttft"], ["32K", "ttft"]] },
        { title: "Prefill throughput (tok/s)", unit: "tok/s", fmt: "rate",
          metrics: [["8K", "prefill"], ["16K", "prefill"], ["32K", "prefill"]] }
    ];

    function speedPoint(dim, label) {
        // Canonical-point alignment is by LABEL -- never array index; a point absent
        // from one subject stays a gap on that side only.
        if (!dim || !Array.isArray(dim.points)) { return null; }
        for (var i = 0; i < dim.points.length; i++) {
            if (String(dim.points[i].label) === label) { return dim.points[i]; }
        }
        return null;
    }

    /** Resolve one metric cell: value | missing point | unsupported point | not recorded. */
    function speedMetricValue(dim, pointLabel, kind) {
        if (!dim || dim.state !== "available") { return { kind: "state" }; }
        if (kind === "average") {
            var avg = dim.average_generation_tps;
            return (typeof avg === "number" && !isNaN(avg)) ? { kind: "value", value: avg } : { kind: "not_recorded" };
        }
        var p = speedPoint(dim, pointLabel);
        if (!p) { return { kind: "missing" }; }
        var status = String(p.status || "").toLowerCase();
        if (status && status !== "completed") { return { kind: "point_state", status: status }; }
        var v = kind === "generation" ? p.generation_tokens_per_second
              : kind === "prefill" ? p.prefill_tokens_per_second
              : p.ttft_seconds;
        if (typeof v !== "number" || isNaN(v)) { return { kind: "not_recorded" }; }
        return { kind: "value", value: v };
    }

    function fmtRate(value) {
        var n = Number(value);
        return (value === null || value === undefined || isNaN(n)) ? "\u2014" : n.toFixed(1);
    }

    /** One numeric cell: aligned number + unit, optional neutral magnitude bar.
     *  The bar is a visual aid only -- the number is always the information. */
    function speedValueCell(slot, dim, pointLabel, kind, groupFmt, otherDim) {
        var res = speedMetricValue(dim, pointLabel, kind);
        var label = (kind === "average" ? "Average generation" : pointLabel + " " + kind);
        if (res.kind !== "value") {
            var text;
            if (res.kind === "missing") { text = "Missing"; }
            else if (res.kind === "point_state") { text = res.status === "unsupported" ? "Not supported" : esc(res.status); }
            else { text = "\u2014"; }  // not recorded: honest gap, never a zero
            return '<td class="cmp-speed-cell cmp-speed-cell-' + slot + '" data-side="' + slot.toUpperCase() +
                '" aria-label="Subject ' + slot.toUpperCase() + ', ' + esc(label) + ': ' + esc(text) + '">'
                + '<span class="cmp-speed-state">' + text + "</span></td>";
        }
        var unit = groupFmt === "ttft" ? "" : " tok/s";
        var display = groupFmt === "ttft" ? formatTtft(res.value) : fmtRate(res.value);
        // Neutral magnitude bar: scaled against the OTHER side's value for this row only
        // when both sides carry numbers. Same colour on both sides -- no verdict.
        var otherRes = speedMetricValue(otherDim, pointLabel, kind);
        var barHtml = "";
        if (otherRes.kind === "value") {
            var maxV = Math.max(res.value, otherRes.value);
            if (maxV > 0) {
                var pct = Math.round(Math.max(8, (res.value / maxV) * 100));
                barHtml = '<span class="cmp-speed-bar" aria-hidden="true"><span style="width:' + pct + '%"></span></span>';
            }
        }
        var numericText = groupFmt === "ttft" ? res.value.toFixed(2) + " seconds" : fmtRate(res.value) + " tokens per second";
        return '<td class="cmp-speed-cell cmp-speed-cell-' + slot + '" data-side="' + slot.toUpperCase() +
            '" aria-label="Subject ' + slot.toUpperCase() + ', ' + esc(label) + ': ' + numericText + '">'
            + '<span class="cmp-speed-num">' + display + "</span>"
            + (unit ? '<span class="cmp-speed-unit">' + unit.trim() + "</span>" : "")
            + barHtml
            + "</td>";
    }

    /** The non-available side renders ONE spanning cell with its state, explanation and
     *  evidence link -- never an empty numeric column. */
    function speedStateCell(slot, dim, span) {
        var st = (dim && STATE_LABELS[dim.state]) ? dim.state : "missing";
        var html = '<td class="cmp-speed-statecell cmp-speed-cell-' + slot + '" data-side="' + slot.toUpperCase() + '"' +
            (span > 1 ? ' rowspan="' + span + '"' : "") +
            ' aria-label="Subject ' + slot.toUpperCase() + ': ' + esc(STATE_LABELS[st]) + '">';
        html += '<span class="rs-badge ' + (STATE_BADGE[st] || STATE_BADGE.unavailable) + '">' + esc(STATE_LABELS[st]) + "</span>";
        html += '<p class="cmp-speed-note">' + esc(SPEED_STATE_NOTES[st] || "") + "</p>";
        if (dim && dim.run_id && dim.deep_link) {
            html += '<a class="rs-view-link cmp-evidence" href="' + esc(dim.deep_link) + '">View Speed evidence (' + esc(String(dim.run_id)) + ') &rarr;</a>';
        }
        return html + "</td>";
    }

    /** Per-side evidence identity line (ST-004): metric version, configuration chips,
     *  run ID and the deep link into the existing Speed detail page. */
    function speedEvidenceLine(slot, dim) {
        var out = '<div class="cmp-speed-side" aria-label="Subject ' + slot.toUpperCase() + ' Speed evidence">';
        out += "<span class='cmp-speed-slot'>Subject " + slot.toUpperCase() + "</span>";
        if (!dim || !STATE_LABELS[dim.state]) { return out + "</div>"; }
        var v = dim.metric_version;
        if (v === 2) { out += '<span class="rs-badge rs-badge-accent">Metric v2</span>'; }
        else if (v === 3) { out += '<span class="rs-badge rs-badge-ok">Metric v3</span>'; }
        else if (dim.state === "legacy") {
            out += '<span class="rs-badge rs-badge-legacy">Legacy metric</span>' +
                '<span class="cmp-speed-sub">' + (v != null ? "metric v" + esc(String(v)) : "no metric version recorded") + "</span>";
        }
        var cfg = dim.config || {};
        if (cfg.quantization) { out += '<code class="cmp-speed-chip">' + esc(String(cfg.quantization)) + "</code>"; }
        if (cfg.loaded_context_tokens != null) {
            out += '<code class="cmp-speed-chip">' + Number(cfg.loaded_context_tokens).toLocaleString("en-US") + " ctx</code>";
        }
        if (dim.run_id) { out += "<code class='cmp-speed-runid'>" + esc(String(dim.run_id)) + "</code>"; }
        if (dim.deep_link) {
            out += '<a class="rs-view-link cmp-evidence" href="' + esc(dim.deep_link) + '">View Speed evidence &rarr;</a>';
        }
        return out + "</div>";
    }

    /** Actual measured inputs (secondary context): canonical point -> actual tokens per side.
     *  The canonical target and the actually-measured input are not necessarily identical. */
    function speedActualInputs(dimA, dimB) {
        var html = '<details class="cmp-speed-details"><summary>Actual measured inputs</summary>';
        html += '<table class="cmp-speed-table cmp-speed-actual" aria-label="Actual measured input tokens by canonical point">';
        html += "<thead><tr><th scope='col'>Point</th><th scope='col'>Subject A</th><th scope='col'>Subject B</th></tr></thead><tbody>";
        SPEED_CANONICAL_POINTS.forEach(function (label) {
            var pa = speedPoint(dimA, label);
            var pb = speedPoint(dimB, label);
            html += "<tr><td class='cmp-speed-metric-cell'>" + label + "</td>"
                + '<td data-side="A">' + actualInputText(pa) + "</td><td data-side=\"B\">" + actualInputText(pb) + "</td></tr>";
        });
        return html + "</tbody></table></details>";
    }

    function actualInputText(point) {
        if (!point) { return '<span class="cmp-speed-state">Missing</span>'; }
        var status = String(point.status || "").toLowerCase();
        if (status && status !== "completed") { return '<span class="cmp-speed-state">' + esc(status === "unsupported" ? "Not supported" : status) + "</span>"; }
        if (point.actual_prompt_tokens == null) { return '<span class="cmp-na">&mdash;</span>'; }
        return Number(point.actual_prompt_tokens).toLocaleString("en-US") + " tokens";
    }

    function renderSpeed(cmp) {
        var container = document.getElementById("compare-speed");
        if (!container) { return; }
        var dims = (cmp && cmp.dimensions) || {};
        var dimA = dims.speed ? dims.speed.subject_a : null;
        var dimB = dims.speed ? dims.speed.subject_b : null;

        // No speed dimension at all -> nothing to render (never fabricated).
        if (!dimA && !dimB) { container.innerHTML = ""; return; }

        var aAvail = dimA && dimA.state === "available";
        var bAvail = dimB && dimB.state === "available";

        var html = '<section class="rs-panel cmp-speed" aria-labelledby="cmp-speed-title">';
        html += '<h3 id="cmp-speed-title" class="cmp-family-title">Speed</h3>';
        html += '<div class="cmp-speed-evidence">' + speedEvidenceLine("a", dimA) + speedEvidenceLine("b", dimB) + "</div>";

        if (!aAvail && !bAvail) {
            // Neither side carries comparable Speed evidence: say so plainly.
            html += '<p class="cmp-speed-note">No comparable Speed values for this subject pair. ' +
                "Each side\u2019s state and evidence link above remain inspectable.</p>";
        } else {
            var both = aAvail && bAvail;
            html += '<table class="cmp-speed-table" aria-label="Speed comparison: Subject A versus Subject B">';
            if (both) {
                html += "<thead><tr><th scope='col'>Subject A</th><th scope='col'>Metric</th><th scope='col'>Subject B</th></tr></thead>";
            }
            html += "<tbody>";
            var totalMetrics = SPEED_GROUPS.reduce(function (n, g) { return n + g.metrics.length; }, 0);
            // A non-available side emits its spanning state cell exactly ONCE -- in the
            // first data row overall (never once per group).
            var aStateEmitted = false;
            var bStateEmitted = false;
            SPEED_GROUPS.forEach(function (group) {
                if (both) {
                    html += '<tr class="cmp-speed-group"><th colspan="3" scope="colgroup">' + esc(group.title) + "</th></tr>";
                }
                group.metrics.forEach(function (metric) {
                    var pointLabel = metric[0];
                    var kind = metric[1];
                    var label = kind === "average"
                        ? "Average generation"
                        : (both ? pointLabel : pointLabel + " " + kind);
                    html += '<tr class="cmp-speed-row">';
                    if (aAvail) {
                        html += speedValueCell("a", dimA, pointLabel, kind, group.fmt, dimB);
                    } else if (!aStateEmitted) {
                        // A is non-available: its spanning state cell sits in the first row.
                        html += speedStateCell("a", dimA, totalMetrics);
                        aStateEmitted = true;
                    }
                    html += '<th scope="row" class="cmp-speed-metric-cell">' + esc(label) + "</th>";
                    if (bAvail) {
                        html += speedValueCell("b", dimB, pointLabel, kind, group.fmt, dimA);
                    } else if (!bStateEmitted) {
                        // B is non-available: its spanning state cell sits in the first row.
                        html += speedStateCell("b", dimB, totalMetrics);
                        bStateEmitted = true;
                    }
                    html += "</tr>";
                });
            });
            html += "</tbody></table>";

            // Secondary context: what was actually measured at each canonical point.
            if (aAvail || bAvail) {
                html += speedActualInputs(aAvail ? dimA : null, bAvail ? dimB : null);
            }
            html += '<p class="cmp-speed-footnote">Average generation is a presentation-only aggregate: arithmetic mean of present, valid canonical-point values; missing or unsupported points are excluded, never zeroed. It is not a benchmark score.</p>';
        }

        html += "</section>";
        container.innerHTML = html;
    }

    function clearSpeed() {
        var container = document.getElementById("compare-speed");
        if (container) { container.innerHTML = ""; }
    }

    // -----------------------------------------------------------------------
    // Workflow side-by-side comparison (v2 Quality).
    //
    // Renders ONLY the authoritative workflow dimension projection: the run-level
    // pass-rate aggregate plus the per-suite breakdown, aligned by suite label.
    // Values are read verbatim from build_read_model -- never recomputed here. No
    // overall-verdict language exists anywhere in this view.
    // -----------------------------------------------------------------------

    // Row template for the Workflow table: one Overall pass-rate row plus one row
    // per authoritative suite (the suite set is shared for one base model).
    function workflowRows(dimA, dimB) {
        // Use whichever side carries suites as the row template; fall back to any
        // available dimension so a non-available side still gets its state cell.
        var source = ((dimA && dimA.state === "available" && dimA.suites) ? dimA : null)
            || ((dimB && dimB.state === "available" && dimB.suites) ? dimB : null)
            || dimA || dimB;
        var suites = (source && Array.isArray(source.suites)) ? source.suites : [];
        var rows = [{ group: "Overall", label: "Pass rate", kind: "overall" }];
        suites.forEach(function (s) {
            if (s && s.suite != null) {
                rows.push({ group: "Per-suite checks", label: esc(String(s.suite)), kind: "suite", suite: s });
            }
        });
        return rows;
    }

    /** One Workflow value cell: aggregate pass rate (overall) or passed/total + failed
     *  (per suite). Honest gap (—) for missing fields -- never a fabricated zero. */
    function workflowValueCell(slot, dim, rowKind, suite) {
        if (!dim || dim.state !== "available") {
            return '<td class="cmp-workflow-cell cmp-cell-' + slot + '" data-side="' + slot.toUpperCase() + '">'
                + '<span class="cmp-workflow-state">\u2014</span></td>';
        }
        var main, sub;
        if (rowKind === "overall") {
            var pct = dim.percentage;
            var passed = dim.checks_passed;
            var total = dim.checks_total;
            main = (pct != null) ? Number(pct).toFixed(1) + "%" : "\u2014";
            if (passed != null && total != null) { sub = passed + " / " + total + " checks"; }
            else if (passed != null) { sub = String(passed) + " / \u2014 checks"; }
            else { sub = "\u2014"; }
        } else {
            var sp = suite.checks_passed;
            var st = suite.checks_total;
            var failed = suite.failed_checks;
            main = (sp != null && st != null) ? (sp + " / " + st) : "\u2014";
            sub = (failed != null) ? String(failed) + " failed" : "\u2014";
        }
        return '<td class="cmp-workflow-cell cmp-cell-' + slot + '" data-side="' + slot.toUpperCase() + '">'
            + '<span class="cmp-workflow-num">' + esc(main) + "</span>"
            + (sub ? '<span class="cmp-workflow-sub">' + esc(sub) + "</span>" : "")
            + "</td>";
    }

    /** The non-available side renders ONE spanning cell with its state, explanation and
     *  evidence link -- never an empty numeric column. */
    function workflowStateCell(slot, dim, span) {
        var st = (dim && STATE_LABELS[dim.state]) ? dim.state : "missing";
        var html = '<td class="cmp-workflow-statecell cmp-cell-' + slot + '" data-side="' + slot.toUpperCase() + '"'
            + (span > 1 ? ' rowspan="' + span + '"' : "")
            + ' aria-label="Subject ' + slot.toUpperCase() + ': ' + esc(STATE_LABELS[st]) + '">';
        html += '<span class="rs-badge ' + (STATE_BADGE[st] || STATE_BADGE.unavailable) + '">' + esc(STATE_LABELS[st]) + "</span>";
        html += '<p class="cmp-workflow-note">' + esc(WORKFLOW_STATE_NOTES[st] || "") + "</p>";
        if (dim && dim.run_id && dim.deep_link) {
            html += '<a class="rs-view-link cmp-evidence" href="' + esc(dim.deep_link) + '">View Workflow evidence (' + esc(String(dim.run_id)) + ') &rarr;</a>';
        }
        return html + "</td>";
    }

    /** Per-side evidence identity line: configuration fingerprint chip, run ID and the
     *  deep link into the existing v2 Quality detail page. */
    function workflowEvidenceLine(slot, dim) {
        var out = '<div class="cmp-workflow-side" aria-label="Subject ' + slot.toUpperCase() + ' Workflow evidence">';
        out += "<span class='cmp-workflow-slot'>Subject " + slot.toUpperCase() + "</span>";
        if (!dim || !STATE_LABELS[dim.state]) { return out + "</div>"; }
        var cfg = dim.config || {};
        if (cfg.configuration_fingerprint) {
            out += '<code class="cmp-workflow-chip" title="' + esc(cfg.configuration_fingerprint) + '">'
                + esc(String(cfg.configuration_fingerprint).slice(0, 16)) + "&hellip;</code>";
        }
        if (dim.run_id) { out += "<code class='cmp-workflow-runid'>" + esc(String(dim.run_id)) + "</code>"; }
        if (dim.deep_link) {
            out += '<a class="rs-view-link cmp-evidence" href="' + esc(dim.deep_link) + '">View Workflow evidence &rarr;</a>';
        }
        return out + "</div>";
    }

    function renderWorkflow(cmp) {
        var container = document.getElementById("compare-workflow");
        if (!container) { return; }
        var dims = (cmp && cmp.dimensions) || {};
        var dimA = dims.workflow ? dims.workflow.subject_a : null;
        var dimB = dims.workflow ? dims.workflow.subject_b : null;

        // No workflow dimension at all -> nothing to render (never fabricated).
        if (!dimA && !dimB) { container.innerHTML = ""; return; }

        var aAvail = dimA && dimA.state === "available";
        var bAvail = dimB && dimB.state === "available";
        var rows = workflowRows(dimA, dimB);

        var html = '<section class="rs-panel cmp-workflow" aria-labelledby="cmp-workflow-title">';
        html += '<h3 id="cmp-workflow-title" class="cmp-family-title">Workflow</h3>';
        html += '<div class="cmp-workflow-evidence">' + workflowEvidenceLine("a", dimA) + workflowEvidenceLine("b", dimB) + "</div>";

        if (!aAvail && !bAvail) {
            // Neither side carries comparable Workflow evidence: say so plainly.
            html += '<p class="cmp-workflow-note">No comparable Workflow evidence for this subject pair. '
                + "Each side\u2019s state and evidence link above remain inspectable.</p>";
        } else if (rows.length <= 1) {
            // Rows exist only as the Overall placeholder: neither side recorded any
            // pass rates to present -- honest gap, never a fabricated zero.
            html += '<p class="cmp-workflow-note">Workflow runs for this subject pair have no recorded pass '
                + "rates to compare.</p>";
        } else {
            var both = aAvail && bAvail;
            html += '<table class="cmp-workflow-table" aria-label="Workflow comparison: Subject A versus Subject B">';
            if (both) {
                html += "<thead><tr><th scope='col'>Subject A</th><th scope='col'>Metric</th><th scope='col'>Subject B</th></tr></thead>";
            }
            html += "<tbody>";
            var totalRows = rows.length;
            // A non-available side emits its spanning state cell exactly ONCE -- in the
            // first data row overall (never once per group).
            var aStateEmitted = false;
            var bStateEmitted = false;
            rows.forEach(function (row) {
                if (both) {
                    html += '<tr class="cmp-workflow-group"><th colspan="3" scope="colgroup">' + esc(row.group) + "</th></tr>";
                }
                html += '<tr class="cmp-workflow-row">';
                if (aAvail) {
                    html += workflowValueCell("a", dimA, row.kind, row.suite);
                } else if (!aStateEmitted) {
                    html += workflowStateCell("a", dimA, totalRows);
                    aStateEmitted = true;
                }
                html += '<th scope="row" class="cmp-workflow-metric-cell">' + esc(row.label) + "</th>";
                if (bAvail) {
                    html += workflowValueCell("b", dimB, row.kind, row.suite);
                } else if (!bStateEmitted) {
                    html += workflowStateCell("b", dimB, totalRows);
                    bStateEmitted = true;
                }
                html += "</tr>";
            });
            html += "</tbody></table>";
            html += '<p class="cmp-workflow-footnote">Pass rate is the authoritative run aggregate from the v2 Quality '
                + "read model (checks passed / checks total); per-suite counts are reported verbatim. It is a "
                + "presentation of recorded evidence, not a recomputed score.</p>";
        }

        html += "</section>";
        container.innerHTML = html;
    }

    function clearWorkflow() {
        var container = document.getElementById("compare-workflow");
        if (container) { container.innerHTML = ""; }
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
            "  <p class='cmp-hint'>Select two model configurations to compare. Identities, configuration truth and dimension availability are shown first, with Speed metrics and Workflow pass rates side by side beneath the identity header.</p>" +
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
            '<div id="compare-identity"></div>' +
            '<div id="compare-speed"></div>' +
            '<div id="compare-workflow"></div>';

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
