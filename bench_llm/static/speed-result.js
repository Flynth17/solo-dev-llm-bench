/* Solo Dev LLM Bench -- dedicated Standard Speed result page (Act 19.1).
 * Vanilla loader (no framework): derive run_id from the URL, fetch the
 * normalized single-run read model at /api/speed/runs/{run_id}, and render the
 * identity block plus one row per canonical point. Persisted metrics are shown
 * verbatim -- nothing is recomputed here. */

(function () {
    "use strict";

    var PATH_PREFIX = "/speed/results/";

    // --- Formatting helpers: never invent precision beyond stored data ------
    function fmtTokens(n) {
        if (n == null || n === "") return null;
        return Number(n).toLocaleString("en-US");
    }
    function fmtMs(sec) {
        var s = Number(sec);
        if (!isFinite(s)) return null;
        // Sub-second TTFT is readably expressed in milliseconds.
        if (s < 1) return Math.round(s * 1000) + " ms";
        return s.toFixed(2) + " s";
    }
    function fmtRate(n) {
        if (n == null || n === "") return null;
        return Number(n).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })
            + " tok/s";
    }
    function fmtWall(n) {
        if (n == null || n === "") return null;
        return Number(n).toFixed(2) + " s";
    }
    // Target calibration: deviation of the actual prompt tokens from the canonical
    // target context. Percent is signed (over/under target); the mean is in tokens.
    function fmtPct(n) {
        if (n == null || n === "") return null;
        var s = Number(n);
        if (!isFinite(s)) return null;
        return s.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + "%";
    }
    function fmtMeanError(n) {
        if (n == null || n === "") return null;
        var s = Number(n);
        if (!isFinite(s)) return null;
        return s.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 1 }) + " tokens";
    }
    // Human-readable byte counts for provenance (RAM / VRAM / file size).
    function fmtBytes(n) {
        if (n == null || n === "") return null;
        var b = Number(n);
        if (!isFinite(b) || b < 0) return null;
        var units = ["B", "KB", "MB", "GB", "TB"];
        var i = 0;
        while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
        return (i === 0 ? String(b) : b.toFixed(1)) + " " + units[i];
    }
    // Authoritative per-point status -> badge class. Only the four contract states are
    // styled distinctly; any other terminal failure state falls to is-failed so it never
    // reads as a healthy completed point.
    function pointStatusClass(status) {
        var s = String(status || "").toLowerCase();
        if (s === "completed") return "is-completed";
        if (s === "partial") return "is-partial";
        if (s === "unsupported") return "is-unsupported";
        return "is-failed";
    }
    // Authoritative per-point status -> uppercase label. Rendered verbatim; never inferred
    // from metric presence.
    function pointStatusText(status) {
        var s = String(status || "").toUpperCase();
        return s === "" ? "UNKNOWN" : s;
    }
    // A stage is valid when its own authoritative status is completed. This classifies the
    // backend speed_point_status value only -- it does not inherit the parent point's
    // status and never reads metrics to decide validity.
    function stageValidity(status) {
        return String(status || "").toLowerCase() === "completed" ? "valid" : "invalid";
    }
    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }
    // A cell: formatted value, or "Not recorded" when the telemetry is absent.
    function cell(value, fn) {
        if (value == null || value === "") return '<span class="srb-none">Not recorded</span>';
        var out = fn(value);
        return out == null ? '<span class="srb-none">Not recorded</span>' : esc(out);
    }

    function by(id) { return document.getElementById(id); }

    function runIdFromPath() {
        var p = window.location.pathname;
        if (p.indexOf(PATH_PREFIX) !== 0) return "";
        return decodeURIComponent(p.substring(PATH_PREFIX.length).split("/")[0]);
    }

    function statusBadgeClass(status) {
        switch (String(status || "").toLowerCase()) {
            case "completed": return "is-completed";
            case "partial":   return "is-partial";
            default:          return "is-unknown";
        }
    }

    // One row per canonical point. Columns: POINT | STATUS | ACTUAL INPUT | TARGET ERR. |
    // TTFT | PREFILL | GENERATION | OUTPUT | WALL. The authoritative per-point status is
    // rendered as an explicit Status cell (completed / partial / unsupported / failed);
    // metrics stay visible for completed and partial points alike -- a partial point still
    // shows its valid metric cells. Unsupported-but-stored points show the status plus a
    // single spanned note -- never zeros for their metrics.
    function pointRow(p) {
        var statusCell = '<span class="badge srb-badge ' + pointStatusClass(p.status) +
            '">' + esc(pointStatusText(p.status)) + "</span>";
        if (String(p.status || "").toLowerCase() === "unsupported") {
            return "<tr class='srb-uns'>" +
                '<td class="srb-point">' + esc(p.label) + "</td>" +
                '<td class="srb-status-cell">' + statusCell + "</td>" +
                '<td colspan="7" class="srb-none">Not supported</td></tr>';
        }
        var row = "<tr class='srb-point-row'>" +
            '<td class="srb-point">' + esc(p.label) + "</td>" +
            '<td class="srb-status-cell">' + statusCell + "</td>" +
            '<td class="srb-num">' + cell(p.actual_prompt_tokens, fmtTokens) + "</td>" +
            '<td class="srb-num">' + cell(p.target_error_percent, fmtPct) + "</td>" +
            '<td class="srb-num">' + cell(p.ttft_seconds, fmtMs) + "</td>" +
            '<td class="srb-num">' + cell(p.prefill_tokens_per_second, fmtRate) + "</td>" +
            '<td class="srb-num">' + cell(p.generation_tokens_per_second, fmtRate) + "</td>" +
            '<td class="srb-num">' + cell(p.completion_tokens, fmtTokens) + "</td>" +
            '<td class="srb-num">' + cell(p.wall_time_seconds, fmtWall) + "</td>" +
            "</tr>";
        // Progressive disclosure of persisted repeatability stages (1 cold + 2 warm). Only
        // stage-aware runs carry ``runs``; legacy single-row runs have none and instead show
        // the run-level repeatability note. Stages render in deterministic cold -> warm_a ->
        // warm_b order using backend values verbatim -- nothing recomputed here.
        var stages = (p && p.runs) ? p.runs : [];
        if (stages.length) {
            row += stageDisclosureRow(stages);
        }
        return row;
    }
    // Deterministic cold -> warm_a -> warm_b ordering; defensive against any store order.
    var STAGE_ORDER = { cold: 0, warm_a: 1, warm_b: 2 };
    function stageDisclosureRow(stages) {
        var ordered = stages.slice().sort(function (a, b) {
            return (STAGE_ORDER[a.speed_run_stage] != null ? STAGE_ORDER[a.speed_run_stage] : 99) -
                (STAGE_ORDER[b.speed_run_stage] != null ? STAGE_ORDER[b.speed_run_stage] : 99);
        });
        var rows = "";
        for (var i = 0; i < ordered.length; i++) {
            var s = ordered[i];
            rows += "<tr>" +
                '<td class="srb-point">' + esc((s.speed_run_stage || "?").toUpperCase()) + "</td>" +
                '<td class="srb-stage-run"><code>' + esc(s.run_id || "\u2014") + "</code></td>" +
                '<td class="srb-status-cell">' + '<span class="badge srb-badge ' + pointStatusClass(s.speed_point_status) +
                    '">' + esc(pointStatusText(s.speed_point_status)) + "</span>" + "</td>" +
                '<td class="srb-stage-validity ' + (stageValidity(s.speed_point_status) === "valid" ? "is-valid" : "is-invalid") +
                    '">' + esc(stageValidity(s.speed_point_status)) + "</td>" +
                '<td class="srb-num">' + cell(s.ttft_seconds, fmtMs) + "</td>" +
                '<td class="srb-num">' + cell(s.warm_ttft_seconds, fmtMs) + "</td>" +
                '<td class="srb-num">' + cell(s.prefill_tokens_per_second, fmtRate) + "</td>" +
                '<td class="srb-num">' + cell(s.generation_tokens_per_second, fmtRate) + "</td>" +
                '<td class="srb-num">' + cell(s.completion_tokens, fmtTokens) + "</td>" +
                '<td class="srb-num">' + cell(s.reasoning_output_tokens, fmtTokens) + "</td>" +
                '<td class="srb-num">' + cell(s.wall_time_seconds, fmtWall) + "</td>" +
                "</tr>";
        }
        return '<tr class="srb-stages-row"><td colspan="9"><details class="srb-stage-details">' +
            '<summary>Repeatability stages &mdash; ' + ordered.length + '</summary>' +
            '<div class="srb-stage-wrap"><table class="srb-stage-table" aria-label="Repeatability stages">' +
            '<thead><tr><th scope="col">Stage</th><th scope="col">Run ID</th><th scope="col">Status</th>' +
            '<th scope="col">Validity</th><th scope="col">TTFT</th><th scope="col">Warm TTFT</th>' +
            '<th scope="col">Prefill</th><th scope="col">Generation</th><th scope="col">Output</th>' +
            '<th scope="col">Reasoning</th><th scope="col">Wall</th></tr></thead>' +
            '<tbody>' + rows + '</tbody></table></div></details></td></tr>';
    }

    function renderIdentity(run) {
        var cfg = run.configuration || {};
        by("sr-model").textContent = run.model_identifier ? esc(run.model_identifier) : "\u2014";
        by("sr-run-id").textContent = run.run_id ? esc(run.run_id) : "\u2014";

        var badge = by("sr-status");
        badge.textContent = (run.status || "unknown");
        badge.className = "badge srb-badge " + statusBadgeClass(run.status);

        var ctx = cfg.loaded_context;
        by("sr-context").textContent = (ctx == null) ? "\u2014" : Number(ctx).toLocaleString("en-US");
        if (cfg.model_max_context != null) {
            by("sr-maxcontext").textContent = " \u00b7 max " +
                Number(cfg.model_max_context).toLocaleString("en-US") + " tokens";
        }

        // Runtime/config metadata: only actually-present fields, as chips.
        var order = ["hardware_label", "execution_environment", "connection_type",
            "max_output_tokens", "reasoning_mode"];
        var chips = [];
        for (var i = 0; i < order.length; i++) {
            var key = order[i];
            if (cfg[key] != null && cfg[key] !== "") {
                var label = key.replace(/_/g, " ");
                var val = cfg[key];
                if (key === "max_output_tokens") val = Number(val).toLocaleString("en-US");
                chips.push('<code>' + esc(label) + ': ' + esc(val) + '</code>');
            }
        }
        by("sr-config").innerHTML = chips.length
            ? chips.join("")
            : '<code class="srb-none">\u2014</code>';
    }

    function renderTable(run) {
        var body = by("sr-body");
        body.innerHTML = "";
        // Read model already returns canonical order; sort defensively anyway.
        var points = (run.points || []).slice().sort(function (a, b) {
            return (a.target_context_tokens || 0) - (b.target_context_tokens || 0);
        });
        if (!points.length) {
            body.innerHTML = '<tr><td colspan="9" class="srb-none">No point data recorded.</td></tr>';
            return;
        }
        // Act 20: legacy runs (no speed_metric_version == 2) still show their stored prefill
        // values, but with an honest warning that they were derived from a cached TTFT.
        if (run.legacy_prefill_warning) {
            body.insertAdjacentHTML("afterbegin",
                '<tr class="srb-legacy"><td colspan="9">Legacy prefill measurement \u2014 affected ' +
                'by prompt-cache reuse.</td></tr>');
        }
        // Act 26-AA-0017: genuine legacy single-row runs (no persisted cold/warm stages)
        // get an explicit repeatability note. Driven by the authoritative read-model
        // ``repeatability_stored`` signal -- never inferred.
        var repNote = by("sr-repeatability-note");
        if (repNote) {
            repNote.classList.toggle("hidden", run.repeatability_stored !== false);
        }
        for (var j = 0; j < points.length; j++) {
            body.insertAdjacentHTML("beforeend", pointRow(points[j]));
        }
    }

    // Run-level target calibration summary (Act 21). Hidden until the read model
    // exposes input data -- a run with no calibration evidence stays hidden rather
    // than showing fabricated numbers.
    function renderCalibration(run) {
        var maxPct = (run && run.max_abs_target_error_percent);
        var meanTok = (run && run.mean_target_error_tokens);
        if (maxPct == null && meanTok == null) { return; }
        var el = by("sr-calib");
        if (!el) { return; }
        var maxEl = by("sr-calib-max");
        var meanEl = by("sr-calib-mean");
        if (maxEl) {
            maxEl.innerHTML = (maxPct == null)
                ? '<span class="srb-none">Not recorded</span>'
                : esc(fmtPct(maxPct));
        }
        if (meanEl) {
            meanEl.innerHTML = (meanTok == null)
                ? '<span class="srb-none">Not recorded</span>'
                : esc(fmtMeanError(meanTok));
        }
        el.classList.remove("hidden");
    }

    // --- Run-level execution provenance (Act 26-AA-0017) --------------------------
    // Progressive disclosure with the three shared meanings -- Stored / Unknown /
    // Not stored -- grouped under Hardware / Runtime / Model / Inference. The read model
    // classifies each field via the shared provenance contract, so "Unknown" never
    // collapses into "Not stored". A legacy run (no provenance object) shows an explicit
    // not-captured note rather than fabricated per-field rows.
    function renderProvenance(run) {
        var el = by("sr-provenance");
        if (!el) { return; }
        var prov = (run && run.provenance);
        if (!prov || !prov.present) {
            el.innerHTML = '<p class="srb-note">Run provenance was not captured for this legacy ' +
                'run &mdash; it predates the shared execution-provenance contract.</p>';
            el.classList.remove("hidden");
            return;
        }
        var html = "";
        html += provSection("Hardware", prov.hardware, prov.hardware_extra);
        html += provSection("Runtime", prov.runtime);
        html += provSection("Model", prov.model);
        html += provSection("Inference", prov.inference);
        el.innerHTML = html;
        el.classList.remove("hidden");
    }
    function provSection(name, fields, extra) {
        var keys = Object.keys(fields || {});
        if (!keys.length && !(extra && Object.keys(extra).length)) { return ""; }
        var out = '<details class="srb-prov-section"><summary>' + esc(name) + '</summary>' +
            '<table class="srb-prov-table" aria-label="' + esc(name) + ' provenance"><tbody>';
        for (var i = 0; i < keys.length; i++) {
            out += '<tr><th scope="row">' + esc(keys[i]) + '</th><td>' + provField(fields[keys[i]]) + '</td></tr>';
        }
        if (extra) {
            var ekeys = Object.keys(extra);
            for (var j = 0; j < ekeys.length; j++) {
                out += '<tr><th scope="row">' + esc(ekeys[j]) + '</th><td>' + provField(extra[ekeys[j]]) + '</td></tr>';
            }
        }
        out += '</tbody></table></details>';
        return out;
    }
    // Render one classified provenance field. stored -> the persisted value; unknown_at
    // execution -> "Unknown"; not_stored -> "Not stored". Booleans render as Yes/No.
    function provField(field) {
        if (!field || !field.status) { return '<span class="srb-prov-none">Not stored</span>'; }
        if (field.status === "unknown_at_execution") {
            return '<span class="srb-prov-unknown" title="Provenance exists but the runtime could not expose this field at execution time">Unknown</span>';
        }
        var v = field.value;
        if (v === null || v === undefined || v === "") {
            return '<span class="srb-prov-none">Not stored</span>';
        }
        if (typeof v === "boolean") { return esc(v ? "Yes" : "No"); }
        if (typeof v === "number") { return esc(v.toLocaleString("en-US")); }
        return esc(v);
    }

    function showRun(run) {
        by("srb-loading").classList.add("hidden");
        by("srb-identity").classList.remove("hidden");
        by("srb-points").classList.remove("hidden");
        renderIdentity(run);
        renderCalibration(run);
        renderTable(run);
        renderProvenance(run);
    }

    function showError(detail) {
        var loading = by("srb-loading");
        if (loading) loading.classList.add("hidden");
        var err = by("srb-error");
        if (err) err.classList.remove("hidden");
        by("srb-error-detail").textContent = detail || "Unknown error.";
    }

    function init() {
        var id = runIdFromPath();
        if (!id) { showError("No Speed run id in URL."); return; }

        fetch("/api/speed/runs/" + encodeURIComponent(id))
            .then(function (r) {
                if (!r.ok) {
                    return r.json().catch(function () { return {}; }).then(function (b) {
                        throw new Error((b && b.detail) ? b.detail : ("HTTP " + r.status));
                    });
                }
                return r.json();
            })
            .then(function (run) { showRun(run); })
            .catch(function (e) { showError(e && e.message ? e.message : "Unable to load Speed result."); });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
