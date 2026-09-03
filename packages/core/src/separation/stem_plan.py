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
    "toms":    "Toms",
    "hi-hat":  "Hi-Hat",
    "ride":    "Ride",
    "crash":   "Crash",
    "crowd":   "Crowd",
    "lead-vocals": "Lead Vocals",
    "backing-vocals": "Backing Vocals",
}

CANONICAL_STEMS: frozenset[str] = frozenset(MANIFEST_TO_CANONICAL.values())

DEFAULT_STEMS: list[str] = ["Vocals", "Bass", "Drums", "Guitar", "Piano", "Other"]


MODEL_CROWD = "mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt"

MODEL_PRIMARY = "BS-Roformer-SW.ckpt"

MODEL_SCNET = "model_scnet_ep_36_sdr_10.0891.ckpt"
# Internal name retained for the fixed ensemble implementation.
MODEL_ENSEMBLE = MODEL_SCNET
ENSEMBLE_ALGORITHM = "avg_wave"
ENSEMBLE_STEMS: frozenset[str] = frozenset({"Bass", "Drums"})

MODEL_DEUX = "becruily_deux.ckpt"

MODEL_DRUMS = "MDX23C-DrumSep-aufr33-jarredou.ckpt"

MODEL_KARAOKE = "mel_band_roformer_karaoke_gabox_v2.ckpt"
_KARAOKE_OUTPUT_CACHE_TAG = "lead-vocals-v2"

DEUX_OUTPUT_STEMS: frozenset[str] = frozenset({"Vocals", "_deux_inst"})
PRIMARY_OUTPUT_STEMS: frozenset[str] = frozenset({"Vocals", "Bass", "Drums", "Guitar", "Piano", "Other"})
PRIMARY_INSTRUMENTAL_STEMS: frozenset[str] = PRIMARY_OUTPUT_STEMS - {"Vocals"}
DRUM_SUB_STEMS: frozenset[str] = frozenset({"Kick", "Snare", "Toms", "Hi-Hat", "Ride", "Crash"})
VOCAL_SUB_STEMS: frozenset[str] = frozenset({"Lead Vocals", "Backing Vocals"})

_CHILD_STEMS_BY_PARENT: dict[str, frozenset[str]] = {
    "Drums": DRUM_SUB_STEMS,
    "Vocals": VOCAL_SUB_STEMS,
}



@dataclass
class SeparationTask:
    """One model invocation in the execution plan.

    Attributes:
        model:        Model filename to load (from the registry constants above).
        input_source: ``"original"`` for the raw input file, or a canonical stem
                      name produced by a previous task (``"_crowd_other"``,
                      ``"_deux_inst"``, ``"Drums"``, or ``"Vocals"``).
        output_stems: All canonical names this model can produce.
        keep_stems:   Final output stems the user requested from this task.
                      Does not include intermediates needed only by later stages.
        stem_overrides: Optional lowercase model-tag → canonical name mapping for
                      this task alone, overriding ``MODEL_STEM_OVERRIDES``.
    """

    model: str
    input_source: str
    output_stems: frozenset[str]
    keep_stems: frozenset[str]
    stem_overrides: dict[str, str] | None = None
    ensemble_models: tuple[str, ...] = ()
    ensemble_stems: frozenset[str] = frozenset()

    @property
    def ensemble_model(self) -> str | None:
        """Compatibility view for the single fixed partner model."""
        return self.ensemble_models[0] if self.ensemble_models else None


@dataclass
class SeparationPlan:
    """Ordered list of model invocations derived from the requested stems.

    Attributes:
        tasks:           Tasks in execution order.
        requested_stems: Canonical names of all final output stems.
        stems_hash:      20-char hex digest of the sorted stem set; used as the
                         stem-cache key component so different stem selections
                         produce separate cache entries.
    """

    tasks: list[SeparationTask]
    requested_stems: frozenset[str]
    stems_hash: str
    inference_hash: str = ""

    @property
    def ensemble_algorithm(self) -> str | None:
        """Fixed algorithm used by an enabled primary-stem ensemble."""
        return ENSEMBLE_ALGORITHM if self.ensemble_models else None

    @property
    def ensemble_models(self) -> tuple[str, ...]:
        """Models participating in the plan's fixed ensemble, if any."""
        for task in self.tasks:
            if task.ensemble_models:
                return task.ensemble_models
        return ()

    @property
    def ensemble_stems(self) -> frozenset[str]:
        """Final/intermediate stems that the plan ensembles."""
        for task in self.tasks:
            if task.ensemble_stems:
                return task.ensemble_stems
        return frozenset()




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


def resolve_separation_plan(
    canonical: list[str],
    stem_ensemble: bool = False,
) -> SeparationPlan:
    """Build an ordered execution plan for the given canonical stem names.

    The resolver determines which model tiers to invoke and in what order:
    crowd isolation runs first when requested; the dual becruily-deux model
    then supplies the final Vocals stem and a clean instrumental residual for
    the primary model to separate the remaining instrument stems from; drum
    and vocal sub-stems are extracted hierarchically from the primary/deux
    parent stems.

    Args:
        canonical: Canonical (title-case) stem names — output of
                   :func:`normalize_stems` or :data:`DEFAULT_STEMS`.
                   An empty list is treated identically to DEFAULT_STEMS.

    Returns:
        :class:`SeparationPlan` with tasks in correct execution order.

        ``stem_ensemble`` enables the fixed primary-stage BS-Roformer-SW +
        SCNet average for Bass and/or Drums. Drum-piece requests include the
        intermediate Drums stem in that closure.
    """
    requested = frozenset(canonical) if canonical else frozenset(DEFAULT_STEMS)

    for parent, children in _CHILD_STEMS_BY_PARENT.items():
        if requested & children:
            requested = requested - {parent}

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
    vocal_sub_needed = bool(requested & VOCAL_SUB_STEMS)
    deux_needed = primary_needed or drum_sub_needed or vocal_sub_needed
    instrumental_needed = bool(requested & (PRIMARY_OUTPUT_STEMS - {"Vocals"}))
    primary_stage_needed = instrumental_needed or drum_sub_needed

    ensemble_stems = frozenset()
    if stem_ensemble and primary_stage_needed:
        ensemble_stems = (requested & ENSEMBLE_STEMS) | (
            {"Drums"} if drum_sub_needed else set()
        )

    if deux_needed:
        deux_input = "_crowd_other" if crowd_needed else "original"
        tasks.append(SeparationTask(
            model=MODEL_DEUX,
            input_source=deux_input,
            output_stems=DEUX_OUTPUT_STEMS,
            keep_stems=requested & {"Vocals"},
        ))

    if primary_stage_needed:
        # output_stems excludes Vocals (unlike PRIMARY_OUTPUT_STEMS): primary
        # is always fed deux's residual, so its own Vocals output is a
        # vocals-free leftover, not real vocal content. Keeping "Vocals" here
        # would collide with deux's disk-cached Vocals key in _execute_plan
        # (both stems land under the same canonical name) and karaoke would
        # then run on whichever task wrote to disk last.
        tasks.append(SeparationTask(
            model=MODEL_PRIMARY,
            input_source="_deux_inst",
            output_stems=PRIMARY_INSTRUMENTAL_STEMS,
            keep_stems=requested & PRIMARY_INSTRUMENTAL_STEMS,
            ensemble_models=((MODEL_ENSEMBLE,) if ensemble_stems else ()),
            ensemble_stems=frozenset(ensemble_stems),
        ))

    if drum_sub_needed:
        tasks.append(SeparationTask(
            model=MODEL_DRUMS,
            input_source="Drums",
            output_stems=DRUM_SUB_STEMS,
            keep_stems=requested & DRUM_SUB_STEMS,
        ))

    if vocal_sub_needed:
        tasks.append(SeparationTask(
            model=MODEL_KARAOKE,
            input_source="Vocals",
            output_stems=VOCAL_SUB_STEMS,
            keep_stems=requested & VOCAL_SUB_STEMS,
        ))

    stems_hash = hashlib.sha256("|".join(sorted(requested)).encode()).hexdigest()[:20]
    inference_identity_parts: list[str] = []
    for task in tasks:
        output_tag = (
            _KARAOKE_OUTPUT_CACHE_TAG if task.model == MODEL_KARAOKE else ""
        )
        inference_identity_parts.append(
            f"{task.model}:{task.input_source}:"
            f"{','.join(sorted(task.output_stems))}:{output_tag}"
            + (
                f"|ensemble={ENSEMBLE_ALGORITHM}:"
                f"{','.join(task.ensemble_models)}:"
                f"{','.join(sorted(task.ensemble_stems))}"
                if task.ensemble_models else ""
            )
        )
    inference_identity = "|".join(inference_identity_parts)
    inference_hash = hashlib.sha256(inference_identity.encode()).hexdigest()[:20]

    return SeparationPlan(
        tasks=tasks,
        requested_stems=requested,
        stems_hash=stems_hash,
        inference_hash=inference_hash,
    )
