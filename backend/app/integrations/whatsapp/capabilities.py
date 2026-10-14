def group_messaging_available(*, groups_enabled: bool, provider: str) -> bool:
    """Ordinary WhatsApp groups are not exposed through the Cloud API.

    The Business Platform has limited group features that require an explicit
    capability grant. This build keeps group sends disabled and delivers
    notifications as individual chats.
    """
    del groups_enabled, provider
    return False


def assert_individual_destination(destination: str) -> str:
    if "@g.us" in destination or destination.endswith("@g.us"):
        raise ValueError("WhatsApp group destinations are not supported; use an individual phone number")
    return destination
