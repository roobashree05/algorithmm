from app.core.config import Settings
from app.integrations.whatsapp.cloud_api import CloudWhatsAppProvider
from app.integrations.whatsapp.mock import MockWhatsAppProvider


def build_whatsapp_provider(settings: Settings):
    if settings.whatsapp_provider == "cloud_api":
        return CloudWhatsAppProvider(
            access_token=settings.whatsapp_access_token,
            phone_number_id=settings.whatsapp_phone_number_id,
            api_version=settings.whatsapp_api_version,
            groups_enabled=settings.whatsapp_groups_enabled,
            timeout=10.0,
        )
    return MockWhatsAppProvider()
