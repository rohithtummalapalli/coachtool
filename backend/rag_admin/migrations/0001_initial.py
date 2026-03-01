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
            name="RagSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("embedding_model", models.CharField(default="sentence-transformers/all-MiniLM-L6-v2", max_length=255)),
                ("chunk_size", models.PositiveIntegerField(default=1000)),
                ("chunk_overlap", models.PositiveIntegerField(default=150)),
                ("retrieval_top_k", models.PositiveIntegerField(default=3)),
                ("similarity_threshold", models.FloatField(default=0.0)),
                ("llm_model", models.CharField(default="gpt-4o-mini", max_length=255)),
                ("temperature", models.FloatField(default=0.2)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.CreateModel(
            name="Document",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("content", models.TextField()),
                ("file_name", models.CharField(max_length=255)),
                ("file_size", models.PositiveBigIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="uploaded_documents",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(fields=["title"], name="rag_admin_d_title_8ebf91_idx"),
        ),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(fields=["created_at"], name="rag_admin_d_created_9f0840_idx"),
        ),
        migrations.AddConstraint(
            model_name="ragsettings",
            constraint=models.UniqueConstraint(
                condition=models.Q(is_active=True),
                fields=("is_active",),
                name="unique_active_rag_settings",
            ),
        ),
        migrations.AddConstraint(
            model_name="ragsettings",
            constraint=models.CheckConstraint(condition=models.Q(chunk_size__gt=0), name="chunk_size_gt_0"),
        ),
        migrations.AddConstraint(
            model_name="ragsettings",
            constraint=models.CheckConstraint(condition=models.Q(chunk_overlap__gte=0), name="chunk_overlap_gte_0"),
        ),
        migrations.AddConstraint(
            model_name="ragsettings",
            constraint=models.CheckConstraint(condition=models.Q(retrieval_top_k__gt=0), name="retrieval_top_k_gt_0"),
        ),
        migrations.AddConstraint(
            model_name="ragsettings",
            constraint=models.CheckConstraint(
                condition=models.Q(temperature__gte=0.0) & models.Q(temperature__lte=2.0),
                name="temperature_between_0_2",
            ),
        ),
    ]
