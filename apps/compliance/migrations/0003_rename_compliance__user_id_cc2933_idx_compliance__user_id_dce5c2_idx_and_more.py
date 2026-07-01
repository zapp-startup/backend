from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("compliance", "0002_auditevent"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="auditevent",
            old_name="compliance__user_id_cc2933_idx",
            new_name="compliance__user_id_dce5c2_idx",
        ),
        migrations.RenameIndex(
            model_name="auditevent",
            old_name="compliance__resourc_05eb54_idx",
            new_name="compliance__resourc_fbc801_idx",
        ),
    ]
