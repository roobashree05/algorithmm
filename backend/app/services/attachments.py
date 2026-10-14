import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationAppError
from app.domain.models import BugAttachment

MIME_SUFFIX = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
ALLOWED_TYPES = set(MIME_SUFFIX)


def detect_image_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def normalize_content_type(value: str | None) -> str:
    content_type = (value or "").split(";")[0].strip().lower()
    if content_type == "image/jpg":
        return "image/jpeg"
    return content_type


def safe_filename(name: str | None, content_type: str) -> str:
    base = os.path.basename(name or "")
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base.replace("\x00", ""))[:80]
    suffix = MIME_SUFFIX[content_type]
    if not cleaned or cleaned in {".", ".."}:
        return f"screenshot{suffix}"
    if not cleaned.lower().endswith(suffix) and not (content_type == "image/jpeg" and cleaned.lower().endswith(".jpeg")):
        stem = cleaned.rsplit(".", 1)[0]
        return f"{stem}{suffix}"
    return cleaned


class AttachmentStore:
    def __init__(self, root: str):
        self.root = Path(root)
        (self.root / "tmp").mkdir(parents=True, exist_ok=True)
        (self.root / "bugs").mkdir(parents=True, exist_ok=True)

    def save_temp(self, data: bytes, filename: str) -> str:
        key = f"tmp/{uuid.uuid4().hex}_{filename}"
        path = self._resolve(key)
        path.write_bytes(data)
        return key

    def move_to_bug(self, storage_key: str, bug_id: uuid.UUID) -> str:
        source = self._resolve(storage_key)
        destination_key = f"bugs/{bug_id}/{source.name}"
        destination = self._resolve(destination_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source != destination:
            source.replace(destination)
        return destination_key

    def read(self, storage_key: str) -> bytes:
        return self._resolve(storage_key).read_bytes()

    def delete(self, storage_key: str) -> None:
        path = self._resolve(storage_key)
        if path.exists():
            path.unlink()

    def _resolve(self, storage_key: str) -> Path:
        if not storage_key or storage_key.startswith(("/", "\\")) or ".." in Path(storage_key).parts:
            raise ValidationAppError("Invalid attachment path")
        root = self.root.resolve()
        path = (root / storage_key).resolve()
        if path != root and root not in path.parents:
            raise ValidationAppError("Invalid attachment path")
        return path


class AttachmentService:
    def __init__(self, session: Session, store: AttachmentStore, max_bytes: int):
        self.session = session
        self.store = store
        self.max_bytes = max_bytes

    def store_temp(
        self,
        data: bytes,
        declared_type: str | None,
        filename: str | None,
        conversation_id: uuid.UUID | None = None,
    ) -> BugAttachment:
        content_type = self._validate(data, declared_type)
        safe_name = safe_filename(filename, content_type)
        key = self.store.save_temp(data, safe_name)
        attachment = BugAttachment(
            conversation_id=conversation_id,
            storage_key=key,
            content_type=content_type,
            size_bytes=len(data),
            original_filename=safe_name,
        )
        self.session.add(attachment)
        self.session.flush()
        return attachment

    def bind_to_bug(self, attachment: BugAttachment, bug_id: uuid.UUID) -> None:
        if attachment.bug_report_id and attachment.bug_report_id != bug_id:
            raise ValidationAppError("Attachment is already linked to another report")
        if not str(attachment.storage_key).startswith(f"bugs/{bug_id}/"):
            attachment.storage_key = self.store.move_to_bug(attachment.storage_key, bug_id)
        attachment.bug_report_id = bug_id

    def cleanup_temporary(self, older_than: datetime) -> int:
        rows = list(
            self.session.scalars(
                select(BugAttachment).where(
                    BugAttachment.bug_report_id.is_(None),
                    BugAttachment.created_at < older_than,
                )
            ).all()
        )
        removed = 0
        for row in rows:
            self.store.delete(row.storage_key)
            self.session.delete(row)
            removed += 1
        tmp_dir = self.store.root / "tmp"
        if tmp_dir.exists():
            for path in tmp_dir.iterdir():
                if not path.is_file():
                    continue
                modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                if modified < older_than:
                    path.unlink(missing_ok=True)
                    removed += 1
        return removed

    def _validate(self, data: bytes, declared_type: str | None) -> str:
        if not data:
            raise ValidationAppError("Screenshot is empty")
        if len(data) > self.max_bytes:
            raise ValidationAppError("Screenshot exceeds the size limit")
        detected = detect_image_type(data)
        if detected not in ALLOWED_TYPES:
            raise ValidationAppError("Screenshot must be a JPEG, PNG, or WebP image")
        declared = normalize_content_type(declared_type) if declared_type else detected
        if declared not in ALLOWED_TYPES or declared != detected:
            raise ValidationAppError("Screenshot content type does not match the file")
        return detected
