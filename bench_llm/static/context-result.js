/** Solo Dev LLM Bench — Context result page (RM-26-AA-0018).
 *
 * Single-run Context degradation detail. Consumes the authoritative, read-only
 * Context read model at GET /api/context/runs/{run_id} and formats/presents it:
 * run identity, validity/state, supported/gap range, baseline, and the ordered
 * per-point retention/degradation signal. This layer only reads and renders --
 * it never recomputes scores or degradation, and N/A is never coerced to zero.
 */
(function () {
    "use strict";

    // --- small DOM helpers -------------------------------------------------
    function esc(text) {
        return typeof escapeHtml === "function" ? escapeHtml(String(text)) : String(text);
    }

    // Missing / NaN -> em-dash, never zero. N/A stays distinct from a real value.
    function na(value) {
        if (value === null || value === undefined || value === "") { return "\u2014"; }
        var num = Number(value);
        if (typeof num !== "number" || isNaN(num)) { return "\u2014"; }
        return num;
    }

    function el(tag, className, html) {
        var node = document.createElement(tag);
        if (className) { node.className = className; }
        if (html != null) { node.innerHTML = html; }
        return node;
    }

    // --- state fragments ---------------------------------------------------
    function hide() {
        var ids = ["cr-loading", "cr-error", "cr-identity", "cr-coverage", "cr-points", "cr-curve"];
        ids.forEach(function (id) {
            var node = document.getElementById(id);
            if (node) { node.classList.add("hidden"); }
        });
    }

    function show(ids) {
        ids.forEach(function (id) {
            var node = document.getElementById(id);
            if (node) { node.classList.remove("hidden"); }
        });
    }

    // --- per-point state -> badge class + label ----------------------------
    // Capability states (unsupported/gap) are distinct from operational failures,
    // and neither is ever rendered as a passing score. Colour is never the sole
    // signal: the word is always shown too.
    function stateMeta(status) {
        switch (status) {
            case "success":
                return { cls: "cr-badge-ok", label: "Success" };
            case "unsupported":
                return { cls: "cr-badge-unavailable", label: "Unsupported (above capacity)" };
            case "extraction_failure":
                return { cls: "cr-badge-warn", label: "Extraction failure" };
            case "malformed":
                return { cls: "cr-badge-warn", label: "Malformed answer" };
            case "failed":
                return { cls: "cr-badge-error", label: "Failed" };
            default:
                return { cls: "cr-badge-unavailable", label: String(status || "\u2014") };
        }
    }

    // --- render the authoritative per-point table --------------------------
    function renderPoints(points) {
        var body = document.getElementById("cr-body");
        if (!body) { return; }
        body.innerHTML = "";

        if (!Array.isArray(points) || points.length === 0) {
            var empty = el("tr", "", "<td colspan=\"8\" class=\"cr-empty\">No context points were gradeable for this run.</td>");
            body.appendChild(empty);
            return;
        }

        // Ordered ascending by requested tokens (the read model already sorts, but
        // re-sort defensively so the curve/table is always monotonic in x).
        var ordered = points.slice().sort(function (a, b) {
            return na(a.requested_context_tokens) - na(b.requested_context_tokens);
        });

        ordered.forEach(function (p) {
            var meta = stateMeta(p.status);
            var scoreText = (p.score === null || p.score === undefined)
                ? "<span class='cr-na'>\u2014</span>"
                : na(p.score).toFixed(3);
            var retentionText = (p.retention_relative_to_baseline === null || p.retention_relative_to_baseline === undefined)
                ? "<span class='cr-na'>\u2014</span>"
                : Math.round(na(p.retention_relative_to_baseline) * 100) + "%";
            var degrText = (p.degradation_from_baseline === null || p.degradation_from_baseline === undefined)
                ? "<span class='cr-na'>\u2014</span>"
                : na(p.degradation_from_baseline).toFixed(3);
            var actualText = (p.actual_context_tokens === null || p.actual_context_tokens === undefined)
                ? "<span class='cr-na'>\u2014</span>"
                : na(p.actual_context_tokens).toLocaleString();
            var factsText = ((p.facts_correct === null || p.facts_correct === undefined) ? "\u2014" : na(p.facts_correct)) + " / " +
                ((p.facts_requested === null || p.facts_requested === undefined) ? "\u2014" : na(p.facts_requested));

            var row = el("tr", "cr-point-row");
            row.appendChild(el("td", "", esc(p.context_point)));
            row.appendChild(el("td", "", String(na(p.requested_context_tokens)).replace(/\B(?=(\d{3})+(?!\d))/g, ",")));
            row.appendChild(el("td", "", actualText));
            row.appendChild(el("td", "", scoreText));
            row.appendChild(el("td", "", factsText));
            row.appendChild(el("td", "", retentionText));
            row.appendChild(el("td", {}, degrText));
            var tdState = el("td", "");
            tdState.appendChild(el("span", "badge " + meta.cls, esc(meta.label)));
            if (p.failure_reason && String(p.failure_reason).trim()) {
                var reason = el("div", "cr-reason", esc(String(p.failure_reason)));
                reason.setAttribute("title", esc(String(p.failure_reason)));
                tdState.appendChild(reason);
            }
            row.appendChild(tdState);

            // L2 evidence drill-down (ST-007): native <details> is semantic and
            // keyboard-operable without JS. Facts are rendered verbatim from the
            // authoritative read-model evidence -- never re-derived or grouped by a
            // frontend rule that could conflict with it. Colour marks pass/fail
            // only; the word label + symbol are always present too.
            row.appendChild(buildEvidenceCell(p));

            body.appendChild(row);
        });
    }

    // Presentation-only summary of authoritative per-point states. No numeric
    // scoring or composite: it only relabels what the read model already exposes.
    function overallStateMeta(points) {
        var hasSuccess = false, hasFailed = false, any = false;
        (points || []).forEach(function (p) {
            any = true;
            if (p.status === "success") { hasSuccess = true; }
            else if (p.status === "failed" || p.status === "extraction_failure" || p.status === "malformed") { hasFailed = true; }
        });
        if (!any) { return { cls: "cr-badge-unavailable", label: "No gradeable points" }; }
        if (hasSuccess) { return { cls: "cr-badge-ok", label: "Completed" }; }
        return { cls: "cr-badge-error", label: hasFailed ? "Failed" : "Unsupported" };
    }

    // --- degradation / retention curve (ST-004) ----------------------------
    // Builds a dependency-free inline SVG from AUTHORITATIVE supported points
    // only. Supported = status "success" with a real score + retention.
    // N/A/unsupported/gap points have no gradeable value, so they are never
    // plotted and never coerced to zero: they lie beyond the effective-capacity
    // boundary, which is drawn as a dashed line rather than as a data marker.
    function buildCurve(data) {
        var container = document.getElementById("cr-curve-inner");
        if (!container) { return; }

        // Supported points: real score + retention, with an actual context size.
        var supported = (data.points || [])
            .filter(function (p) { return p.status === "success" && p.retention_relative_to_baseline != null && p.actual_context_tokens > 0; })
            .slice()
            .sort(function (a, b) { return a.actual_context_tokens - b.actual_context_tokens; });

        // Need at least two supported points to draw a degradation curve.
        if (supported.length < 2) {
            container.innerHTML =
                "<div class='cr-curve-empty'>At least two supported context points are needed to draw the degradation curve. " +
                esc(String(supported.length)) + " supported point(s) recorded for this run.</div>";
            return;
        }

        var modelLabel = (data.model_display_name && data.model_display_name.trim()) || esc(data.model_key) || "this run";

        // X domain: smallest actual context .. effective capacity (or largest requested).
        var xMin = supported[0].actual_context_tokens;
        var cap = data.effective_capacity != null ? Number(data.effective_capacity) : null;
        var maxRequested = 0;
        (data.points || []).forEach(function (p) { if (Number(p.requested_context_tokens) > maxRequested) { maxRequested = Number(p.requested_context_tokens); } });
        var xMax = cap && cap > xMin ? cap : Math.max(maxRequested, supported[supported.length - 1].actual_context_tokens);

        // Y domain: retention in [0, up to the largest observed (allow headroom above 1)].
        var yMax = 1;
        supported.forEach(function (p) { if (Number(p.retention_relative_to_baseline) > yMax) { yMax = Number(p.retention_relative_to_baseline); } });
        yMax = yMax * 1.05;

        var W = 920, H = 360;
        var m = { l: 58, r: 28, t: 26, b: 46 };
        var pw = W - m.l - m.r, ph = H - m.t - m.b;
        function sx(x) { return m.l + (x - xMin) / (xMax - xMin) * pw; }
        function sy(y) { return m.t + ph - (y - 0) / (yMax - 0) * ph; }

        var parts = [];
        parts.push('<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' +
            esc("Degradation retention curve for " + modelLabel + ", supported across " + supported.length + " context points") +
            '" xmlns="http://www.w3.org/2000/svg">');
        parts.push('<style>' +
            '.cr-curve .axis { stroke: #2a4165; stroke-width: 1; }' +
            '.cr-curve .grid { stroke: #17263d; stroke-width: 1; }' +
            '.cr-curve .tick-label { fill: #7f93af; font-size: 11px; font-family: ui-monospace, monospace; }' +
            '.cr-curve .axis-label { fill: #9fb0c8; font-size: 12px; font-family: system-ui, sans-serif; }' +
            '.cr-curve .baseline { stroke: #5b7396; stroke-width: 1; stroke-dasharray: 4 4; }' +
            '.cr-curve .capacity { stroke: #e6c56b; stroke-width: 1; stroke-dasharray: 6 4; }' +
            '.cr-curve .curve { fill: none; stroke: #8ee0a8; stroke-width: 2.5; stroke-linejoin: round; stroke-linecap: round; }' +
            '.cr-curve .dot { fill: #0c1626; stroke: #8ee0a8; stroke-width: 2; }' +
            '.cr-curve .baseline-dot { fill: #e6c56b; stroke: #0c1626; stroke-width: 2; }' +
            '</style>');

        // Axes.
        parts.push('<line class="axis" x1="' + m.l + '" y1="' + (m.t + ph) + '" x2="' + (m.l + pw) + '" y2="' + (m.t + ph) + '">');
        parts.push('<line class="axis" x1="' + m.l + '" y1="' + m.t + '" x2="' + m.l + '" y2="' + (m.t + ph) + '">');

        // Horizontal gridlines + retention ticks (0%, 25%, ... up to yMax).
        var steps = [0, 0.25, 0.5, 0.75, 1.0];
        if (yMax > 1.05) { steps.push(yMax); }
        steps.forEach(function (t) {
            if (t < 0 || t > yMax) { return; }
            var yy = sy(t);
            parts.push('<line class="grid" x1="' + m.l + '" y1="' + yy + '" x2="' + (m.l + pw) + '" y2="' + yy + '">');
            parts.push('<text class="tick-label" x="' + (m.l - 8) + '" y="' + (yy + 3) + '" text-anchor="end">' + Math.round(t * 100) + '%</text>');
        });

        // Baseline reference at 100% retention.
        if (sy(1.0) >= m.t && sy(1.0) <= m.t + ph) {
            parts.push('<line class="baseline" x1="' + m.l + '" y1="' + sy(1.0) + '" x2="' + (m.l + pw) + '" y2="' + sy(1.0) + '">');
        }

        // Capacity boundary at effective_capacity (supported ends here; gap lies beyond).
        if (cap && cap >= xMin && cap <= xMax) {
            var cx = sx(cap);
            parts.push('<line class="capacity" x1="' + cx + '" y1="' + m.t + '" x2="' + cx + '" y2="' + (m.t + ph) + '">');
            parts.push('<text class="axis-label" x="' + (cx - 6) + '" y="' + (m.t - 8) + '" text-anchor="end">effective capacity</text>');
        }

        // Degradation/retention polyline through supported points.
        var pts = supported.map(function (p) {
            return sx(p.actual_context_tokens).toFixed(1) + "," + sy(p.retention_relative_to_baseline).toFixed(1);
        });
        parts.push('<polyline class="curve" points="' + pts.join(" ") + '">');

        // X-axis labels: one per supported point (context_point + tokens).
        supported.forEach(function (p) {
            var px = sx(p.actual_context_tokens);
            var isBaseline = p.baseline === true;
            parts.push('<circle class="' + (isBaseline ? "baseline-dot" : "dot") + '" cx="' + px.toFixed(1) + '" cy="' + sy(p.retention_relative_to_baseline).toFixed(1) + '" r="' + (isBaseline ? 5 : 3.5) + '">');
            var label = esc(String(p.context_point)) + "\n" + String(Math.round(p.actual_context_tokens)).toLocaleString() + " tok";
            var lab = label.replace(/\n/g, "\\n");
            parts.push('<text class="tick-label" x="' + px.toFixed(1) + '" y="' + (m.t + ph + 16) + '" text-anchor="middle">' + esc(String(p.context_point)) + '</text>');
            parts.push('<text class="tick-label" x="' + px.toFixed(1) + '" y="' + (m.t + ph + 30) + '" text-anchor="middle" style="font-size:10px;fill:#5f7390">' + String(Math.round(p.actual_context_tokens)).toLocaleString() + '</text>');
            if (isBaseline) {
                parts.push('<text class="axis-label" x="' + px.toFixed(1) + '" y="' + (sy(p.retention_relative_to_baseline) - 9) + '" text-anchor="middle">baseline</text>');
            }
        });

        // X-axis title.
        parts.push('<text class="axis-label" x="' + (m.l + pw / 2) + '" y="' + (H - 6) + '" text-anchor="middle">Context size (actual input tokens)</text>');
        // Y-axis title (rotated).
        parts.push('<text class="axis-label" x="' + 14 + '" y="' + (m.t + ph / 2) + '" text-anchor="middle" transform="rotate(-90 14 ' + (m.t + ph / 2) + '">Retention vs baseline</text>');

        parts.push('</svg>');
        container.innerHTML = parts.join("");
    }

    // --- key metrics (ST-005) ----------------------------------------------
    // Presents baseline / retention / degradation as authoritative per-point
    // values read straight from the read model -- never a recomputed score or
    // composite. Direction colour conveys sign only; the signed value is always
    // shown too, so colour is never the sole signal.
    function renderMetrics(data) {
        var body = document.getElementById("cr-metrics-body");
        if (!body) { return; }
        var points = data.points || [];
        var supported = points.filter(function (p) { return p.status === "success"; });

        // Baseline point (authoritative flag), else the earliest supported point.
        var baseline = points.filter(function (p) { return p.baseline === true; })[0];
        if (!baseline && supported.length) { baseline = supported.slice().sort(function (a, b) { return a.requested_context_tokens - b.requested_context_tokens; })[0]; }

        // Latest supported point (largest actual context) for retention/degradation.
        var latest = supported.slice().sort(function (a, b) { return b.actual_context_tokens - a.actual_context_tokens; })[0];
        var latestSubHint;

        function metricCard(label, value, sub, dirClass) {
            var valCls = "cr-metric-value" + (dirClass ? " " + dirClass : "");
            return '<div class="cr-metric">' +
                "<div class='cr-metric-label'>" + esc(label) + "</div>" +
                "<div class='" + valCls + "'>" + value + "</div>" +
                (sub ? "<div class='cr-metric-sub'>" + sub + "</div>" : "") +
                "</div>";
        }

        // 1) Baseline reference.
        var baselineVal, baselineSub;
        if (baseline) {
            baselineVal = esc(String(baseline.context_point));
            var bRet = baseline.retention_relative_to_baseline != null ? Math.round(na(baseline.retention_relative_to_baseline) * 100) + "%" : "\u2014";
            baselineSub = "Baseline retention " + bRet;
        } else {
            baselineVal = "\u2014";
            baselineSub = "No baseline point recorded";
        }

        // 2) Latest supported retention.
        var latestRet, latestRetDir;
        if (latest && latest.retention_relative_to_baseline != null) {
            var pct = Math.round(na(latest.retention_relative_to_baseline) * 100);
            latestRet = pct + "%";
            latestRetDir = pct >= 100 ? "cr-dir-up" : (pct >= 90 ? "cr-dir-flat" : "cr-dir-down");
            latestSubHint = "at " + esc(String(latest.context_point));
        } else {
            latestRet = "\u2014";
            latestRetDir = "";
            latestSubHint = supported.length ? "No supported point beyond baseline" : "No supported points";
        }

        // 3) Degradation from baseline (signed, authoritative).
        var degrVal, degrDir, degrSub;
        if (latest && latest.degradation_from_baseline != null) {
            var d = na(latest.degradation_from_baseline);
            degrVal = (d > 0 ? "+" : "") + d.toFixed(3);
            degrDir = d > 0.0005 ? "cr-dir-up" : (d < -0.0005 ? "cr-dir-down" : "cr-dir-flat");
            degrSub = latest.retention_relative_to_baseline != null ? "from baseline retention" : "score vs baseline";
        } else {
            degrVal = "\u2014";
            degrDir = "";
            degrSub = "Degradation not recorded";
        }

        body.innerHTML =
            metricCard("Baseline", baselineVal, baselineSub) +
            metricCard("Latest retention", latestRet, latestSubHint || "", latestRetDir) +
            metricCard("Degradation from baseline", degrVal, degrSub, degrDir);
    }

    // --- point coverage rollup (ST-006) ------------------------------------
    // Categorises Context points into AUTHORITATIVE states so the categories stay
    // distinct at a glance: supported / unsupported(capacity) / missing(no run
    // record) / invalid(unusable answer) / failed(operational). This is pure
    // presentation aggregation of per-point status + context_points_contract --
    // no scoring, no recomputation, nothing coerced to zero. Unknown statuses are
    // bucketed separately (and only shown when present) so the rollup never drops
    // or mislabels a point.
    function computeCoverage(data) {
        var points = data.points || [];
        var supported = 0, unsupported = 0, invalid = 0, failed = 0, other = 0;
        var byRequested = {};
        var unknownStatuses = {};
        points.forEach(function (p) {
            byRequested[p.requested_context_tokens] = true;
            switch (p.status) {
                case "success": supported++; break;
                case "unsupported": unsupported++; break;
                case "failed": failed++; break;
                case "extraction_failure":
                case "malformed": invalid++; break;
                default:
                    other++;
                    if (p.status != null) { unknownStatuses[String(p.status)] = true; }
                    break;
            }
        });

        // Missing = contract points with no run record at all. Distinct from
        // unsupported, which is a capacity limit on a point that WAS attempted.
        var contract = Array.isArray(data.context_points_contract) ? data.context_points_contract : [];
        var missing = 0;
        contract.forEach(function (req) {
            if (!byRequested[req]) { missing++; }
        });

        return {
            supported: supported,
            unsupported: unsupported,
            missing: missing,
            invalid: invalid,
            failed: failed,
            other: other,
            unknownStatuses: Object.keys(unknownStatuses),
            contractTotal: contract.length
        };
    }

    function renderCoverage(data) {
        var body = document.getElementById("cr-coverage-body");
        if (!body) { return; }
        var c = computeCoverage(data);

        function cell(cls, count, label, hint) {
            return '<div class="cr-cov-cell ' + cls + '">' +
                "<span class='cr-cov-count'>" + count + "</span>" +
                "<span class='cr-cov-label'>" + esc(label) + "</span>" +
                (hint ? "<span class='cr-cov-hint'>" + esc(hint) + "</span>" : "") +
                "</div>";
        }

        var cells =
            cell("cr-cov-supported", c.supported, "Supported", "Gradeable") +
            cell("cr-cov-unsupported", c.unsupported, "Unsupported", "Above capacity") +
            cell("cr-cov-missing", c.missing, "Missing", c.contractTotal ? "No run record" : "") +
            cell("cr-cov-invalid", c.invalid, "Invalid", "Unusable answer") +
            cell("cr-cov-failed", c.failed, "Failed", "Operational");

        if (c.other > 0) {
            cells += cell("cr-cov-other", c.other, "Other", c.unknownStatuses.join(", "));
        }
        if (!c.contractTotal) {
            cells += "<p class='cr-note'>Contract points unknown; coverage derived from recorded points only.</p>";
        }

        body.innerHTML = "<div class='cr-coverage cr-cov-compact'>" + cells + "</div>";
    }

    // --- L2 evidence drill-down (ST-007) -----------------------------------
    // Traces a measured Context point back to its authoritative per-fact evidence.
    // Renders point.evidence verbatim; empty evidence surfaces honestly as "No
    // per-fact evidence recorded" rather than inventing results. Telemetry is
    // shown only when present, also verbatim.
    function buildEvidenceCell(point) {
        var ev = Array.isArray(point.evidence) ? point.evidence : [];
        var passedCount = 0;
        ev.forEach(function (f) { if (f && f.passed) { passedCount++; } });

        var summaryText = ev.length
            ? "Evidence (— " + String(ev.length) + " facts, " + String(passedCount) + " pass)"
            : "No evidence";

        var inner;
        if (!ev.length) {
            inner = "<p class='cr-ev-empty'>No per-fact evidence recorded for this point.</p>";
        } else {
            var parts = [];
            parts.push('<table class="cr-ev-table" aria-label="Authoritative evidence for ' + esc(String(point.context_point)) + '">');
            parts.push('<thead><tr><th scope="col">Fact</th><th scope="col">Expected</th><th scope="col">Actual</th><th scope="col">Result</th></tr></thead>');
            parts.push('<tbody>');
            ev.forEach(function (f) {
                var passed = !!(f && f.passed);
                var resultCls = passed ? "cr-ev-pass" : "cr-ev-fail";
                var actualText = (f.actual == null || f.actual === "") ? "<span class='cr-na'>\u2014</span>" : esc(String(f.actual));
                parts.push('<tr>');
                var label = resultLabelFor(passed);
                parts.push("<td>" + esc(String(f.key)) + "</td>");
                parts.push("<td>" + esc(String(f.expected)) + "</td>");
                parts.push("<td>" + actualText + "</td>");
                parts.push("<td class='cr-ev-result'><span class='" + resultCls + "' aria-hidden='true'>" + (passed ? "\u2713" : "\u2717") + "</span> " +
                    "<span class='cr-ev-resultlabel' role='img' aria-label='Result: " + esc(label) + "'>" + label + "</span></td>");
                parts.push("</tr>");
            });
            parts.push("</tbody></table>");

            // Telemetry (verbatim), only when present -- reinforces L2 traceability.
            var tel = point.telemetry && typeof point.telemetry === "object" ? point.telemetry : null;
            if (tel) {
                var keys = Object.keys(tel).filter(function (k) { return tel[k] != null; });
                if (keys.length) {
                    parts.push("<div class='cr-ev-tel'><span class='cr-ev-tel-label'>Telemetry</span>");
                    keys.forEach(function (k) {
                        parts.push("<div class='cr-ev-tel-item'><span class='cr-ev-tel-key'>" + esc(String(k)) + "</span>: " +
                            "<span class='cr-ev-tel-val'>" + esc(String(tel[k])) + "</span></div>");
                    });
                    parts.push("</div>");
                }
            }
            inner = parts.join("");
        }

        return el("td", "cr-ev-cell",
            "<details class='cr-evidence'><summary>" + esc(summaryText) + "</summary>" + inner + "</details>");
    }

    function resultLabelFor(passed) {
        return passed ? "Pass" : "Fail";
    }

    // --- render run identity + per-point data (available state) ------------
    function renderAvailable(data) {
        hide();

        // Identity.
        var model = document.getElementById("cr-model");
        if (model) { model.textContent = (data.model_display_name && data.model_display_name.trim()) || esc(data.model_key) || "\u2014"; }

        var version = document.getElementById("cr-version");
        if (version) { version.textContent = esc(data.model_key || "\u2014"); }
        // Architecture is not persisted in the Context artifact; resolve it from the
        // unified ranking foundation at UI time. Never invent it here.
        var arch = document.getElementById("cr-architecture");
        if (arch) { arch.textContent = "\u2014"; arch.setAttribute("title", "Architecture resolves via the unified ranking read model, not the Context artifact."); }

        var quant = document.getElementById("cr-quantization");
        if (quant) { quant.textContent = esc(data.model_quantization || "\u2014"); }

        var cls = document.getElementById("cr-classification");
        if (cls) { cls.textContent = esc(data.classification || "\u2014"); }

        var runId = document.getElementById("cr-run-id");
        if (runId) { runId.textContent = esc(data.run_id || "\u2014"); }

        var fp = document.getElementById("cr-fingerprint");
        if (fp) { fp.textContent = esc((data.configuration_fingerprint || "\u2014").slice(0, 24)); }

        // Overall status / validity badge (derived from authoritative per-point
        // states; never a recomputed score or composite).
        var status = document.getElementById("cr-status");
        if (status) {
            var sm = overallStateMeta(data.points);
            status.className = "badge " + sm.cls;
            status.textContent = esc(sm.label);
        }

        var baseline = document.getElementById("cr-baseline");
        if (baseline) { baseline.textContent = data.baseline_context_point != null ? esc(String(data.baseline_context_point)) : "\u2014"; }

        var capacity = document.getElementById("cr-capacity");
        if (capacity) { capacity.textContent = data.effective_capacity != null ? String(na(data.effective_capacity)).toLocaleString() + " tok" : "\u2014"; }

        var supported = document.getElementById("cr-supported");
        if (supported) { supported.textContent = String(data.supported_point_count != null ? data.supported_point_count : 0); }
        var gap = document.getElementById("cr-gap");
        if (gap) { gap.textContent = String(data.gap_point_count != null ? data.gap_point_count : 0); }

        // Per-point table.
        renderPoints(data.points);

        // Key metrics (ST-005): baseline / retention / degradation figures.
        renderMetrics(data);

        // Point coverage rollup (ST-006): supported / unsupported / missing /
        // invalid / failed -- kept distinct, derived authoritatively.
        renderCoverage(data);

        // Degradation / retention curve (ST-004).
        buildCurve(data);

        show(["cr-loading" /* keep hidden */, "cr-identity", "cr-coverage", "cr-points", "cr-metrics", "cr-curve"]);
        var loading = document.getElementById("cr-loading");
        if (loading) { loading.classList.add("hidden"); }
    }

    // --- not-found / error states ------------------------------------------
    function renderNotFound(detail) {
        hide();
        var err = document.getElementById("cr-error");
        var detailEl = document.getElementById("cr-error-detail");
        if (err) { err.classList.remove("hidden"); }
        if (detailEl) {
            detailEl.innerHTML =
                "<p>No Context run matches this link.</p>" +
                "<p>Run a Context benchmark from the <a href=\"/\">Benchmarks / Run</a> page; its " +
                "degradation/drift curve, baseline and retention open here at " +
                "<code>/context/results/{run_id}</code>.</p>";
        }
    }

    function renderError(detail) {
        hide();
        var err = document.getElementById("cr-error");
        var detailEl = document.getElementById("cr-error-detail");
        if (err) { err.classList.remove("hidden"); }
        if (detailEl) {
            detailEl.textContent = detail || "The Context read model could not be loaded. Try refreshing the page.";
        }
    }

    // --- bootstrap ---------------------------------------------------------
    function init() {
        var m = /\/context\/results\/([^/]+)\//.exec(location.pathname);
        if (!m) { m = /\/context\/results\/([^/]+)/.exec(location.pathname); }
        var runId = m ? decodeURIComponent(m[1]) : "";
        if (!runId) { renderError("No Context run id in the page URL."); return; }

        fetch("/api/context/runs/" + encodeURIComponent(runId), { credentials: "same-origin" })
            .then(function (r) {
                if (r.status === 404) { renderNotFound(); return null; }
                if (!r.ok) { throw new Error("HTTP " + r.status); }
                return r.json();
            })
            .then(function (data) {
                if (data) { renderAvailable(data); }
            })
            .catch(function () {
                // A 404 already rendered; only surface a generic error for other failures.
                if (document.getElementById("cr-error") && !document.getElementById("cr-error").classList.contains("hidden")) { return; }
                renderError();
            });
    }

    document.addEventListener("DOMContentLoaded", init);
})();
