"""Private, content-addressed Supabase Storage for Agent raw bytes."""

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from agent_collect.schemas.validation import require_aware
from agent_collect.storage.raw import StorageReceipt


_BUCKET = re.compile(r"^[a-z][a-z0-9-]{2,62}$")
_REF = re.compile(r"^supabase:([a-z][a-z0-9-]{2,62}):raw/sha256/([0-9a-f]{2})/([0-9a-f]{64})\.raw$")


class SupabaseRawContentStorage:
    # Content-addressed objects can be shared by several RawDocuments. Never
    # delete one on a metadata failure; an orphan sweeper must check references.
    delete_orphan_on_metadata_failure = False

    def __init__(self, client: Any, bucket: str = "agent-raw", *, retention_days: int = 90) -> None:
        if not _BUCKET.fullmatch(bucket) or retention_days <= 0:
            raise ValueError("invalid private raw storage configuration")
        metadata = client.storage.get_bucket(bucket)
        public = metadata.get("public") if isinstance(metadata, dict) else getattr(metadata, "public", None)
        if public is not False:
            raise ValueError("raw storage bucket must be private")
        self.bucket = bucket
        self.objects = client.storage.from_(bucket)
        self.retention_days = retention_days
        self.ref_prefix = f"supabase:{bucket}:"

    @staticmethod
    def key_for(digest: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid SHA-256 digest")
        return f"raw/sha256/{digest[:2]}/{digest}.raw"

    def _key(self, ref: str) -> tuple[str, str]:
        match = _REF.fullmatch(ref)
        if not match or match.group(1) != self.bucket or match.group(2) != match.group(3)[:2]:
            raise ValueError("invalid raw storage reference")
        return self.key_for(match.group(3)), match.group(3)

    def put(self, content: bytes, *, expected_sha256: str | None = None,
            stored_at: datetime | None = None) -> StorageReceipt:
        if not isinstance(content, bytes) or not content:
            raise ValueError("raw content must be non-empty bytes")
        digest = hashlib.sha256(content).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError("raw content hash mismatch")
        timestamp = stored_at or datetime.now(timezone.utc)
        require_aware(timestamp, "stored_at")
        key = self.key_for(digest)
        ref = f"supabase:{self.bucket}:{key}"
        if self.objects.exists(key):
            if self.get(ref) != content:
                raise ValueError("existing raw object hash/content mismatch")
        else:
            try:
                self.objects.upload(key, content, {"content-type": "application/octet-stream", "upsert": "false"})
            except Exception:
                # A concurrent upload may have won the deterministic key. Do
                # not suppress a genuine provider failure or mismatched bytes.
                if not self.objects.exists(key) or self.get(ref) != content:
                    raise
        return StorageReceipt(ref, digest, len(content), timestamp)

    def get(self, ref: str) -> bytes:
        key, digest = self._key(ref)
        content = self.objects.download(key)
        if not isinstance(content, bytes) or hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("stored raw content hash mismatch")
        return content

    def exists(self, ref: str) -> bool:
        key, _ = self._key(ref)
        return bool(self.objects.exists(key))

    def delete(self, ref: str) -> None:
        key, _ = self._key(ref)
        self.objects.remove([key])

    def purge_expired(self, *, now: datetime | None = None) -> int:
        # Shared content-addressed objects require DB-reference-aware cleanup.
        # Do not delete solely from object age.
        raise NotImplementedError("use a reference-aware Agent cleanup job")
