from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, UnauthorizedError, ValidationAppError
from app.core.security import hash_token
from app.domain.models import Customer, Product, User
from app.domain.permissions import can_submit_bug
from app.repositories.access import customers_for_user, user_by_token_hash


class IdentityService:
    def __init__(self, session: Session):
        self.session = session

    def user_from_token(self, token: str) -> User:
        user = user_by_token_hash(self.session, hash_token(token))
        if user is None:
            raise UnauthorizedError()
        return user

    def resolve_customer(self, user: User, customer_code: str | None) -> Customer:
        if not can_submit_bug(user.role):
            raise ForbiddenError("Only customers and testers can submit bug reports")
        links = customers_for_user(self.session, user.id)
        if not links:
            raise ForbiddenError("No customer account is assigned to this user")
        if customer_code is None:
            if len(links) != 1:
                raise ValidationAppError("customer_code is required when multiple customer accounts are assigned")
            return links[0]
        for customer in links:
            if customer.code == customer_code:
                return customer
        raise ForbiddenError("The user is not authorized for that customer account")

    def resolve_product(self, slug: str) -> Product:
        product = self.session.scalar(select(Product).where(Product.slug == slug, Product.is_active.is_(True)))
        if product is None:
            raise ValidationAppError("Unknown or inactive product")
        if not product.gitlab_project_id or not product.developer_team_id:
            raise ValidationAppError("Product is not mapped to a GitLab project and developer team")
        return product
