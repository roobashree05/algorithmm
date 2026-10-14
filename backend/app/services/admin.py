import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.security import hash_token, normalize_phone
from app.domain.enums import KNOWN_RULES, Role
from app.domain.models import Customer, CustomerUser, DeveloperTeam, NotificationRule, Product, TeamMember, User
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
from app.services.audit import add_audit


class AdminService:
    def __init__(self, session: Session):
        self.session = session

    def create_customer(self, actor: User, payload: CustomerCreate) -> CustomerRead:
        customer = Customer(name=payload.name, code=payload.code, is_active=True)
        self._add(customer, "Customer code already exists")
        add_audit(self.session, action="customer_created", entity_type="customer", entity_id=str(customer.id), actor_user_id=actor.id, detail={"code": customer.code})
        self.session.commit()
        return CustomerRead(id=customer.id, name=customer.name, code=customer.code, is_active=customer.is_active)

    def list_customers(self) -> list[CustomerRead]:
        rows = self.session.scalars(select(Customer).order_by(Customer.code)).all()
        return [CustomerRead(id=row.id, name=row.name, code=row.code, is_active=row.is_active) for row in rows]

    def create_team(self, actor: User, payload: TeamCreate) -> TeamRead:
        team = self.session.scalar(select(DeveloperTeam).where(DeveloperTeam.slug == payload.slug))
        if team is None:
            team = DeveloperTeam(name=payload.name, slug=payload.slug)
            self._add(team, "Team slug already exists")
        else:
            team.name = payload.name
        emails = []
        for email in payload.member_emails:
            user = self.session.scalar(select(User).where(User.email == email))
            if user is None:
                raise NotFoundError(f"User {email} was not found")
            if user.role not in {Role.DEVELOPER, Role.ADMIN}:
                raise ValidationAppError(f"{email} must have the DEVELOPER or ADMIN role to join a team")
            existing = self.session.scalar(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user.id))
            if existing is None:
                self.session.add(TeamMember(team_id=team.id, user_id=user.id))
            emails.append(email)
        add_audit(self.session, action="team_saved", entity_type="developer_team", entity_id=str(team.id), actor_user_id=actor.id, detail={"slug": team.slug})
        self.session.commit()
        return self._team_read(team)

    def list_teams(self) -> list[TeamRead]:
        teams = self.session.scalars(select(DeveloperTeam).order_by(DeveloperTeam.slug)).all()
        return [self._team_read(team) for team in teams]

    def create_product(self, actor: User, payload: ProductCreate) -> ProductRead:
        team = self.session.scalar(select(DeveloperTeam).where(DeveloperTeam.slug == payload.developer_team_slug))
        if team is None:
            raise NotFoundError("Developer team was not found")
        product = self.session.scalar(select(Product).where(Product.slug == payload.slug))
        if product is None:
            product = Product(
                name=payload.name,
                slug=payload.slug,
                gitlab_project_id=payload.gitlab_project_id,
                gitlab_project_path=payload.gitlab_project_path,
                developer_team_id=team.id,
                default_assignee_gitlab_id=payload.default_assignee_gitlab_id,
                default_labels=payload.default_labels,
                is_active=payload.is_active,
            )
            self._add(product, "Product slug already exists")
        else:
            product.name = payload.name
            product.gitlab_project_id = payload.gitlab_project_id
            product.gitlab_project_path = payload.gitlab_project_path
            product.developer_team_id = team.id
            product.default_assignee_gitlab_id = payload.default_assignee_gitlab_id
            product.default_labels = payload.default_labels
            product.is_active = payload.is_active
        add_audit(self.session, action="product_saved", entity_type="product", entity_id=str(product.id), actor_user_id=actor.id, detail={"slug": product.slug})
        self.session.commit()
        return self._product_read(product, team.slug)

    def list_products(self) -> list[ProductRead]:
        products = self.session.scalars(select(Product).order_by(Product.slug)).all()
        reads = []
        for product in products:
            team = self.session.get(DeveloperTeam, product.developer_team_id)
            reads.append(self._product_read(product, team.slug if team else ""))
        return reads

    def map_user(self, actor: User, payload: UserMappingCreate) -> UserMappingRead:
        if payload.role in {Role.CUSTOMER, Role.TESTER} and not payload.customer_code:
            raise ValidationAppError("customer_code is required for CUSTOMER and TESTER mappings")
        customer = None
        if payload.customer_code:
            customer = self.session.scalar(select(Customer).where(Customer.code == payload.customer_code))
            if customer is None:
                raise NotFoundError("Customer was not found")
        team = None
        if payload.team_slug:
            team = self.session.scalar(select(DeveloperTeam).where(DeveloperTeam.slug == payload.team_slug))
            if team is None:
                raise NotFoundError("Developer team was not found")
        phone = None
        if payload.phone_e164:
            try:
                phone = normalize_phone(payload.phone_e164)
            except ValueError as exc:
                raise ValidationAppError(str(exc)) from exc
        user = self.session.scalar(select(User).where(User.email == payload.email))
        issued_token = None
        if user is None:
            raw_token = payload.api_token or secrets.token_urlsafe(24)
            issued_token = raw_token
            user = User(
                email=payload.email,
                display_name=payload.display_name,
                role=payload.role,
                phone_e164=phone,
                token_hash=hash_token(raw_token),
                is_active=True,
            )
            self._add(user, "A user with that email, phone, or token already exists")
        else:
            user.display_name = payload.display_name
            user.role = payload.role
            user.is_active = True
            if phone:
                user.phone_e164 = phone
            if payload.api_token:
                user.token_hash = hash_token(payload.api_token)
                issued_token = payload.api_token
        if customer is not None:
            link = self.session.scalar(
                select(CustomerUser).where(CustomerUser.customer_id == customer.id, CustomerUser.user_id == user.id)
            )
            if link is None:
                self.session.add(CustomerUser(customer_id=customer.id, user_id=user.id))
        if team is not None and payload.role in {Role.DEVELOPER, Role.ADMIN}:
            member = self.session.scalar(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user.id))
            if member is None:
                self.session.add(TeamMember(team_id=team.id, user_id=user.id))
        add_audit(
            self.session,
            action="user_mapped",
            entity_type="user",
            entity_id=str(user.id),
            actor_user_id=actor.id,
            detail={"email": user.email, "role": user.role, "customer_code": payload.customer_code},
        )
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError("A user with that email, phone, or token already exists") from exc
        return UserMappingRead(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            phone_e164=user.phone_e164,
            customer_code=payload.customer_code,
            team_slug=payload.team_slug,
            api_token=issued_token,
        )

    def save_rule(self, actor: User, payload: NotificationRuleCreate) -> NotificationRuleRead:
        product_id = None
        product_slug = None
        if payload.product_slug:
            product = self.session.scalar(select(Product).where(Product.slug == payload.product_slug))
            if product is None:
                raise NotFoundError("Product was not found")
            product_id = product.id
            product_slug = product.slug
        rule = self.session.scalar(
            select(NotificationRule).where(
                NotificationRule.event_type == payload.event_type,
                NotificationRule.audience == payload.audience,
                NotificationRule.product_id.is_(None) if product_id is None else NotificationRule.product_id == product_id,
            )
        )
        if rule is None:
            rule = NotificationRule(
                event_type=payload.event_type,
                audience=payload.audience,
                product_id=product_id,
                enabled=payload.enabled,
            )
            self.session.add(rule)
        else:
            rule.enabled = payload.enabled
        add_audit(
            self.session,
            action="notification_rule_saved",
            entity_type="notification_rule",
            entity_id=None,
            actor_user_id=actor.id,
            detail={"event_type": payload.event_type, "audience": payload.audience, "enabled": payload.enabled},
        )
        self.session.commit()
        self.session.refresh(rule)
        return NotificationRuleRead(
            id=rule.id,
            event_type=rule.event_type,
            audience=rule.audience,
            product_slug=product_slug,
            enabled=rule.enabled,
        )

    def ensure_default_rules(self) -> None:
        for event_type, audience in KNOWN_RULES:
            exists = self.session.scalar(
                select(NotificationRule.id).where(
                    NotificationRule.event_type == event_type,
                    NotificationRule.audience == audience,
                    NotificationRule.product_id.is_(None),
                )
            )
            if exists is None:
                self.session.add(NotificationRule(event_type=event_type, audience=audience, enabled=True))
        self.session.commit()

    def _add(self, obj, conflict: str) -> None:
        try:
            with self.session.begin_nested():
                self.session.add(obj)
                self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(conflict) from exc

    def _team_read(self, team: DeveloperTeam) -> TeamRead:
        emails = list(
            self.session.scalars(
                select(User.email)
                .join(TeamMember, TeamMember.user_id == User.id)
                .where(TeamMember.team_id == team.id)
                .order_by(User.email)
            ).all()
        )
        return TeamRead(id=team.id, name=team.name, slug=team.slug, member_emails=emails)

    def _product_read(self, product: Product, team_slug: str) -> ProductRead:
        return ProductRead(
            id=product.id,
            name=product.name,
            slug=product.slug,
            gitlab_project_id=product.gitlab_project_id,
            gitlab_project_path=product.gitlab_project_path,
            developer_team_slug=team_slug,
            default_labels=list(product.default_labels or []),
            default_assignee_gitlab_id=product.default_assignee_gitlab_id,
            is_active=product.is_active,
        )
