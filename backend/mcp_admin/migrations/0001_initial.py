from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MCPServer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True)),
                ("base_url", models.URLField(max_length=500)),
                ("auth_type", models.CharField(blank=True, default="none", max_length=40)),
                ("is_enabled", models.BooleanField(default=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_mcp_servers",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="MCPTool",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True)),
                ("is_enabled", models.BooleanField(default=True)),
                ("input_schema", models.JSONField(blank=True, default=dict)),
                ("rate_limit_per_minute", models.PositiveIntegerField(default=60)),
                ("timeout_seconds", models.PositiveIntegerField(default=30)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "server",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tools",
                        to="mcp_admin.mcpserver",
                    ),
                ),
            ],
            options={"ordering": ["server__name", "name"], "unique_together": {("server", "name")}},
        ),
        migrations.CreateModel(
            name="MCPAuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=120)),
                ("status", models.CharField(default="success", max_length=40)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="mcp_audit_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "server",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="mcp_admin.mcpserver"),
                ),
                (
                    "tool",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="mcp_admin.mcptool"),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
