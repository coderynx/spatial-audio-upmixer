"""Stem vocabulary, model registry, and separation plan resolver.

Users declare desired output stems in the manifest; this module resolves
which models to run, in which order, and which intermediate files to manage.

Model selection is not user-facing — the mapping from stems to models is
encoded here and updated as new capabilities are added to the library.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


MANIFEST_TO_CANONICAL: dict[str, str] = {
    "vocals":  "Vocals",
    "bass":    "Bass",
    "drums":   "Drums",
    "guitar":  "Guitar",
    "piano":   "Piano",
    "other":   "Other",
    "kick":    "Kick",
    "snare":   "Snare",
    "hi-hat":  "Hi-Hat",
    "ride":    "Ride",
    "crash":   "Crash",
    "crowd":   "Crowd",
}

CANONICAL_STEMS: frozenset[str] = frozenset(MANIFEST_TO_CANONICAL.values())

DEFAULT_STEMS: list[str] = ["Vocals", "Bass", "Drums", "Guitar", "Piano", "Other"]


MODEL_CROWD = "mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt"

MODEL_PRIMARY = "BS-Roformer-SW.ckpt"

MODEL_DRUMS = "MDX23C-DrumSep-aufr33-jarredou.ckpt"

PRIMARY_OUTPUT_STEMS: frozenset[str] = frozenset({"Vocals", "Bass", "Drums", "Guitar", "Piano", "Other"})
DRUM_SUB_STEMS: frozenset[str] = frozenset({"Kick", "Snare", "Toms", "Hi-Hat", "Ride", "Crash"})



@dataclass
class SeparationTask:
    """One model invocation in the execution plan.

    Attributes:
        model:        Model filename to load (from the registry constants above).
        input_source: ``"original"`` for the raw input file, or a canonical stem
                      name produced by a previous task (``"_crowd_other"`` or
                      ``"Drums"``).
        output_stems: All canonical names this model can produce.
        keep_stems:   Final output stems the user requested from this task.
                      Does not include intermediates needed only by later stages.
    """

    model: str
    input_source: str
    output_stems: frozenset[str]
    keep_stems: frozenset[str]


@dataclass
class SeparationPlan:
    """Ordered list of model invocations derived from the requested stems.

    Attributes:
        tasks:           Tasks in execution order (Stage 0 → 1 → 2).
        requested_stems: Canonical names of all final output stems.
        stems_hash:      20-char hex digest of the sorted stem set; used as the
                         stem-cache key component so different stem selections
                         produce separate cache entries.
    """

    tasks: list[SeparationTask]
    requested_stems: frozenset[str]
    stems_hash: str




def normalize_stems(stems: list[str]) -> list[str]:
    """Convert manifest stem names to canonical title-case names.

    Accepts both lowercase manifest names (``"vocals"``) and already-canonical
    names (``"Vocals"``).  Deduplicates while preserving first-seen order.

    Args:
        stems: Stem names from the manifest or CLI.

    Returns:
        Deduplicated list of canonical names.

    Raises:
        ValueError: If any name is not in the supported vocabulary.
    """
    seen: set[str] = set()
    result: list[str] = []
    for s in stems:
        canonical = MANIFEST_TO_CANONICAL.get(s) or MANIFEST_TO_CANONICAL.get(s.lower())
        if canonical is None and s in CANONICAL_STEMS:
            canonical = s
        if canonical is None:
            valid = sorted(MANIFEST_TO_CANONICAL.keys())
            raise ValueError(
                f"Unknown stem name '{s}'. "
                f"Valid names: {', '.join(valid)}."
            )
        if canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result


def resolve_separation_plan(canonical: list[str]) -> SeparationPlan:
    """Build an ordered execution plan for the given canonical stem names.

    The resolver determines which of the three model tiers to invoke and in
    what order, ensuring that crowd isolation runs before primary separation
    when requested, and that drum sub-stems are extracted hierarchically from
    the primary model's Drums output.

    Args:
        canonical: Canonical (title-case) stem names — output of
                   :func:`normalize_stems` or :data:`DEFAULT_STEMS`.
                   An empty list is treated identically to DEFAULT_STEMS.

    Returns:
        :class:`SeparationPlan` with tasks in correct execution order.
    """
    requested = frozenset(canonical) if canonical else frozenset(DEFAULT_STEMS)

    tasks: list[SeparationTask] = []

    crowd_needed = "Crowd" in requested
    if crowd_needed:
        tasks.append(SeparationTask(
            model=MODEL_CROWD,
            input_source="original",
            output_stems=frozenset({"Crowd", "_crowd_other"}),
            keep_stems=frozenset({"Crowd"}),
        ))

    primary_needed = bool(requested & PRIMARY_OUTPUT_STEMS)
    drum_sub_needed = bool(requested & DRUM_SUB_STEMS)

    if primary_needed or drum_sub_needed:
        stage1_input = "_crowd_other" if crowd_needed else "original"
        stage1_keep = requested & PRIMARY_OUTPUT_STEMS
        tasks.append(SeparationTask(
            model=MODEL_PRIMARY,
            input_source=stage1_input,
            output_stems=PRIMARY_OUTPUT_STEMS,
            keep_stems=stage1_keep,
        ))

    if drum_sub_needed:
        tasks.append(SeparationTask(
            model=MODEL_DRUMS,
            input_source="Drums",
            output_stems=DRUM_SUB_STEMS,
            keep_stems=requested & DRUM_SUB_STEMS,
        ))

    stems_hash = hashlib.sha256("|".join(sorted(requested)).encode()).hexdigest()[:20]

    return SeparationPlan(
        tasks=tasks,
        requested_stems=requested,
        stems_hash=stems_hash,
    )
