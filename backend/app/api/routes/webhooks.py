from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse

from app.api.deps import get_webhook_service
from app.services.webhooks import WebhookService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("/whatsapp")
def verify_whatsapp(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
    service: WebhookService = Depends(get_webhook_service),
):
    verified = service.verify_whatsapp(mode, token, challenge)
    return PlainTextResponse(verified)


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, service: WebhookService = Depends(get_webhook_service)):
    body = await request.body()
    return service.handle_whatsapp(body, request.headers.get("x-hub-signature-256"))


@router.post("/gitlab")
async def gitlab_webhook(request: Request, service: WebhookService = Depends(get_webhook_service)):
    body = await request.body()
    return service.handle_gitlab(
        body,
        request.headers.get("x-gitlab-token"),
        request.headers.get("x-gitlab-event-uuid"),
    )
