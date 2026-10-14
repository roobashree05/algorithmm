from fastapi import APIRouter, Depends

from app.api.deps import get_admin_service, require_admin
from app.domain.models import User
from app.domain.schemas import (
    CustomerCreate,
    CustomerRead,
    NotificationRuleCreate,
    NotificationRuleRead,
    ProductCreate,
    ProductRead,
    TeamCreate,
    TeamRead,
    UserMappingCreate,
    UserMappingRead,
)
from app.services.admin import AdminService

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/customers", response_model=list[CustomerRead])
def list_customers(actor: User = Depends(require_admin), service: AdminService = Depends(get_admin_service)):
    del actor
    return service.list_customers()


@router.post("/customers", response_model=CustomerRead, status_code=201)
def create_customer(
    payload: CustomerCreate,
    actor: User = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
):
    return service.create_customer(actor, payload)


@router.get("/products", response_model=list[ProductRead])
def list_products(actor: User = Depends(require_admin), service: AdminService = Depends(get_admin_service)):
    del actor
    return service.list_products()


@router.post("/products", response_model=ProductRead, status_code=201)
def create_product(
    payload: ProductCreate,
    actor: User = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
):
    return service.create_product(actor, payload)


@router.get("/developer-teams", response_model=list[TeamRead])
def list_teams(actor: User = Depends(require_admin), service: AdminService = Depends(get_admin_service)):
    del actor
    return service.list_teams()


@router.post("/developer-teams", response_model=TeamRead, status_code=201)
def create_team(
    payload: TeamCreate,
    actor: User = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
):
    return service.create_team(actor, payload)


@router.post("/user-mappings", response_model=UserMappingRead, status_code=201)
def map_user(
    payload: UserMappingCreate,
    actor: User = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
):
    return service.map_user(actor, payload)


@router.post("/notification-rules", response_model=NotificationRuleRead, status_code=201)
def save_rule(
    payload: NotificationRuleCreate,
    actor: User = Depends(require_admin),
    service: AdminService = Depends(get_admin_service),
):
    return service.save_rule(actor, payload)
