# Results UX — Discovery & Gap Analysis

**Phase:** Results UX Redesign (Discovery) · **Owner surface:** results presentation
**Branch:** `feat/results-ux` · Foundation commit: `e4cf075`
**Status:** planning artifact — no files in `bench_llm/` modified

> This document diagnoses the *current* live Results experience and maps it against the
> canonical **results data contract §12** (the presentation surface the contract guarantees).
> It is evidence from code read in this worktree, not assumption. Every claim cites a file or
> behaviour. Recommendations live in `docs/results-ux-design-system.md` and
> `docs/results-ux-implementation-plan.md`.

---

## 1. Scope of "the Results experience"

The Results experience is everything a user sees after a benchmark finishes:

| Surface | Route | Owner(s) | Delivered by |
|---------|-------|----------|--------------|
| Unified Results shell (Overall / Speed / Workflow / Context / Compare / Intelligence tabs) | `/results` | `static/results.html`, `static/results-shell.js`, `static/results.js`, `static/results-compare.js` | RM-26-AA-0016, -0017, -0013 |
| Standard Speed detail | `/speed/results/{run_id}` | `static/speed-result.*` (legacy) + `results.js` modal | RM-26-AA-0017 |
| Workflow / Agentic evidence | `/v2/results/{run_id}` | `static/v2-result.html/js/css` | RM-26-AA-0017 |
| Context degradation | `/context/results/{run_id}` | `static/context-result.*` | RM-26-AA-0018 |
| Persistent sidebar navigation | injected by `static/app-shell.js` | — | RM-26-AA-0016/0013 |

Authoritative data always comes from read-only read models: `build_ranking` (`src/ranking_read_model.py` → `/api/ranking`), `load_speed_run_by_id`, `load_v2_result`. The frontend **formats, sorts, filters and drills down only** — it never scores or recomputes (contract §0.3, §12).

---

## 2. Problem statement

> The Results experience currently reads like an **inspector over a read model**, not a
> **coherent benchmark product**.

Concretely, the surface exposes *how the data is stored* more than *what it means*:

- Raw identity material is shown as product signal: 12-character configuration fingerprints
  (`c.configuration_fingerprint.slice(0,12)` in `results-shell.js` `renderL0Models` /
  `renderWorkflowRunRow`), status enums rendered verbatim (`"incomplete"`, `"unknown"`,
  `"unavailable"` chips), and a prominent **NO COMPOSITE** banner on the Overall view.
- The dominant empty state is *absence* ("No composite", "Composite score unavailable",
  Context shown as "run a benchmark or open a run"), so the landing tab (Overall) leads with
  what it *cannot* do rather than what it *can* show.
- Three different code owners render three different patterns for the same job (Speed → table +
  `<dialog>` modal in `results.js`; Overall/Workflow → collapsible `<details>` list in
  `results-shell.js`; Context → a separate full page with its own canvas curve). There is no
  single, consistent "how we show a result" language yet — only a shared shell *primitive* set.

This is not a correctness problem. Scores, eligibility, provenance and N/A-≠-0 are all correct.
It is a **product-coherence and legibility** problem: the same evidence could read as a ranked,
evidence-backed benchmark report instead of a data-table dump with status chips.

---

## 3. Evidence-based gap analysis

Gaps are ordered by impact on the "product vs viewer" perception. Each cites what was observed.

### GAP-1 — The Overall landing view leads with unavailability
**Observed.** `results-shell.js` `renderOverall()` emits a `NO COMPOSITE` badge and an
`unavailable` banner whose text is *"Composite score … is not yet approved; no Overall Solo Bench
score is computed or inferred."* Per-dimension availability then renders each dimension as either
"✓ Available" or "○ — <reason>" (e.g. a hollow circle + reason text). Intelligence carries a
"Research" chip; Context exists only as prose saying results open on a run's detail page.

**Why it reads like a viewer.** The tab that loads first and gets the most screen real estate
tells the user "there is no headline number here." A product leads with the strongest *available*
signal (per-dimension readiness, what each benchmark family does, where to go next) and treats the
absent composite as a footnote — not the heading.

**Contract alignment.** Fully compliant (§0.3: no composite invented; §7: Intelligence absent until
a contract exists). The gap is *framing*, not correctness.

---

### GAP-2 — Read-model internals leak into product language
**Observed.**
- Config fingerprints (`…slice(0,12)`) appear as first-class cells in the Overall model group and
  every Workflow run row (`results-shell.js`).
- Raw status enums are shown as chips: `status === "completed" ? ok : (unknown ? unavailable : warn)`
  — so a user sees a literal `"incomplete"` / `"unknown"` token.
- The compare identity header renders family states from the read-model vocabulary verbatim:
  *available / ambiguous / missing / in_progress / failed / unsupported / unavailable*
  (`results-compare.js`), and Speed projections carry a presentation-only **LEGACY** state.

**Why it reads like a viewer.** Enum values are an internal state machine, not user language. A
user does not know what "in_progress" or "ambiguous" means; they know "running", "this config can't
 be compared to that one", "not measured". The contract §5.1 already defines an authoritative status
vocabulary — the UI maps *some* of it (Speed/Context) but leaves Workflow/Overall/Compare exposing
the raw enum in places.

**Contract alignment.** Compliant (§4/§5.1: states are authoritative; the UI must not invent them).
The fix is a **human-language mapping layer**, not changing the states.

---

### GAP-3 — Three code owners, three interaction patterns for "show a result"
**Observed.**
- Speed (`results.js`): summary table + average-throughput bar (0–500 tok/s fixed scale) + native
  `<dialog>` modal per run (`renderModal`, `modalRow`). Filters live in the view panel.
- Overall/Workflow (`results-shell.js`): single collapsible `<details>` list, drill-down inline,
  filters for Workflow only.
- Context (`context-result.*`, separate page): its own identity block, canvas curve, metric cards,
  coverage rollup, per-point evidence column — a bespoke layout not reused anywhere.
- Workflow detail is a *separate route* (`/v2/results/{id}`) while Speed detail is an in-page modal
  and Context detail is another separate route.

**Why it reads like a viewer.** "How do I see more about this run?" answers differ by tab: click a
modal, expand inline, or navigate to another page. A product uses one consistent disclosure model
per result type so the behaviour is learnable once.

**Contract alignment.** Compliant (contract §12 lists the surfaces; it does not mandate a single
widget). The gap is consistency/learnability.

---

### GAP-4 — Status is partly colour-coded and partly textual, inconsistently
**Observed.** `results-shell.js` uses `rs-badge-ok` / `rs-badge-unavailable` / `rs-badge-warn` and
the shell palette (`--rs-ok:#34d399`, `--rs-warn:#fbbf24`, `--rs-error:#f87171`,
`--rs-unavailable:#6b7f99`). The Workflow row code *does* guard against colour-only signal
("colour is never the sole signal — the word is always shown too"), but the Overall dimension cards
render "○ — <reason>" where the hollow circle carries meaning that only becomes text on hover/
focus, and the unavailable-dimension chip is a muted grey with no label beyond the reason string.

**Why it reads like a viewer.** Mixed colour/text treatment makes validity state feel decorative in
some places and authoritative in others. The contract §5.1 requires each status to be distinctly
rendered; accessibility (§008 acceptance for 0018) requires meaning not dependent on colour alone.

**Contract alignment.** Mostly compliant; the inconsistency is the risk, not a violation.

---

### GAP-5 — Empty / no-result states are functional but non-guiding
**Observed.** Speed empty state: "There are no Standard Speed benchmark results to display. Run a
benchmark from the main page" + a **Run Benchmark** button (`results.js` `showEmpty`). Workflow:
"No workflow results yet. Run a Workflow suite …". Context: dense explanatory paragraph pointing at
other routes. Overall: unavailable banner (GAP-1).

**Why it reads like a viewer.** The states are correct and actionable, but they read as system
messages ("no rows") rather than product onboarding. A product uses empty states to teach what the
benchmark *is* and guide the first run, not just report an empty table.

**Contract alignment.** Compliant (§0.2: unsuccessful evidence hidden behind a toggle; here it is
"no runs yet"). Gap is tone/guidance.

---

### GAP-6 — Evidence quality / "why this number" narrative is thin
**Observed.** Speed rows show generation tok/s per point and an average bar but little about *evidence
confidence*: how many eligible points fed the average, whether points are current-metric vs LEGACY,
cold-vs-warm repeatability provenance. Workflow rows show "X / Y checks" and a percentage plus suite
chips — good — but the connection between a run's validity (`partial`/`completed`) and the trustworthiness
of its fraction is not narrated. Context detail is actually strong here (coverage rollup: supported /
unsupported / missing / invalid / failed) but that pattern is **not reused** elsewhere.

**Why it reads like a viewer.** A benchmark product answers "how much do I trust this?" The contract
§6 (eligibility) and §5.4 (partial evidence preservation) give us the data; the UI surfaces scores
without always surfacing *why* a score is or isn't trustworthy. Context's coverage rollup is the
template to generalise.

**Contract alignment.** Compliant (§6 eligibility preserved). Gap is narrative depth + reuse of the
Context coverage pattern.

---

### GAP-7 — Inconsistent "unsuccessful / diagnostic" handling across views
**Observed.** The contract §0.2/§8 requires partial/failed/interrupted/diagnostic runs hidden behind
an explicit *Show unsuccessful* toggle, and diagnostic artifacts carrying a visible
`DIAGNOSTIC RUN — NOT SCORED` marker. Workflow does surface incomplete/diagnostic runs with an
"incomplete run — inspectable diagnostic evidence; no approved composite score" note (`results-shell.js`
`buildWorkflowEntries`), but Speed's summary table *excludes* legacy metric runs entirely (per roadmap)
and there is no unified unsuccessful-run toggle on the Results shell. Context has its own coverage-based
invalid/failed handling.

**Why it reads like a viewer.** Whether an unsuccessful run is hidden, shown, or excluded depends on
which tab you are in. A product applies one consistent rule (contract §0.2) across all views.

**Contract alignment.** Partially aligned: Workflow is compliant; the unified toggle across the shell
is not yet universal. This is a real contract-consistency gap, not only perception.

---

## 4. What is already correct (do not "fix")

Protect these while redesigning — they are the foundation this phase must not disturb:

- **N/A ≠ 0.** Missing values render as em-dash (`na()` in `results-shell.js`), never coerced
  (contract §0.1, §12). This is a hard invariant; the redesign must preserve it exactly.
- **Frontend formats only.** No client-side scoring/composite/dimension recomputation anywhere
  (`results-shell.js` header comment, roadmap 0016/0017 evidence). Preserve.
- **Authoritative identity.** Model/config keyed by `model_family`+`model_version` and configuration
  fingerprint, never display name (contract §2/§3; comparison read model). Preserve.
- **Composite deliberately unavailable.** Overall correctly shows "no composite" because the scoring
  contract (RM-26-AA-0019) is not approved. This is *intentional*, not a bug to hide — see GAP-1:
  reframe, never invent.
- **Dark-only visual system.** "dark mode or nothing", no light/dark toggle (0016/0017/0018). Preserve.
- **Provenance on every byte.** Run IDs + benchmark/version identity exported per §9. Preserve.

---

## 5. Gap → theme summary

| ID | Theme | Type | Contract impact |
|----|-------|------|-----------------|
| GAP-1 | Overall leads with unavailability | framing / product-hero | none (compliant) |
| GAP-2 | Read-model internals as product language | language mapping | compliant; fix = mapping layer |
| GAP-3 | Three disclosure patterns | consistency | none |
| GAP-4 | Colour vs text on status | accessibility/consistency | mostly compliant |
| GAP-5 | Non-guiding empty states | tone | none |
| GAP-6 | Thin evidence-quality narrative | depth + reuse | none (Context is the template) |
| GAP-7 | Inconsistent unsuccessful/diagnostic handling | **consistency** | partial — unify per §0.2/§8 |

Themes 1–6 are perception/coherence; **GAP-7 is a genuine contract-consistency gap** and should be
resolved as part of the redesign, not deferred.

---

## 6. Constraints on any change in this phase

1. **No backend semantic changes.** Read models, scoring eligibility, provenance and schema are the
   foundation. This phase touches presentation only (`bench_llm/static/*` and, where strictly
   presentation, `results-shell.js`/`results.js` formatting). See implementation plan §Non-goals.
2. **N/A stays N/A.** Never coerce missing to zero; never invent a composite or winner.
3. **Authoritative states only.** Map contract §5.1 states to human language; never add or rename a
   state, never fabricate a termination reason.
4. **Dark-only, accessible, keyboard-reachable.** Extend the existing `rs-*` palette and
   `:focus-visible` discipline; do not introduce a light theme.
5. **Ship in slices.** Each phase is independently shippable and testable (fast gate + browser
   acceptance), mirroring how 0016/0017/0018 closed.

---

*Next: `docs/results-ux-design-system.md` (the "how it should look and behave") then
`docs/results-ux-implementation-plan.md` (the "how we get there in slices").*
