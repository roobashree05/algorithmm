import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import Role
from app.domain.models import BugReport, Customer, CustomerUser, Product, TeamMember, User


def customers_for_user(session: Session, user_id: uuid.UUID) -> list[Customer]:
    return list(
        session.scalars(
            select(Customer)
            .join(CustomerUser, CustomerUser.customer_id == Customer.id)
            .where(CustomerUser.user_id == user_id, Customer.is_active.is_(True))
            .order_by(Customer.code)
        ).all()
    )


def linked_to_customer(session: Session, user_id: uuid.UUID, customer_id: uuid.UUID) -> bool:
    return (
        session.scalar(
            select(CustomerUser.id).where(CustomerUser.user_id == user_id, CustomerUser.customer_id == customer_id)
        )
        is not None
    )


def on_team(session: Session, user_id: uuid.UUID, team_id: uuid.UUID) -> bool:
    return (
        session.scalar(select(TeamMember.id).where(TeamMember.user_id == user_id, TeamMember.team_id == team_id))
        is not None
    )


def user_by_phone(session: Session, phone_e164: str) -> User | None:
    return session.scalar(select(User).where(User.phone_e164 == phone_e164, User.is_active.is_(True)))


def user_by_token_hash(session: Session, token_hash: str) -> User | None:
    return session.scalar(select(User).where(User.token_hash == token_hash, User.is_active.is_(True)))


def visible_bug_statement(user: User):
    statement = select(BugReport)
    if user.role in (Role.ADMIN, Role.MANAGEMENT):
        return statement
    if user.role in (Role.CUSTOMER, Role.TESTER):
        customer_ids = select(CustomerUser.customer_id).where(CustomerUser.user_id == user.id)
        return statement.where(BugReport.customer_id.in_(customer_ids))
    if user.role == Role.DEVELOPER:
        team_ids = select(TeamMember.team_id).where(TeamMember.user_id == user.id)
        product_ids = select(Product.id).where(Product.developer_team_id.in_(team_ids))
        return statement.where(BugReport.product_id.in_(product_ids))
    return statement.where(BugReport.id.is_(None))
