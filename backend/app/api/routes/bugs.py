import uuid

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_bug_service, get_current_user, get_db
from app.core.errors import ValidationAppError
from app.core.logging import correlation_id_var
from app.domain.models import User
from app.domain.schemas import BugCreate, BugHistory, BugList, BugRead, NoteCreate, QaCreate
from app.services.attachments import AttachmentService, AttachmentStore
from app.services.bugs import BugService

router = APIRouter(prefix="/api/v1/bugs", tags=["bugs"])


@router.post("", response_model=BugRead, status_code=201)
async def create_bug(
    request: Request,
    user: User = Depends(get_current_user),
    service: BugService = Depends(get_bug_service),
    db: Session = Depends(get_db),
):
    payload, upload = await _read_bug_request(request)
    attachment = None
    if upload is not None:
        data = await upload.read()
        settings = request.app.state.settings
        attachment = AttachmentService(db, AttachmentStore(settings.attachment_dir), settings.attachment_max_bytes).store_temp(
            data,
            upload.content_type,
            upload.filename,
        )
    idempotency_key = payload.idempotency_key or request.headers.get("idempotency-key")
    bug = service.create(
        reporter=user,
        product_slug=payload.product_slug,
        description=payload.description,
        severity=payload.severity.value,
        customer_code=payload.customer_code,
        idempotency_key=idempotency_key,
        attachment=attachment,
        correlation_id=correlation_id_var.get(),
    )
    return service.serialize(user, bug)


@router.get("", response_model=BugList)
def list_bugs(
    user: User = Depends(get_current_user),
    service: BugService = Depends(get_bug_service),
    status: str | None = None,
    product_slug: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return service.list_bugs(user, status=status, product_slug=product_slug, limit=limit, offset=offset)


@router.get("/{bug_id}", response_model=BugRead)
def get_bug(bug_id: uuid.UUID, user: User = Depends(get_current_user), service: BugService = Depends(get_bug_service)):
    bug = service.get_bug(user, bug_id)
    return service.serialize(user, bug)


@router.get("/{bug_id}/history", response_model=BugHistory)
def bug_history(bug_id: uuid.UUID, user: User = Depends(get_current_user), service: BugService = Depends(get_bug_service)):
    return service.history(user, bug_id)


@router.post("/{bug_id}/notes", status_code=204)
def add_note(
    bug_id: uuid.UUID,
    payload: NoteCreate,
    user: User = Depends(get_current_user),
    service: BugService = Depends(get_bug_service),
):
    service.add_note(user, bug_id, payload.body, payload.visibility)


@router.post("/{bug_id}/qa", status_code=204)
def record_qa(
    bug_id: uuid.UUID,
    payload: QaCreate,
    user: User = Depends(get_current_user),
    service: BugService = Depends(get_bug_service),
):
    service.record_qa(user, bug_id, payload.result, payload.comment)


async def _read_bug_request(request: Request) -> tuple[BugCreate, object | None]:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        try:
            payload = BugCreate(
                product_slug=str(form.get("product_slug") or ""),
                description=str(form.get("description") or ""),
                severity=str(form.get("severity") or ""),
                customer_code=form.get("customer_code") or None,
                idempotency_key=form.get("idempotency_key") or None,
            )
        except Exception as exc:
            raise ValidationAppError("Invalid bug report") from exc
        upload = form.get("screenshot")
        if upload is not None and not hasattr(upload, "read"):
            upload = None
        return payload, upload
    try:
        body = await request.json()
    except Exception as exc:
        raise ValidationAppError("Invalid JSON") from exc
    try:
        return BugCreate.model_validate(body), None
    except ValidationError as exc:
        raise ValidationAppError("Invalid bug report") from exc
