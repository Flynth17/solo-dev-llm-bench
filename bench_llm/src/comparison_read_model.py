"""Comparison read model for RM-26-AA-0013 (ST-002).

A pure *composition* layer that aligns two benchmark subjects across the three
independent benchmark families (Speed, Workflow/Quality, Context) and projects a
single, stable comparison shape for the frontend to render.

Design invariants (see ``docs/architecture.md`` §3/§5/§9 and ST-001's contract
report):

* **Composition only.** This module never touches SQLite or CSV directly and never
  recomputes any benchmark score, dimension, configuration fingerprint or run
  identity. It receives *already-resolved candidate evidence* from the route layer
  (exactly like :func:`src.ranking_read_model.build_ranking` composes Speed +
  Quality views). The route is responsible for enumerating persisted runs and
  loading each artifact once.

* **Honest cross-family identity.** Speed, Workflow and Context each own an
  independent configuration fingerprint / identity space (ST-001). Cross-family
  fingerprints are **not** comparable for equality and cross-family alignment is
  only reliable at *base model identity*. This module therefore never asserts that
  one Speed run, one Workflow run and one Context run share a single physical
  configuration. It aligns them only by base-model identity and reports each
  family's distinguishability honestly.

* **No false equivalence.** When two subjects are the *same base model at different
  configurations* (e.g. ``Ornith Q4_K_M`` vs ``Ornith Q5_K_M``), a benchmark
  family that cannot independently encode the distinguishing configuration
  (Workflow's fingerprint deliberately omits quantization) is reported as
  :data:`AMBIGUOUS` for **both** subjects -- never silently attributed to one side
  and never duplicated into both columns.

* **State semantics are preserved verbatim.** ``null`` stays null, unsupported stays
  unsupported, missing stays missing, failed stays failed. Unsupported is *not*
  treated as a failure and a missing dimension is *not* coerced to an error.

* **Neutral run selection.** When several authoritative runs exist for one subject +
  family, the most recent *terminal* run is selected (Workflow/Context order by the
  artifact's ``generated_at``; Speed has no ordering field so it falls back to a
  deterministic ``run_id`` tie-break). A newer *failed* terminal run is **not**
  silently replaced by an older successful one.

* **Metric-version compatibility (Speed, ST-004).** Only corrected-baseline evidence
  (metric version 2 or 3) is presented as comparable values. Current-metric runs are
  preferred during selection; a legacy (pre-v2) run is surfaced -- and reported as
  :data:`LEGACY` -- only when no corrected evidence exists for the subject, never
  silently compared against current values. A current-metric run whose shape the
  single-run read model cannot normalize reports :data:`UNAVAILABLE`. Available Speed
  projections additionally carry the normalized canonical points (8K/16K/32K), the run's
  metric version and a presentation-only average generation aggregate (mean of present,
  valid canonical-point values; missing / unsupported excluded, never zeroed) -- not a
  score and never a winner input.

The public surface is intentionally small:

* :func:`build_subject_catalogue` -- distinct comparison subjects from committed
  evidence (used by the catalogue endpoint).
* :func:`resolve_comparison` -- validate two subject keys and project the
  per-dimension comparison shape (used by the comparison endpoint).
"""

from __future__ import annotations

from typing import Any, Optional

# ---------------------------------------------------------------------------
# Comparison states (one stable vocabulary for the frontend). These are the ONLY
# strings this module emits as a dimension state -- keep them stable.
# ---------------------------------------------------------------------------

AVAILABLE = "available"
IN_PROGRESS = "in_progress"
AMBIGUOUS = "ambiguous"
MISSING = "missing"
FAILED = "failed"
UNSUPPORTED = "unsupported"
UNAVAILABLE = "unavailable"
# Speed evidence measured before the corrected metric baseline (pre-metric-v2, i.e. a
# cached-TTFT artifact). It is real persisted evidence -- but its values are NOT
# comparable with current-metric values and are never rendered as numbers alongside them.
LEGACY = "legacy"

# Families in canonical comparison order.
FAMILIES = ("speed", "workflow", "context")

# Canonical Standard Speed context points. Comparison aligns by these labels -- never by
# array index, and a point absent from one subject stays a gap on that side only.
CANONICAL_SPEED_POINTS = ("8K", "16K", "32K")

# Metric versions carrying the corrected measurement baseline: 2 = corrected prefill
# semantics, 3 = calibrated targets. Missing/None or < 2 is a legacy cached-TTFT artifact.
CURRENT_METRIC_VERSIONS = frozenset({2, 3})

# A run is *terminal* (a finished, selectable result) unless its status is one of
# these non-finished states. Everything else -- completed, failed, unsupported,
# invalid, incomplete -- is terminal so a newer *failed* run is surfaced as FAILED
# rather than silently falling back to an older success.
NON_TERMINAL_STATES = frozenset(
    {"running", "in_progress", "pending", "interrupted", "partial", "queued"}
)

# Benchmark families whose authoritative identity encodes the quantization /
# configuration axis that distinguishes two same-model configurations. Workflow's
# fingerprint deliberately omits quantization, so it CANNOT distinguish same-model
# configs and is excluded here -- this is what drives the AMBIGUOUS state.
DISTINGUISHING_FAMILIES = frozenset({"speed", "context"})

# Evidence traceability: documented stable deep-link routes (architecture §9).
DEEP_LINK_PATTERNS = {
    "speed": "/speed/results/{run_id}",
    "workflow": "/v2/results/{run_id}",
    "context": "/context/results/{run_id}",
}

# Identity columns that together pin a single Speed run's configuration. Speed does
# not persist a computed fingerprint, so these columns are the authoritative
# distinguisher between two Speed runs of the same base model. Order is significant.
SPEED_IDENTITY_KEYS = (
    "model_key",
    "model_quantization",
    "loaded_context",
    "reasoning_mode",
    "flash_attention",
    "offload_kv_cache_to_gpu",
    "eval_batch_size",
    "physical_batch_size",
    "parallel",
    "num_experts",
    "speculative_draft_model",
    "kv_cache_k_quantization",
    "kv_cache_v_quantization",
    "hardware_label",
)


# ---------------------------------------------------------------------------
# Candidate accessors -- every candidate the route passes in is one of:
#   {"family": "speed", "run_id", "rows": [...], "classification", "status"}
#   {"family": "workflow"|"context", "run_id", "generated_at", "view": {...}}
# The helpers below normalise both shapes so the rest of the module never cares.
# ---------------------------------------------------------------------------


def _as_view(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return the identity-bearing mapping for a candidate (row or view).

    Speed carries its identity on the point row; Context exposes a flat view. The
    Workflow (v2_quality) read model *nests* identity under ``view['run']`` -- this
    unwraps that layer so identity fields are read uniformly across families. Without
    it, Workflow base-model / fingerprint resolution falls back to empty strings and
    real Workflow evidence is misreported as missing/ambiguous.
    """
    if candidate.get("family") == "speed":
        rows = candidate.get("rows") or []
        return rows[0] if rows else {}
    view = candidate.get("view") or {}
    run = view.get("run")
    if isinstance(run, dict) and (run.get("model_identifier") or run.get("configuration_fingerprint")):
        return run
    return view


def _base_model(candidate: dict[str, Any]) -> str:
    """Authoritative base-model identity for a candidate (never a display name)."""
    view = _as_view(candidate)
    return (view.get("model_key") or view.get("model_identifier") or "").strip()


def _identity_signature(candidate: dict[str, Any]) -> tuple[Any, ...]:
    """A stable distinguisher for one run within its family.

    * Speed: the tuple of identity columns (no stored fingerprint exists).
    * Workflow/Context: the persisted ``configuration_fingerprint``.

    Two runs of the same base model are *distinguishable* iff their signatures
    differ. Workflow's fingerprint omits quantization, so two same-model configs
    share a signature and are therefore indistinguishable here.
    """
    if candidate.get("family") == "speed":
        view = _as_view(candidate)
        return tuple(view.get(k) for k in SPEED_IDENTITY_KEYS)
    return (_as_view(candidate).get("configuration_fingerprint") or "",)


def _is_terminal(candidate: dict[str, Any]) -> bool:
    # An explicit top-level ``status`` wins when present; otherwise fall back to the
    # family's ``classification`` (Speed stores it on the candidate, Workflow/Context
    # store it on the projected view). No status at all => treat as terminal.
    status = str(candidate.get("status") or "").lower()
    if not status:
        if candidate.get("family") == "speed":
            status = str(candidate.get("classification") or "").lower()
        else:
            status = str(_as_view(candidate).get("classification") or "").lower()
    if not status:
        return True
    return status not in NON_TERMINAL_STATES


def _short_signature(signature: tuple[Any, ...]) -> Optional[str]:
    """A stable short identifier for API display; run_id remains the trace key."""
    if signature is None:
        return None
    import hashlib

    blob = "|".join("" if v is None else str(v) for v in signature)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16] or None


# ---------------------------------------------------------------------------
# Neutral run selection (Phase 4 / Phase 5).
# ---------------------------------------------------------------------------


def _is_comparable_speed(candidate: dict[str, Any]) -> bool:
    """True when a Speed candidate carries presentable current-metric evidence.

    Both conditions are required: the route attached normalized canonical points AND the
    run's metric version sits on the corrected baseline (2 or 3). A legacy run is never
    silently substituted for corrected evidence, and a run whose shape the single-run read
    model cannot normalize has no values to present -- it is reported as an honest state,
    not compared.
    """
    if not candidate.get("points"):
        return False
    return candidate.get("metric_version") in CURRENT_METRIC_VERSIONS


def _select_authoritative(
    candidates: list[dict[str, Any]], family: str
) -> Optional[dict[str, Any]]:
    """Pick the authoritative run for one subject + family.

    * Workflow/Context order by ``generated_at`` (authoritative recency); latest
      terminal run wins.
    * Speed has no ordering field, so it falls back to a deterministic
      ``run_id`` lexicographic tie-break (neutral -- never score-based).
    * Speed metric-version compatibility: current-metric evidence is preferred; a legacy
      run is only surfaced when NO corrected evidence exists for the subject (it then
      reports as LEGACY, never silently compared). Within whichever pool is chosen the
      neutral deterministic rule applies unchanged.

    Returns the chosen candidate, or ``None`` when only non-terminal runs exist.
    """
    terminal = [c for c in candidates if _is_terminal(c)]
    pool = terminal if terminal else list(candidates)
    if not pool:
        return None
    if family in ("workflow", "context"):
        ordered = sorted(pool, key=lambda c: (c.get("generated_at") or ""), reverse=True)
        return ordered[0]
    # Speed: prefer current-metric evidence; deterministic run_id tie-break within the
    # chosen pool (no timestamp exists anywhere -- never a score-based pick).
    comparable = [c for c in pool if _is_comparable_speed(c)]
    chosen = comparable if comparable else list(pool)
    return sorted(chosen, key=lambda c: (c.get("run_id") or ""))[0]


_SPEED_POINT_FIELDS = (
    "label",
    "target_context_tokens",
    "actual_prompt_tokens",
    "ttft_seconds",
    "prefill_tokens_per_second",
    "generation_tokens_per_second",
    "completion_tokens",
    "wall_time_seconds",
    "status",
)


def project_speed_point(point: dict[str, Any]) -> dict[str, Any]:
    """Project one normalized Speed point verbatim (authoritative read-model values).

    Values are preserved as-is -- missing telemetry stays None so the UI renders an honest
    gap; nothing is coerced to zero. Shared by the route layer and the projection so both
    surfaces expose the identical point shape.
    """
    return {k: point.get(k) for k in _SPEED_POINT_FIELDS}


def average_generation_tps(points: Optional[list[dict[str, Any]]]) -> Optional[float]:
    """Presentation aggregate only -- NOT a benchmark score and never a winner input.

    Arithmetic mean of PRESENT + VALID canonical-point (8K/16K/32K) generation values,
    mirroring the accepted compact Speed Results table contract: missing / unsupported /
    invalid points are excluded (never zeroed); no valid value -> None (rendered N/A).
    Alignment is by canonical label, never array index.
    """
    values: list[float] = []
    for point in points or []:
        if str(point.get("label")) not in CANONICAL_SPEED_POINTS:
            continue
        # Only completed points are valid measurements; unsupported / failed / other
        # non-completed statuses are excluded (never zeroed, never blended).
        status = str(point.get("status") or "").strip().lower()
        if status and status != "completed":
            continue
        gen = point.get("generation_tokens_per_second")
        if isinstance(gen, bool) or not isinstance(gen, (int, float)):
            continue
        g = float(gen)
        if g != g or g in (float("inf"), float("-inf")) or g <= 0:
            continue
        values.append(g)
    return sum(values) / len(values) if values else None


def _state_for_run(candidate: dict[str, Any], family: str) -> dict[str, Any]:
    """Project one authoritative run into a dimension state + traceability."""
    run_id = candidate.get("run_id")
    deep_link = DEEP_LINK_PATTERNS.get(family, "").format(run_id=run_id)
    # Classification source mirrors ``_is_terminal``: explicit status wins, else the
    # family classification (Speed on the candidate, Workflow/Context on the view).
    if candidate.get("status"):
        classification = str(candidate.get("status")).lower()
    elif candidate.get("family") == "speed":
        classification = str(candidate.get("classification") or "").lower()
    else:
        classification = str(_as_view(candidate).get("classification") or "").lower()
    # State semantics are preserved verbatim: unsupported stays unsupported (it is
    # not a failure), and only an explicit failure maps to FAILED.
    if "unsupported" in classification:
        state = UNSUPPORTED
    else:
        is_failed = "fail" in classification or candidate.get("status") == "failed"
        state = FAILED if is_failed else AVAILABLE
    projection: dict[str, Any] = {
        "state": state,
        "run_id": run_id,
        "deep_link": deep_link,
        "classification": candidate.get("classification"),
        # Authoritative per-family configuration for the chosen run (ST-003 identity
        # header). Never fabricated: Speed carries material identity columns, Context
        # exposes quant/capacity/baseline, and Workflow deliberately does NOT record
        # quantization -- so its config reports quantization as absent rather than
        # copying another family's value.
        "config": _config_for(candidate, family),
    }
    if family == "speed" and state == AVAILABLE:
        # Metric-version compatibility (ST-004): only corrected-baseline evidence is
        # presented as comparable values. A legacy run is never silently compared -- it
        # reports LEGACY with its traceability so the evidence stays inspectable; a
        # current-metric run whose shape the single-run read model cannot normalize has
        # no representable values and reports UNAVAILABLE (never fabricated numbers).
        if _is_comparable_speed(candidate):
            points = [project_speed_point(p) for p in candidate.get("points") or []]
            projection["metric_version"] = candidate.get("metric_version")
            projection["points"] = points
            # Presentation aggregate only (mean of present valid canonical points);
            # None -> N/A. Never a score, never a winner input.
            projection["average_generation_tps"] = average_generation_tps(points)
        elif (candidate.get("metric_version") or 0) < 2:
            projection["state"] = LEGACY
            projection["metric_version"] = candidate.get("metric_version")
        else:
            projection["state"] = UNAVAILABLE
            projection["metric_version"] = candidate.get("metric_version")
    return projection


def _config_for(candidate: dict[str, Any], family: str) -> dict[str, Any]:
    """Authoritative configuration fields for one resolved run, per family."""
    if family == "speed":
        row = (candidate.get("rows") or [{}])[0]
        return {
            "quantization": row.get("model_quantization") or None,
            "loaded_context_tokens": row.get("loaded_context"),
            "hardware_label": row.get("hardware_label") or None,
            # Speed-specific evidence identity (ST-004): environment provenance, never
            # copied from another family.
            "execution_environment": row.get("execution_environment") or None,
            "connection_type": row.get("connection_type") or None,
        }
    view = _as_view(candidate)
    if family == "context":
        return {
            "quantization": view.get("model_quantization") or None,
            "effective_capacity": view.get("effective_capacity"),
            "baseline_context_point": view.get("baseline_context_point"),
            "configuration_fingerprint": view.get("configuration_fingerprint") or None,
        }
    # Workflow: fingerprint only; quantization is intentionally not recorded.
    return {
        "quantization": None,
        "configuration_fingerprint": view.get("configuration_fingerprint") or None,
    }


def _resolve_single(
    candidates: list[dict[str, Any]], family: str
) -> dict[str, Any]:
    """Resolve one subject's dimension when it is the *only* base model in play.

    Used for different-base-model comparisons (clean per-model resolution).
    """
    if not candidates:
        return {"state": MISSING}
    terminal = [c for c in candidates if _is_terminal(c)]
    if not terminal:
        # Only non-terminal runs exist -> the dimension is still being produced.
        return {"state": IN_PROGRESS}
    return _state_for_run(_select_authoritative(terminal, family), family)


# ---------------------------------------------------------------------------
# Subject catalogue (Phase 3).
# ---------------------------------------------------------------------------


def build_subject_catalogue(
    speed_runs: list[dict[str, Any]],
    workflow_candidates: list[dict[str, Any]],
    context_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """List distinct comparison subjects from committed authoritative evidence.

    Subjects are keyed by *base model identity* plus a family fingerprint /
    signature that distinguishes same-model configurations -- never by display
    name. A subject carries its available dimensions and per-family distinguishable
    configuration so the frontend can present selectable, route-able entries.

    Grouping key per base model:

    * Speed evidence present -> cluster by distinct Speed identity signature
      (encodes quantization) so ``Q4`` and ``Q5`` become separate subjects.
    * else Context evidence present -> cluster by distinct Context fingerprint
      (also encodes quantization).
    * else -> one subject per base model (Workflow-only evidence cannot encode
      quantization, so same-model configs legitimately collapse here).
    """
    families = {
        "speed": speed_runs,
        "workflow": workflow_candidates,
        "context": context_candidates,
    }

    # base_model -> family -> set of identity signatures (distinct configs).
    sigs: dict[str, dict[str, set[tuple[Any, ...]]]] = {}
    counts: dict[str, dict[str, int]] = {}
    display: dict[str, str] = {}
    for family in FAMILIES:
        for cand in families[family]:
            base = _base_model(cand)
            if not base:
                continue
            sigs.setdefault(base, {}).setdefault(family, set()).add(
                _identity_signature(cand)
            )
            counts.setdefault(base, {})[family] = (
                counts.get(base, {}).get(family, 0) + 1
            )
            view = _as_view(cand)
            label = view.get("model_display_name") or base
            if label and base not in display:
                display[base] = str(label)

    subjects: list[dict[str, Any]] = []
    for base in sorted(sigs):
        per_family = sigs[base]
        has_speed = bool(per_family.get("speed"))
        has_context = bool(per_family.get("context"))
        rep_family = "speed" if has_speed else ("context" if has_context else "base")

        # Distinct subject clusters for this base model.
        rep_sigs = sorted(
            (s for s in per_family.get(rep_family, set()) if s is not None),
            key=_short_signature,
        )
        cluster_sigs = rep_sigs if rep_sigs else [None]

        for sig in cluster_sigs:
            rep_sig_str = _short_signature(sig) if sig is not None else None
            available = [f for f in FAMILIES if counts.get(base, {}).get(f)]
            config_fingerprints = {
                f: (
                    _short_signature(next(iter(per_family.get(f, set()))))
                    if len(per_family.get(f, set())) == 1
                    and any(s is not None for s in per_family.get(f, set()))
                    else None
                )
                for f in FAMILIES
            }
            subjects.append(
                {
                    "subject_key": _subject_key(base, rep_family, sig),
                    "model_version": base,
                    "architecture": "",  # not persisted per-run; best-effort unavailable
                    "display_name": display.get(base, base),
                    "representative_family": rep_family,
                    "available_dimensions": available,
                    "config_fingerprints": config_fingerprints,
                    "evidence_counts": {f: counts.get(base, {}).get(f, 0) for f in FAMILIES},
                }
            )

    return {"subjects": subjects}


def _subject_key(base_model: str, rep_family: str, sig: tuple[Any, ...]) -> str:
    """Deterministic routing key. Never derived from a display name."""
    short = _short_signature(sig) if sig is not None else ""
    return f"{base_model}|{rep_family}:{short}"


# ---------------------------------------------------------------------------
# Two-subject resolution (Phase 7 / Phase 12).
# ---------------------------------------------------------------------------


def _parse_subject_key(subject_key: str) -> dict[str, str]:
    """Split ``base|family:short_sig`` into its parts (``_sig`` is the short hash)."""
    if "|" not in subject_key:
        raise ValueError(f"malformed subject_key {subject_key!r}")
    base, rest = subject_key.split("|", 1)
    if ":" in rest:
        rep_family, sig = rest.split(":", 1)
    else:
        rep_family, sig = rest, ""
    return {"base_model": base, "representative_family": rep_family, "_sig": sig}


def resolve_comparison(
    subject_a_key: str,
    subject_b_key: str,
    speed_runs: list[dict[str, Any]],
    workflow_candidates: list[dict[str, Any]],
    context_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate two subjects and project the per-dimension comparison shape.

    Rules (Phase 7 / Phase 12):

    * Identical subject keys are rejected -- you cannot compare a subject with
      itself.
    * Different base models resolve each dimension independently by base model.
    * Same base model at different configurations: only the *representative*
      family (the one whose signature defines the subject key) is pinned and
      reported AVAILABLE for both; every other family -- which cannot
      authoritatively attribute a run to one specific configuration -- is reported
      AMBIGUOUS for both. This is the honest handling of Workflow's quantization-
      blind identity and prevents duplicating one shared run into both columns.
    * State semantics are preserved: missing evidence -> MISSING, only non-terminal
      runs -> IN_PROGRESS, a failed authoritative run -> FAILED (never silently
      replaced by an older success).
    """
    if subject_a_key == subject_b_key:
        raise ValueError(
            "cannot compare a subject with itself; select two distinct subjects"
        )

    a = _parse_subject_key(subject_a_key)
    b = _parse_subject_key(subject_b_key)
    same_model = a["base_model"] == b["base_model"]
    rep_family = a["representative_family"] if same_model else None
    a_short, b_short = a["_sig"], b["_sig"]
    families = {
        "speed": speed_runs,
        "workflow": workflow_candidates,
        "context": context_candidates,
    }

    dimensions: dict[str, dict[str, Any]] = {}

    if same_model:
        # Same base model at different configurations. Only the representative family
        # (whose identity signature defines the subject key) can authoritatively
        # attribute a run to one specific configuration; every other family -- which
        # cannot distinguish the two configs -- is AMBIGUOUS for both and never has a
        # single shared run duplicated into both columns.
        all_cands = {f: [c for c in families[f] if _base_model(c) == a["base_model"]] for f in FAMILIES}
        for family in FAMILIES:
            if family != rep_family or family not in DISTINGUISHING_FAMILIES:
                dimensions[family] = {
                    "subject_a": {"state": AMBIGUOUS},
                    "subject_b": {"state": AMBIGUOUS},
                }
                continue
            a_pinned = [c for c in all_cands[family] if _short_signature(_identity_signature(c)) == a_short]
            b_pinned = [c for c in all_cands[family] if _short_signature(_identity_signature(c)) == b_short]
            dimensions[family] = {
                "subject_a": _resolve_single(a_pinned, family),
                "subject_b": _resolve_single(b_pinned, family),
            }
    else:
        # Different base models: each dimension resolves independently by base model.
        for family in FAMILIES:
            a_cands = [c for c in families[family] if _base_model(c) == a["base_model"]]
            b_cands = [c for c in families[family] if _base_model(c) == b["base_model"]]
            dimensions[family] = {
                "subject_a": _resolve_single(a_cands, family),
                "subject_b": _resolve_single(b_cands, family),
            }

    return {
        "comparison": {
            "subject_a": {
                "subject_key": subject_a_key,
                "model_version": a["base_model"],
            },
            "subject_b": {
                "subject_key": subject_b_key,
                "model_version": b["base_model"],
            },
            "same_model_configuration": same_model,
            "dimensions": dimensions,
        }
    }
