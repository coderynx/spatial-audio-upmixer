"""Persistent process boundary for MLX SCNet inference."""

from __future__ import annotations

import logging
import multiprocessing
import os
import shutil
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

_log = logging.getLogger(__name__)

_CTX = multiprocessing.get_context("spawn")
SCNET_STARTUP_TIMEOUT_S = 120.0
SCNET_INACTIVITY_TIMEOUT_S = 120.0
_POLL_INTERVAL_S = 0.25


@dataclass
class _WorkerState:
    model: Any
    config: Any
    spec: Any
    device: Any
    last_engine: Any = None


def _new_engine(
    state: _WorkerState, settings: dict[str, Any], output_dir: str
) -> Any:
    from .engine import SeparationEngine

    return SeparationEngine(
        model=state.model,
        config=state.config,
        arch=state.spec.arch,
        model_filename=settings["model"],
        device=state.device,
        output_dir=output_dir,
        sample_rate=settings["sample_rate"],
        batch_size=settings["batch_size"],
        segment_size=settings["segment_size"],
        chunk_duration_s=settings["chunk_duration_s"],
        overlap=settings["overlap"],
        default_chunk_samples=state.spec.default_chunk_samples,
        tta=settings["tta"],
        pitch_shift=settings["pitch_shift"],
    )


def _normalise_output_paths(
    stage_dir: Path, final_dir: Path, output_paths: list[str]
) -> list[str]:
    """Map engine paths from the staging directory to its committed sibling."""
    stage_root = stage_dir.resolve()
    paths = (Path(path) for path in output_paths)
    return [
        str(
            final_dir
            / (path if path.is_absolute() else stage_dir / path)
            .resolve()
            .relative_to(stage_root)
        )
        for path in paths
    ]


def _request_dirs(base_dir: str, request_id: str) -> tuple[Path, Path]:
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    stage = base / f".scnet-{request_id}"
    stage.mkdir()
    final = base / f"scnet-{request_id}"
    return stage, final


def _discard_request_dirs(base_dir: str, request_id: str) -> None:
    base = Path(base_dir)
    for name in (f".scnet-{request_id}", f"scnet-{request_id}"):
        shutil.rmtree(base / name, ignore_errors=True)


def _send(connection: Any, event: dict[str, Any]) -> bool:
    try:
        connection.send(event)
    except (BrokenPipeError, EOFError, OSError):
        return False
    return True


def _build_worker_state(settings: dict[str, Any]) -> _WorkerState:
    from .device import DeviceManager
    from .registry import get_model_spec
    from .scnet_mlx import load_model

    spec = get_model_spec(settings["model"])
    if spec.arch != "scnet":
        raise ValueError(f"MLX SCNet worker accepts only 'scnet', got {spec.arch!r}")
    model, config = load_model(settings["model"], settings["model_dir"])
    return _WorkerState(
        model=model,
        config=config,
        spec=spec,
        device=DeviceManager("mlx"),
    )


def _write_parent(parent: np.ndarray, base_dir: str) -> str:
    path = Path(base_dir) / f".scnet-parent-{uuid.uuid4().hex}.npy"
    try:
        with path.open("wb") as handle:
            np.save(handle, np.ascontiguousarray(parent), allow_pickle=False)
        final = path.with_name(path.name.removeprefix("."))
        os.replace(path, final)
        return str(final)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _execute_request(
    state: _WorkerState,
    settings: dict[str, Any],
    audio_path: str,
    output_dir: str,
    request_id: str,
    retain_parent: bool,
    wanted: frozenset[str] | None,
    progress_callback: Callable[[float], None],
) -> list[str]:
    state.last_engine = None
    stage_dir, final_dir = _request_dirs(output_dir, request_id)
    try:
        engine = _new_engine(state, settings, str(stage_dir))
        paths = engine.separate(
            audio_path,
            retain_parent=retain_parent,
            progress_callback=progress_callback,
            wanted=wanted,
        )
        committed = _normalise_output_paths(
            stage_dir, final_dir, [str(path) for path in paths or []]
        )
        os.replace(stage_dir, final_dir)
        state.last_engine = engine if retain_parent else None
        return committed
    except BaseException:
        _discard_request_dirs(output_dir, request_id)
        raise


def _worker_main(connection: Any, settings: dict[str, Any]) -> None:
    """Child entrypoint; keep imports here so spawn starts a light parent."""
    state: _WorkerState | None = None
    try:
        state = _build_worker_state(settings)
        if not _send(connection, {"kind": "ready"}):
            return
        while True:
            try:
                request = connection.recv()
            except (EOFError, OSError):
                return
            kind = request.get("kind")
            if kind == "close":
                _send(connection, {"kind": "closed"})
                return
            if kind == "take_parent":
                try:
                    if state.last_engine is None:
                        raise RuntimeError("No completed separation input is available")
                    parent = state.last_engine.take_last_parent()
                    path = _write_parent(parent, request["output_dir"])
                    _send(
                        connection,
                        {
                            "kind": "parent",
                            "request_id": request.get("request_id"),
                            "path": path,
                        },
                    )
                except BaseException as exc:
                    _send(
                        connection,
                        {
                            "kind": "error",
                            "request_id": request.get("request_id"),
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                            "traceback": traceback.format_exc(),
                        },
                    )
                continue
            if kind != "separate":
                _send(
                    connection,
                    {"kind": "error", "message": f"Unknown request: {kind!r}"},
                )
                continue

            request_id = request["request_id"]

            def progress(fraction: float) -> None:
                _send(
                    connection,
                    {
                        "kind": "progress",
                        "request_id": request_id,
                        "fraction": max(0.0, min(1.0, float(fraction))),
                    },
                )

            try:
                request_settings = dict(settings)
                request_settings.update(request.get("settings", {}))
                committed_paths = _execute_request(
                    state,
                    request_settings,
                    request["audio_path"],
                    request["output_dir"],
                    request_id,
                    bool(request.get("retain_parent", False)),
                    request.get("wanted"),
                    progress,
                )
                _send(
                    connection,
                    {
                        "kind": "result",
                        "request_id": request_id,
                        "paths": committed_paths,
                    },
                )
            except BaseException as exc:
                _send(
                    connection,
                    {
                        "kind": "error",
                        "request_id": request_id,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )
    except BaseException as exc:
        _send(
            connection,
            {
                "kind": "startup_error",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
    finally:
        if state is not None and state.device is not None:
            try:
                state.model = None
                state.last_engine = None
                state.device.empty_cache()
            except Exception:
                _log.debug("SCNet worker cache cleanup failed", exc_info=True)
        try:
            connection.close()
        except OSError:
            pass


def _remote_exception(event: dict[str, Any]) -> BaseException:
    message = event.get("message") or "SCNet worker failed"
    if event.get("traceback"):
        _log.error("SCNet worker error: %s\n%s", message, event["traceback"])
    if event.get("error_type") == "MemoryError":
        return MemoryError(message)
    return RuntimeError(message)


class SCNetWorker:
    """Persistent MLX SCNet supervisor used by :class:`StemSeparator`."""

    def __init__(
        self,
        model: str,
        model_dir: str,
        *,
        context: Any | None = None,
        force_in_process: bool | None = None,
    ) -> None:
        self._settings: dict[str, Any] = {"model": model, "model_dir": model_dir}
        self._context = context or _CTX
        self._in_process = (
            multiprocessing.current_process().daemon
            if force_in_process is None
            else force_in_process
        )
        self._connection: Any | None = None
        self._process: Any | None = None
        self._state: _WorkerState | None = None
        self._active_request: tuple[str, str] | None = None

    @property
    def is_alive(self) -> bool:
        if self._in_process:
            return self._state is not None
        return bool(self._process is not None and self._process.is_alive())

    def _ensure_local_state(self) -> _WorkerState:
        if self._state is None:
            self._state = _build_worker_state(self._settings)
        return self._state

    def _ensure_started(self, deadline: float) -> None:
        if self._in_process:
            self._ensure_local_state()
            return
        if self._process is not None and self._process.is_alive():
            return

        if self._process is not None:
            self._process.join(timeout=0.0)
        if self._connection is not None:
            try:
                self._connection.close()
            except OSError:
                pass

        parent, child = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=_worker_main, args=(child, self._settings)
        )
        process.daemon = True
        self._connection = parent
        self._process = process
        try:
            process.start()
        except BaseException:
            child.close()
            self._process = None
            self._connection = None
            parent.close()
            raise
        child.close()

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not parent.poll(min(_POLL_INTERVAL_S, remaining)):
                if remaining <= 0:
                    self.terminate()
                    raise TimeoutError(
                        "SCNet worker startup timed out after 120 seconds"
                    )
                if not process.is_alive():
                    event = {
                        "kind": "startup_error",
                        "message": "SCNet worker exited during startup",
                    }
                    self.terminate()
                    raise _remote_exception(event)
                continue
            try:
                event = parent.recv()
            except (EOFError, OSError) as exc:
                self.terminate()
                raise RuntimeError("SCNet worker exited during startup") from exc
            kind = event.get("kind")
            if kind == "ready":
                return
            if kind == "startup_error":
                self.terminate()
                raise _remote_exception(event)

    def _separate_in_process(
        self,
        audio_path: str,
        output_dir: str,
        settings: dict[str, Any],
        retain_parent: bool,
        wanted: frozenset[str] | None,
        progress_callback: Callable[[float], None] | None,
    ) -> list[str]:
        state = self._ensure_local_state()
        request_id = uuid.uuid4().hex
        self._active_request = (output_dir, request_id)
        callback = progress_callback or (lambda _fraction: None)
        try:
            return _execute_request(
                state,
                settings,
                audio_path,
                output_dir,
                request_id,
                retain_parent,
                wanted,
                callback,
            )
        finally:
            self._active_request = None

    def separate(
        self,
        audio_path: str,
        output_dir: str,
        *,
        sample_rate: int,
        batch_size: int,
        segment_size: int | None,
        chunk_duration_s: float | None,
        overlap: int | None,
        tta: bool,
        pitch_shift: float | None,
        retain_parent: bool = False,
        wanted: frozenset[str] | None = None,
        progress_callback: Callable[[float], None] | None = None,
    ) -> list[str]:
        """Run one path-based request while reusing the resident model."""
        settings = {
            "sample_rate": sample_rate,
            "batch_size": batch_size,
            "segment_size": segment_size,
            "chunk_duration_s": chunk_duration_s,
            "overlap": overlap,
            "tta": tta,
            "pitch_shift": pitch_shift,
        }
        if self._in_process:
            return self._separate_in_process(
                audio_path,
                output_dir,
                {**self._settings, **settings},
                retain_parent,
                wanted,
                progress_callback,
            )

        deadline = time.monotonic() + SCNET_STARTUP_TIMEOUT_S
        self._ensure_started(deadline)
        request_id = uuid.uuid4().hex
        self._active_request = (output_dir, request_id)
        request = {
            "kind": "separate",
            "request_id": request_id,
            "audio_path": audio_path,
            "output_dir": output_dir,
            "retain_parent": retain_parent,
            "wanted": wanted,
            "settings": settings,
        }
        connection = self._connection
        process = self._process
        if connection is None or process is None:
            raise RuntimeError("SCNet worker is not running")
        timeout = SCNET_INACTIVITY_TIMEOUT_S
        deadline = time.monotonic() + timeout
        try:
            connection.send(request)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"SCNet worker request timed out after {timeout:g} seconds of inactivity"
                    )
                if not connection.poll(min(_POLL_INTERVAL_S, remaining)):
                    if not process.is_alive():
                        raise RuntimeError(
                            f"SCNet worker exited unexpectedly (code {process.exitcode})"
                        )
                    continue
                event = connection.recv()
                kind = event.get("kind")
                if event.get("request_id") not in (None, request_id):
                    raise RuntimeError("SCNet worker returned an unexpected request id")
                if kind == "progress":
                    if progress_callback is not None:
                        try:
                            progress_callback(float(event["fraction"]))
                        except BaseException:
                            self.terminate()
                            raise
                    deadline = time.monotonic() + timeout
                elif kind == "result":
                    self._active_request = None
                    return [str(path) for path in event.get("paths", [])]
                elif kind == "error":
                    raise _remote_exception(event)
        except TimeoutError:
            self.terminate()
            raise
        except (EOFError, BrokenPipeError, OSError, RuntimeError) as exc:
            if process.is_alive() and isinstance(exc, RuntimeError):
                _discard_request_dirs(output_dir, request_id)
                self._active_request = None
            else:
                self.terminate()
            raise

    def take_last_parent(self, output_dir: str) -> np.ndarray:
        """Return and release the exact parent from the last retained request."""
        if self._in_process:
            if self._state is None or self._state.last_engine is None:
                raise RuntimeError("No completed separation input is available")
            parent = self._state.last_engine.take_last_parent()
            self._state.last_engine = None
            return np.asarray(parent)

        self._ensure_started(time.monotonic() + SCNET_STARTUP_TIMEOUT_S)
        connection = self._connection
        process = self._process
        if connection is None or process is None:
            raise RuntimeError("SCNet worker is not running")
        request_id = uuid.uuid4().hex
        connection.send(
            {"kind": "take_parent", "request_id": request_id, "output_dir": output_dir}
        )
        deadline = time.monotonic() + SCNET_STARTUP_TIMEOUT_S
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.terminate()
                raise TimeoutError("SCNet worker parent retrieval timed out")
            if not connection.poll(min(_POLL_INTERVAL_S, remaining)):
                if not process.is_alive():
                    raise RuntimeError("SCNet worker exited during parent retrieval")
                continue
            event = connection.recv()
            if event.get("request_id") not in (None, request_id):
                raise RuntimeError("SCNet worker returned an unexpected request id")
            if event.get("kind") == "parent":
                path = Path(event["path"])
                try:
                    return np.load(path, allow_pickle=False).copy()
                finally:
                    path.unlink(missing_ok=True)
            if event.get("kind") == "error":
                raise _remote_exception(event)

    def terminate(self) -> None:
        """Stop the worker immediately and discard its client-side handles."""
        if self._in_process:
            active_request = self._active_request
            self._active_request = None
            if active_request is not None:
                _discard_request_dirs(*active_request)
            if self._state is not None:
                try:
                    self._state.model = None
                    self._state.last_engine = None
                    self._state.device.empty_cache()
                except Exception:
                    _log.debug("SCNet local worker cleanup failed", exc_info=True)
            self._state = None
            return
        active_request = self._active_request
        self._active_request = None
        process, connection = self._process, self._connection
        self._process = self._connection = None
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        if process is None:
            if active_request is not None:
                _discard_request_dirs(*active_request)
            return
        if process.is_alive():
            process.terminate()
        process.join(timeout=5.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=5.0)
        if active_request is not None:
            _discard_request_dirs(*active_request)

    def close(self) -> None:
        """Ask the worker to exit cleanly, falling back to termination."""
        if self._in_process:
            self.terminate()
            return
        process, connection = self._process, self._connection
        if process is None:
            return
        try:
            if process.is_alive() and connection is not None:
                connection.send({"kind": "close"})
                if connection.poll(5.0):
                    connection.recv()
        except (BrokenPipeError, EOFError, OSError):
            pass
        finally:
            self.terminate()
