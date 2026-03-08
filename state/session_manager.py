"""
Session manager — handles session cleanup and lifecycle.

Automates the cleanup of stale sessions based on inactivity (Feature 2.3.2).
Recommended to run this periodically (e.g., via the AgentScheduler).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List

from config.db import get_conn
from state.app_config import get_runtime_value

logger = logging.getLogger("ops_agent.session_manager")


class SessionManager:
    """Manages lifecycle of agent sessions (conversations)."""

    @staticmethod
    def cleanup_stale_sessions(max_age_days: int = None) -> int:
        """
        Delete or archive sessions that haven't been updated in X days.
        
        Returns the number of cleaned up sessions.
        """
        if max_age_days is None:
            max_age_days = int(get_runtime_value("SESSION_TTL_DAYS", 30))

        cutoff = datetime.now() - timedelta(days=max_age_days)
        
        logger.info(f"Cleaning up sessions older than {cutoff} ({max_age_days} days)")
        
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # 1. Get IDs for logging
                    cur.execute(
                        "SELECT conversation_id FROM conversation_state WHERE updated_at < %s",
                        (cutoff,)
                    )
                    stale_ids = [row[0] for row in cur.fetchall()]
                    
                    if not stale_ids:
                        return 0
                    
                    # 2. Delete from related tables (cascading manually if no FK)
                    # Note: We keep chat_messages for long-term history but clear the state.
                    # If we wanted full deletion, we'd delete from chat_messages too.
                    # For enterprise, we usually archive.
                    
                    cur.execute(
                        "DELETE FROM issue_registry WHERE conversation_id IN %s",
                        (tuple(stale_ids),)
                    )
                    
                    cur.execute(
                        "DELETE FROM conversation_state WHERE conversation_id IN %s",
                        (tuple(stale_ids),)
                    )
                    
                    count = len(stale_ids)
                    logger.info(f"Successfully cleaned up {count} stale sessions.")
                    return count
        except Exception as e:
            logger.error(f"Session cleanup failed: {e}")
            return 0

    @staticmethod
    def get_inactive_sessions(hours: int = 24) -> List[str]:
        """Find sessions that have been inactive for more than X hours."""
        cutoff = datetime.now() - timedelta(hours=hours)
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT conversation_id FROM conversation_state WHERE updated_at < %s",
                        (cutoff,)
                    )
                    return [row[0] for row in cur.fetchall()]
        except Exception:
            return []


def register_cleanup_task():
    """Register the session cleanup task with the global scheduler."""
    from agents.scheduler import get_scheduler, ScheduledTask, ScheduleType, TaskResult
    
    def cleanup_handler(**kwargs) -> TaskResult:
        days = kwargs.get("days", 30)
        count = SessionManager.cleanup_stale_sessions(max_age_days=days)
        return TaskResult(
            success=True,
            message=f"Cleaned up {count} stale sessions older than {days} days."
        )

    sched = get_scheduler()
    sched.register_handler("session_cleanup", cleanup_handler)
    sched.remove_task("system-session-cleanup")
    
    # Run once a day at 2 AM
    sched.add_task(ScheduledTask(
        task_id="system-session-cleanup",
        name="Session Cleanup",
        description="Cleanup stale conversation states and sessions",
        schedule_type=ScheduleType.CRON_LIKE,
        cron_hour=2,
        cron_minute=0,
        handler_name="session_cleanup",
        handler_args={"days": int(get_runtime_value("SESSION_TTL_DAYS", 30))},
        enabled=True,
        is_system=True,
    ))
    logger.info("Session cleanup task registered.")
