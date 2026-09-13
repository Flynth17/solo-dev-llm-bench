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

    // ---- Act 8 filter/navigation state --------------------------------
    // DATA caches the authoritative API payload once so filtering never touches it.
    // FILTER holds active criteria (AND-combined). EXPANDED is a Set of locators that
    // are expanded; it persists across rebuilds so hidden rows retain their state.
    var DATA = { suites: null, failures: null, orderedSuites: [], byKey: {}, byKeyItems: {}, successes: [], reqOrder: [] };
    var FILTER = { suite: "", type: "", query: "" };
    var EXPANDED = new Set();
    // Act 9: whether the collapsed Request Details section has been revealed. Default false.
    var REQUEST_REVEALED = false;
    var SUITE_FILTER_BTNS = [];   // toolbar suite filter <button> refs
    var STRIP_CELLS = [];         // suite-strip cell <button> refs
    var SEARCH_TIMER = null;

    // ---- DOM helpers ---------------------------------------------------
    function by(id) { return document.getElementById(id); }

    // One visibility mechanism for this page: the page-specific .v2-hidden rule.
    // Helpers also defensively clear any bare `hidden` (shared.css) so shell elements
    // that still carry it in HTML (sticky/config/failures/error/loading) keep working
    // without touching markup. hide() normalises to .v2-hidden rather than stacking a
    // stray bare `hidden`, keeping state consistent across toggles.
    function show(el) { if (!el) return; el.classList.remove("hidden"); el.classList.remove("v2-hidden"); }
    function hide(el) { if (!el) return; el.classList.add("v2-hidden"); el.classList.remove("hidden"); }

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
        STRIP_CELLS = [];
        (suites || []).forEach(function (s) {
            var passed = s.checks_passed;
            var total = s.checks_total;
            var pct = total ? Math.round((passed / total) * 100) : 0;

            var li = document.createElement("li");
            li.className = "v2-suite-li";

            // Accessible button: clicking a suite activates its failure filter and scrolls to the inspector.
            // This only changes which diagnostics are visible; it never alters score presentation.
            var cell = document.createElement("button");
            cell.type = "button";
            cell.className = "v2-suite-cell";
            cell.dataset.suite = s.suite;
            cell.setAttribute("aria-pressed", "false");
            cell.setAttribute("aria-label",
                SUITE_LABELS[s.suite] + " score " + fmt(passed) + " of " + fmt(total) + ". Click to filter failures.");

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

            cell.append(label, score, bar);
            li.append(cell);
            list.appendChild(li);
            STRIP_CELLS.push(cell);

            cell.addEventListener("click", function () {
                FILTER.suite = s.suite;
                applyFilterUI();
                refreshGroups();
                scrollToInspector();
            });
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
            // Initial visibility is set exclusively via the page .v2-hidden rule (no bare
            // `hidden`): adding one to shortEl would hide it through shared.css and break
            // returning to the Short view. The helper mismatch previously did exactly this.
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

    // Numeric cell (readable thousands grouping) for request diagnostics/telemetry.
    function numSpan(v) {
        var s = document.createElement("span");
        s.className = "v2-v is-num";
        if (v == null || v === "") { s.textContent = "N/A"; return s; }
        s.textContent = typeof v === "number" ? Number(v).toLocaleString("en-US") : String(v);
        return s;
    }

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

    // Deterministic, URL-safe DOM id derived from the stable read-model locator.
    function makeRowId(loc) {
        var slug = String(loc || "")
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-+|-+$/g, "")
            .slice(0, 120);
        if (!slug) return "diag";
        return "fail-" + slug;
    }

    function countsOfRecords() {
        return DATA.failures ? DATA.failures.length : 0;
    }

    function countsOfSuite(key) {
        var list = DATA.byKeyItems[key];
        return list ? list.length : 0;
    }

    function countOfType(t) {
        var n = 0;
        (DATA.failures || []).forEach(function (f) { if (f.failure_type === t) n++; });
        return n;
    }

    // Cache authoritative data once, build the toolbar, set headline counts,
    // then refresh the filtered view. Headline + legend counts are NOT affected by filters.
    function renderFailures(suites, failures) {
        DATA.suites = suites || [];
        DATA.failures = failures || [];
        DATA.byKey = {};
        DATA.byKeyItems = {};
        (DATA.suites || []).forEach(function (s) {
            var key = s.suite;
            var failed = (s.failed_checks != null) ? s.failed_checks
                : Math.max(0, (s.checks_total || 0) - (s.checks_passed || 0));
            DATA.byKey[key] = { label: suiteDisplayLabel(key), failed: failed, items: [] };
            DATA.byKeyItems[key] = [];
        });
        DATA.orderedSuites = (DATA.suites || []).map(function (s) { return s.suite; });

        // Attach diagnostics to canonical-suite buckets (preserves source order).
        (DATA.failures || []).forEach(function (f) {
            if (DATA.byKeyItems[f.suite]) DATA.byKeyItems[f.suite].push(f);
            if (DATA.byKey[f.suite]) DATA.byKey[f.suite].items.push(f);
        });

        // Headline counts are authoritative and invariant to filtering.
        var diagCountEl = by("v2-failure-diag-count");
        var summaryEl = by("v2-failure-summary");
        if (diagCountEl) diagCountEl.textContent = String(DATA.failures.length);
        var totalFailed = DATA.orderedSuites.reduce(function (acc, key) {
            return acc + (DATA.byKey[key] ? DATA.byKey[key].failed : 0);
        }, 0);
        var suitesWithFailures = DATA.orderedSuites.filter(function (key) {
            return DATA.byKey[key] && DATA.byKey[key].failed > 0;
        }).length;
        if (summaryEl) {
            summaryEl.textContent = plural(totalFailed, "failed check") + " · " +
                plural(DATA.failures.length, "recorded diagnostic");
            if (suitesWithFailures > 0) {
                summaryEl.textContent += " across " + plural(suitesWithFailures, "suite");
            }
        }

        buildToolbar();

        // Reveal the inspector section and its filter toolbar. Both start classed
        // `hidden` (shared.css `.hidden { display:none }`); nothing ever removed it,
        // so the panel stayed display:none in a real browser, hiding every row and
        // every Act 8 control inside it. Idempotent with show()/hide().
        show(by("v2-failures"));
        if (DATA.failures.length > 0) {
            show(by("v2-failure-toolbar"));
        }

        var emptyEl = by("v2-failures-empty");
        if (DATA.failures.length === 0) {
            if (emptyEl) show(emptyEl);
        } else if (emptyEl) {
            hide(emptyEl);
        }

        refreshGroups();
    }

    // Build the suite filter buttons + failure-type select once. Counts are recorded
    // diagnostic records per suite/type — never canonical failed-check counts.
    function buildToolbar() {
        var suiteBox = by("v2-suite-filters");
        if (!suiteBox) return;
        suiteBox.innerHTML = "";
        SUITE_FILTER_BTNS = [];

        var allBtn = document.createElement("button");
        allBtn.type = "button";
        allBtn.className = "v2-filter-btn is-active";
        allBtn.dataset.suite = "";
        allBtn.textContent = "All " + countsOfRecords();
        allBtn.setAttribute("aria-pressed", "true");
        allBtn.addEventListener("click", function () {
            FILTER.suite = "";   // empty string == All
            applyFilterUI();
            refreshGroups();
            scrollToInspector();
        });
        suiteBox.appendChild(allBtn);
        SUITE_FILTER_BTNS.push(allBtn);

        (DATA.orderedSuites || []).forEach(function (key) {
            var b = document.createElement("button");
            b.type = "button";
            b.className = "v2-filter-btn";
            b.dataset.suite = key;
            b.textContent = suiteDisplayLabel(key) + " " + countsOfSuite(key);
            b.setAttribute("aria-pressed", "false");
            b.addEventListener("click", function () {
                FILTER.suite = b.dataset.suite;   // e.g. "evidence"
                applyFilterUI();
                refreshGroups();
                scrollToInspector();
            });
            suiteBox.appendChild(b);
            SUITE_FILTER_BTNS.push(b);
        });

        // Type select: populated dynamically from real failure records (sorted).
        var sel = by("v2-type-filter");
        if (sel) {
            sel.innerHTML = "";
            var allOpt = document.createElement("option");
            allOpt.value = "";
            allOpt.textContent = "All types";
            sel.appendChild(allOpt);
            var typeSet = {};
            (DATA.failures || []).forEach(function (f) {
                if (f.failure_type != null && String(f.failure_type).trim() !== "") typeSet[String(f.failure_type)] = true;
            });
            Object.keys(typeSet).sort().forEach(function (t) {
                var o = document.createElement("option");
                o.value = t;
                o.textContent = t + " (" + countOfType(t) + ")";
                sel.appendChild(o);
            });
            sel.value = FILTER.type;
            sel.addEventListener("change", function () {
                FILTER.type = sel.value;
                applyFilterUI();
                refreshGroups();
            });
        }

        applyFilterUI();
    }

    // AND-combined filter predicate. Pure: returns a filtered list without touching DOM.
    // Search uses a derived normalized string for matching only; it never mutates the
    // original (byte-exact) strings used for display.
    function getVisibleFailures() {
        var q = (FILTER.query || "").trim().toLowerCase();
        return (DATA.failures || []).filter(function (f) {
            if (FILTER.suite && f.suite !== FILTER.suite) return false;
            if (FILTER.type && f.failure_type !== FILTER.type) return false;
            if (q) {
                var hay = [
                    String(f.failure_type || ""),
                    String(f.source_type || ""),
                    String(f.failure_reason || ""),
                    String(f.case_id || ""),
                    String(f.request_id || ""),
                    String(f.locator || "")
                ].join("\n").toLowerCase();
                if (hay.indexOf(q) === -1) return false;
            }
            return true;
        });
    }

    // Re-render filtered groups into the DOM. Group legends keep AUTHORITATIVE counts;
    // only which rows/groups are visible is controlled by the filter.
    function refreshGroups() {
        var groupsEl = by("v2-failure-groups");
        var showingEl = by("v2-showing-count");
        if (!groupsEl) return;
        groupsEl.innerHTML = "";

        var visible = getVisibleFailures();
        var buckets = {};
        DATA.orderedSuites.forEach(function (k) { buckets[k] = []; });
        visible.forEach(function (f) { if (buckets[f.suite]) buckets[f.suite].push(f); });

        DATA.orderedSuites.forEach(function (key) {
            var items = buckets[key] || [];
            if (!items.length) return;              // hide groups with no matching records
            groupsEl.appendChild(buildGroup(DATA.byKey[key], items));
        });

        // Showing count / no-match state. Distinct from the global zero-failure empty state.
        if (showingEl) {
            var total = DATA.failures.length;
            if (total === 0) {
                showingEl.textContent = "";
            } else if (visible.length === 0) {
                showingEl.textContent = "No recorded diagnostics match the current filters.";
            } else {
                showingEl.textContent = "Showing " + visible.length + " of " + total +
                    " recorded diagnostic" + (total === 1 ? "" : "s");
            }
        }
    }

    // Keep toolbar suite buttons and suite-strip pressed state in sync with FILTER.suite.
    function applyFilterUI() {
        SUITE_FILTER_BTNS.forEach(function (b) {
            var on = (!FILTER.suite && b.dataset.suite === "") || (b.dataset.suite === FILTER.suite);
            b.classList.toggle("is-active", on);
            b.setAttribute("aria-pressed", String(on));
        });
        STRIP_CELLS.forEach(function (c) {
            var on = (!FILTER.suite && c.dataset.suite === "") || (c.dataset.suite === FILTER.suite);
            c.classList.toggle("v2-suite-cell--active", on);
            c.setAttribute("aria-pressed", String(on));
        });
    }

    function scrollToInspector() {
        var panel = by("v2-failures");
        if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    // ---- Group / row builders -----------------------------------------
    function buildGroup(group, items) {
        var listItems = items != null ? items : (group.items || []);
        var doc = document.createElement("section");
        doc.className = "v2-failure-group";

        var legend = document.createElement("div");
        legend.className = "v2-group-legend";

        var label = document.createElement("span");
        label.className = "v2-group-label";
        label.textContent = group.label;

        // Legend shows AUTHORITATIVE counts (canonical failed checks + total records).
        var counts = document.createElement("span");
        counts.className = "v2-group-counts";
        counts.textContent = plural(group.failed, "failed check") +
            " · " + plural(listItems.length, "recorded diagnostic");

        legend.append(label, counts);

        var rowsList = document.createElement("ul");
        rowsList.className = "v2-failure-rows";
        listItems.forEach(function (f) {
            rowsList.appendChild(buildRow(f));
        });

        doc.append(legend, rowsList);
        return doc;
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

    // Copyable stable link for a diagnostic row (origin + path + #fragment).
    function deepLinkUrlFor(f) {
        return location.origin + location.pathname + "#" + makeRowId(f.locator);
    }

    function buildRow(f) {
        var li = document.createElement("li");
        li.className = "v2-failure-row";
        // Stable, unique, URL-safe id derived from the read-model locator (deep link target).
        li.id = makeRowId(f.locator);

        // Collapsed row toggle (accessible <button>).
        var toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "v2-row-toggle";
        toggle.setAttribute("aria-label", "Toggle failure details: " + rowKindLabel(f));

        var glyph = document.createElement("span");
        glyph.className = "v2-row-glyph";
        glyph.textContent = "▸";
        glyph.setAttribute("aria-hidden", "true");

        var kind = document.createElement("span");
        kind.className = "v2-row-kind";
        kind.textContent = rowKindLabel(f);

        // Subtle copy-link control (deep link) — always available on the collapsed row.
        var linkBtn = copyBtn(deepLinkUrlFor(f), "Copy link to this diagnostic");
        linkBtn.setAttribute("aria-label", "Copy link to this diagnostic: " + f.locator);

        var preview = document.createElement("span");
        preview.className = "v2-row-preview";
        preview.textContent = shortPreview(f.failure_reason);

        toggle.append(glyph, kind, linkBtn, preview);

        // Expanded body. Visibility is driven by the .v2-hidden CSS rule (there is no
        // bare .hidden rule), so it must be toggled directly — not via show()/hide(),
        // which toggle the non-existent class and are a no-op.
        var body = document.createElement("div");
        body.className = "v2-row-body";
        buildBodyFields(f, body);

        // Expand state persists across rebuilds via the EXPANDED set (Act 8).
        var isExp = EXPANDED.has(f.locator);
        toggle.setAttribute("aria-expanded", String(isExp));
        glyph.textContent = isExp ? "▾" : "▸";
        if (isExp) body.classList.remove("v2-hidden"); else body.classList.add("v2-hidden");

        toggle.addEventListener("click", function () {
            var nowOpen = EXPANDED.has(f.locator);
            if (nowOpen) EXPANDED.delete(f.locator); else EXPANDED.add(f.locator);
            toggle.setAttribute("aria-expanded", String(!nowOpen));
            glyph.textContent = nowOpen ? "▸" : "▾";
            // Collapse -> add hidden, Expand -> remove hidden.
            body.classList.toggle("v2-hidden", nowOpen);
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

    // ---- Request traceability (Act 9) ---------------------------------

    // Deterministic, URL-safe DOM id for request rows. Distinct prefix from failure
    // rows so the two stable-anchor namespaces never collide (#req-...).
    function makeReqId(loc) {
        var slug = String(loc || "")
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-+|-+$/g, "")
            .slice(0, 120);
        if (!slug) return "req";
        return "req-" + slug;
    }

    // Render response.successes (request records whose extraction succeeded).
    // This is a secondary, collapsed-by-default traceability section. It NEVER
    // influences the canonical score/denominators -- those come only from body.run
    // and body.suites, which are untouched here.
    function renderRequests(suites, successes) {
        DATA.successes = successes || [];
        var countEl = by("v2-request-count");
        if (countEl) countEl.textContent = String(DATA.successes.length);

        // Bucket by canonical suite order, preserving source/request order within.
        DATA.reqByKey = {};
        DATA.reqOrder = [];
        (suites || []).forEach(function (s) { DATA.reqByKey[s.suite] = []; });
        (DATA.successes || []).forEach(function (r) {
            if (!DATA.reqByKey[r.suite]) return;   // unknown-suite guard
            if (!DATA.reqByKey[r.suite].length) DATA.reqOrder.push(r.suite);
            DATA.reqByKey[r.suite].push(r);
        });

        var listEl = by("v2-request-list");
        var emptyEl = by("v2-request-empty");
        if (listEl) listEl.innerHTML = "";

        // Collapsed by default: the reveal button controls visibility of the list.
        REQUEST_REVEALED = false;
        var toggle = by("v2-request-toggle");
        if (toggle) {
            toggle.setAttribute("aria-expanded", "false");
            toggle.textContent = "Show request details";
            hide(listEl);
            toggle.addEventListener("click", function () {
                REQUEST_REVEALED = !REQUEST_REVEALED;
                toggle.setAttribute("aria-expanded", String(REQUEST_REVEALED));
                toggle.textContent = REQUEST_REVEALED ? "Hide request details" : "Show request details";
                if (REQUEST_REVEALED) show(listEl); else hide(listEl);
            });
        }

        // Zero extraction-successful requests: reveal the empty state, keep list hidden.
        if (DATA.successes.length === 0) {
            if (emptyEl) show(emptyEl);
            return;
        }
        if (emptyEl) hide(emptyEl);

        // One <section> per suite that has >=1 extraction-successful request.
        DATA.reqOrder.forEach(function (key) {
            if (listEl) listEl.appendChild(buildReqGroup(key, DATA.reqByKey[key]));
        });
    }

    function buildReqGroup(suiteKey, items) {
        var doc = document.createElement("section");
        doc.className = "v2-failure-group v2-request-group";

        var legend = document.createElement("div");
        legend.className = "v2-group-legend";
        var label = document.createElement("span");
        label.className = "v2-group-label";
        label.textContent = suiteDisplayLabel(suiteKey);
        // Count is request RECORDS (not failed checks) -- distinct semantics from failures.
        var counts = document.createElement("span");
        counts.className = "v2-group-counts";
        counts.textContent = plural(items.length, "request record");
        legend.append(label, counts);

        var rowsList = document.createElement("ul");
        rowsList.className = "v2-failure-rows v2-request-rows";
        (items || []).forEach(function (r) { rowsList.appendChild(buildReqRow(r)); });

        doc.append(legend, rowsList);
        return doc;
    }

    function reqDeepLinkUrlFor(r) {
        return location.origin + location.pathname + "#" + makeReqId(r.locator);
    }

    function buildReqRow(r) {
        var li = document.createElement("li");
        li.className = "v2-failure-row v2-request-row";
        li.id = makeReqId(r.locator);   // stable anchor target: #req-...

        var toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "v2-row-toggle";
        toggle.setAttribute("aria-label", "Toggle request details: " + r.request_id);

        var glyph = document.createElement("span");
        glyph.className = "v2-row-glyph";
        glyph.textContent = "\u25b8";
        glyph.setAttribute("aria-hidden", "true");

        var kind = document.createElement("span");
        kind.className = "v2-row-kind";
        kind.textContent = suiteDisplayLabel(r.suite);

        // Stable deep-link copy control (always available on the collapsed row).
        var linkBtn = copyBtn(reqDeepLinkUrlFor(r), "link to this request record");

        // Collapsed preview preserves exact API values (no normalization of display).
        var preview = document.createElement("span");
        preview.className = "v2-row-preview";
        preview.textContent = r.request_id + " \u00b7 " + suiteDisplayLabel(r.suite) +
            " \u00b7 extraction: " + String(r.extraction_classification);

        toggle.append(glyph, kind, linkBtn, preview);

        var body = document.createElement("div");
        body.className = "v2-row-body";
        buildReqBodyFields(r, body);

        var isExp = EXPANDED.has(r.locator);
        toggle.setAttribute("aria-expanded", String(isExp));
        glyph.textContent = isExp ? "\u25be" : "\u25b8";
        if (isExp) body.classList.remove("v2-hidden"); else body.classList.add("v2-hidden");

        toggle.addEventListener("click", function () {
            var nowOpen = EXPANDED.has(r.locator);
            if (nowOpen) EXPANDED.delete(r.locator); else EXPANDED.add(r.locator);
            toggle.setAttribute("aria-expanded", String(!nowOpen));
            glyph.textContent = nowOpen ? "\u25b8" : "\u25be";
            body.classList.toggle("v2-hidden", nowOpen);
        });

        li.append(toggle, body);
        return li;
    }

    function buildReqBodyFields(r, body) {
        // Request id (copyable stable identifier).
        if (r.request_id) {
            body.appendChild(fieldRow("Request id", monoText(r.request_id), [copyBtn(r.request_id, "request ID")]));
        }
        // Locator: read-model reference + deep-link key.
        if (r.locator) {
            body.appendChild(fieldRow("Locator", monoText(r.locator), [copyBtn(r.locator, "locator")]));
        }
        // Extraction classification (exact API value).
        if (r.extraction_classification != null) {
            body.appendChild(fieldRow("Extraction", monoText(r.extraction_classification)));
        }

        // Validator diagnostics: INSPECTIONAL ONLY. Never a benchmark denominator.
        // The bogus per-request checks_total_validator (e.g. 9999) lives here and is
        // explicitly labeled -- it never becomes a score, progress bar, or total.
        var passed = r.checks_passed;
        var recTotal = r.checks_total;   // read-model field: checks_total_validator
        if (passed != null || recTotal != null) {
            var dia = document.createElement("div");
            dia.className = "v2-req-dia-block";
            var dhead = document.createElement("div");
            dhead.className = "v2-req-dia-head";
            dhead.textContent = "Validator diagnostics -- diagnostic only, not a benchmark denominator";
            dia.appendChild(dhead);
            if (passed != null) {
                dia.appendChild(fieldRow("Checks passed", numSpan(passed)));
            }
            if (recTotal != null) {
                // Labeled as the recorded validator total; when it is the bogus
                // diagnostic value it stays inside this amber block, never near score.
                dia.appendChild(fieldRow("Recorded validator total", numSpan(recTotal)));
            }
            body.appendChild(dia);
        }

        // Request telemetry: only request-scoped values actually recorded. Omitted
        // entirely when all null (avoid all-null rows); never suite-level telemetry.
        var tel = r.telemetry || {};
        var telFields = [
            ["ttft_seconds", "TTFT (s)"],
            ["prefill_throughput", "Prefill throughput (tok/s)"],
            ["decode_throughput", "Decode throughput (tok/s)"],
            ["reasoning_tokens", "Reasoning tokens"]
        ];
        var present = telFields.filter(function (t) { return tel[t[0]] != null; });
        if (present.length) {
            var telBox = document.createElement("div");
            telBox.className = "v2-telemetry-block";
            var thead = document.createElement("div");
            thead.className = "v2-req-tel-head";
            thead.textContent = "Request telemetry";
            telBox.appendChild(thead);
            present.forEach(function (t) {
                telBox.appendChild(fieldRow(t[1], numSpan(tel[t[0]])));
            });
            body.appendChild(telBox);
        }

        // Optional raw record (compact JSON), same debugging aid as failures.
        var rawToggle = document.createElement("button");
        rawToggle.type = "button";
        rawToggle.className = "v2-mini-btn v2-raw-toggle";
        rawToggle.textContent = "Raw details \u25b8";
        rawToggle.setAttribute("aria-expanded", "false");
        var rawEl = document.createElement("pre");
        rawEl.className = "v2-raw v2-hidden";
        try { rawEl.textContent = JSON.stringify(r, null, 2); }
        catch (e) { rawEl.textContent = String(r); }
        rawToggle.addEventListener("click", function () {
            var open = rawToggle.getAttribute("aria-expanded") === "true";
            rawToggle.setAttribute("aria-expanded", String(!open));
            rawToggle.textContent = open ? "Raw details \u25b8" : "Raw details \u25be";
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
                renderRequests(body.suites || [], body.successes || []);

                wireInteractions(runId);

                handleInitialHash();
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

        // ---- Act 8 toolbar actions --------------------------------------
        var expandAll = by("v2-expand-all");
        var collapseAll = by("v2-collapse-all");
        if (expandAll) {
            expandAll.addEventListener("click", function () {
                getVisibleFailures().forEach(function (f) { EXPANDED.add(f.locator); });
                refreshGroups();
            });
        }
        if (collapseAll) {
            collapseAll.addEventListener("click", function () {
                EXPANDED.clear();
                refreshGroups();
            });
        }
        var search = by("v2-search");
        if (search) {
            search.addEventListener("input", function () {
                clearTimeout(SEARCH_TIMER);
                var self = this;
                SEARCH_TIMER = setTimeout(function () {
                    FILTER.query = self.value;
                    refreshGroups();
                }, 80);
            });
        }
        wireKeyboard();
    }

    // Sync toolbar controls (type select + search box) to the current FILTER state.
    function syncToolbarToState() {
        var sel = by("v2-type-filter");
        if (sel) sel.value = FILTER.type;
        var search = by("v2-search");
        if (search) search.value = FILTER.query;
        applyFilterUI();
    }

    // Initial hash navigation: when the page loads with a valid diagnostic fragment,
    // reset filters so the target is visible, expand it, scroll into view + highlight.
    function handleInitialHash() {
        var hash = location.hash;
        if (!hash || hash.length <= 1) return;
        var id = hash.slice(1);
        var locator = "";
        var isRequest = false;
        (DATA.failures || []).forEach(function (f) {
            if (makeRowId(f.locator) === id) locator = f.locator;
        });
        if (!locator) {
            // Stable request-row anchor: #req-...
            (DATA.successes || []).forEach(function (r) {
                if (makeReqId(r.locator) === id) { locator = r.locator; isRequest = true; }
            });
        }
        if (!locator) return; // invalid fragment — ignore cleanly

        if (isRequest) {
            // Reveal the collapsed section, expand + scroll to the target row.
            var toggle = by("v2-request-toggle");
            if (toggle && !REQUEST_REVEALED) {
                REQUEST_REVEALED = true;
                toggle.setAttribute("aria-expanded", "true");
                toggle.textContent = "Hide request details";
                show(by("v2-request-list"));
            }
            EXPANDED.add(locator);
            var relEl = by(id);
            if (relEl) {
                relEl.classList.add("v2-row-flash");
                setTimeout(function () { relEl.classList.remove("v2-row-flash"); }, 1800);
                relEl.scrollIntoView({ behavior: "smooth", block: "center" });
            }
            return;
        }

        FILTER.suite = "";
        FILTER.type = "";
        FILTER.query = "";
        syncToolbarToState();
        EXPANDED.add(locator);
        refreshGroups();

        var el = by(id);
        if (el) {
            el.classList.add("v2-row-flash");
            setTimeout(function () { el.classList.remove("v2-row-flash"); }, 1800);
            el.scrollIntoView({ behavior: "smooth", block: "center" });
        }
    }

    // ---- Keyboard navigation (Act 8) ----------------------------------
    function isInsideFormControl() {
        var ae = document.activeElement;
        if (!ae) return false;
        var tag = ae.tagName ? ae.tagName.toUpperCase() : "";
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return false;
        return !!ae.isContentEditable;
    }

    function visibleToggles() {
        var ul = by("v2-failure-groups");
        if (!ul) return [];
        return Array.prototype.slice.call(ul.querySelectorAll(".v2-row-toggle"));
    }

    // j/k move focus among currently-visible row toggles, wrapping around.
    function focusInToggles(delta) {
        var rows = visibleToggles();
        if (!rows.length) return;
        var idx = rows.indexOf(document.activeElement);
        if (idx === -1) idx = delta > 0 ? -1 : rows.length;
        var n = rows.length;
        var ni = ((idx + delta) % n + n) % n;
        rows[ni].focus();
    }

    function toggleFocusedRow() {
        var focused = visibleToggles().filter(function (r) { return r === document.activeElement; })[0];
        if (focused) focused.click();
    }

    function wireKeyboard() {
        document.addEventListener("keydown", function (e) {
            if (isInsideFormControl()) return;             // don't hijack typing
            if (e.ctrlKey || e.altKey || e.metaKey) return; // never hijack browser shortcuts
            var key = e.key.length === 1 ? e.key.toLowerCase() : "";
            if (key === "j") { e.preventDefault(); focusInToggles(1); }
            else if (key === "k") { e.preventDefault(); focusInToggles(-1); }
            else if (key === "e" || e.key === "Enter") { e.preventDefault(); toggleFocusedRow(); }
        });
    }

    init();
})();
