# Results Data Contract — Evidence-First Redesign

**Status:** CONTRACT (canonical schema reference) · **Owner:** results surface
**Authoritative runtime identity:** `solo-dev-llm-bench` · Branch: `feat/benchmark-v2`

> This document defines the **single canonical result data model** that every persistence
> writer, read-model adapter, API route and UI view in Solo Dev BenchLLM must conform to.
> It is written *before* the presentation layer so the UI never invents identity, validity,
> scoring-eligibility or provenance — it only renders what the contract guarantees.

## 0. Invariants (non-negotiable)

These are the behavioural laws of the contract. They override any field-level default below.

1. **Unrecorded = absent, never zero.** Any measurement that was not observed is `null`
   (`None`/SQL NULL), never coerced to `0`. A score of `0` means "measured zero"; absent
   telemetry means "not measured". The UI renders both as distinct states (e.g. *N/A* /
   *NOT REPORTED*), never the same blank cell.
2. **Nothing is discarded, only hidden.** Failed / partial / interrupted / diagnostic runs
   remain fully persisted and queryable by default; they are collapsed in the UI behind an
   explicit *Show unsuccessful* toggle but are never deleted or re-scored as passing.
3. **Only valid, score-eligible evidence feeds scores.** A subtest that is partial /
   interrupted / failed / diagnostic contributes no numeric value to any aggregate. An absent
   dimension drops out of a composite; it is never padded with zero and the denominator shrinks
   to match (see §6).
4. **One headline result per distinct model family/version.** Dense and MoE are distinct
   families. Different versions (weights / architecture revision) are distinct. Quantisations
   and configurations are *children* of a model, never siblings of it. See §2.
5. **Strict provenance on every byte that leaves the machine.** Every export and every API
   payload carries Run IDs and benchmark/version identity. Exported metadata is an explicit
   allow-list (§9); host/device identifiers are exported only by deliberate exception, never
   by default.
6. **Additive / backward-safe schema.** New fields/columns are appended; existing rows load as
   `null` rather than failing. This mirrors the existing `ResultsStore` ALTER pattern and the
   v2_quality artifact `schema_version`.

---

## 1. Three-level hierarchy (canonical entity graph)

```
┌──────────────────────────────────────────────────────────────┐
│ L0  MODEL  (family / version)            one headline result  │
│     ├─ identity + architecture (dense|MoE)                    │
│     └─ distinct per model family/version                      │
└───────────────▲──────────────────────────────────────────────┘
                │ 1
                │ has (≥0)
    ┌───────────┴───────────────────────────────────────────────┐
    │ L1  CONFIGURATION (quantisation / runtime settings)        │
    │     ├─ weight quantisation + resolved inference config      │
    │     └─ distinct per material configuration of the model    │
    └──────────────▲──────────────────────────────────────────────┘
                   │ 1
                   │ has (≥1 benchmark RUN)
    ┌──────────────┴───────────────────────────────────────────────┐
    │ L2  RUN / EVIDENCE (subtests + telemetry + status)           │
    │     ├─ unique Run ID; subtests carry traceable child IDs      │
    │     ├─ validity: valid | partial | interrupted | failed      │
    │     ├─ scoring eligibility derived from validity (§6)         │
    │     └─ diagnostics preserved even when the run is incomplete  │
    └──────────────────────────────────────────────────────────────┘
```

- **L0 (Model):** the unit that gets ranked. Ranked by a composite of its *eligible* runs'
  results across every benchmark dimension that has evidence for it. See §6, §7.
- **L1 (Configuration):** one entry per distinct *material* configuration — primarily weight
  quantisation plus resolved inference settings (context window, KV-cache quant, MTP/spec
  state, batch/parallel). Quantisations are never scored in isolation; they exist so L2 runs
  can be grouped and compared. See §3.
- **L2 (Run / Evidence):** the durable record of a single benchmark execution. Contains every
  subtest, its validity, scores where valid, telemetry, termination reason, and provenance.

A model with zero completed runs still appears in ranking surfaces but is labelled *no
eligible results* — it is never ranked as best/worst by absence.

---

## 2. L0 — Model (family / version) identity

| Field | Type | Null rule | Notes |
|-------|------|-----------|-------|
| `model_family` | string | **never null** | Vendor/product family, e.g. `Qwen2.5`, `Llama`, `DeepSeek`. Canonical label for grouping/ranking. |
| `model_version` | string | **never null** | Weight / architecture revision that changes behaviour, e.g. version tag, release, or the model key when no explicit version exists. Distinct from display name. |
| `architecture` | enum `{dense, moe}` | **never null** | **Dense and MoE are distinct families for ranking.** Resolve authoritatively (registry / config `num_experts > 1` ⇒ `moe`). Never infer from a guessed parameter count; unknown ⇒ record as `"unknown"` but keep the family/version identity. |
| `model_key` | string | **never null** | Stable, non-mutable identifier used for fingerprinting and joins (the loaded-instance key). Never the mutable display name. |
| `model_display_name` | string | nullable | Human label only; never used for identity or grouping. |
| `hardware_identity` | object | nullable | Static host identity for canonical classification (§3): `cpu_model`, `installed_ram_bytes`, `gpu_model`. At least one required to be *canonical*. |

**Ranking key = `(model_family, model_version)`.** This is the identity of one headline
result. Changing only quantisation/config produces a new **L1 child**, not a new L0.

---

## 3. L1 — Configuration (quantisation / runtime settings)

A configuration is identified by its *material* inference-configuration fields, exactly as in
`results.py::compute_configuration_fingerprint`. New material keys must be added to both the
fingerprint key set and this table together so identity stays coherent.

| Field | Type | Null rule | Notes |
|-------|------|-----------|-------|
| `configuration_fingerprint` | string (sha-256 hex) | nullable | Deterministic hash over material config; identical config ⇒ identical fingerprint. Used for grouping/comparison, never re-derived downstream. |
| `model_quantization` | string | **never null** | Resolved weight quantisation verbatim from the registry. Failure modes persist as stable non-fabricated labels (`metadata_absent`, `metadata_malformed`, `model_not_found`, `lookup_failed`) — none of which mark a run *canonical* (§6). |
| `loaded_context` | int>0 | nullable | Authoritative loaded context window. Required for canonical. |
| `model_max_context` | int>0 | nullable | Configured maximum context from the registry. |
| `reasoning_mode` | string | never null | Canonical benchmark rule = `"off"` (sent in request, persisted verbatim). |
| `kv_cache_k_quantization` / `kv_cache_v_quantization` | string | nullable | KV-cache quant; registry does not expose these ⇒ record `"unknown"`, never inferred from memory. |
| `kv_cache_quantization_source` | string | never null | e.g. `"registry"` / `"unknown"`. |
| `flash_attention` | bool | nullable | |
| `offload_kv_cache_to_gpu` | bool | nullable | |
| `eval_batch_size` / `physical_batch_size` / `parallel` | int>0 | nullable | All required (>0) for canonical. |
| `num_experts` | int | nullable | Drives `architecture` (moe when >1). |
| `speculative_draft_mtp` / `speculative_draft_simple` | bool | nullable | MTP/speculative state. Active MTP requires a recorded `speculative_draft_model`, else the configuration is *incomplete*. |
| `speculative_draft_model` | string | nullable | Draft model name; required when MTP active. |
| `speculative_draft_max_tokens` / `_min_tokens` / `_min_continue_probability` | number | nullable | |
| `output_budget_policy` | string | nullable | Output-length policy that changes behaviour across versions (e.g. fixed budget / 75% context). Part of fingerprint. |

**Canonical vs incomplete.** A configuration is `"canonical"` only when every material field
above that the benchmark defines is *known*; anything unknown ⇒ `"incomplete"`. Incomplete
configurations persist and are inspectable, but are never surfaced as an official leaderboard
entry (§6). This reuses `results.py::classify_run_for_result` semantics verbatim.

---

## 4. L2 — Run / Evidence identity & provenance

Every benchmark execution gets a unique **Run ID**. Diagnostic runs use the same ID space with
a distinct type tag so they are traceable but never mistaken for scored results.

| Field | Type | Null rule | Notes |
|-------|------|-----------|-------|
| `run_id` | string | **never null** | Unique per benchmark execution. Prefixed by family (e.g. `speed-<hex>`, workflow/UUID), filesystem-safe, path-traversal guarded. Immutable once persisted. |
| `artifact_type` | string | never null | e.g. `solo-dev-llm-bench.speed-run`, `.v2-quality-run`. Identifies the benchmark family document shape. |
| `benchmark_family` | enum `{speed, agentic, context_degradation, intelligence}` | never null | Which benchmark produced this run. Drives tab membership and scoring contract (§6/§7). |
| `run_type` | enum `{benchmark, diagnostic}` | never null | **Diagnostic runs are explicitly NOT scored.** See §8. |
| `benchmark_version` | int/string | never null | Version of the benchmark *semantics* that produced this run (e.g. Standard Speed metric `v3`). Lets corrected semantics stay distinguishable from legacy cached artifacts; historical rows carry their own version or a sentinel. |
| `timestamp` | ISO-8601 UTC | nullable | When the run was recorded. Standalone/legacy runs may omit it. |
| `lm_studio_url` | string | nullable | Backend base URL used (loopback-resolved, allow-listed). Provenance only. |
| `schema_version` | int | never null | Version of *this* result document schema (§0.6). Bump on shape change; old artifacts kept under their own version. |

**Subtest IDs.** Each subtest receives a **traceable child ID derived from the parent Run ID**
so evidence can always be linked back without a join:

```
<benchmark_family>:<run_id>:<subtest_key>[:<index>]
```

Example: `speed:speed-3f9a…:16K:0`. The derivation is deterministic and prefix-traversal guarded
(§ same rules as `_validate_run_id`). A subtest ID must never contain `/`, `\` or NUL bytes.

---

## 5. Subtest / evidence record

One run contains one or more **subtests**, each a distinct measurement at a fixed context
point / task bucket. Only *valid, score-eligible* subtests contribute to scores (§6).

### 5.1 Status & validity vocabulary (authoritative)

| `status` | Meaning | Score? | UI default |
|----------|---------|--------|-----------|
| `completed` | Ran to end; all expected measurements present and valid | Yes (if eligible) | shown |
| `valid` | Synonym alias accepted for a fully-scored completed subtest | Yes | shown |
| `partial` | Ran but some required measurement(s) missing/aborted mid-way | **No** numeric score | hidden until *Show unsuccessful* |
| `interrupted` | Stopped by an external stop / cancellation before completion | **No** | hidden |
| `failed` | Terminated with an observable error/failure reason | **No** (diagnostic only) | hidden |
| `unsupported` | Contract point unhostable for this config (e.g. context above capacity) — recorded as a *gap*, not scored, never rescaled | **No** | shown as gap (documented explicitly) |

A subtest with no numeric score is rendered *NOT SCORED* and its telemetry still shows so the
result remains inspectable as diagnostic evidence.

### 5.2 Structured termination reasons (record where observable)

`termination_reason` is an object, present whenever a run/subtest did **not** complete cleanly:

```jsonc
{
  "kind":            "user_cancel" | "timeout" | "backend_failure"
                  | "system_ram_exhaustion" | "vram_exhaustion"
                  | "context_limit_overflow" | "runtime_error" | null,
  "observable":      true/false,          // whether the cause was observed vs inferred
  "detail":          string|null,         // human-safe message; never a stack trace/secret
  "observed_at":     ISO-8601 UTC|null    // when observed, if known
}
```

Rules: record `kind` only when **observable**; never fabricate a cause. `resource_utilisation_alone`
is **not** a termination reason (§7). When the run completed cleanly, `termination_reason` is `null`.

### 5.3 Per-subtest evidence fields (Speed family — canonical example)

| Field | Type | Null rule | Notes |
|-------|------|-----------|-------|
| `subtest_key` | string | never null | Fixed bucket label, e.g. `"8K"`, `"16K"`, `"32K"` for Speed. |
| `target_context_tokens` | int>0 | never null | Canonical target of the bucket. |
| `actual_prompt_tokens` | int≥0 | nullable | Authoritative runtime input-token count; recorded, not assumed. |
| `ttft_seconds` | number≥0 | nullable | Time to first token (cold full-prefill sample for Speed). |
| `prefill_tokens_per_second` | number | nullable | Derived only from real prompt-tokens / TTFT when both > 0; else null. |
| `generation_tokens_per_second` | number | nullable | Sustained decode throughput. |
| `reasoning_throughput_tokens_per_second` | number | nullable | Reasoning/debug token throughput **where independently observable** (§10). Never merged into generation. |
| `completion_tokens` | int≥0 | nullable | Output tokens generated. |
| `wall_time_seconds` | number≥0 | nullable | Single-request wall time. |
| `benchmark_duration_seconds` | number≥0 | nullable | Invocation-level elapsed across all sub-requests (distinct from per-subtest wall time). |
| `status` | enum (§5.1) | never null | |
| `termination_reason` | object (§5.2) | nullable | |
| `score` | number/null | **never a fabricated 0** | Numeric, present **only** when the subtest is valid & eligible; otherwise null. |
| `telemetry` | object (§11) | never null (may be empty subset) | Per-subtest resource + runtime telemetry. |

### 5.4 Preserving partial evidence

When a complete run does not finish, **successful subtests are preserved independently** and
retained with full provenance; incomplete ones keep their `partial`/`interrupted` status and
telemetry so nothing is lost. An aggregate over the run reports only its valid subtests.

---

## 6. Scoring eligibility

### 6.1 Composite "Overall Solo Bench" score

The **Overall** score of a model is a composite across every benchmark **dimension** that has
eligible evidence for that model:

```
overall = COMPOSITE( eligible_dimensions )
eligible_dimensions = { d in {speed, agentic, context_degradation, intelligence}
                        : at least one valid, eligible run exists for the model }
```

- Each dimension contributes its own normalised signal; a dimension with **no** eligible runs
  is *dropped* from the composite — it never contributes zero and never dilutes others.
- The composite method (weights / combination) is defined per benchmark family in that family's
  scoring contract; this data contract only guarantees the **eligibility rule** above so no
  missing dimension is silently scored as zero.
- A model with zero eligible runs has `overall = null` and is labelled *no eligible results*.

### 6.2 Eligibility predicate (per subtest / run)

A subtest contributes to scoring **iff** all hold:

1. `run_type == "benchmark"` (never a diagnostic run),
2. `status ∈ {"completed", "valid"}`,
3. every field the dimension's score *requires* is present and non-null,
4. its parent configuration is `"canonical"` (§3).

Otherwise the subtest yields **no score**. This is the single gate that keeps partial/failed/
diagnostic evidence out of all aggregates while leaving it inspectable as diagnostics.

### 6.3 Utilisation is not failure

Resource utilisation alone (CPU/GPU/RAM % or bytes used) **does not** invalidate a completed
subtest and **is not** a termination reason. A subtest that finishes with expected measurements
remains `completed`/eligible even at high utilisation; exhaustion only becomes a reason when it
actually aborts measurement (`system_ram_exhaustion` / `vram_exhaustion`).

---

## 7. Tab membership & family scoring scope

| Tab | `benchmark_family` | Scored now? | Notes |
|-----|--------------------|-------------|-------|
| Overall | composite | — | Aggregates eligible dimensions (§6). |
| Speed | `speed` | Yes | Cold/warm contract, fixed buckets (§12 repeatability). |
| Agentic | `agentic` | Per family contract | e.g. Workflow-style deterministic correctness; valid subtests per suite/check. |
| Context degradation | `context_degradation` | Per family contract | Unsupported points shown as **gaps**, never rescaled to hide them. |
| Intelligence | `intelligence` | **Not yet** | Tab + scoring appear only after the Intelligence benchmark & its scoring contract are implemented (§FUTURE). |

A tab exists in navigation only once its family has a defined scoring contract; until then it
is absent, not shown empty.

---

## 8. Diagnostic runs

`run_type == "diagnostic"` marks evidence collected for inspection **without** scoring intent:

- Never contributes to any score or ranking (§6), regardless of `status`.
- Every export and every detachable section heading from a diagnostic run must visibly carry the
  marker **`DIAGNOSTIC RUN — NOT SCORED`**, including headings where content could be detached
  from the report body. This holds throughout the artifact, not just on the cover.
- Still carries full Run IDs + benchmark/version provenance (§4).
- Preserved exactly like other unsuccessful evidence (never discarded, hidden behind the toggle).

---

## 9. Export & provenance allow-list

Every export (Markdown / JPEG / PDF — all derived from the same canonical result data) carries:

- **Run IDs** for the run and its subtests, and
- **benchmark/version provenance** (`benchmark_family`, `run_type`, `benchmark_version`, `schema_version`).

### 9.1 Exported metadata allow-list (permitted by default)

| Category | Fields |
|----------|--------|
| Hardware (benchmark characteristics) | `cpu_model`, `installed_ram_bytes`/RAM total, `gpu_model`, `total_vram_bytes` |
| Benchmark identity | model family/version, configuration fingerprint, quantization, context points, benchmark family + version, run/subtest IDs, timestamps, scores & telemetry, termination reason (safe detail) |
| Environment labels | `hardware_label` (user-provided label only), `execution_environment`, `connection_type` |

### 9.2 Forbidden exports (never exported — not even null/empty strings that could identify the host)

- username, hostname, Windows/user account names
- MAC address, IP address, any network endpoint beyond the loopback-resolved backend URL provenance already listed
- Windows/product keys, credentials, API keys / tokens
- unrelated environment variables (full env dump), session/cookie data
- any other host/device identifier not in §9.1

Exports are generated against this allow-list explicitly; a new field may only be added by
extending §9.1, never by pulling from the raw run record by default. This is enforced by the
export builder (never by omission at each call site). See `docs/SECURITY.md` for the local-
runtime boundary.

---

## 10. Speed telemetry resolution contract (precondition for speed time-series)

> Evidence-grounded finding — see also the dedicated findings doc. LM Studio's native v1 API
> (`GET /api/v1/models`, `POST /api/v1/chat`) exposes **aggregate per-completion stats only**:
> `tokens_per_second`, `time_to_first_token_seconds`, `input_tokens`, `total_output_tokens`,
> `model_load_time_seconds`. There is **no** streaming token stream and **no** per-token or
> 1 Hz time-series in the current `stream=False` path. Consequences:

- Generation throughput is a single scalar per completion (already persisted), not a series.
- Token-arrival timing with finer resolution would require client-side instrumentation that the
  native API does not provide under `stream=False`; it cannot be assumed at 1 Hz without evidence.
- Directly observable prompt-processing measurements are: `input_tokens` and `time_to_first_token_seconds`
  (⇒ cold full-prefill TTFT). Everything finer than per-completion is **not** currently capturable
  and must be recorded as absent rather than synthesised.
- Reasoning/debug token throughput (`reasoning_output_tokens`) is observable only when the backend
  stats expose it; record null otherwise, never merged into generation throughput.

Any time-series persistence for Speed (planned) must first confirm what the live backend actually
emits before changing this contract (§Speed telemetry investigation task).

---

## 11. Telemetry record

Per-subtest / per-run resource + runtime telemetry. All byte fields nullable; absent ⇒ not measured.

| Field | Type | Null rule | Notes |
|-------|------|-----------|-------|
| `cpu_model` | string | nullable | Static identity (§3). |
| `cpu_logical_cores` / `cpu_physical_cores` | int | nullable | |
| `installed_ram_bytes` | int | nullable | Total; static. |
| `gpu_model` | string | nullable | Static identity (§3). |
| `total_vram_bytes` | int | nullable | Total VRAM (export-allow-listed, §9). |
| `system_ram_used_start/peak/end_bytes` | int | nullable | Utilisation over the run — not a failure signal alone. |
| `process_rss_start/peak/end_bytes` | int | nullable | Current-process resident memory. |
| `vram_used_start/peak/end_bytes` | int | nullable | VRAM in use. |
| `cpu_util_avg_pct` / `cpu_util_peak_pct` | number 0–100 | nullable | |
| `gpu_util_avg_pct` / `gpu_util_peak_pct` | number 0–100 | nullable | |
| `telemetry_sample_count` | int≥0 | nullable | **Zero is meaningful** (sampled, nothing captured) vs **null** (not sampled at all). Distinct states. |
| `os_platform` / `os_version` / `nvidia_driver_version` / `python_version` | string | nullable | Environment provenance; export-allow-listed only in safe subsets. |

---

## 12. UI-contract surface (what the contract guarantees to the presentation layer)

The UI renders, never recomputes:

- **Ranking** = one headline card per `(model_family, model_version)` with `overall` (+ per-dimension
  signals), canonical/incomplete label, and a drill-down into its configurations.
- **Configuration drill-down** lists every distinct configuration (quantisation + settings) of the
  model and all its runs.
- **Run/evidence view** shows each subtest: status badge, valid scores, absent-as-*N/A* telemetry,
  termination reason, Run/subtest IDs, benchmark/version provenance.
- **Overall / Speed / Agentic / Context degradation** tabs; **Intelligence** tab added only when its
  family has a scoring contract.
- **Unsuccessful handling**: partial/failed/interrupted/diagnostic runs hidden behind *Show
  unsuccessful*; `unsupported` shown explicitly as a gap.
- **Compare & scorecard**: side-by-side deltas over the same canonical evidence; scorecard is a
  non-intrusive modal (single-result or model-vs-model); exports derive from this data with the §9
  allow-list and the `DIAGNOSTIC RUN — NOT SCORED` marker on diagnostic artifacts.

---

## 13. Mapping to existing persistence (migration notes)

The canonical contract above is a **view/schema contract**, not an immediate schema rewrite. The
existing layers already carry most fields:

- **Standard Speed** rows in `ResultsStore` (`results.py`) already store identity, config fingerprint/
  classification, per-point metrics and static telemetry. Map them to L2 subtests keyed by
  `(run_id, context_point)`; `speed_point_status ∈ {completed, unsupported, failed}` ⇒ §5.1 status.
- **Workflow** artifacts (`v2_quality_artifact.py`) already implement run → suite → check with
  fingerprint/classification and atomic versioned persistence — the closest existing L0→L2 pattern.
  Map `suite`/`check` to subtests; pass/fail against fixed denominators ⇒ validity + eligibility.

Any **new** column/field required by this contract is added **additively** (ALTER / schema_version),
never by mutating existing rows, preserving the byte-for-byte data-preservation rule already in place
for `benchmark_results.csv` / `.db`.

---

## 14. Open questions resolved by later tasks

- §10 telemetry: confirm live backend emission before any speed time-series (§Speed telemetry task).
- §6 composite weights: each benchmark family defines its own; this contract fixes only eligibility.
- §12 UI ordering: implementation proceeds shell → ranking → drill-down → evidence → per-family tabs →
  compare/scorecard → diagnostic views, **only after** this contract and the telemetry investigation
  are complete (per design-sequence).
