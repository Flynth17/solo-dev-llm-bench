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

    // ---- Failure inspector (Act 7) -------------------------------------
    // Canonical failed-check counts come from the authoritative suite aggregate
    // (presentation math only). Diagnostics are inspection records. The overall
    // /166 score is never derived from these rows, and any bogus per-row validator
    // total (e.g. 9999) never becomes a canonical denominator.

    function plural(n, word) {
        return n + " " + word + (n === 1 ? "" : "s");
    }

    function suiteDisplayLabel(key) {
        return SUITE_LABELS[key] || String(key).replace(/_/g, " ");
    }

    // Compact collapsed label; avoid repetition when failure_type == source_type.
    function rowKindLabel(f) {
        var t = String(f.failure_type || "").trim();
        var s = String(f.source_type || "").trim();
        if (!t && !s) return "failure";
        if (t === s) return t || "unknown";
        return (t || "unknown") + " · " + s;
    }

    // Single-line visual preview for the collapsed row. Whitespace is collapsed
    // ONLY here; the expanded value stays byte-exact.
    function shortPreview(reason) {
        if (reason === null || reason === undefined) return "";
        var oneLine = String(reason).replace(/\s+/g, " ").trim();
        var MAX = 120;
        return oneLine.length > MAX ? oneLine.slice(0, MAX) + "…" : oneLine;
    }

    function monoText(value) {
        var span = document.createElement("span");
        span.className = "v2-v v2-mono";
        span.textContent = value == null ? "N/A" : String(value);
        return span;
    }

    function renderFailures(suites, failures) {
        var groupsEl = by("v2-failure-groups");
        var emptyEl = by("v2-failures-empty");
        var summaryEl = by("v2-failure-summary");
        var diagCountEl = by("v2-failure-diag-count");
        if (!groupsEl) return;

        groupsEl.innerHTML = "";

        // Authoritative per-suite failed-check counts (fallback: total - passed).
        var byKey = {};
        (suites || []).forEach(function (s) {
            var key = s.suite;
            var failed = (s.failed_checks != null) ? s.failed_checks
                : Math.max(0, (s.checks_total || 0) - (s.checks_passed || 0));
            byKey[key] = { label: suiteDisplayLabel(key), failed: failed, items: [] };
        });

        // Attach diagnostics to their canonical-suite group.
        (failures || []).forEach(function (f) {
            var g = byKey[f.suite];
            if (g) g.items.push(f);
        });

        // Canonical suite order; include a group when it has rows or failed checks.
        var orderedSuites = (suites || []).map(function (s) { return s.suite; });
        var groups = orderedSuites.filter(function (key) {
            return byKey[key] && (byKey[key].items.length > 0 || byKey[key].failed > 0);
        }).map(function (key) { return byKey[key]; });

        // Headline: canonical failed checks vs. number of diagnostic records.
        var totalFailed = orderedSuites.reduce(function (acc, key) {
            return acc + (byKey[key] ? byKey[key].failed : 0);
        }, 0);
        var suitesWithFailures = orderedSuites.filter(function (key) {
            return byKey[key] && byKey[key].failed > 0;
        }).length;

        if (diagCountEl) diagCountEl.textContent = String((failures || []).length);
        summaryEl.textContent = plural(totalFailed, "failed check") + " · " +
            plural((failures || []).length, "recorded diagnostic");
        if (suitesWithFailures > 0) {
            summaryEl.textContent += " across " + plural(suitesWithFailures, "suite");
        }

        if (!groups.length) {
            if (emptyEl) show(emptyEl);
            return;
        }
        if (emptyEl) hide(emptyEl);

        groups.forEach(function (group) {
            groupsEl.appendChild(buildGroup(group));
        });
    }

    function buildGroup(group) {
        var doc = document.createElement("section");
        doc.className = "v2-failure-group";

        var legend = document.createElement("div");
        legend.className = "v2-group-legend";

        var label = document.createElement("span");
        label.className = "v2-group-label";
        label.textContent = group.label;

        var counts = document.createElement("span");
        counts.className = "v2-group-counts";
        counts.textContent = plural(group.failed, "failed check") +
            " · " + plural((group.items || []).length, "recorded diagnostic");

        legend.append(label, counts);

        var rowsList = document.createElement("ul");
        rowsList.className = "v2-failure-rows";
        (group.items || []).forEach(function (f) {
            rowsList.appendChild(buildRow(f));
        });

        doc.append(legend, rowsList);
        return doc;
    }

    function buildRow(f) {
        var li = document.createElement("li");
        li.className = "v2-failure-row";

        // Collapsed row toggle (accessible <button>).
        var toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "v2-row-toggle";
        toggle.setAttribute("aria-expanded", "false");
        toggle.setAttribute("aria-label", "Toggle failure details: " + rowKindLabel(f));

        var glyph = document.createElement("span");
        glyph.className = "v2-row-glyph";
        glyph.textContent = "▸";
        glyph.setAttribute("aria-hidden", "true");

        var kind = document.createElement("span");
        kind.className = "v2-row-kind";
        kind.textContent = rowKindLabel(f);

        var preview = document.createElement("span");
        preview.className = "v2-row-preview";
        preview.textContent = shortPreview(f.failure_reason);

        toggle.append(glyph, kind, preview);

        // Expanded body.
        var body = document.createElement("div");
        body.className = "v2-row-body v2-hidden";
        buildBodyFields(f, body);

        toggle.addEventListener("click", function () {
            var nowOpen = toggle.getAttribute("aria-expanded") === "true";
            toggle.setAttribute("aria-expanded", String(!nowOpen));
            glyph.textContent = nowOpen ? "▸" : "▾";
            if (nowOpen) hide(body); else show(body);
        });

        li.append(toggle, body);
        return li;
    }

    // Build a label/value field row with optional trailing copy buttons.
    function fieldRow(keyLabel, valueEl, extraButtons) {
        var row = document.createElement("div");
        row.className = "v2-row-field";
        var k = document.createElement("span");
        k.className = "v2-k";
        k.textContent = keyLabel;
        row.appendChild(k);
        if (valueEl) row.appendChild(valueEl);
        if (extraButtons) extraButtons.forEach(function (b) { row.appendChild(b); });
        return row;
    }

    function copyBtn(text, label) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "v2-mini-btn";
        b.textContent = "Copy";
        b.title = "Copy " + label;
        b.setAttribute("aria-label", "Copy " + label);
        b.addEventListener("click", function () { copyText(text, this); });
        return b;
    }

    // Expected / Actual: ONLY render when at least one side is non-null. Never
    // fabricate a comparison block for null data (current fixture has none).
    function buildEacompare(f) {
        var exp = f.expected;
        var act = f.actual;
        if ((exp !== null && exp !== undefined) || (act !== null && act !== undefined)) {
            var box = document.createElement("div");
            box.className = "v2-eacompare";
            var eh = document.createElement("span"); eh.textContent = "Expected";
            var av = document.createElement("span"); av.textContent = String(exp == null ? "" : exp);
            var ah = document.createElement("span"); ah.textContent = "Actual";
            var vv = document.createElement("span"); vv.textContent = String(act == null ? "" : act);
            box.append(eh, av, ah, vv);
            return box;
        }
        return null; // no comparison block
    }

    function buildBodyFields(f, body) {
        body.appendChild(fieldRow("Failure type", monoText(f.failure_type)));
        body.appendChild(fieldRow("Source type", monoText(f.source_type)));

        // Request linkage: only when a real request id exists (never fabricated).
        if (f.request_id) {
            body.appendChild(fieldRow("Request", monoText(f.request_id), [copyBtn(f.request_id, "request ID")]));
        }

        // Locator / case id: stable reference (read-model field names).
        if (f.locator) {
            body.appendChild(fieldRow("Locator", monoText(f.locator), [copyBtn(f.locator, "locator")]));
        } else if (f.case_id) {
            body.appendChild(fieldRow("Case", monoText(f.case_id), [copyBtn(f.case_id, "case ID")]));
        }

        // Exact-preservation reason block (white-space: pre-wrap in CSS).
        if (f.failure_reason !== null && f.failure_reason !== undefined) {
            var rhead = document.createElement("div");
            rhead.className = "v2-row-field";
            var rk = document.createElement("span");
            rk.className = "v2-k";
            rk.textContent = "Reason";
            rhead.appendChild(rk);
            body.appendChild(rhead);

            var reasonEl = document.createElement("div");
            reasonEl.className = "v2-reason";
            reasonEl.textContent = String(f.failure_reason); // byte-exact

            var reasonBtn = copyBtn(String(f.failure_reason), "failure reason");
            body.appendChild(reasonEl);
            body.appendChild(reasonBtn);
        }

        // Conditional Expected / Actual (only non-null).
        var eacompare = buildEacompare(f);
        if (eacompare) body.appendChild(eacompare);

        // Optional raw diagnostic detail (debugging aid; compact JSON).
        var rawToggle = document.createElement("button");
        rawToggle.type = "button";
        rawToggle.className = "v2-mini-btn v2-raw-toggle";
        rawToggle.textContent = "Raw details ▸";
        rawToggle.setAttribute("aria-expanded", "false");

        var rawEl = document.createElement("pre");
        rawEl.className = "v2-raw v2-hidden";
        try {
            var clone = Object.assign({}, f);
            clone.failure_reason_preview = String(f.failure_reason || "");
            rawEl.textContent = JSON.stringify(clone, null, 2);
        } catch (e) {
            rawEl.textContent = String(f);
        }

        rawToggle.addEventListener("click", function () {
            var open = rawToggle.getAttribute("aria-expanded") === "true";
            rawToggle.setAttribute("aria-expanded", String(!open));
            rawToggle.textContent = open ? "Raw details ▸" : "Raw details ▾";
            if (open) hide(rawEl); else show(rawEl);
        });

        body.appendChild(rawToggle);
        body.appendChild(rawEl);
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
        hide(by("v2-failures"));
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
                renderFailures(body.suites || [], body.failures || []);

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
