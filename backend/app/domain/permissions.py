from app.domain.enums import Role


def can_view_bug(*, role: str, linked_to_customer: bool, on_product_team: bool) -> bool:
    if role in (Role.ADMIN, Role.MANAGEMENT):
        return True
    if role in (Role.CUSTOMER, Role.TESTER):
        return linked_to_customer
    if role == Role.DEVELOPER:
        return on_product_team
    return False


def can_submit_bug(role: str) -> bool:
    return role in (Role.CUSTOMER, Role.TESTER)


def delivery_allowed(
    *,
    audience: str,
    recipient_role: str,
    recipient_active: bool,
    is_reporter: bool,
    linked_to_customer: bool,
    on_team: bool,
) -> bool:
    if not recipient_active:
        return False
    if audience == "conversation":
        return True
    if audience == "customer":
        if is_reporter and recipient_role in (Role.CUSTOMER, Role.TESTER):
            return True
        return recipient_role == Role.CUSTOMER and linked_to_customer
    if audience == "developer":
        return on_team and recipient_role in (Role.DEVELOPER, Role.ADMIN)
    if audience == "management":
        return recipient_role == Role.MANAGEMENT
    return False
