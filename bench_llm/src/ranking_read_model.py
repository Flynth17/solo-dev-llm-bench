"""Canonical ranking / aggregation read model (Results UI prerequisite).

A pure, **read-only** adapter over *already-persisted* Standard Speed and V2
Quality evidence. It builds the L0/L1/L2 representation that the future Results
UI asks for:

    L0  model family + version      one headline result per identity
    L1  tested configuration / quantisation   children of the model
    L2  run / evidence            individual runs with validity + provenance

It performs **no benchmark logic** and **invents no scores**:

* it never executes a benchmark, never loads an LM Studio model, never touches
  SQLite or CSV directly -- it only reads already-persisted rows/documents that
  the caller supplies.
* it **reuses** verified helpers rather than reimplementing them:
    - :func:`src.results.compute_configuration_fingerprint` /
      :func:`src.results.classify_run_for_result` (L1 identity + eligibility),
    - :func:`src.v2_speed_read_model.load_speed_run_by_id` (canonical single-run
      Speed point structure, legacy compatibility and per-point scores for free),
    - the validated V2 Quality read-model view produced by
      :func:`src.v2_quality_read_model.build_read_model`.

**No scoring formula is implemented.** The composite *Overall Solo Bench* score
is deliberately **unavailable**: the aggregation exposes component scores,
eligibility, available/missing dimensions and provenance, plus a reserved
`overall_solo_bench_score` placeholder so the future UI can slot in an approved
composite without schema churn. An Overall Solo Bench score must remain absent
until its scoring contract is separately approved (results-data-contract §6).

**No new benchmark families are invented.** Only evidence supported by current
verified data is exposed: Speed (Standard Speed) and Agentic (V2 Quality,
Workflow-style deterministic correctness). Context-degradation and Intelligence
families have no implemented benchmark or scoring contract; they are represented
as *unavailable* with a null score -- **never** as an inferred/zeroed number.

## Eligibility / validity (results-data-contract §6)

A run contributes to a component aggregate **iff**:

1. it is a *benchmark* run (all persisted Speed rows and V2 Quality runs are),
2. its status is ``completed`` at the point/run level,
3. every required field for the dimension's score is present and non-null,
4. its parent configuration is ``canonical`` (:func:`classify_run_for_result`).

Partial / failed / interrupted / unsupported / diagnostic evidence never becomes
a numeric zero; it stays inspectable as diagnostic evidence with an explicit
status while contributing no value to aggregates (contract §0.1). Unavailable
dimensions are rendered *N/A* (``None``), not ``0``.

## Identity rules (results-data-contract §2/§3)

* **L0 identity** = ``(model_version, architecture)`` where ``model_version`` is
  the stable key (``model_key`` / ``model_identifier``). Different model versions
  therefore remain distinct; Dense and MoE are distinguished by ``architecture``.
* Configurations / quantisations are **children** of the model identity, keyed by
  ``(benchmark_family, configuration_fingerprint)`` -- never siblings of the
  model. The two families keep their own fingerprint spaces (the Speed store's
  fingerprint is a *different construction* from the V2 Quality artifact's), so
  they are **never** joined by fingerprint equality.

## Backward compatibility (contract §0.6)

The read model only **reads**; it performs no schema mutation and never rewrites
historical rows. :func:`load_speed_run_by_id` already preserves legacy
single-row Speed evidence, and :func:`classify_run_for_result` accepts any row
shape, so historical evidence remains readable through this layer unchanged.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

# Reuse verified helpers -- composition over replacement.
from src.results import (  # noqa: E402
    classify_run_for_result,
)
from src.v2_speed_read_model import (  # noqa: E402
    STANDARD_SPEED_CANONICAL_POINTS,
    SpeedReadModelIntegrityError,
    SpeedRunNotFoundError,
    load_speed_run_by_id,
)


# ---------------------------------------------------------------------------
# Canonical constants.
#
# These mirror the scoring-scope table in results-data-contract §7: only Speed
# and Agentic (V2 Quality) have an implemented benchmark + scoring contract. The
# other two families are represented as unavailable -- never scored.
# ---------------------------------------------------------------------------

DIMENSION_SPEED = "speed"
DIMENSION_AGENtic = "agentic"
DIMENSION_CONTEXT_DEGRADATION = "context_degradation"
DIMENSION_INTELLIGENCE = "intelligence"

# Dimensions that currently have an implemented benchmark + approved metric.
APPROVED_DIMENSIONS: tuple[str, ...] = (DIMENSION_SPEED, DIMENSION_AGENtic)

# Families with no implemented benchmark/scoring contract today.
UNIMPLEMENTED_DIMENSIONS: dict[str, str] = {
    DIMENSION_CONTEXT_DEGRADATION: "context_degradation benchmark is not implemented",
    DIMENSION_INTELLIGENCE: "intelligence benchmark is not implemented",
}


# ---------------------------------------------------------------------------
# Small type guards (local; mirror conventions in the read models).
# ---------------------------------------------------------------------------

def _norm_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _as_number(value: Any) -> Optional[float]:
    """Return a finite float for ``value``; ``None`` when absent / non-numeric.

    Never coerces absence to zero -- this is the whole point of "N/A not 0".
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num or num in (float("inf"), float("-inf")):
        return None
    return num


# ---------------------------------------------------------------------------
# Architecture resolution (results-data-contract §2).
#
# Dense and MoE are distinct families. Resolve authoritatively from the number of
# experts; unknown when not recorded -- but *always* keep the identity.
# ---------------------------------------------------------------------------

def resolve_architecture(num_experts: Any) -> str:
    value = num_experts
    if value is None or isinstance(value, bool):
        return "unknown"
    try:
        n = int(value)
    except (TypeError, ValueError):
        return "unknown"
    if n > 1:
        return "moe"
    if n == 1:
        return "dense"
    # Zero / negative / not recorded => unknown is a valid, honest recorded state.
    return "unknown"


# ---------------------------------------------------------------------------
# Model identity (L0).
# ---------------------------------------------------------------------------

def _model_version(row: dict) -> str:
    """Stable version identity from whichever field the evidence carries."""
    v = _norm_str(row.get("model_key")) or _norm_str(row.get("model_identifier"))
    return v or "unknown"


def _display_label(value: Optional[str]) -> Optional[str]:
    """Best-effort human label ONLY -- never used for identity or grouping.

    Per contract §2 the display name is never part of the ranking key; this exists
    so a UI can show a friendly family token when one is available. Takes the first
    whitespace-delimited token (e.g. "Ornith ..."), else falls back to ``value``.
    """
    s = _norm_str(value)
    if not s:
        return None
    tokens = s.split()
    return tokens[0] if tokens else s


# ---------------------------------------------------------------------------
# Speed evidence (L2) via the verified single-run read model.
# ---------------------------------------------------------------------------

def _speed_component(run_id: str, rows: list[dict], classification: str) -> dict[str, Any]:
    """Build the Speed component view + eligibility for one persisted run id.

    Reuses :func:`load_speed_run_by_id` so canonical point ordering, legacy
    compatibility and per-point scores are all inherited -- no re-derivation here.
    On integrity failure the run is kept as *diagnostic* evidence (no score) rather
    than collapsing the whole aggregation.
    """
    provenance = {
        "run_id": run_id,
        "configuration_fingerprint": _norm_str(
            next((r.get("configuration_fingerprint") for r in rows if r.get("configuration_fingerprint")), None)
        ),
        "benchmark_family": DIMENSION_SPEED,
    }

    try:
        view = load_speed_run_by_id(run_id, rows)
    except (SpeedRunNotFoundError, SpeedReadModelIntegrityError):
        return {
            **provenance,
            "status": "unavailable",
            "valid": False,
            "eligible": False,
            "component_score": None,
            "reason": "persisted Speed run could not be read as a valid Standard Speed result",
        }

    points = list(view.get("points") or [])
    completed_points = [p for p in points if str(p.get("status") or "").lower() == "completed"]

    generation_values = [g for g in (
        _as_number(p.get("generation_tokens_per_second")) for p in completed_points
    ) if isinstance(g, (int, float))]
    warm_mean: Optional[float] = None
    if generation_values:
        warm_mean = round(sum(generation_values) / len(generation_values), 2)

    # Cold full-prefill TTFT from the representative (cold-first) point.
    cold_ttft: Optional[float] = None
    for p in points:
        t = _as_number(p.get("ttft_seconds"))
        if isinstance(t, (int, float)):
            cold_ttft = t
            break

    # Eligibility (§6): config canonical AND at least one eligible completed point.
    eligible = classification == "canonical" and len(completed_points) > 0

    component_score: dict[str, Any] | None = None
    if eligible and warm_mean is not None:
        component_score = {
            "warm_generation_tokens_per_second": warm_mean,
            "cold_prefill_ttft_seconds": cold_ttft,
            "point_count": len(points),
            "eligible_point_count": len(completed_points),
        }

    return {
        **provenance,
        "status": view.get("status"),
        # `valid` = the run produced inspectable evidence (read succeeded). It is a
        # distinct notion from scoring eligibility.
        "valid": view.get("status") in {"completed", "partial"},
        "eligible": eligible,
        "classification": classification,
        "component_score": component_score,
        # Per-point evidence stays inspectable (diagnostics); unsupported/failed
        # points keep their status and are never coerced to a numeric score.
        "points": [
            {
                "label": p.get("label"),
                "target_context_tokens": p.get("target_context_tokens"),
                "actual_prompt_tokens": p.get("actual_prompt_tokens"),
                "ttft_seconds": p.get("ttft_seconds"),
                "prefill_tokens_per_second": p.get("prefill_tokens_per_second"),
                "generation_tokens_per_second": p.get("generation_tokens_per_second"),
                "status": p.get("status"),
                "eligible": bool(p.get("status") == "completed" and classification == "canonical"),
            }
            for p in points
        ],
    }


# ---------------------------------------------------------------------------
# V2 Quality / Agentic evidence (L2) via the validated read-model view.
# ---------------------------------------------------------------------------

def _quality_view_component(quality_view: dict[str, Any], fingerprint: Optional[str]) -> dict[str, Any]:
    """Build the Agentic component view + eligibility from a *validated* quality view.

    ``quality_view`` is expected to be the output of
    :func:`src.v2_quality_read_model.build_read_model` (i.e. already integrity-
    validated). The route layer performs that validation and error handling; this
    function only projects the verified aggregate into the component score shape.
    """
    run = quality_view.get("run") or {}
    run_id = _norm_str(run.get("run_id"))

    suites = [
        {
            "suite": s.get("suite"),
            "checks_passed": s.get("checks_passed"),
            "checks_total": s.get("checks_total"),
        }
        for s in (quality_view.get("suites") or [])
    ]

    overall_passed = run.get("checks_passed")
    overall_total = run.get("checks_total")

    classification = _norm_str(run.get("classification")) or "incomplete"
    eligible = classification == "canonical" and isinstance(overall_passed, int) \
        and not isinstance(overall_passed, bool) and isinstance(overall_total, int) \
        and not isinstance(overall_total, bool) and overall_total > 0

    fraction: Optional[float] = None
    if eligible and isinstance(overall_total, int) and overall_total > 0:
        fraction = round(float(overall_passed) / float(overall_total), 4)

    component_score: dict[str, Any] | None = None
    if eligible:
        component_score = {
            "checks_passed": overall_passed,
            "checks_total": overall_total,
            "fraction": fraction,
            "per_suite": suites,
        }

    return {
        "run_id": run_id,
        "configuration_fingerprint": fingerprint or _norm_str(run.get("configuration_fingerprint")),
        "benchmark_family": DIMENSION_AGENtic,
        "status": classification,
        "valid": eligible,
        "eligible": eligible,
        "classification": classification,
        "component_score": component_score,
    }


# ---------------------------------------------------------------------------
# Aggregation.
# ---------------------------------------------------------------------------

def _unavailable_dimension(name: str) -> dict[str, Any]:
    """A dimension with no implemented benchmark -- N/A, never a score."""
    reason = UNIMPLEMENTED_DIMENSIONS.get(name, f"{name} is not available")
    return {"status": "unavailable", "score": None, "reason": reason}


def build_ranking(
    speed_runs: Iterable[dict],
    quality_views: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical L0/L1/L2 ranking/aggregation view.

    Parameters
    ----------
    speed_runs:
        Iterable of persisted Standard Speed rows (as returned by
        ``ResultsStore.get_all()``). Only rows tagged to a standard point are
        considered; legacy/custom rows are ignored for this surface.
    quality_views:
        Optional iterable of validated V2 Quality read-model views (the output of
        :func:`src.v2_quality_read_model.build_read_model`). Each contributes one
        Agentic run/evidence entry under its model identity.

    Returns a JSON-serialisable dict -- the presentation-facing representation the
    future UI consumes; it knows nothing about SQLite tables or CSV columns.
    """
    speed_rows = [r for r in (speed_runs or []) if isinstance(r, dict)]

    # --- Group Speed rows by run id, keeping only standard-point evidence. ----
    speed_by_run: dict[str, list[dict]] = {}
    for row in speed_rows:
        target = _as_number(row.get("target_context_tokens"))
        if target is None or target not in STANDARD_SPEED_CANONICAL_POINTS:
            continue  # legacy/custom run -> not part of the Standard Speed ranking surface
        rid = _norm_str(row.get("run_id"))
        if not rid:
            continue
        speed_by_run.setdefault(rid, []).append(row)

    # --- Collect model identity anchors + best-effort display labels. ----------
    version_architectures: dict[str, set[str]] = {}
    display_by_version: dict[str, Optional[str]] = {}

    def _anchor(mv: str, arch: str) -> None:
        version_architectures.setdefault(mv, set()).add(arch)

    for row in speed_rows:
        mv = _model_version(row)
        display_by_version.setdefault(mv, _display_label(_norm_str(row.get("model_display_name")) or mv))
        _anchor(mv, resolve_architecture(row.get("num_experts")))

    def quality_mv(qv: dict) -> str:
        run = qv.get("run") or {}
        return _norm_str(run.get("model_identifier")) or "unknown"

    for qv in (quality_views or []):
        if not isinstance(qv, dict):
            continue
        mv = quality_mv(qv)
        display_by_version.setdefault(mv, _display_label(mv))
        cfg = qv.get("configuration") or {}
        _anchor(mv, resolve_architecture(cfg.get("num_experts")))

    # --- Build L0 entries. ----------------------------------------------------
    models_out: list[dict[str, Any]] = []
    for mv in sorted(version_architectures):  # deterministic outer order; arch is inner
        arch_set = version_architectures[mv]
        for arch in sorted(arch_set):  # distinct families (dense/moe/unknown) separated

            configs: dict[tuple[str, Optional[str]], dict[str, Any]] = {}

            def _config_slot(family: str, fingerprint: Optional[str]) -> dict[str, Any]:
                key = (family, fingerprint)
                slot = configs.get(key)
                if slot is None:
                    slot = {
                        "benchmark_family": family,
                        "configuration_fingerprint": fingerprint,
                        "run_ids": [],
                        "_eligible": {},   # run_id -> eligible component score
                        "_count": 0,
                    }
                    configs[key] = slot
                return slot

            # Speed evidence -> L1 config keyed by the (Speed) configuration fingerprint.
            for rid, rows in speed_by_run.items():
                first = next((r for r in rows if r.get("configuration_fingerprint")), None) or rows[0]
                fp = _norm_str(first.get("configuration_fingerprint"))
                classification = classify_run_for_result(rows[0])
                slot = _config_slot(DIMENSION_SPEED, fp)
                comp = _speed_component(rid, rows, classification)
                slot["run_ids"].append(rid)
                if comp.get("eligible") and comp.get("component_score"):
                    slot["_eligible"][rid] = comp["component_score"]
                slot["_count"] += 1

            # Quality evidence -> L1 config keyed by the (V2) configuration fingerprint.
            for qv in (quality_views or []):
                if not isinstance(qv, dict):
                    continue
                run = qv.get("run") or {}
                if _norm_str(run.get("model_identifier")) != mv:
                    continue
                fp = _norm_str(run.get("configuration_fingerprint"))
                comp = _quality_view_component(qv, fp)
                slot = _config_slot(DIMENSION_AGENtic, fp)
                if comp.get("run_id"):
                    slot["run_ids"].append(comp["run_id"])
                if comp.get("eligible") and comp.get("component_score"):
                    slot["_eligible"][comp["run_id"]] = comp["component_score"]
                slot["_count"] += 1

            # Collapse each config into its presentation shape. A config slot belongs
            # to exactly one benchmark_family, so there is at most one component score
            # family per slot (never averaged across families). The emitted shape is
            # ALWAYS keyed by the single ``benchmark_family`` here -- never flat -- so
            # downstream helpers that index ``component_score[family]``
            # (_model_dimensions / _representative) agree with this construction for
            # single-family and multi-family slots alike.
            configurations: list[dict[str, Any]] = []
            for slot in configs.values():
                fam = slot["benchmark_family"]
                eligible_scores = list(slot["_eligible"].values())
                if len(eligible_scores) == 1:
                    comp_score = {fam: eligible_scores[0]}
                elif eligible_scores:
                    comp_score = {fam: eligible_scores[-1]}
                else:
                    comp_score = None

                configurations.append({
                    "benchmark_family": fam,
                    "configuration_fingerprint": slot["configuration_fingerprint"],
                    "run_ids": sorted(set(slot["run_ids"])),
                    "evidence_count": slot["_count"],
                    "has_eligible_component_score": bool(eligible_scores),
                    "component_score": comp_score,
                })

            dimension_availability = _model_dimensions(configurations)
            has_approved_evidence = any(
                c.get("has_eligible_component_score") for c in configurations
            )
            rep_fraction, rep_speed = _representative(configurations)

            models_out.append({
                "model_version": mv,
                "model_family": display_by_version.get(mv) or mv,
                "architecture": arch,
                "configurations": configurations,
                "dimensions": dimension_availability,
                "has_approved_evidence": has_approved_evidence,
                # Reserved placeholder for a future approved composite score. Never
                # computed here; stays null until the scoring contract exists.
                "overall_solo_bench_score": None,
                # Private ranking keys (deterministic); N/A sinks via sentinel, never 0.
                "_agentic_fraction": rep_fraction,
                "_speed_generation": rep_speed,
            })

    ranking = _rank(models_out)

    return {
        "composite": {
            "status": "unavailable",
            "reason": (
                "Overall Solo Bench composite scoring contract is not yet approved; "
                "no Overall Solo Bench score is computed or inferred."
            ),
            "available_dimensions": list(APPROVED_DIMENSIONS),
            "missing_dimensions": list(UNIMPLEMENTED_DIMENSIONS),
        },
        "available_dimensions": list(APPROVED_DIMENSIONS),
        "all_dimensions": list(APPROVED_DIMENSIONS) + list(UNIMPLEMENTED_DIMENSIONS),
        "models": ranking,
    }


def _model_dimensions(configurations: list[dict]) -> dict[str, Any]:
    """Per-dimension availability + component score for a model identity.

    A dimension is *available* when at least one of the model's configurations has
    an approved, eligibility-passed aggregate; otherwise it is N/A (never 0).
    Unimplemented families are reported as unavailable with their reason.
    """
    dim_scores: dict[str, Any] = {}
    for fam in APPROVED_DIMENSIONS:
        scores = [c["component_score"][fam] for c in configurations
                  if c.get("component_score") and isinstance(c["component_score"], dict)
                  and fam in c["component_score"] and c.get("has_eligible_component_score")]
        dim_scores[fam] = {
            "status": "available" if scores else "unavailable",
            "score": next(iter(scores)) if len(scores) == 1 else (scores[0] if scores else None),
        }
    for fam, reason in UNIMPLEMENTED_DIMENSIONS.items():
        dim_scores[fam] = {"status": "unavailable", "score": None, "reason": reason}
    return dim_scores


def _representative(configurations: list[dict]) -> tuple[Optional[float], Optional[float]]:
    """Best approved component score per dimension for deterministic ranking.

    Returns ``(agentic_fraction, speed_generation)`` where each is ``None`` when the
    model has no eligible aggregate for that dimension -- so N/A sinks in ordering
    rather than sorting as zero.
    """
    agentic_frac: Optional[float] = None
    speed_gen: Optional[float] = None
    for c in configurations:
        comp = c.get("component_score")
        if not isinstance(comp, dict):
            continue
        agg = comp.get(DIMENSION_AGENtic)
        if isinstance(agg, dict) and isinstance(agg.get("fraction"), (int, float)):
            agentic_frac = agg["fraction"]
        spd = comp.get(DIMENSION_SPEED)
        if isinstance(spd, dict) and isinstance(spd.get("warm_generation_tokens_per_second"), (int, float)):
            speed_gen = spd["warm_generation_tokens_per_second"]
    return (float(agentic_frac) if agentic_frac is not None else None), \
           (float(speed_gen) if speed_gen is not None else None)


def _rank(models: list[dict]) -> list[dict]:
    """Deterministic ordering of models.

    Models with an approved, eligibility-passed component precede those without;
    within the ranked group a higher approved fraction/score sorts first; **N/A
    sinks below every real value via sentinel** (never as zero); ties break on the
    model version string. No global "best" is claimed -- composite score is
    unavailable (results-data-contract §7).
    """
    def _key(m: dict):
        ranked = 1 if m["has_approved_evidence"] else 0
        # Higher fraction/score sorts first -> negate; None sentinel sinks to last.
        frac = m["_agentic_fraction"]
        gen = m["_speed_generation"]
        frac_key = float("-inf") if frac is None else frac
        gen_key = float("-inf") if gen is None else gen
        return (
            -ranked,
            -frac_key,
            -gen_key,
            str(m["model_version"]),
        )

    ordered = sorted(models, key=_key)
    for index, m in enumerate(ordered, start=1):
        # Public rank; "best"/"worst" labels are intentionally omitted at the
        # composite level because no approved overall score exists.
        m["rank"] = index
        # Strip private ranking keys before returning (presentation-facing view).
        m.pop("_agentic_fraction", None)
        m.pop("_speed_generation", None)
    return ordered
