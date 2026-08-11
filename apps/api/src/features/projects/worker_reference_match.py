"""Reference-match FIR precompute scheduling, mixed into ``WorkerManager``."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import tempfile
from contextlib import ExitStack
from pathlib import Path

from upmixer.config import UpmixConfig
from upmixer.manifest import apply_asset_job, parse_manifest
from upmixer.mastering.match_reference import ReferenceMatchProcessor
from upmixer.separation.stem_pipeline import PreMasterAbort, StemUpmixPipeline
from upmixer_web.features.projects.service import get_project
from upmixer_web.features.projects.storage import ProjectStemStorage
from upmixer_web.shared.models import Project

_log = logging.getLogger("upmixer_web")


def _reference_match_signature(project: Project) -> str | None:
    """Hash of everything a project's reference-match curve asset depends on.

    Deliberately excludes live mixing edits (routing/rebalance/stem EQ/
    anchor) — the asset is a bounded Tier-3 approximation computed against a
    canonical server-rendered bed, not the browser's live-edited mix (see
    docs/contracts/preview_export_parity.md Ledger D12). Also excludes
    ``strength``, ``spectrum``, ``rms``, and ``max_db``: the precompute
    (`_capture_and_abort` below) always derives the curve and level gain with
    both stages forced on, and all four are live client-side gates/knobs
    applied on top of that persisted result — `ProjectDetailPage.tsx`'s
    `previewMastering` in the browser, `strength`/`max_db` query params on
    the FIR endpoint (see Ledger D21). None of them change the curve or
    `rms_gain_db` themselves, so hashing any of them would force needless
    full-song recomputes while a slider is dragged.
    Includes ``stem_generation``: unprepared stems make `prepare_reference_match`
    stamp a signature-matching empty asset (see below) so an unrelated save
    doesn't reopen `reference_match_pending`; without `stem_generation` in the
    signature that empty stamp would never go stale, and a later stem
    re-prepare (which bumps `stem_generation`, `worker.py`'s
    `_run_project`) would never re-trigger the real compute.
    Returns ``None`` when no reference is attached, meaning "no asset should
    exist."
    """
    if not project.mastering_reference_id:
        return None
    manifest = project.manifest if isinstance(project.manifest, dict) else {}
    mixing = manifest.get("mixing", {}) if isinstance(manifest.get("mixing"), dict) else {}
    reference = project.mastering_reference
    payload = {
        "reference_id": project.mastering_reference_id,
        "reference_sha256": reference.sha256 if reference else None,
        "channel_layout": mixing.get("channel_layout"),
        "stem_generation": project.stem_generation,
    }
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _reference_match_needs_work(project: Project | None, project_stems: ProjectStemStorage) -> bool:
    """Whether `prepare_reference_match` would do anything for *project* right
    now — mirrors that method's own early-outs (see below) so
    `schedule_reference_match` can skip opening a `reference_match_pending`
    window for a call that is provably a no-op.

    `prepare_reference_match` remains the authority: it re-validates
    everything itself, since project state can change between scheduling and
    the run actually executing.
    """
    if not project:
        return False
    target_signature = _reference_match_signature(project)
    if target_signature is None:
        # No reference attached — work is needed only to clear a
        # still-existing asset from a prior reference.
        return project_stems.read_reference_match_meta(project.id) is not None
    if not project.prepared_stems or not project.tracks:
        return False
    existing = project_stems.read_reference_match_meta(project.id)
    return not existing or existing.get("signature") != target_signature


class ReferenceMatchMixin:
    """Reference-match scheduling/execution methods for ``WorkerManager``.

    Reads/writes the host's ``sessions``, ``source``, ``project_stems``,
    ``_lock``, ``_refmatch_executor``, ``_refmatch_pending``, and
    ``_refmatch_running`` attributes — set up by ``WorkerManager.__init__``
    and ``WorkerManager.start``/``stop``.
    """

    def schedule_reference_match(self, project_id: str) -> None:
        """Queue a project's reference-match precompute on a dedicated
        single-thread executor, coalescing rapid repeat calls into one
        trailing run instead of one run per call.

        `prepare_reference_match` runs a full-song mix pass and is too heavy
        to run inline on an API request thread (see
        docs/contracts/preview_export_parity.md Ledger D12) — every settings
        save (debounced at 350ms in the browser) used to call it directly,
        pegging the CPU while a reference-match slider was being dragged.
        This schedules the work on `_refmatch_executor` instead; if a run is
        already in flight for this project, the request is recorded and
        picked up by that run's trailing check rather than starting a second
        one.

        Callers (the settings-save endpoint, stem-prep completion) always
        run after their own `session.commit()`, so the fresh session opened
        here to check `_reference_match_needs_work` sees current state. This
        check is purely an optimisation for `reference_match_pending`'s
        window — it must never open (and the caller's UI must never show
        "preparing…") for an edit that changes nothing the FIR depends on
        (see `_reference_match_signature`'s exclusions). It is not a
        correctness gate: `prepare_reference_match` re-validates everything
        itself once it actually runs.
        """
        if not self._refmatch_executor:
            return
        with self.sessions() as session:
            project = get_project(session, project_id)
            if not _reference_match_needs_work(project, self.project_stems):
                return
        with self._lock:
            self._refmatch_pending.add(project_id)
            if project_id in self._refmatch_running:
                return
            self._refmatch_running.add(project_id)
        self._refmatch_executor.submit(self._run_reference_match, project_id)

    def _run_reference_match(self, project_id: str) -> None:
        while True:
            with self._lock:
                if project_id not in self._refmatch_pending:
                    self._refmatch_running.discard(project_id)
                    return
                self._refmatch_pending.discard(project_id)
            try:
                self.prepare_reference_match(project_id)
            except Exception:
                # Non-fatal: a stale or missing reference-match asset just
                # means the preview falls back to no reference-match EQ —
                # it must not fail project preparation or the settings save.
                _log.exception(
                    "Reference-match precompute failed for project %s", project_id
                )

    def reference_match_pending(self, project_id: str) -> bool:
        """Whether a reference-match recompute is queued or running for
        *project_id* — surfaced to the API so the frontend keeps polling
        until the async asset lands."""
        with self._lock:
            return project_id in self._refmatch_pending or project_id in self._refmatch_running

    def prepare_reference_match(self, project_id: str) -> None:
        """Recompute a project's server-side reference-match curve asset if
        its signature has drifted since the last compute; a cheap no-op
        otherwise.

        Runs in the caller's thread rather than a :class:`JobSubprocess`: this
        is only safe because it never runs inference itself — it bails if the
        track's plain stem store isn't populated yet (see that check below),
        rather than falling through to a full uncached separation pass with
        none of JobSubprocess's crash isolation or progress reporting. Safe to
        call after every project stem preparation and every settings save;
        only a signature mismatch with stems already prepared triggers the
        mix + PSD-match pass.
        """
        with self.sessions() as session:
            project = get_project(session, project_id)
            if not project:
                return
            target_signature = _reference_match_signature(project)
            if target_signature is None:
                self.project_stems.clear_reference_match(project_id)
                return
            if not project.prepared_stems or not project.tracks:
                return
            existing = self.project_stems.read_reference_match_meta(project_id)
            if existing and existing.get("signature") == target_signature:
                return
            reference = project.mastering_reference
            if reference is None:
                return
            manifest = copy.deepcopy(project.manifest)
            requested_stems = list(project.requested_stems)
            track_id = project.tracks[0].id
            track_overrides = copy.deepcopy(project.tracks[0].manifest_overrides)
            source_key = project.tracks[0].asset.storage_key
            reference_key = reference.storage_key

        stem_dir = self.project_stems.stem_dir(project_id, track_id)

        with ExitStack() as sources:
            input_path = sources.enter_context(self.source.materialize(source_key))
            reference_path = sources.enter_context(self.source.materialize(reference_key))

            with tempfile.TemporaryDirectory() as tmp_dir:
                data = copy.deepcopy(manifest)
                data.setdefault("engine", {})["mode"] = "stem"
                data["engine"]["stems"] = requested_stems
                data["assets"] = [{
                    "input": str(input_path),
                    "output": str(Path(tmp_dir) / "refmatch-prepare.wav"),
                    "stem_input_dir": str(stem_dir),
                    **{
                        block: value
                        for block, value in track_overrides.items()
                        if isinstance(value, dict) and value
                    },
                }]
                _, asset_jobs = parse_manifest(data)
                asset_job = asset_jobs[0]
                config = UpmixConfig()
                apply_asset_job(config, asset_job)
                config.stems = asset_job.engine.get("stems") or requested_stems

                if not (stem_dir / "stems.json").is_file():
                    # Stems haven't been (re-)prepared yet for the current
                    # track — running process_file here would fall through to
                    # a full, unisolated separation pass on this thread. Bail
                    # and let the next real stem preparation (which does run
                    # isolated, with progress) populate the store and
                    # re-trigger this.
                    _log.warning(
                        "Reference-match precompute skipped for project %s: "
                        "stems not yet prepared", project_id,
                    )
                    # Record a signature-stamped empty result so
                    # `_reference_match_needs_work` stops reopening the
                    # `reference_match_pending` window on every unrelated
                    # settings save while stems stay unprepared.
                    self.project_stems.write_reference_match(
                        project_id, [], [], 0.0, 0, 0, target_signature,
                    )
                    return

                captured: dict[str, object] = {}

                def _capture_and_abort(channels, sr, output_fmt) -> None:
                    # Both stages forced on regardless of the live manifest's
                    # spectrum/rms toggles: those (plus strength/max_db) are
                    # applied as client-side gates on top of this persisted
                    # curve/gain, not baked into the precompute — see
                    # `_reference_match_signature`'s docstring and Ledger D21.
                    processor = ReferenceMatchProcessor(
                        reference_path=str(reference_path),
                        output_fmt=output_fmt,
                        match_spectrum=True,
                        match_rms=True,
                        sample_rate=sr,
                    )
                    curve, rms_gain_db = processor.compute_curve(channels)
                    captured["curve"] = curve
                    captured["channels"] = [name for name in channels if name != "LFE"]
                    captured["rms_gain_db"] = rms_gain_db
                    captured["sample_rate"] = sr
                    captured["n_taps"] = processor._n_taps
                    raise PreMasterAbort()

                pipeline = StemUpmixPipeline(config=config)
                try:
                    pipeline.process_file(
                        str(input_path), asset_job.output,
                        pre_master_hook=_capture_and_abort,
                    )
                except PreMasterAbort:
                    pass
                finally:
                    pipeline.close()

        if "curve" not in captured:
            return
        self.project_stems.write_reference_match(
            project_id,
            captured["curve"],
            captured["channels"],
            captured["rms_gain_db"],
            captured["sample_rate"],
            captured["n_taps"],
            target_signature,
        )
