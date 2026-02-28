"""
Django models for the AI Studio Extension.
Handles message deduplication, conversation state, case lifecycle,
and approval tracking — all persisted in AI Studio's PostgreSQL.
"""
from __future__ import annotations

from django.db import models
from django.utils import timezone


class ProcessedMessage(models.Model):
    """Deduplication table — prevents double-processing of Teams messages."""
    thread_id = models.CharField(max_length=256)
    teams_message_id = models.CharField(max_length=256)
    processed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("thread_id", "teams_message_id")
        indexes = [models.Index(fields=["thread_id", "processed_at"])]


class ConversationState(models.Model):
    """Per-thread conversation tracking for the Extension hook."""
    thread_id = models.CharField(max_length=256, unique=True)
    active_case_id = models.CharField(max_length=64, null=True, blank=True)
    last_user_message_id = models.CharField(max_length=256, null=True, blank=True)
    last_bot_message_id = models.CharField(max_length=256, null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)


class Case(models.Model):
    """
    A single support case within a thread.
    Tracks planning state, execution plan, ownership, and resolution.
    """
    case_id = models.CharField(max_length=64, unique=True)
    thread_id = models.CharField(max_length=256)

    state = models.CharField(max_length=64)
    owner_type = models.CharField(max_length=32, default="BOT_L1")
    owner_team = models.CharField(max_length=64, null=True, blank=True)

    user_type = models.CharField(max_length=32, null=True, blank=True)
    ticket_id = models.CharField(max_length=128, null=True, blank=True)

    planner_state_json = models.JSONField(default=dict, blank=True)
    latest_plan_json = models.JSONField(default=dict, blank=True)
    plan_version = models.IntegerField(default=0)

    error_signatures = models.JSONField(default=list, blank=True)
    workflows_involved = models.JSONField(default=list, blank=True)
    recurrence_count = models.IntegerField(default=0)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_summary = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["thread_id", "state"]),
            models.Index(fields=["ticket_id"]),
        ]


class Approval(models.Model):
    """Tracks approval requests and decisions for risky actions."""
    case_id = models.CharField(max_length=64)
    plan_version = models.IntegerField()
    status = models.CharField(max_length=32, default="PENDING")
    requested_to = models.JSONField(default=list, blank=True)
    decided_by = models.CharField(max_length=256, null=True, blank=True)
    reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["case_id", "status"])]


class IssueLink(models.Model):
    """Bidirectional link between related cases (cascade, recurrence)."""
    case_id_1 = models.CharField(max_length=64)
    case_id_2 = models.CharField(max_length=64)
    link_type = models.CharField(max_length=32)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("case_id_1", "case_id_2")
        indexes = [
            models.Index(fields=["case_id_1"]),
            models.Index(fields=["case_id_2"]),
        ]

    @classmethod
    def get_linked_cases(cls, case_id: str):
        from django.db.models import Q
        return cls.objects.filter(Q(case_id_1=case_id) | Q(case_id_2=case_id))
