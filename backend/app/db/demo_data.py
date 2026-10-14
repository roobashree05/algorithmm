"""Fixed local demo credentials. These are not production secrets."""

DEMO_USERS = [
    {
        "email": "admin@local.test",
        "display_name": "Local Admin",
        "role": "ADMIN",
        "phone_e164": "+15550001000",
        "token": "local-admin-token",
        "customer_code": None,
        "teams": [],
    },
    {
        "email": "customer@local.test",
        "display_name": "Northwind Reporter",
        "role": "CUSTOMER",
        "phone_e164": "+15550001001",
        "token": "local-customer-token",
        "customer_code": "northwind",
        "teams": [],
    },
    {
        "email": "contoso@local.test",
        "display_name": "Contoso Reporter",
        "role": "CUSTOMER",
        "phone_e164": "+15550001002",
        "token": "local-customer-2-token",
        "customer_code": "contoso",
        "teams": [],
    },
    {
        "email": "tester@local.test",
        "display_name": "Northwind Tester",
        "role": "TESTER",
        "phone_e164": "+15550001003",
        "token": "local-tester-token",
        "customer_code": "northwind",
        "teams": [],
    },
    {
        "email": "developer@local.test",
        "display_name": "Platform Developer",
        "role": "DEVELOPER",
        "phone_e164": "+15550001004",
        "token": "local-developer-token",
        "customer_code": None,
        "teams": ["try-on", "inventory", "recommendation-engine", "design-studio"],
    },
    {
        "email": "management@local.test",
        "display_name": "Operations Manager",
        "role": "MANAGEMENT",
        "phone_e164": "+15550001005",
        "token": "local-management-token",
        "customer_code": None,
        "teams": [],
    },
]

DEMO_CUSTOMERS = [
    ("Northwind Traders", "northwind"),
    ("Contoso Ltd", "contoso"),
]

DEMO_PRODUCTS = [
    ("Try-On", "try-on", "1001", "demo/try-on", ["bug", "whatsapp", "try-on", "customer-reported"]),
    ("Inventory", "inventory", "1002", "demo/inventory", ["bug", "whatsapp", "inventory", "customer-reported"]),
    (
        "Recommendation Engine",
        "recommendation-engine",
        "1003",
        "demo/recommendation-engine",
        ["bug", "whatsapp", "recommendation-engine", "customer-reported"],
    ),
    (
        "Design Studio",
        "design-studio",
        "1004",
        "demo/design-studio",
        ["bug", "whatsapp", "design-studio", "customer-reported"],
    ),
]
