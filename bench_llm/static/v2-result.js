/* V2 result summary page (Act 6). Vanilla JS, no framework, no benchmark logic.
 * Responsibilities: parse run id from URL -> fetch the read-only API -> render the
 * authoritative score / suite strip / configuration block + copy interactions and
 * loading/error states. All numbers come straight from the API response; the page
 * never reconstructs /166 from suite rows or diagnostic data. */

(function () {
    "use strict";

    // Presentation-only labels (not benchmark values). Suite display names are stable UI text.
    var SUITE_LABELS = {
        python: "Python", java: "Java", markdown: "Markdown",
        evidence: "Evidence", drift: "DRIFT"
    };

    // Human-friendly output-budget policy display; raw code kept as title/copy value.
    var OUTPUT_POLICY_LABELS = {
        "75_percent_context": "75% of context",
        "90_percent_context": "90% of context",
        "80_percent_context": "80% of context"
    };

    // ---- DOM helpers ---------------------------------------------------
    function by(id) { return document.getElementById(id); }

    function show(el) { if (el) el.classList.remove("hidden"); }
    function hide(el) { if (el) el.classList.add("hidden"); }

    function fmt(n) {
        if (n === null || n === undefined || n === "") return "N/A";
        return Number(n).toLocaleString("en-US");
    }

    // Short a long hex string: first8 + … + last8
    function shortFp(fp) {
        if (!fp || fp.length <= 20) return fp;
        return fp.slice(0, 8) + "…" + fp.slice(-8);
    }

    function classificationState(c) {
        var v = String(c || "").trim().toLowerCase();
        if (v === "canonical") return "is-canonical";
        if (v === "incomplete") return "is-incomplete";
        return "is-default";
    }

    // ---- Copy with graceful fallback ----------------------------------
    function copyText(text, btn) {
        var done = function () {
            if (btn) {
                btn.classList.add("is-copied");
                setTimeout(function () { btn.classList.remove("is-copied"); }, 1200);
            }
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text, btn, done); });
        } else {
            fallbackCopy(text, btn, done);
        }
    }

    function fallbackCopy(text, btn, done) {
        try {
            var ta = document.createElement("textarea");
            ta.value = text;
            ta.style.position = "fixed";
            ta.style.opacity = "0";
            document.body.appendChild(ta);
            ta.select();
            document.execCommand("copy");
            document.body.removeChild(ta);
            done();
        } catch (e) { /* clipboard unavailable — feedback just won't flash */ }
    }

    // ---- Rendering -----------------------------------------------------
    function renderHeader(run) {
        by("v2-model").textContent = run.model_identifier || "(unknown model)";
        by("v2-overall").textContent = fmt(run.checks_passed) + " / " + fmt(run.checks_total);

        var pct;
        if (typeof run.percentage === "number") {
            pct = String(run.percentage);
        } else if (run.checks_total) {
            pct = ((run.checks_passed / run.checks_total) * 100).toFixed(1);
        } else {
            pct = "";
        }
        by("v2-pct").textContent = pct ? pct + "%" : "";

        var cls = run.classification || "unknown";
        var badge = by("v2-classification");
        badge.textContent = String(cls).toUpperCase();
        badge.className = "badge v2-badge " + classificationState(cls);
    }

    function suiteFill(passed, total) {
        // subtle per-suite health colour; never used to derive canonical totals
        var r = total ? (passed / total) : 0;
        if (r >= 0.9) return "var(--v2-success)";
        if (r >= 0.5) return "var(--v2-warning)";
        return "var(--v2-danger)";
    }

    function renderSuites(suites) {
        var list = by("v2-suites");
        list.innerHTML = "";
        (suites || []).forEach(function (s) {
            var passed = s.checks_passed;
            var total = s.checks_total;
            var pct = total ? Math.round((passed / total) * 100) : 0;

            var li = document.createElement("li");
            li.className = "v2-suite-cell";

            var label = document.createElement("div");
            label.className = "v2-suite-label";
            label.textContent = SUITE_LABELS[s.suite] || String(s.suite).replace(/_/g, " ");

            var score = document.createElement("div");
            score.className = "v2-suite-score";
            score.textContent = fmt(passed) + " / " + fmt(total);

            var bar = document.createElement("div");
            bar.className = "v2-suite-bar";
            var fill = document.createElement("span");
            fill.style.setProperty("--pct", pct + "%");
            fill.style.setProperty("--fill", suiteFill(passed, total));
            bar.appendChild(fill);

            li.append(label, score, bar);
            list.appendChild(li);
        });
    }

    function renderConfiguration(cfg, run) {
        cfg = cfg || {};
        by("c-context").textContent = fmt(cfg.effective_context_capacity);

        var policyRaw = cfg.output_budget_policy;
        var outEl = by("c-output");
        outEl.textContent = OUTPUT_POLICY_LABELS[policyRaw] || (policyRaw ? String(policyRaw).replace(/_/g, " ") : "N/A");
        outEl.title = policyRaw || ""; // raw code preserved on hover

        by("c-maxout").textContent = fmt(cfg.requested_max_output_tokens);
        by("c-reasoning").textContent = (run.reasoning_policy || cfg.reasoning_policy || "N/A");

        var cls = run.classification || "unknown";
        var cBadge = by("c-classification");
        cBadge.textContent = String(cls).toUpperCase();
        cBadge.className = "badge v2-badge " + classificationState(cls);

        // Fingerprint: short by default, full hidden until "Full".
        var fp = cfg.configuration_fingerprint || "";
        var shortEl = by("c-fp-short");
        var fullEl = by("c-fp-full");
        if (fp) {
            shortEl.textContent = shortFp(fp);
            fullEl.textContent = fp; // exact, untruncated value kept for copy + expand
            show(fullEl);
            hide(shortEl);
            // Show short first via explicit toggle state below.
            fullEl.classList.add("v2-hidden");
            shortEl.classList.remove("v2-hidden");
        } else {
            by("c-fp-short").textContent = "N/A";
            fullEl.textContent = "N/A";
        }
    }

    // ---- Init / fetch --------------------------------------------------
    function extractRunId() {
        var parts = location.pathname.split("/").filter(Boolean); // drop empty segments
        // Expect: v2 / results / <run_id>
        if (parts.length >= 3 && parts[0] === "v2" && parts[1] === "results") {
            return decodeURIComponent(parts.slice(2).join("/"));
        }
        return "";
    }

    function showError(message) {
        hide(by("v2-loading"));
        hide(by("v2-sticky"));
        hide(by("v2-config"));
        hide(by("v2-preview"));
        var el = by("v2-error");
        by("v2-error-detail").textContent = message;
        show(el);
    }

    function init() {
        var runId = extractRunId();
        if (!runId) {
            showError("No run ID found in URL (expected /v2/results/{run_id}).");
            return;
        }

        fetch("/api/v2/results/" + encodeURIComponent(runId), { headers: { Accept: "application/json" } })
            .then(function (resp) {
                if (!resp.ok) {
                    // Read-only API never leaks stack traces; surface the concise detail.
                    return resp.json().then(function (body) {
                        var d = body && body.detail ? body.detail : ("HTTP " + resp.status);
                        throw new Error(d);
                    }).catch(function () { throw new Error("HTTP " + resp.status); });
                }
                return resp.json();
            })
            .then(function (body) {
                if (!body || typeof body.run !== "object" || Array.isArray(body.run)) {
                    throw new Error("Unexpected response shape from API.");
                }
                hide(by("v2-loading"));
                show(by("v2-sticky"));
                show(by("v2-config"));

                renderHeader(body.run);
                renderSuites(body.suites || []);
                renderConfiguration(body.configuration, body.run);

                wireInteractions(runId);
            })
            .catch(function (err) {
                showError(err && err.message ? err.message : "Unable to load V2 result.");
            });
    }

    function wireInteractions(runId) {
        by("v2-copy-run").addEventListener("click", function () { copyText(runId, this); });
        by("v2-copy-fp").addEventListener("click", function () {
            copyText(by("c-fp-full").textContent, this);
        });

        var fpShort = by("c-fp-short");
        var fpFull = by("c-fp-full");
        by("v2-toggle-fp").addEventListener("click", function () {
            var showingFull = !fpFull.classList.contains("v2-hidden");
            if (showingFull) {
                fpFull.classList.add("v2-hidden");
                fpShort.classList.remove("v2-hidden");
                this.textContent = "Full";
            } else {
                fpFull.classList.remove("v2-hidden");
                fpShort.classList.add("v2-hidden");
                this.textContent = "Short";
            }
        });
    }

    init();
})();
