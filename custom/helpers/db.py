"""
Deduplication utilities for Teams messages.
"""

from datetime import timedelta

from django.utils import timezone as tz

from custom.models import ProcessedMessage


def is_duplicate_message(thread_id: str, teams_message_id: str) -> bool:
    return ProcessedMessage.objects.filter(
        thread_id=thread_id,
        teams_message_id=teams_message_id,
    ).exists()


def mark_message_processed(thread_id: str, teams_message_id: str):
    ProcessedMessage.objects.create(
        thread_id=thread_id,
        teams_message_id=teams_message_id,
    )


def cleanup_old_messages(days: int = 30):
    """Prune dedup records older than N days to prevent table bloat."""
    cutoff = tz.now() - timedelta(days=days)
    deleted, _ = ProcessedMessage.objects.filter(
        processed_at__lt=cutoff
    ).delete()
    return deleted
