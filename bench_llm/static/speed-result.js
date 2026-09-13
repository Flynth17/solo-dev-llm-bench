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

    // One row per canonical point. Unsupported-but-stored points render as a
    // single spanned note -- never zeros for their metrics.
    function pointRow(p) {
        if (String(p.status || "").toLowerCase() === "unsupported") {
            return '<tr class="srb-uns"><td class="srb-point">' + esc(p.label) +
                '</td><td colspan="6" class="srb-none">Not supported</td></tr>';
        }
        return "<tr>" +
            '<td class="srb-point">' + esc(p.label) + "</td>" +
            '<td class="srb-num">' + cell(p.actual_prompt_tokens, fmtTokens) + "</td>" +
            '<td class="srb-num">' + cell(p.ttft_seconds, fmtMs) + "</td>" +
            '<td class="srb-num">' + cell(p.prefill_tokens_per_second, fmtRate) + "</td>" +
            '<td class="srb-num">' + cell(p.generation_tokens_per_second, fmtRate) + "</td>" +
            '<td class="srb-num">' + cell(p.completion_tokens, fmtTokens) + "</td>" +
            '<td class="srb-num">' + cell(p.wall_time_seconds, fmtWall) + "</td>" +
            "</tr>";
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
            body.innerHTML = '<tr><td colspan="7" class="srb-none">No point data recorded.</td></tr>';
            return;
        }
        // Act 20: legacy runs (no speed_metric_version == 2) still show their stored prefill
        // values, but with an honest warning that they were derived from a cached TTFT.
        if (run.legacy_prefill_warning) {
            body.insertAdjacentHTML("afterbegin",
                '<tr class="srb-legacy"><td colspan="7">Legacy prefill measurement \u2014 affected ' +
                'by prompt-cache reuse.</td></tr>');
        }
        for (var j = 0; j < points.length; j++) {
            body.insertAdjacentHTML("beforeend", pointRow(points[j]));
        }
    }

    function showRun(run) {
        by("srb-loading").classList.add("hidden");
        by("srb-identity").classList.remove("hidden");
        by("srb-points").classList.remove("hidden");
        renderIdentity(run);
        renderTable(run);
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
