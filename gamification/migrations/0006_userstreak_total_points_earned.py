from django.db import migrations, models
from django.db.models import Sum


def backfill_total_points(apps, schema_editor):
    UserStreak = apps.get_model("gamification", "UserStreak")
    PointEvent = apps.get_model("gamification", "PointEvent")

    totals = {
        row["user_id"]: max(row["points_total"] or 0, 0)
        for row in PointEvent.objects.values("user_id").annotate(points_total=Sum("points"))
    }
    existing_user_ids = set(UserStreak.objects.values_list("user_id", flat=True))

    for user_id, total_points in totals.items():
        if user_id not in existing_user_ids:
            UserStreak.objects.create(user_id=user_id, total_points_earned=total_points)

    for streak in UserStreak.objects.all():
        streak.total_points_earned = totals.get(streak.user_id, 0)
        streak.save(update_fields=["total_points_earned"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("gamification", "0005_sync_badge_catalog"),
    ]

    operations = [
        migrations.AddField(
            model_name="userstreak",
            name="total_points_earned",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(backfill_total_points, noop),
    ]
