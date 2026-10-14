import logging

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_token
from app.db.demo_data import DEMO_CUSTOMERS, DEMO_PRODUCTS, DEMO_USERS
from app.db.session import build_engine, build_session_factory
from app.domain.models import Customer, CustomerUser, DeveloperTeam, Product, TeamMember, User
from app.services.admin import AdminService

logger = logging.getLogger(__name__)


def seed(session) -> None:
    customers = {}
    for name, code in DEMO_CUSTOMERS:
        customer = session.scalar(select(Customer).where(Customer.code == code))
        if customer is None:
            customer = Customer(name=name, code=code, is_active=True)
            session.add(customer)
            session.flush()
        customers[code] = customer

    teams = {}
    for _name, slug, _project_id, _path, _labels in DEMO_PRODUCTS:
        team = session.scalar(select(DeveloperTeam).where(DeveloperTeam.slug == slug))
        if team is None:
            team = DeveloperTeam(name=f"{_name} Team", slug=slug)
            session.add(team)
            session.flush()
        teams[slug] = team

    users = {}
    for item in DEMO_USERS:
        user = session.scalar(select(User).where(User.email == item["email"]))
        if user is None:
            user = User(
                email=item["email"],
                display_name=item["display_name"],
                role=item["role"],
                phone_e164=item["phone_e164"],
                token_hash=hash_token(item["token"]),
                is_active=True,
            )
            session.add(user)
            session.flush()
        users[item["email"]] = user
        if item["customer_code"]:
            customer = customers[item["customer_code"]]
            link = session.scalar(
                select(CustomerUser).where(CustomerUser.customer_id == customer.id, CustomerUser.user_id == user.id)
            )
            if link is None:
                session.add(CustomerUser(customer_id=customer.id, user_id=user.id))
        for team_slug in item["teams"]:
            team = teams[team_slug]
            member = session.scalar(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user.id))
            if member is None:
                session.add(TeamMember(team_id=team.id, user_id=user.id))

    for name, slug, project_id, path, labels in DEMO_PRODUCTS:
        product = session.scalar(select(Product).where(Product.slug == slug))
        if product is None:
            session.add(
                Product(
                    name=name,
                    slug=slug,
                    gitlab_project_id=project_id,
                    gitlab_project_path=path,
                    developer_team_id=teams[slug].id,
                    default_labels=labels,
                    is_active=True,
                )
            )
    session.commit()
    AdminService(session).ensure_default_rules()
    logger.info("demo data ready")


def main() -> None:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    session = factory()
    try:
        seed(session)
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    main()
