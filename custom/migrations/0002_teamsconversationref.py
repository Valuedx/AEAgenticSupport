"""
Add Teams conversation-reference persistence for proactive messaging.
"""
from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("custom", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="TeamsConversationRef",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("thread_id", models.CharField(max_length=512, unique=True)),
                ("service_url", models.TextField()),
                ("conversation_id", models.CharField(max_length=512)),
                ("bot_id", models.CharField(default="", max_length=256)),
                ("channel_id", models.CharField(default="msteams", max_length=64)),
                ("tenant_id", models.CharField(blank=True, max_length=256, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddIndex(
            model_name="teamsconversationref",
            index=models.Index(
                fields=["thread_id"],
                name="custom_team_thread_idx",
            ),
        ),
    ]
