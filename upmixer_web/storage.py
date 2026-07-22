"""Source, sink, and object storage boundaries."""

from __future__ import annotations

import hashlib
import shutil
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO


class ObjectStorage(ABC):
    """Minimal blob interface implementable by a local disk or S3."""

    @abstractmethod
    def put_stream(self, key: str, stream: BinaryIO) -> tuple[int, str]:
        """Store bytes and return size and SHA-256 digest."""

    @abstractmethod
    def put_file(self, key: str, source: Path) -> int:
        """Store a file and return its size."""

    @abstractmethod
    def open(self, key: str) -> BinaryIO:
        """Open a stored object for reading."""

    @abstractmethod
    def local_path(self, key: str) -> Path:
        """Materialize an object as a local worker-readable path."""

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None:
        """Delete objects beneath an application-owned prefix."""


class LocalObjectStorage(ObjectStorage):
    """Filesystem object storage used by the first deployment target."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        normalized = PurePosixPath(key)
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ValueError("Storage key must be a relative path")
        path = (self.root / Path(*normalized.parts)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Storage key escapes storage root")
        return path

    def put_stream(self, key: str, stream: BinaryIO) -> tuple[int, str]:
        destination = self._resolve(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.upload")
        digest = hashlib.sha256()
        size = 0
        with temporary.open("wb") as handle:
            while chunk := stream.read(1024 * 1024):
                handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        temporary.replace(destination)
        return size, digest.hexdigest()

    def put_file(self, key: str, source: Path) -> int:
        destination = self._resolve(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination:
            temporary = destination.with_name(f".{destination.name}.copy")
            shutil.copyfile(source, temporary)
            temporary.replace(destination)
        return destination.stat().st_size

    def open(self, key: str) -> BinaryIO:
        return self._resolve(key).open("rb")

    def local_path(self, key: str) -> Path:
        path = self._resolve(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path

    def delete_prefix(self, prefix: str) -> None:
        path = self._resolve(prefix)
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


class AudioSource(ABC):
    """Materializes input audio for a worker."""

    @abstractmethod
    @contextmanager
    def materialize(self, storage_key: str) -> Iterator[Path]:
        """Yield a local path for processing."""


class StorageAudioSource(AudioSource):
    """Source backed by configured object storage."""

    def __init__(self, storage: ObjectStorage):
        self.storage = storage

    @contextmanager
    def materialize(self, storage_key: str) -> Iterator[Path]:
        yield self.storage.local_path(storage_key)


class AudioSink(ABC):
    """Accepts a pipeline export and returns storage metadata."""

    @abstractmethod
    def store(self, key: str, path: Path) -> tuple[str, int]:
        """Store output, returning key and size."""


class StorageAudioSink(AudioSink):
    """Output sink backed by configured object storage."""

    def __init__(self, storage: ObjectStorage):
        self.storage = storage

    def store(self, key: str, path: Path) -> tuple[str, int]:
        return key, self.storage.put_file(key, path)
