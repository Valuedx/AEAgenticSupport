"""
Initial migration for the AE Agentic Support Extension models.
Creates tables for message deduplication, conversation state,
case lifecycle, approval tracking, and issue linking.
"""
from __future__ import annotations

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ProcessedMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("thread_id", models.CharField(max_length=256)),
                ("teams_message_id", models.CharField(max_length=256)),
                ("processed_at", models.DateTimeField(
                    default=django.utils.timezone.now)),
            ],
            options={
                "unique_together": {("thread_id", "teams_message_id")},
            },
        ),
        migrations.AddIndex(
            model_name="processedmessage",
            index=models.Index(fields=["thread_id", "processed_at"],
                               name="custom_proc_thread_ts_idx"),
        ),
        migrations.CreateModel(
            name="ConversationState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("thread_id", models.CharField(max_length=256, unique=True)),
                ("active_case_id", models.CharField(
                    max_length=64, null=True, blank=True)),
                ("last_user_message_id", models.CharField(
                    max_length=256, null=True, blank=True)),
                ("last_bot_message_id", models.CharField(
                    max_length=256, null=True, blank=True)),
                ("updated_at", models.DateTimeField(
                    default=django.utils.timezone.now)),
            ],
        ),
        migrations.CreateModel(
            name="Case",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("case_id", models.CharField(max_length=64, unique=True)),
                ("thread_id", models.CharField(max_length=256)),
                ("state", models.CharField(max_length=64)),
                ("owner_type", models.CharField(
                    max_length=32, default="BOT_L1")),
                ("owner_team", models.CharField(
                    max_length=64, null=True, blank=True)),
                ("user_type", models.CharField(
                    max_length=32, null=True, blank=True)),
                ("ticket_id", models.CharField(
                    max_length=128, null=True, blank=True)),
                ("planner_state_json", models.JSONField(
                    default=dict, blank=True)),
                ("latest_plan_json", models.JSONField(
                    default=dict, blank=True)),
                ("plan_version", models.IntegerField(default=0)),
                ("error_signatures", models.JSONField(
                    default=list, blank=True)),
                ("workflows_involved", models.JSONField(
                    default=list, blank=True)),
                ("recurrence_count", models.IntegerField(default=0)),
                ("resolved_at", models.DateTimeField(null=True, blank=True)),
                ("resolution_summary", models.TextField(
                    null=True, blank=True)),
                ("created_at", models.DateTimeField(
                    default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(
                    default=django.utils.timezone.now)),
            ],
        ),
        migrations.AddIndex(
            model_name="case",
            index=models.Index(fields=["thread_id", "state"],
                               name="custom_case_thread_state_idx"),
        ),
        migrations.AddIndex(
            model_name="case",
            index=models.Index(fields=["ticket_id"],
                               name="custom_case_ticket_idx"),
        ),
        migrations.CreateModel(
            name="Approval",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("case_id", models.CharField(max_length=64)),
                ("plan_version", models.IntegerField()),
                ("status", models.CharField(max_length=32, default="PENDING")),
                ("requested_to", models.JSONField(default=list, blank=True)),
                ("decided_by", models.CharField(
                    max_length=256, null=True, blank=True)),
                ("reason", models.TextField(null=True, blank=True)),
                ("created_at", models.DateTimeField(
                    default=django.utils.timezone.now)),
                ("decided_at", models.DateTimeField(null=True, blank=True)),
            ],
        ),
        migrations.AddIndex(
            model_name="approval",
            index=models.Index(fields=["case_id", "status"],
                               name="custom_appr_case_status_idx"),
        ),
        migrations.CreateModel(
            name="IssueLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("case_id_1", models.CharField(max_length=64)),
                ("case_id_2", models.CharField(max_length=64)),
                ("link_type", models.CharField(max_length=32)),
                ("created_at", models.DateTimeField(
                    default=django.utils.timezone.now)),
            ],
            options={
                "unique_together": {("case_id_1", "case_id_2")},
            },
        ),
        migrations.AddIndex(
            model_name="issuelink",
            index=models.Index(fields=["case_id_1"],
                               name="custom_link_c1_idx"),
        ),
        migrations.AddIndex(
            model_name="issuelink",
            index=models.Index(fields=["case_id_2"],
                               name="custom_link_c2_idx"),
        ),
    ]
