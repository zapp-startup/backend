# Generated manually - add merchant overrides for airlines, fast food, electronics

from django.db import migrations


def add_merchant_rules(apps, schema_editor):
    MerchantCategoryRule = apps.get_model("banking", "MerchantCategoryRule")
    TRAVEL = "travel"
    FLIGHTS = "flights"
    DINING_CAFES = "dining_cafes"
    FAST_FOOD = "fast_food"
    SHOPPING = "shopping"
    ELECTRONICS = "electronics"

    rules = [
        # Airlines -> Travel / Flights
        ("united airlines", TRAVEL, FLIGHTS, 100),
        ("united airline", TRAVEL, FLIGHTS, 100),
        ("delta", TRAVEL, FLIGHTS, 100),
        ("american airlines", TRAVEL, FLIGHTS, 100),
        ("southwest", TRAVEL, FLIGHTS, 100),
        ("jetblue", TRAVEL, FLIGHTS, 100),
        ("alaska airlines", TRAVEL, FLIGHTS, 100),
        ("spirit airlines", TRAVEL, FLIGHTS, 100),
        ("frontier airlines", TRAVEL, FLIGHTS, 100),
        ("allegiant", TRAVEL, FLIGHTS, 100),
        ("british airways", TRAVEL, FLIGHTS, 100),
        ("lufthansa", TRAVEL, FLIGHTS, 100),
        ("emirates", TRAVEL, FLIGHTS, 100),
        # Fast food -> Dining & Cafes / Fast Food
        ("mcdonald", DINING_CAFES, FAST_FOOD, 100),
        ("mcdonalds", DINING_CAFES, FAST_FOOD, 100),
        ("burger king", DINING_CAFES, FAST_FOOD, 100),
        ("wendy", DINING_CAFES, FAST_FOOD, 100),
        ("wendys", DINING_CAFES, FAST_FOOD, 100),
        ("taco bell", DINING_CAFES, FAST_FOOD, 100),
        ("chipotle", DINING_CAFES, FAST_FOOD, 100),
        ("subway", DINING_CAFES, FAST_FOOD, 100),
        ("pizza hut", DINING_CAFES, FAST_FOOD, 100),
        ("dominos", DINING_CAFES, FAST_FOOD, 100),
        ("domino", DINING_CAFES, FAST_FOOD, 100),
        ("kfc", DINING_CAFES, FAST_FOOD, 100),
        ("popeyes", DINING_CAFES, FAST_FOOD, 100),
        ("chick-fil-a", DINING_CAFES, FAST_FOOD, 100),
        ("chick fil a", DINING_CAFES, FAST_FOOD, 100),
        ("five guys", DINING_CAFES, FAST_FOOD, 100),
        ("in-n-out", DINING_CAFES, FAST_FOOD, 100),
        ("panda express", DINING_CAFES, FAST_FOOD, 100),
        ("panera", DINING_CAFES, FAST_FOOD, 100),
        # Electronics / maker / tooling -> Shopping / Electronics
        ("sparkfun", SHOPPING, ELECTRONICS, 100),
        ("adafruit", SHOPPING, ELECTRONICS, 100),
        ("digikey", SHOPPING, ELECTRONICS, 100),
        ("mouser", SHOPPING, ELECTRONICS, 100),
        ("micro center", SHOPPING, ELECTRONICS, 100),
        ("best buy", SHOPPING, ELECTRONICS, 100),
        ("newegg", SHOPPING, ELECTRONICS, 100),
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


def reverse_add(apps, schema_editor):
    MerchantCategoryRule = apps.get_model("banking", "MerchantCategoryRule")
    patterns = [
        "united airlines", "united airline", "delta", "american airlines",
        "southwest", "jetblue", "alaska airlines", "spirit airlines",
        "frontier airlines", "allegiant", "british airways", "lufthansa", "emirates",
        "mcdonald", "mcdonalds", "burger king", "wendy", "wendys", "taco bell",
        "chipotle", "subway", "pizza hut", "dominos", "domino", "kfc", "popeyes",
        "chick-fil-a", "chick fil a", "five guys", "in-n-out", "panda express", "panera",
        "sparkfun", "adafruit", "digikey", "mouser", "micro center", "best buy", "newegg",
    ]
    MerchantCategoryRule.objects.filter(match_pattern__in=patterns).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("banking", "0004_seed_merchant_category_rules"),
    ]

    operations = [
        migrations.RunPython(add_merchant_rules, reverse_add),
    ]
