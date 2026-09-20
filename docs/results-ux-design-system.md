# Results UX — Design System

**Phase:** Results UX Redesign (Design) · **Owner surface:** results presentation
**Branch:** `feat/results-ux` · Foundation commit: `e4cf075`
**Status:** planning artifact — no files in `bench_llm/` modified

> The design system defines *how the Results experience should look and behave* so it reads as a
> coherent benchmark product rather than an inspector over a read model. It extends, never
> replaces, the existing unified shell (RM-26-AA-0016) and dark visual system. Every rule maps back
> to a gap in `docs/results-ux-discovery.md` and an invariant in the **results data contract §0 /
> §5.1 / §12**. Presentation only — no scoring, no semantic change.

---

## 0. Guiding principle: lead with meaning, not storage

The single rule everything below serves:

> **Surface what the evidence means; expose how it is stored only on demand.**

Concretely: human language over enum, headline signal over absence, one disclosure model per result
type, and trust (evidence quality) always visible next to every number. Fingerprints, raw status
tokens and composite-unavailable notices recede into secondary/detailed positions — they are still
there for inspection (contract §0.2 / §9 provenance), but they no longer *lead*.

This directly closes GAP-1 (framing), GAP-2 (language), GAP-3 (consistency), GAP-4 (status),
GAP-5 (empty states), GAP-6 (evidence narrative) and unifies the rule behind GAP-7.

---

## 1. Tokens (extend, do not redefine)

Reuse the existing shell palette (`results-shell.css` `:root`). Do **not** introduce a light theme or
a new primary colour — "dark mode or nothing" is an invariant (0016/0017/0018). Additive tokens only.

### 1.1 Colour (existing, preserved)

| Token | Value | Role |
|-------|-------|------|
| `--rs-bg` | `#07111f` | app background |
| `--rs-surface` | `#0f1b2d` | panel/card surface |
| `--rs-surface-2` | (elevated) | hover/selected surface |
| `--rs-border` | `#1e3350` | hard divider |
| `--rs-border-soft` | `#243754` | soft divider |
| `--rs-text` | `#e8eef6` | primary text |
| `--rs-text-muted` | `#9fb0c7` | secondary text |
| `--rs-text-faint` | `#7f93af` | tertiary / hint text |
| `--rs-accent` | `#93c4f5` | primary accent (links, active) |
| `--rs-accent-strong` | `#7dd3fc` | accent emphasis |
| `--rs-accent-hover` | `#183a5e` | hover fill |
| `--rs-ok` / `--rs-ok-text` | `#34d399` / `#8ce6a8` | success / valid / passed |
| `--rs-warn` / `--rs-warn-text` | `#fbbf24` / `#fcd34d` | incomplete / caution |
| `--rs-error` / `--rs-error-text` | `#f87171` / `#fca5a5` | failed / error |
| `--rs-info` | `#93c4f5` | informational |
| `--rs-unavailable` | `#6b7f99` | not-available / N/A material |

### 1.2 New additive tokens (proposed)

These exist to carry the **evidence-confidence** and **status-background** needs the redesign adds,
without reusing text colour as the sole signal.

| Token | Suggested value | Purpose |
|-------|-----------------|---------|
| `--rs-ok-bg` / `--rs-warn-bg` / `--rs-error-bg` / `--rs-unavail-bg` | tinted versions of the above (e.g. `rgba(51,211,153,.14)`) | chip *fill* so status is colour+text+fill, never colour alone (§5.1 accessibility) |
| `--rs-trust-high` / `--rs-trust-mid` / `--rs-trust-low` | ok / warn / unavailable tints | evidence-confidence bar + label (GAP-6) |
| `--rs-radius-sm` `--rs-radius-md` `--rs-radius-lg` | 6 / 10 / 14px | consistent rounding across cards, modals, detail pages |
| `--rs-space-xs..3xl` | 4/8/12/16/24/32/48px | one spacing scale (reuse where already present) |
| `--rs-font-mono` | existing mono stack | fingerprints / run IDs only, always in `<code>` |

> All suggested values are **proposals for sign-off**; the palette must stay within the dark system
> and pass contrast for the text roles above. Do not ship new colours without a contrast check.

### 1.3 Typography scale

One type scale across every results surface (shell already sets body; extend for result headings):

| Role | Weight | Use |
|------|--------|-----|
| Result title (H2, per panel) | 600 | section heads (`rs-panel-title`, `v2` panels) |
| Model / run headline | 600 | model family + version in group summary |
| Metric value | 600 | scores, tok/s, percentages — the things users scan |
| Metric label | 400 | axis/col labels |
| Provenance (run id, fp, timestamp) | 400 mono | always monospace, faint text, never a headline |

Rule: **numbers are louder than prose.** Metric values get weight + accent where it aids scanning;
provenance stays quiet (faint mono). This is the visual opposite of the current "fingerprint as a
first-class cell" treatment (GAP-2).

---

## 2. Human-language mapping layer (closes GAP-2)

Introduce one function per surface family that maps **authoritative contract states → human labels**.
The contract §5.1/§4 states are the source of truth; this layer only *translates* them, never adds,
renames or drops a state. It is the single place enum→language lives, so all views agree.

### 2.1 Run / subtest status (§5.1) → label + chip

| Contract `status` | Human label | Chip treatment |
|-------------------|-------------|----------------|
| `completed` / `valid` | **Passed** (Workflow) · **Complete** (Speed/Context) | ok fill+text |
| `partial` | **Partial — some checks missing** | warn fill+text |
| `interrupted` | **Interrupted** | warn fill+text |
| `failed` | **Failed** | error fill+text |
| `unsupported` | **Not supported at this point** (a gap, not a failure) | unavailable fill+text, labelled as *gap* |
| `unknown` / config `incomplete` | **Invalid — could not be scored** | error fill+text (distinct from `failed`) |

Rules:
- **Colour is never the only signal.** Every chip shows its word; background tint reinforces.
  (`results-shell.js` already guards this for Workflow rows — generalise to all views.)
- `unknown` (couldn't be scored) and `failed` (ran, errored) stay **distinct** — a viewer merges
  them; a product does not (contract §5.1).
- Never show the raw token. If an unknown state arrives from the backend, map to **"Unknown status"**
  (error chip), never leak `"???"`.

### 2.2 Dimension availability → label

| Read-model dimension state | Human label |
|----------------------------|-------------|
| `available` | **Available** ✓ |
| `missing` | **No results yet** |
| `in_progress` | **Running** |
| `ambiguous` | **Can't compare this pairing** |
| `unsupported` | **Not supported for this config** |
| `unavailable` (family undelivered) | **Coming soon** |

### 2.3 Metric-version states (Speed)

| State | Human label |
|-------|-------------|
| current-metric | *(default — no badge, or "Current")* |
| `LEGACY` | **Legacy metric run** (with traceable deep link) |
| point `unsupported` | **Not supported at this context** (gap) |

### 2.4 Composite / Overall state

| State | Human label |
|-------|-------------|
| composite approved | *(the score, as headline)* — not yet possible, blocked on RM-26-AA-0019 |
| composite unavailable (current) | **"Overall Solo Bench — per-dimension readiness"** (see §3), never "NO COMPOSITE" as the heading |

> The word *composite* stays in **secondary** copy only (a one-line note that scoring is pending
> approval). It must not be the panel title. This is the core of GAP-1.

---

## 3. Overall view as a product hero (closes GAP-1)

The Overall tab loads first and gets the most space, so it should tell the user what the benchmark
*can* show today — not what it can't.

### 3.1 New heading + structure

Replace the current `NO COMPOSITE` badge + `unavailable` banner with:

```
Overall — per-dimension readiness
[one-line note: "Overall Solo Bench composite scoring is pending an approved contract; individual
 benchmark results below are available and comparable."]
```

Then, in order of descending availability/strength:

1. **Available dimensions row** — the same dimension cards, but reframed: each shows its human
   label, a ✓/○ marker, and (where available) a *leading signal* — e.g. Speed → "avg gen tok/s",
   Workflow → "best pass rate", Context → "N runs". This turns "○ — reason" into "○ — no results
   yet" **or** "✓ — <headline>", so the view leads with what exists.
2. **Model readiness list** — the existing L0→L1 drill-down (`renderL0Models`) kept, but:
   - model family/version is the headline (already is);
   - architecture + dimension chips are secondary;
   - the 12-char fingerprint recedes to a copy-on-hover / "show" toggle in provenance, not a first
     cell (GAP-2).

### 3.2 Do NOT

- Invent or infer any composite/overall score.
- Rank models by absence ("no eligible results" never best/worst — contract §2).
- Replace the honest pending-composite note with marketing language. It stays truthful, just less
  prominent as a *heading*.

---

## 4. One disclosure model per result type (closes GAP-3)

Define the canonical "how do I see more" for each result type and apply it everywhere. No new route
per tab; reuse existing deep links (`/speed/results/{id}`, `/v2/results/{id}`, `/context/results/{id}`)
as the authoritative detail pages, but make *in-shell* disclosure consistent.

| Result type | Primary disclosure | Detail page (deep link) |
|-------------|--------------------|--------------------------|
| Speed summary row | expand inline `<details>` **or** modal — pick **one** and remove the other | `/speed/results/{id}` |
| Workflow run row | inline `<details>` (current) — keep; unify styling with Speed | `/v2/results/{id}` |
| Context point / model | inline `<details>` on the Context page | `/context/results/{id}` |
| Overall model group | inline `<details>` (current) — keep | (drill into config/run) |

Recommendation: **prefer progressive inline disclosure (`<details>`) within the shell for summary
views**, and reserve full navigation to the dedicated detail pages. This makes "see more" learnable
once across Speed + Overall + Workflow, while Context keeps its bespoke curve page (it is genuinely
different — a degradation curve). The key is that **summary views stop mixing modal + inline +
navigate** (GAP-3).

### 4.1 Disclosure content contract (all types)

Every disclosure, in order:
1. Human headline (model/version or metric), provenance quiet (mono/faint).
2. Human-labelled status chip (§2.1/§2.2), colour+text+fill.
3. **Evidence-confidence line** (GAP-6): e.g. "3 of 3 points eligible · current-metric" /
   "X/Y checks passed · completed". Never imply trust the data doesn't carry.
4. Provenance footer: run id, benchmark family + version, fingerprint (on demand).

---

## 5. Status treatment — consistent, never colour-only (closes GAP-4)

- **Every** status chip = word **+** background tint (**new** `--rs-*-bg` tokens) **+** text colour.
  Colour is reinforcement, never the sole carrier (accessibility; 0018 §ST-008).
- Status semantics come from the contract (§5.1); the mapping layer (§2) renders them identically on
  every view. One component class per status, reused across `results-shell.js`, `results.js`,
  `context-result.js`, `v2-result.css`.
- **Trust vs status are different axes.** A run can be `completed` (status = ok) yet low-trust if it
  fed few points; show both: status chip + a small trust indicator (§1.3 trust tokens). This is the
  main addition over the current system and directly addresses GAP-6.

### 5.1 Chip inventory (single source of truth)

| Class | State family | Content |
|-------|--------------|---------|
| `rs-chip-ok` | passed / complete / available | word + ok-bg |
| `rs-chip-warn` | partial / interrupted / incomplete | word + warn-bg |
| `rs-chip-error` | failed / invalid | word + error-bg |
| `rs-chip-gap` | unsupported (a gap) | word + unavailable-bg, labelled "gap" |
| `rs-chip-info` | running / in-progress | word + info tint |
| `rs-chip-faint` | not-yet-delivered / coming soon | faint text, no strong colour |
| `rs-chip-provenance` | run id / fp / version | mono, faint, copyable |

---

## 6. Evidence-confidence narrative (closes GAP-6)

Generalise the **Context coverage rollup** (supported / unsupported / missing / invalid / failed) —
the strongest existing evidence-narrative pattern — to Speed and Workflow summary views as a compact
"evidence bar":

```
[██████░░] 4 eligible · 1 gap · current-metric
```

- **Eligible** = subtests/runs that feed scoring (§6 eligibility contract).
- **Gap / unsupported** = contract points with no run record, labelled as gaps (never zero-filled).
- **Metric version** = current vs LEGACY (Speed).
- Rendered as text + a small bar; the bar is decorative reinforcement, not the meaning.

This answers "how much do I trust this number?" at a glance — the difference between a viewer (shows
the number) and a product (shows the number *and its basis*).

---

## 7. Empty / loading / error states (closes GAP-5)

| State | Behaviour |
|-------|-----------|
| **No results yet** (family delivered, zero runs) | Guiding message: what the benchmark is + one primary CTA ("Run your first Speed benchmark") — not "no rows". Keep the existing Run Benchmark link but give it product copy. |
| **No matches** (after filters) | "No results match your filters." + Clear filters button; never a bare empty table. |
| **Loading** | Spinner + label ("Loading Speed results…"); never show placeholder scores (already guarded on v2-sticky). |
| **Error** | Readable title + message + retry; no stack traces / raw JSON (already the contract). |
| **Unavailable dimension** | "Coming soon" with a one-line what-it-will-show, not an error. |

Rule: empty states **teach and guide**, they do not report an empty data table.

---

## 8. Language & tone guide

| Show (human) | Never show as product text (read-model internals) |
|---------------|--------------------------------------------------|
| "Passed" / "Complete" | `"completed"` / `"valid"` raw token |
| "Partial — some checks missing" | `"partial"` alone |
| "Not supported at this context (gap)" | `"unsupported"` alone |
| "Legacy metric run" | `"LEGACY"` as a bare chip label without explanation |
| "Can't compare this pairing" | `"ambiguous"` alone |
| "Overall — per-dimension readiness" | "NO COMPOSITE" as the heading |
| Run id / fp in `<code>`, faint, copyable | fingerprint as a first-class sortable cell |

Additional tone rules:
- **No winner/leader/composite language** anywhere (0013 product contract). Avoid "best/worst",
  "leading", "rank #1". Use neutral "highest measured", "most eligible points".
- **N/A stays N/A.** Missing → em-dash (`na()`), never `0`, never blank that reads as zero.
- Diagnostic/incomplete runs carry the explicit marker **DIAGNOSTIC RUN — NOT SCORED** (§8) wherever
  content could detach from its report; keep it in headings, not just footers.

---

## 9. Unsuccessful / diagnostic handling — one rule (closes GAP-7)

Apply the contract §0.2 / §8 uniformly across **all** summary views:

- Partial / interrupted / failed / diagnostic runs are **hidden by default** behind a single
  **"Show unsuccessful"** toggle on each summary view's filter bar.
- `unsupported` points are **shown as gaps** (not hidden) — they are evidence of capacity, not
  failure.
- Diagnostic/incomplete runs keep the NOT SCORED marker and never contribute to any score.
- The toggle is consistent in wording, position and behaviour across Speed / Overall / Workflow so
  it is learnable once.

This is the one gap that is a **real contract-consistency issue**, not only perception — resolving it
is part of the redesign.

---

## 10. Accessibility (extends existing)

- `:focus-visible` on every interactive control (already required by 0016/0017/0018; extend to new
  chips, toggles, trust bar).
- Semantic controls: `<select>` for state filters, checkbox for "Show unsuccessful", native
  `<details>` for disclosure, `<dialog>` only where a modal is the chosen model.
- `aria-live` on filter counts and state changes; `aria-current="page"` on active nav (already in
  `app-shell.js`).
- Meaning never conveyed by colour alone (§5).
- Responsive: summary tables reflow to stacked metric blocks at narrow widths (Speed compare already
  does this — generalise the pattern).

---

## 11. Component inventory (single, reused)

| Component | Used now in | Reuse target |
|-----------|-------------|--------------|
| `rs-panel` / `rs-panel-title` | shell | all views |
| `rs-metric-card` / `rs-dim-*` | Overall | also Speed/Context metric cards |
| `rs-model-group` (L0→L1 `<details>`) | Overall | consistent with Workflow run `<details>` |
| `rs-wf-run` (Workflow row) | Workflow | unify styling with Speed summary row |
| Status chips (§5.1) | scattered | single shared set, all views |
| Evidence-confidence bar (§6) | **Context only** | extend to Speed + Overall |
| Coverage rollup (§6) | Context only | generalise to a "evidence basis" component |
| Banners/states (`rs-banner`, `stateLoading/NoResults`) | shell | shared across all views + detail pages |
| `<dialog>` modal (Speed) | Speed | keep *or* replace with inline — decide in §4 |

Goal: by end of redesign, summary views share ~80% of these components instead of three bespoke
patterns.

---

## 12. What this system does NOT do (invariants preserved)

- No light theme; no new primary colour without sign-off + contrast check.
- No client-side scoring/composite/dimension recomputation (formats only).
- No invented composite, winner, or arbitrary weights.
- N/A ≠ 0; missing ≠ unsupported; invalid ≠ failed — all kept distinct.
- Identity stays authoritative (family+version, fingerprint); display name never drives grouping.
- Provenance on every exported/detachable byte (§9); DIAGNOSTIC marker preserved (§8).

---

*Next: `docs/results-ux-implementation-plan.md` maps this system to shippable slices against the
existing RM-26-AA subtasks.*
