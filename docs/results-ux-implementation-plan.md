# Results UX Redesign — Implementation Plan

**Phase:** Results UX Redesign (Implementation planning) · **Owner surface:** results presentation
**Branch:** `feat/results-ux` · Foundation commit: `e4cf075`
**Status:** planning artifact — no files in `bench_llm/` modified; nothing committed or pushed

> Phased, shippable plan for turning the Results experience into a coherent benchmark product. It
> consumes the discovery (`docs/results-ux-discovery.md`) and design system
> (`docs/results-ux-design-system.md`). Every phase maps to existing **RM-26-AA** subtasks or a new
> tracking item; nothing here changes backend semantics.

---

## Delivery status (execution update)

Surfaces are delivered in the order Overall → Speed → Workflow → Context → Compare. The table below
records live work on `feat/results-ux`; it is distinct from the Phase 0–7 proposal in §4, which remains
the uncreated RM-26-AA-0022 specification.

| Surface | Item | Status |
|---------|------|--------|
| Overall | P2 | delivered — product hero, per-dimension readiness |
| Speed | P3 | delivered — primary results experience |
| Workflow | P4 | delivered — trend/coverage + progressive disclosure |
| **Context** | **P5** | **BLOCKED** — aggregate Context enumeration/projection not exposed by the API (see below) |
| Compare | P6 | next in queue — uses the existing `/api/comparison` read model, presentation-only |

### BLOCKED — Context (P5): missing aggregate Context contract

Context is the one delivered surface that cannot yet render its primary experience. The per-run read
model exists (`GET /api/context/runs/{run_id}` → points, scores, degradation, retention, status), but
**no API enumerates or projects committed Context runs**, so the JS cannot build the identity → trend →
coverage → disclosure hierarchy P5 specifies.

Exact API gap (verified against `routes/ranking.py`, `ranking_read_model.py`, `routes/context.py`,
`v2_context_read_model.py`):

- `/api/ranking` exposes only Speed + Agentic (`available_dimensions = [speed, agentic]`); Context is an
  explicitly *unimplemented* dimension.
- Context runs are reachable **per-run only** via `GET /api/context/runs/{run_id}`; there is no listing/
  enumeration endpoint. The sole server-side scan of `CONTEXT_RUNS_DIR` lives in `routes/comparison.py`,
  which feeds `/api/comparison?a=<key>&b=<key>` and needs known run keys — not an enumeration surface.

Required future backend/read-model contract (a separate Act, solved after this presentation-only phase):

```
model/config
→ available Context runs        (enumeration/listing endpoint over committed artifacts)
→ per-run context point series  (requested_context_tokens, score, degradation_from_baseline,
                                 retention_relative_to_baseline, status) verbatim
→ status/coverage metadata      (supported / partial / unsupported / invalid / failed counts, baseline point)
→ evidence deep-link            (/context/results/{run_id})
```

No production code was changed for P5. P5 resumes once that contract exists; until then the Context tab
keeps its honest grounded placeholder. This entry does not touch `docs/ROADMAP.md` and does not create
RM-26-AA-0022.

---

## 1. North-star goal

By the end of this work, a user opening `/results` sees a **ranked, evidence-backed benchmark
report**: the strongest available signal leads, every number carries its trust basis, status is
human and consistent, "see more" behaves the same everywhere, and unsuccessful/diagnostic evidence is
handled by one rule — while scores, eligibility, provenance and N/A-≠-0 remain exactly as the data
contract guarantees.

**In one line:** *mean first, storage second; trust next to every number; one language, one model.*

---

## 2. Non-goals (hard boundaries)

| Not in scope | Why |
|--------------|-----|
| Change scoring / eligibility / composite logic | Foundation invariant (§0.3/§6); composite blocked on RM-26-AA-0019 |
| Introduce a light/dark toggle or new primary colour | "dark mode or nothing" invariant (0016/0017/0018) |
| Invent an Overall/composite score, winner, or arbitrary weights | Contract §2/§6; 0019 blocks it |
| Rename/add contract states or fabricate termination reasons | States are authoritative (§5.1); this phase only *maps* them |
| Redesign benchmark families, tasks, or the read-model schema | Presentation-only; read models are the foundation |
| Change provenance/export allow-list (§9) | Security invariant |

**Definition of "presentation only":** changes touch `bench_llm/static/*` (HTML/CSS/JS rendering,
formatting, sorting, filtering, disclosure, labels, states). No change to `src/**` read models,
routes, scoring, or persistence. Where a label needs data the read model does not expose, we **map
what is already there** rather than extend the backend (see Phase 4 fallback).

---

## 3. Tracking item (proposed — not created)

This work does not fit cleanly under an existing RM (0013 = comparison, 0016/0017/0018 = already
delivered foundations). **Propose a new ticket `RM-26-AA-0022 — Results UX product polish`** and add
it to the ROADMAP `ACTIVE` section once approved. This plan is its specification.

> Not created here: per ACT instructions I will not modify `docs/ROADMAP.md` or create the ticket
> without explicit approval. The proposal below exists so the plan is actionable if accepted.

**RM-26-AA-0022 product statement (proposed):** *Polish the unified Results shell so it reads as a
coherent benchmark product rather than a read-model viewer: Overall leads with per-dimension
readiness, human-language status mapping across all views, one disclosure model per result type, an
evidence-confidence narrative generalised from Context's coverage rollup, consistent unsuccessful/
diagnostic handling (§0.2/§8), and guiding empty states. Presentation only; no scoring/composite/
semantic change; N/A ≠ 0.*

---

## 4. Phases

Each phase is independently shippable, fast-gate + browser-acceptance tested, and closes specific
gaps. Order respects dependencies (language layer before its consumers; unified toggle before
per-view polish).

### Phase 0 — Alignment & contract sign-off (no code)
**Closes:** none (enabler). **Depends on:** nothing.
- Confirm composite stays unavailable until RM-26-AA-0019 approves it (do not build an Overall score).
- Sign off the human-language mapping (§2 of design system) and new token suggestions (contrast
  check for any proposed `--rs-*-bg`).
- **Exit:** written sign-off on §2 mappings + tokens; RM-26-AA-0022 created if approved.

### Phase 1 — Human-language mapping layer (closes GAP-2, feeds all later phases)
**Closes:** GAP-2. **Depends on:** Phase 0.
- Add one translation function per surface family in a shared module (e.g. `results-utils.js` or a
  new `results-status.js`) mapping contract §5.1/§4 states → human labels + chip classes (§5.1 chip
  inventory). No view renders a raw enum after this phase.
- Add additive background-token usage so chips = word + tint + colour (never colour alone).
- **Files:** `static/results-utils.js` / new shared status module + `results-shell.css`.
- **Backend:** none. All states already exist in the read model.
- **Exit:** every view emits human labels; a raw `"partial"`/`"unknown"`/`"LEGACY"` can no longer
  reach the DOM un-mapped. Fast gate + browser check for each status chip.

### Phase 2 — Overall view as product hero (closes GAP-1)
**Closes:** GAP-1. **Depends on:** Phase 1.
- Replace `NO COMPOSITE` heading with "Overall — per-dimension readiness" + one-line pending-composite
  note (secondary, not a badge-as-heading).
- Reframe dimension cards: available dims show a leading signal (avg gen tok/s / best pass rate / N
  runs); unavailable dims show "No results yet" / "Coming soon", not "○ — reason".
- Recede fingerprint from first cell to copy-on-hover/provenance footer.
- **Do NOT** invent a composite or rank by absence.
- **Files:** `static/results-shell.js` `renderOverall()`, `results-shell.css`.
- **Backend:** none (leading signals come from existing `/api/ranking` fields; if a specific leading
  value is absent, render it as N/A, never inferred).
- **Exit:** Overall loads first and leads with available signal; honest pending-composite note
  present; no composite computed. Browser acceptance: Overall reads as product state, not unavailability.

### Phase 3 — One disclosure model per result type (closes GAP-3)
**Closes:** GAP-3. **Depends on:** Phase 2.
- Decide: summary views use inline `<details>`; full detail stays on dedicated pages. Remove the
  duplicate Speed modal-or-inline ambiguity (pick one; keep the deep link).
- Apply the §4 disclosure content contract (human headline → human status chip → evidence-confidence
  line → provenance footer) to Speed / Overall / Workflow summary disclosures.
- **Files:** `static/results.js` (Speed), `static/results-shell.js` (Overall/Workflow).
- **Backend:** none.
- **Exit:** "see more" behaves consistently across the three summary views; detail pages unchanged.
  Browser acceptance: disclosure behaviour identical across views.

### Phase 4 — Evidence-confidence narrative (closes GAP-6)
**Closes:** GAP-6. **Depends on:** Phase 3.
- Generalise Context's coverage rollup into a reusable "evidence basis" component and add it to Speed
  and Overall summary rows: "*X eligible · Y gap · current/legacy metric*".
- Add the small trust indicator (status ≠ trust axis) per §6 of the design system.
- **Files:** new shared evidence-basis component + `results-shell.css`; wire into Speed/Overall.
- **Backend:** eligibility counts derive from already-exposed run/subtest status in `/api/ranking`
  (arithmetic over present values, presentation only — same discipline as Workflow failed-check
  counting). If a needed count is not exposed, render N/A rather than extend the backend.
- **Exit:** every summary number carries its trust basis; gaps labelled as gaps, never zero-filled.

### Phase 5 — Unsuccessful / diagnostic unification (closes GAP-7 — real contract gap)
**Closes:** GAP-7. **Depends on:** Phase 1.
- Add one consistent **"Show unsuccessful"** toggle to each summary view's filter bar; partial/
  interrupted/failed/diagnostic hidden by default; `unsupported` shown as gaps.
- Keep DIAGNOSTIC RUN — NOT SCORED marker (§8) on diagnostic/incomplete content.
- **Files:** filter bars in `static/results.js` (Speed) + `results-shell.js` (Workflow); shared
  toggle component; shell markup in `static/results.html`.
- **Backend:** none — filtering is client-side over already-loaded evidence (current Workflow filter
  pattern). Ensure hidden-by-default does not drop data from the read model or exports.
- **Exit:** one rule across all views; unsuccessful handling matches contract §0.2/§8. This is the
  highest-value consistency fix and may ship ahead of Phases 3–4 if ordering changes.

### Phase 6 — Empty / loading / error state polish (closes GAP-5)
**Closes:** GAP-5. **Depends on:** nothing (independent).
- Guiding empty-state copy + primary CTA for "no results yet"; "no matches after filters" with Clear;
  keep guarded loading (no placeholder scores) and readable errors.
- **Files:** `static/results.js` `showEmpty()`, `results-shell.js` state fragments, `results-shell.css`.
- **Backend:** none.
- **Exit:** no bare empty tables; empty states teach + guide.

### Phase 7 — Cross-cutting: accessibility, responsive, tests, docs
**Closes:** residual a11y/responsive from all phases. **Depends on:** Phases 1–6.
- `:focus-visible` on all new controls; semantic form controls; meaning not by colour alone;
  responsive reflow of summary tables (generalise the Speed compare pattern).
- Fast gate + browser acceptance per phase accumulated here; update README/architecture/ROADMAP
  results tables to reflect delivered polish.
- **Exit:** full suite green for new behaviour; fast gate shows zero new failures attributable to this
  work (document any pre-existing baseline failures, as prior Acts did).

---

## 5. Dependency map

```
Phase 0 (sign-off)
   │
   ├──→ Phase 1 (language layer) ──→ Phase 2 (Overall hero) ──→ Phase 3 (disclosure) ──→ Phase 4 (evidence basis)
   │        ▲                                                              │
   │        └────────────────────────────────────────────────────────────┘
   ├──→ Phase 5 (unsuccessful toggle)   [independent of 2–4, can lead]
   └──→ Phase 6 (empty states)          [independent]
                                          │
                                       Phase 7 (a11y/tests/docs)
```

- **Phase 1 is the critical path foundation** — every later phase depends on human labels existing.
- **Phase 5 can ship earlier** than 2/3/4 because it is a standalone consistency fix with its own
  filter bars.
- **Phase 6 is fully independent.**

---

## 6. Risk register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Accidentally changing scoring/composite semantics | Med | High | Scope review checklist = "did I touch `src/**`? did I compute/infer a number?"; formats-only definition of done |
| Leading-signal values not exposed by read model | Med | Low | Render N/A, never infer; Phase 1 fallback rule baked into definition of done |
| New colour tokens fail contrast | Med | Med | Contrast check before shipping any new token (Phase 0 gate) |
| Three-file inconsistency resurfaces | High | Med | Shared components + mapping layer in one module (Phases 1/3/4); component inventory (§11 design system) as the target |
| "Show unsuccessful" hides data from exports | Low | High | Verify hidden-by-default is presentation-only; read model/exports still carry all evidence (§0.2) |
| Scope creep into backend/read-model work | Med | Med | Non-goals table + Phase 0 sign-off; new backend need → N/A or a separate ticket |

---

## 7. Per-phase acceptance (summary)

| Phase | Acceptance |
|-------|-----------|
| 0 | Composite-unavailable decision signed off; tokens contrast-checked; RM-26-AA-0022 created if approved |
| 1 | No raw enum reaches DOM; chips = word + tint + colour; browser check per status |
| 2 | Overall leads with available signal; pending-composite note secondary; no composite computed |
| 3 | "See more" identical across Speed/Overall/Workflow summary views; detail pages unchanged |
| 4 | Every number carries a trust basis; gaps ≠ zero; Context pattern generalised |
| 5 | One "Show unsuccessful" rule across views; unsupported shown as gaps; DIAGNOSTIC marker present |
| 6 | No bare empty tables; empty states guide; loading never shows placeholder scores |
| 7 | `:focus-visible` everywhere new; responsive reflow; fast gate + browser acceptance clean for new behaviour |

---

## 8. How this relates to existing tickets

- **RM-26-AA-0013 (Compare, ACTIVE):** this plan does not redo comparison metrics; it ensures the
  Compare view follows the same language/status/disclosure system once Phase 1–5 land. ST-006/007
  (Context compare, N/A/unsuccessful preservation) partially overlaps Phase 5 — coordinate, don't
  duplicate.
- **RM-26-AA-0019/0020 (Composite, FUTURE):** this phase deliberately leaves Overall as *readiness*,
  not composite. When 0019 approves and 0020 ships, the Overall hero (§3) swaps "readiness" for the
  approved score with a small change — the shell already reserves that slot.
- **RM-26-AA-0017/0018 (Speed/Workflow, Context, DONE):** this is polish on top, not re-delivery.

---

*End of plan. Artifacts produced by this ACT: `docs/results-ux-discovery.md`,
`docs/results-ux-design-system.md`, `docs/results-ux-implementation-plan.md`. All uncommitted; no
`bench_llm/` code modified.*
