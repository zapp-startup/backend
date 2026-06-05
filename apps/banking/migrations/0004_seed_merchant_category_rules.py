# Generated manually for Zapp categorization merchant override rules

from django.db import migrations


def seed_merchant_rules(apps, schema_editor):
    MerchantCategoryRule = apps.get_model("banking", "MerchantCategoryRule")
    # Use raw choice values (same as ZappPrimaryCategory / ZappSubcategory enums)
    SUBSCRIPTIONS = "subscriptions"
    STREAMING = "streaming"
    MUSIC = "music"
    SOFTWARE_PRODUCTIVITY = "software_productivity"
    DINING_CAFES = "dining_cafes"
    DELIVERY = "delivery"
    COFFEE = "coffee"
    TRANSPORTATION = "transportation"
    RIDE_SHARE = "ride_share"
    SHOPPING = "shopping"
    GENERAL = "general"
    FINANCIAL_TRANSFERS = "financial_transfers"
    PEER_TRANSFER = "peer_transfer"
    INCOME = "income"
    PAYROLL = "payroll"

    rules = [
        # Streaming
        ("netflix", SUBSCRIPTIONS, STREAMING, 100),
        ("hulu", SUBSCRIPTIONS, STREAMING, 100),
        ("disney+", SUBSCRIPTIONS, STREAMING, 100),
        ("disney plus", SUBSCRIPTIONS, STREAMING, 100),
        ("hbo max", SUBSCRIPTIONS, STREAMING, 100),
        ("hbo max", SUBSCRIPTIONS, STREAMING, 100),
        ("peacock", SUBSCRIPTIONS, STREAMING, 100),
        ("paramount", SUBSCRIPTIONS, STREAMING, 100),
        ("apple tv", SUBSCRIPTIONS, STREAMING, 100),
        ("prime video", SUBSCRIPTIONS, STREAMING, 100),
        # Music
        ("spotify", SUBSCRIPTIONS, MUSIC, 100),
        ("apple music", SUBSCRIPTIONS, MUSIC, 100),
        ("youtube premium", SUBSCRIPTIONS, MUSIC, 100),
        ("youtube music", SUBSCRIPTIONS, MUSIC, 100),
        ("tidal", SUBSCRIPTIONS, MUSIC, 100),
        ("pandora", SUBSCRIPTIONS, MUSIC, 100),
        # Software / Productivity
        ("adobe", SUBSCRIPTIONS, SOFTWARE_PRODUCTIVITY, 100),
        ("notion", SUBSCRIPTIONS, SOFTWARE_PRODUCTIVITY, 100),
        ("chatgpt", SUBSCRIPTIONS, SOFTWARE_PRODUCTIVITY, 100),
        ("openai", SUBSCRIPTIONS, SOFTWARE_PRODUCTIVITY, 100),
        ("github copilot", SUBSCRIPTIONS, SOFTWARE_PRODUCTIVITY, 100),
        ("copilot", SUBSCRIPTIONS, SOFTWARE_PRODUCTIVITY, 90),  # lower priority - could match many
        ("microsoft 365", SUBSCRIPTIONS, SOFTWARE_PRODUCTIVITY, 100),
        ("office 365", SUBSCRIPTIONS, SOFTWARE_PRODUCTIVITY, 100),
        ("dropbox", SUBSCRIPTIONS, SOFTWARE_PRODUCTIVITY, 100),
        ("google one", SUBSCRIPTIONS, SOFTWARE_PRODUCTIVITY, 100),
        ("icloud", SUBSCRIPTIONS, SOFTWARE_PRODUCTIVITY, 100),
        # Delivery
        ("uber eats", DINING_CAFES, DELIVERY, 100),
        ("doordash", DINING_CAFES, DELIVERY, 100),
        ("grubhub", DINING_CAFES, DELIVERY, 100),
        ("postmates", DINING_CAFES, DELIVERY, 100),
        ("instacart", DINING_CAFES, DELIVERY, 100),
        # Coffee
        ("starbucks", DINING_CAFES, COFFEE, 100),
        ("dunkin", DINING_CAFES, COFFEE, 100),
        ("dunkin donuts", DINING_CAFES, COFFEE, 100),
        ("peets", DINING_CAFES, COFFEE, 100),
        ("peet's", DINING_CAFES, COFFEE, 100),
        ("caribou", DINING_CAFES, COFFEE, 100),
        ("philz", DINING_CAFES, COFFEE, 100),
        # Ride share
        ("uber", TRANSPORTATION, RIDE_SHARE, 100),
        ("lyft", TRANSPORTATION, RIDE_SHARE, 100),
        # Shopping
        ("amazon", SHOPPING, GENERAL, 100),
        # Peer transfer
        ("venmo", FINANCIAL_TRANSFERS, PEER_TRANSFER, 100),
        ("zelle", FINANCIAL_TRANSFERS, PEER_TRANSFER, 100),
        ("cash app", FINANCIAL_TRANSFERS, PEER_TRANSFER, 100),
        ("paypal", FINANCIAL_TRANSFERS, PEER_TRANSFER, 90),
        # Payroll / Income
        ("payroll", INCOME, PAYROLL, 100),
        ("direct deposit", INCOME, PAYROLL, 100),
        ("salary", INCOME, PAYROLL, 100),
        ("paycheck", INCOME, PAYROLL, 100),
        ("adp", INCOME, PAYROLL, 100),
        ("paychex", INCOME, PAYROLL, 100),
        ("gusto", INCOME, PAYROLL, 100),
    ]

    for pattern, primary, sub, priority in rules:
        MerchantCategoryRule.objects.get_or_create(
            match_pattern=pattern,
            defaults={
                "zapp_primary_category": primary,
                "zapp_subcategory": sub,
                "priority": priority,
                "is_active": True,
            },
        )


def reverse_seed(apps, schema_editor):
    MerchantCategoryRule = apps.get_model("banking", "MerchantCategoryRule")
    MerchantCategoryRule.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("banking", "0003_zapp_categorization"),
    ]

    operations = [
        migrations.RunPython(seed_merchant_rules, reverse_seed),
    ]
