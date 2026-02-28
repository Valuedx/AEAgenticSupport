"""
Shared heuristic signal lists used by both the Extension-side
issue classifier and the standalone IssueTracker.
Single source of truth — import from here to avoid drift.
"""
from __future__ import annotations

APPROVAL_SIGNALS = ["approve", "reject", "yes", "no", "go ahead", "proceed"]

CONTINUE_SIGNALS = [
    "same workflow", "same one", "related to that",
    "on the same topic", "regarding that", "about that",
    "for that same", "going back to",
]

NEW_ISSUE_SIGNALS = [
    "different issue", "new problem", "something else",
    "unrelated", "separate issue", "by the way",
    "changing topic", "another issue", "on a different note",
]

RECURRENCE_SIGNALS = [
    "happened again", "same error again", "still failing",
    "back again", "recurring", "keeps failing", "not fixed",
    "failed again", "same issue", "it's back",
]

FOLLOWUP_SIGNALS = [
    "did it work", "is it fixed", "did the restart work",
    "how did it go", "any update", "what happened after",
    "is it running now", "did it complete",
]

STATUS_CHECK_SIGNALS = [
    "what's the status", "status update", "where are we",
    "any progress", "how's it going", "current status",
    "what's happening", "status check", "show status",
    "case status", "all cases", "open cases",
]

CANCEL_SIGNALS = [
    "cancel", "never mind", "nevermind", "stop",
    "forget it", "forget about it", "don't bother",
    "abort", "scratch that", "disregard",
]
