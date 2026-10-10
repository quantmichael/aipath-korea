"""Raw bytes live outside Agent metadata tables, behind a replaceable port."""

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from agent_collect.schemas.validation import require_aware


@dataclass(frozen=True, slots=True)
class StorageReceipt:
    ref: str
    content_sha256: str
    byte_count: int
    stored_at: datetime


class RawContentStorage(Protocol):
    def put(
        self, content: bytes, *, expected_sha256: str | None = None,
        stored_at: datetime | None = None,
    ) -> StorageReceipt: ...

    def get(self, ref: str) -> bytes: ...

    def exists(self, ref: str) -> bool: ...

    def delete(self, ref: str) -> None: ...

    def purge_expired(self, *, now: datetime | None = None) -> int: ...


_REF_PATTERN = re.compile(r"^local:(\d{4})/(\d{2})/(\d{2})/([0-9a-f]{64}-[0-9a-f]{32}\.raw)$")


class LocalRawContentStorage:
    delete_orphan_on_metadata_failure = True
    ref_prefix = "local:"
    def __init__(self, root: Path, *, retention_days: int = 90) -> None:
        if retention_days <= 0:
            raise ValueError("retention_days must be positive")
        self.root = Path(root).resolve()
        self.retention_days = retention_days

    def put(
        self, content: bytes, *, expected_sha256: str | None = None,
        stored_at: datetime | None = None,
    ) -> StorageReceipt:
        if not isinstance(content, bytes) or not content:
            raise ValueError("raw content must be non-empty bytes")
        digest = hashlib.sha256(content).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError("raw content hash mismatch")
        timestamp = stored_at or datetime.now(timezone.utc)
        require_aware(timestamp, "stored_at")
        date_path = Path(timestamp.strftime("%Y/%m/%d"))
        directory = self.root / date_path
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{digest}-{uuid4().hex}.raw"
        path = directory / filename
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return StorageReceipt(
            ref=f"local:{date_path.as_posix()}/{filename}",
            content_sha256=digest,
            byte_count=len(content),
            stored_at=timestamp,
        )

    def get(self, ref: str) -> bytes:
        path = self._path(ref)
        content = path.read_bytes()
        expected_digest = path.name.split("-", 1)[0]
        if hashlib.sha256(content).hexdigest() != expected_digest:
            raise ValueError("stored raw content hash mismatch")
        return content

    def delete(self, ref: str) -> None:
        self._path(ref).unlink(missing_ok=True)

    def exists(self, ref: str) -> bool:
        return self._path(ref).is_file()

    def purge_expired(self, *, now: datetime | None = None) -> int:
        timestamp = now or datetime.now(timezone.utc)
        require_aware(timestamp, "now")
        cutoff = timestamp - timedelta(days=self.retention_days)
        if not self.root.exists():
            return 0
        removed = 0
        for directory, _, filenames in os.walk(self.root, followlinks=False):
            for filename in filenames:
                path = Path(directory) / filename
                relative = path.relative_to(self.root).as_posix()
                if not _REF_PATTERN.fullmatch(f"local:{relative}") or path.is_symlink():
                    continue
                if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                    path.unlink()
                    removed += 1
        return removed

    def _path(self, ref: str) -> Path:
        match = _REF_PATTERN.fullmatch(ref)
        if not match:
            raise ValueError("invalid local raw content reference")
        relative = Path(*match.groups()[:3], match.group(4))
        path = self.root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError("raw content reference escapes storage root")
        return path
