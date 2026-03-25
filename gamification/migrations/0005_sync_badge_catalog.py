from django.db import migrations

from gamification.badges import BADGE_CATALOG


def sync_badges(apps, schema_editor):
    Badge = apps.get_model("gamification", "Badge")
    active_codes = set()

    for badge in BADGE_CATALOG:
        active_codes.add(badge["code"])
        Badge.objects.update_or_create(
            code=badge["code"],
            defaults={
                "name": badge["name"],
                "description": badge["description"],
                "icon": badge["icon"],
                "category": badge["category"],
                "is_active": True,
            },
        )

    Badge.objects.exclude(code__in=active_codes).update(is_active=False)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("gamification", "0004_alter_badge_category_alter_groupinvite_status_and_more"),
    ]

    operations = [
        migrations.RunPython(sync_badges, noop),
    ]
