"""
Main orchestrator agent.
Routes messages, manages the investigation/remediation loop,
coordinates tool calls via RAG-selected tools, and handles
issue lifecycle (new, continue, recurrence, escalation).
"""

import json
import logging

from vertexai.generative_models import Content, Part

from agents.approval_gate import ApprovalGate
from agents.escalation import EscalationAgent
from config.llm_client import llm_client
from config.settings import CONFIG
from rag.engine import get_rag_engine
from state.conversation_state import ConversationState, ConversationPhase
from state.issue_tracker import (
    IssueTracker,
    MessageClassification,
    IssueStatus,
    RECURRENCE_ESCALATION_THRESHOLD,
)
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.orchestrator")
audit = logging.getLogger("ops_agent.audit")


class Orchestrator:

    def __init__(self):
        self.approval_gate = ApprovalGate()
        self.escalation = EscalationAgent()
        self.issue_trackers: dict[str, IssueTracker] = {}

    def _get_issue_tracker(self, conversation_id: str) -> IssueTracker:
        if conversation_id not in self.issue_trackers:
            self.issue_trackers[conversation_id] = IssueTracker(conversation_id)
        return self.issue_trackers[conversation_id]

    # =====================================================================
    # Public entry point
    # =====================================================================

    def handle_message(self, user_message: str,
                       state: ConversationState) -> str:
        if not user_message.strip():
            return "It looks like your message was empty. How can I help?"

        state.add_message("user", user_message)
        tracker = self._get_issue_tracker(state.conversation_id)

        # ── Approval flow ──
        if state.phase == ConversationPhase.AWAITING_APPROVAL:
            active = tracker.get_active_issue()
            if active:
                active.touch()
                tracker._persist_issue(active)
            return self._handle_approval_response(user_message, state, tracker)

        # ── Classify message ──
        classification, issue_id = tracker.classify_message(
            user_message, state.messages
        )

        # ── Route based on classification ──
        if classification == MessageClassification.NEW_ISSUE:
            tracker.create_issue(
                title=user_message[:80],
                description=user_message,
            )
            state.phase = ConversationPhase.IDLE
            response = self._process_message(user_message, state, tracker)

        elif classification == MessageClassification.CONTINUE_EXISTING:
            tracker.switch_to_issue(issue_id or tracker.active_issue_id)
            response = self._process_message(user_message, state, tracker)

        elif classification == MessageClassification.RELATED_NEW:
            parent_id = issue_id or tracker.active_issue_id
            issue = tracker.create_issue(
                title=user_message[:80],
                description=user_message,
            )
            if parent_id:
                tracker.link_issues(parent_id, issue.issue_id)
            response = (
                "This looks related to the issue I'm already investigating "
                "but appears to be a separate problem. I'll track it as a "
                "linked issue.\n\n"
                + self._process_message(user_message, state, tracker)
            )

        elif classification == MessageClassification.RECURRENCE:
            old_issue = tracker.reopen_issue(issue_id)

            if tracker.should_escalate_recurrence(old_issue.issue_id):
                old_issue.status = IssueStatus.ESCALATED
                tracker._persist_issue(old_issue)
                return (
                    f"This issue has now recurred {old_issue.recurrence_count} "
                    f"times. Previous resolution "
                    f"({old_issue.resolution[:150]}) is not holding. "
                    f"I'm escalating to the operations team for a permanent fix."
                )

            state.phase = ConversationPhase.IDLE
            recurrence_note = (
                f"This appears to be a recurrence of a previous issue "
                f"(occurrence #{old_issue.recurrence_count}). "
            )
            if old_issue.resolution:
                recurrence_note += (
                    f"Last time the resolution was: "
                    f"{old_issue.resolution[:200]}. "
                    f"Let me check if the same root cause applies.\n\n"
                )
            response = recurrence_note + self._process_message(
                user_message, state, tracker
            )

        elif classification == MessageClassification.FOLLOWUP:
            target_issue = (
                tracker.issues.get(issue_id) if issue_id else None
            ) or tracker.get_active_issue()

            if target_issue and target_issue.status == IssueStatus.RESOLVED:
                response = (
                    f"Regarding [{target_issue.issue_id}] "
                    f"{target_issue.title}: it was resolved. "
                    f"{target_issue.resolution}\n\n"
                    f"Would you like me to verify the current status?"
                )
            elif target_issue and target_issue.status == IssueStatus.STALE:
                tracker.resume_stale_issue(target_issue.issue_id)
                response = (
                    f"Resuming investigation of [{target_issue.issue_id}] "
                    f"{target_issue.title}.\n\n"
                    + self._process_message(user_message, state, tracker)
                )
            else:
                response = self._process_message(user_message, state, tracker)

        elif classification == MessageClassification.STATUS_CHECK:
            summary = tracker.get_all_issues_summary()
            response = (
                f"Here's the current session status:\n\n{summary}\n\n"
                + self._process_message(user_message, state, tracker)
            )
        else:
            response = self._process_message(user_message, state, tracker)

        state.save()
        return response

    # =====================================================================
    # Core investigation / remediation loop
    # =====================================================================

    def _process_message(self, user_message: str,
                         state: ConversationState,
                         tracker: IssueTracker) -> str:
        state.is_agent_working = True
        state.phase = ConversationPhase.INVESTIGATING

        try:
            system_prompt = self._build_system_prompt(state, tracker)
            rag = get_rag_engine()

            tool_hits = rag.search_tools(user_message, top_k=5)
            kb_hits = rag.search_kb(user_message, top_k=3)
            sop_hits = rag.search_sops(user_message, top_k=3)

            context_block = self._format_rag_context(tool_hits, kb_hits, sop_hits)

            messages = [
                Content(
                    role="user",
                    parts=[Part.from_text(
                        f"{context_block}\n\nUser: {user_message}"
                    )],
                )
            ]

            active_issue = tracker.get_active_issue()
            max_iterations = CONFIG.get("MAX_AGENT_ITERATIONS", 15)

            for iteration in range(max_iterations):
                if state.interrupt_requested:
                    state.interrupt_requested = False
                    state.is_agent_working = False
                    return "Investigation paused. What would you like me to do?"

                response = llm_client.chat_with_tools(
                    messages,
                    tools=tool_registry.get_vertex_tools(),
                    system=system_prompt,
                )

                if not hasattr(response, 'candidates') or not response.candidates:
                    break

                candidate = response.candidates[0]
                parts = candidate.content.parts

                tool_called = False
                for part in parts:
                    if hasattr(part, 'function_call') and part.function_call:
                        tool_called = True
                        fc = part.function_call
                        tool_name = fc.name
                        tool_args = dict(fc.args) if fc.args else {}

                        tool_def = tool_registry.get_tool(tool_name)
                        if not tool_def:
                            messages.append(candidate.content)
                            messages.append(Content(parts=[
                                Part.from_function_response(
                                    name=tool_name,
                                    response={"error": f"unknown tool '{tool_name}'"},
                                )
                            ]))
                            continue

                        if self.approval_gate.needs_approval(
                            tool_name, tool_def.tier, tool_args
                        ):
                            state.pending_action = {
                                "tool": tool_name,
                                "args": tool_args,
                                "tier": tool_def.tier,
                            }
                            summary = (
                                f"{tool_name} on "
                                f"{tool_args.get('workflow_name', 'unknown')}"
                            )
                            state.pending_action_summary = summary
                            state.phase = ConversationPhase.AWAITING_APPROVAL
                            state.is_agent_working = False
                            return self.approval_gate.format_approval_prompt(
                                self.approval_gate.create_approval_request(
                                    tool_name, tool_def.tier, tool_args, summary
                                )
                            )

                        result = tool_registry.execute(tool_name, **tool_args)
                        state.log_tool_call(
                            tool_name, tool_args, result.data, result.success
                        )

                        if active_issue:
                            active_issue.touch()
                            wf = tool_args.get("workflow_name")
                            if wf:
                                tracker.add_workflow_to_issue(
                                    active_issue.issue_id, wf
                                )
                            if not result.success:
                                tracker.add_error_signature(
                                    active_issue.issue_id,
                                    result.error[:100],
                                )

                        messages.append(candidate.content)
                        result_payload = (
                            {"result": result.data}
                            if result.success
                            else {"error": result.error}
                        )
                        messages.append(Content(parts=[
                            Part.from_function_response(
                                name=tool_name,
                                response=result_payload,
                            )
                        ]))

                if not tool_called:
                    text_parts = [
                        p.text for p in parts
                        if hasattr(p, 'text') and p.text
                    ]
                    final_response = "\n".join(text_parts) if text_parts else (
                        "I've completed my investigation. Let me know if "
                        "you need anything else."
                    )
                    final_response = self._filter_for_persona(
                        final_response, state
                    )
                    state.add_message("assistant", final_response)
                    state.is_agent_working = False
                    return final_response

            state.is_agent_working = False
            return (
                "I've reached the maximum investigation steps. Here's what "
                "I found so far — would you like me to continue, escalate, "
                "or generate an RCA?"
            )
        except Exception as e:
            logger.error(f"Processing error: {e}", exc_info=True)
            state.is_agent_working = False
            return (
                "I encountered an error during investigation. "
                "The operations team has been notified."
            )

    # =====================================================================
    # Approval handling
    # =====================================================================

    def _handle_approval_response(self, user_message: str,
                                  state: ConversationState,
                                  tracker: IssueTracker) -> str:
        decision = self.approval_gate.parse_approval_response(user_message)

        if decision is None:
            return (
                "I didn't understand your response. "
                "Please reply **approve** or **reject**."
            )

        if not decision:
            state.phase = ConversationPhase.IDLE
            state.pending_action = None
            return "Action rejected. What would you like me to do instead?"

        action = state.pending_action
        if not action:
            state.phase = ConversationPhase.IDLE
            return "No pending action found. How can I help?"

        state.phase = ConversationPhase.EXECUTING
        result = tool_registry.execute(action["tool"], **action["args"])
        state.log_tool_call(
            action["tool"], action["args"], result.data, result.success
        )

        state.pending_action = None
        state.phase = ConversationPhase.IDLE

        active_issue = tracker.get_active_issue()
        if result.success:
            if active_issue:
                tracker.resolve_issue(
                    active_issue.issue_id,
                    f"Approved and executed: {state.pending_action_summary}",
                )
            return (
                f"Done. {action['tool']} completed successfully.\n"
                f"Result: {json.dumps(result.data, default=str)[:300]}"
            )

        return (
            f"The action failed: {result.error}\n"
            f"Would you like me to try something else or escalate?"
        )

    # =====================================================================
    # Prompt building
    # =====================================================================

    def _build_system_prompt(self, state: ConversationState,
                             tracker: IssueTracker) -> str:
        base_prompt = """You are an AutomationEdge operations support agent.
You help investigate and resolve issues with RPA workflows.

Rules:
1. Always verify before acting — never guess based on symptoms alone.
2. Check input files early — 800+ workflows are file-based.
3. When multiple failures exist, trace to the upstream root cause.
4. Adjust detail level based on user role.
5. Every tool call is audited.

Available tool categories: status, logs, file, remediation, dependency,
config, notification. Use the most specific tool for the task."""

        persona = ""
        if state.user_role == "business":
            persona = """
## Persona: Business User
Explain in plain English. No workflow names, execution IDs, or error codes.
Focus on business impact, timing, and resolution status."""
        else:
            persona = """
## Persona: Technical Staff
Include workflow names, execution IDs, error details, and timestamps.
Provide full diagnostic information."""

        issue_context = ""
        if tracker and tracker.issues:
            active = tracker.get_active_issue()
            issue_context = f"""
## Active Issues in This Session
{tracker.get_all_issues_summary()}

Currently focused issue: {active.issue_id if active else 'None'}

IMPORTANT: Scope your investigation to the currently focused issue."""

        return f"{base_prompt}\n{persona}\n{issue_context}"

    def _format_rag_context(self, tool_hits, kb_hits, sop_hits) -> str:
        sections = []
        if kb_hits:
            kb_text = "\n".join(
                f"- {h.get('content', '')[:200]}" for h in kb_hits[:3]
            )
            sections.append(f"## Knowledge Base\n{kb_text}")
        if sop_hits:
            sop_text = "\n".join(
                f"- {h.get('content', '')[:200]}" for h in sop_hits[:3]
            )
            sections.append(f"## SOPs\n{sop_text}")
        if tool_hits:
            tool_text = "\n".join(
                f"- {h.get('content', '')[:150]}" for h in tool_hits[:5]
            )
            sections.append(f"## Relevant Tools\n{tool_text}")
        return "\n\n".join(sections) if sections else ""

    def _filter_for_persona(self, response: str,
                            state: ConversationState) -> str:
        if state.user_role != "business":
            return response

        try:
            filtered = llm_client.chat(
                f"Rewrite this for a non-technical business user. "
                f"Remove workflow names, execution IDs, error codes. "
                f"Focus on impact and status:\n\n{response}",
                system="Rewrite technical text for business audiences.",
                max_tokens=1024,
            )
            return filtered
        except Exception:
            return response
