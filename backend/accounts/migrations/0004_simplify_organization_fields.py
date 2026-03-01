from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_remove_team_code_fields"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="organization",
            name="is_active",
        ),
        migrations.RemoveField(
            model_name="organization",
            name="name",
        ),
        migrations.RemoveField(
            model_name="organization",
            name="slug",
        ),
        migrations.RemoveField(
            model_name="organization",
            name="team_name",
        ),
    ]
