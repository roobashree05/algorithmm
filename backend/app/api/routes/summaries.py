from fastapi import APIRouter, Depends

from app.api.deps import get_bug_service, require_summary_reader
from app.domain.models import User
from app.domain.schemas import ProductSummary, TeamSummary
from app.services.bugs import BugService

router = APIRouter(prefix="/api/v1/summaries", tags=["summaries"])


@router.get("/products", response_model=list[ProductSummary])
def product_summaries(user: User = Depends(require_summary_reader), service: BugService = Depends(get_bug_service)):
    del user
    return service.product_summaries()


@router.get("/teams", response_model=list[TeamSummary])
def team_summaries(user: User = Depends(require_summary_reader), service: BugService = Depends(get_bug_service)):
    del user
    return service.team_summaries()
