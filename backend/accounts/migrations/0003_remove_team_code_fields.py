from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_organization_company_id_organization_company_name_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="organization",
            name="team_code",
        ),
        migrations.RemoveField(
            model_name="user",
            name="team_code",
        ),
    ]
