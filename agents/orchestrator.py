"""
Main orchestrator agent.
Routes messages, manages the investigation/remediation loop,
coordinates tool calls via RAG-selected tools, and handles
issue lifecycle (new, continue, recurrence, escalation).
"""
from __future__ import annotations

import json
import logging

from vertexai.generative_models import Content, Part

from agents.approval_gate import ApprovalGate
from agents.escalation import EscalationAgent
from config.llm_client import llm_client
from config.settings import CONFIG
from gateway.progress import ProgressCallback, create_noop_progress
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
                       state: ConversationState,
                       on_progress: ProgressCallback | None = None) -> str:
        if not user_message.strip():
            return "It looks like your message was empty. How can I help?"

        progress = on_progress or create_noop_progress()
        state.add_message("user", user_message)
        tracker = self._get_issue_tracker(state.conversation_id)

        # ── Approval flow ──
        if state.phase == ConversationPhase.AWAITING_APPROVAL:
            active = tracker.get_active_issue()
            if active:
                active.touch()
                tracker._persist_issue(active)
            progress.on_phase("executing_fix")
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
            response = self._process_message(user_message, state, tracker, progress)

        elif classification == MessageClassification.CONTINUE_EXISTING:
            tracker.switch_to_issue(issue_id or tracker.active_issue_id)
            response = self._process_message(user_message, state, tracker, progress)

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
                + self._process_message(user_message, state, tracker, progress)
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
                user_message, state, tracker, progress
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
                    + self._process_message(user_message, state, tracker, progress)
                )
            else:
                response = self._process_message(user_message, state, tracker, progress)

        elif classification == MessageClassification.STATUS_CHECK:
            summary = tracker.get_all_issues_summary()
            response = (
                f"Here's the current session status:\n\n{summary}\n\n"
                + self._process_message(user_message, state, tracker, progress)
            )
        else:
            response = self._process_message(user_message, state, tracker, progress)

        state.save()
        return response

    # =====================================================================
    # Core investigation / remediation loop
    # =====================================================================

    def _process_message(self, user_message: str,
                         state: ConversationState,
                         tracker: IssueTracker,
                         progress: ProgressCallback | None = None) -> str:
        progress = progress or create_noop_progress()
        state.is_agent_working = True
        state.phase = ConversationPhase.INVESTIGATING
        progress.on_phase("investigating")

        try:
            system_prompt = self._build_system_prompt(state, tracker)
            rag = get_rag_engine()

            query_vec = rag.embed_query(user_message)
            tool_hits = rag.search_tools(user_message, top_k=12,
                                         query_embedding=query_vec)
            kb_hits = rag.search_kb(user_message, top_k=3,
                                    query_embedding=query_vec)
            sop_hits = rag.search_sops(user_message, top_k=3,
                                       query_embedding=query_vec)

            context_block = self._format_rag_context(
                tool_hits[:5], kb_hits, sop_hits
            )

            rag_tool_names = [
                h.get("metadata", {}).get("tool_name", h.get("id", ""))
                for h in tool_hits
            ]
            max_rag = CONFIG.get("MAX_RAG_TOOLS", 12)
            vertex_tools = tool_registry.get_vertex_tools_filtered(
                rag_tool_names, max_rag_tools=max_rag,
            )
            active_tool_names = self._extract_active_tool_names(vertex_tools)

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

                progress.on_iteration(iteration, max_iterations)

                response = llm_client.chat_with_tools(
                    messages,
                    tools=vertex_tools,
                    system=system_prompt,
                )

                if not hasattr(response, 'candidates') or not response.candidates:
                    break

                candidate = response.candidates[0]
                parts = candidate.content.parts

                fn_calls = [
                    p for p in parts
                    if hasattr(p, 'function_call') and p.function_call
                ]
                tool_called = bool(fn_calls)

                if tool_called:
                    needs_expansion = False
                    for fc_part in fn_calls:
                        fc = fc_part.function_call
                        tool_name = fc.name
                        tool_args = dict(fc.args) if fc.args else {}
                        tool_def = tool_registry.get_tool(tool_name)

                        if not tool_def:
                            tool_def = tool_registry.resolve_discovered_tool(
                                tool_name
                            )
                            if tool_def:
                                needs_expansion = True

                        if tool_def and self.approval_gate.needs_approval(
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
                                    tool_name, tool_def.tier,
                                    tool_args, summary,
                                )
                            )

                    messages.append(candidate.content)

                    response_parts = []
                    discovered_names: list[str] = []

                    for fc_part in fn_calls:
                        fc = fc_part.function_call
                        tool_name = fc.name
                        tool_args = dict(fc.args) if fc.args else {}

                        tool_def = tool_registry.get_tool(tool_name)
                        if not tool_def:
                            tool_def = tool_registry.resolve_discovered_tool(
                                tool_name
                            )
                        if not tool_def:
                            response_parts.append(
                                Part.from_function_response(
                                    name=tool_name,
                                    response={
                                        "error": f"unknown tool '{tool_name}'"
                                    },
                                )
                            )
                            continue

                        progress.on_tool_start(tool_name, tool_args)
                        result = tool_registry.execute(tool_name, **tool_args)
                        state.log_tool_call(
                            tool_name, tool_args, result.data, result.success
                        )
                        progress.on_tool_done(
                            tool_name, result.success,
                            result.error if not result.success else "",
                        )

                        if tool_name == "discover_tools":
                            found = (result.data or {}).get("tools", [])
                            discovered_names.extend(
                                t["name"] for t in found
                                if t["name"] not in active_tool_names
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

                        result_payload = (
                            {"result": result.data}
                            if result.success
                            else {"error": result.error}
                        )
                        response_parts.append(
                            Part.from_function_response(
                                name=tool_name,
                                response=result_payload,
                            )
                        )

                    messages.append(Content(parts=response_parts))

                    if discovered_names or needs_expansion:
                        expanded = list(active_tool_names) + discovered_names
                        vertex_tools = tool_registry.get_vertex_tools_filtered(
                            expanded, max_rag_tools=20,
                        )
                        active_tool_names = self._extract_active_tool_names(
                            vertex_tools
                        )

                if not tool_called:
                    progress.on_phase("almost_done")
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

                    queued = self._drain_queued_messages(state, tracker)
                    if queued:
                        final_response += "\n\n" + queued
                    return final_response

            state.is_agent_working = False
            max_iter_msg = (
                "I've reached the maximum investigation steps. Here's what "
                "I found so far — would you like me to continue, escalate, "
                "or generate an RCA?"
            )
            queued = self._drain_queued_messages(state, tracker)
            if queued:
                max_iter_msg += "\n\n" + queued
            return max_iter_msg
        except Exception as e:
            logger.error(f"Processing error: {e}", exc_info=True)
            state.is_agent_working = False
            return (
                "I encountered an error during investigation. "
                "The operations team has been notified."
            )

    # =====================================================================
    # Queued message processing
    # =====================================================================

    def _drain_queued_messages(self, state: ConversationState,
                               tracker: IssueTracker) -> str:
        queued = state.get_queued_messages()
        if not queued:
            return ""

        parts = []
        for msg in queued:
            hint = msg.get("hint", "")
            content = msg.get("content", "")
            if hint == "interrupt":
                parts.append(
                    f"**Queued (urgent):** Processing your earlier message "
                    f"now — \"{content[:150]}\""
                )
                state.add_message("user", content)
                resp = self._process_message(content, state, tracker)
                parts.append(resp)
            elif hint == "additive":
                state.add_message("user", f"[Additional context] {content}")
                parts.append(
                    f"**Noted additional context:** {content[:200]}"
                )
            elif hint == "new_request":
                parts.append(
                    f"**Queued request:** \"{content[:150]}\" — "
                    f"I'll handle this next."
                )
                state.add_message("user", content)
                resp = self._process_message(content, state, tracker)
                parts.append(resp)

        return "\n\n".join(parts)

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
            state.pending_action_summary = ""
            return "Action rejected. What would you like me to do instead?"

        action = state.pending_action
        if not action:
            state.phase = ConversationPhase.IDLE
            return "No pending action found. How can I help?"

        allowed = action.get("authorized_users", [])
        if allowed and state.user_id and state.user_id not in allowed:
            return (
                "You are not authorized to approve this action. "
                f"Authorized reviewers: {', '.join(allowed)}"
            )

        state.phase = ConversationPhase.EXECUTING
        result = tool_registry.execute(action["tool"], **action["args"])
        state.log_tool_call(
            action["tool"], action["args"], result.data, result.success
        )

        action_summary = state.pending_action_summary
        state.pending_action = None
        state.pending_action_summary = ""
        state.phase = ConversationPhase.IDLE

        active_issue = tracker.get_active_issue()
        if result.success:
            if active_issue:
                tracker.resolve_issue(
                    active_issue.issue_id,
                    f"Approved and executed: {action_summary}",
                )
            return (
                f"Done. {action['tool']} completed successfully.\n"
                f"Result: {json.dumps(result.data, default=str)[:300]}"
            )

        return (
            f"The action failed: {result.error}\n"
            f"Would you like me to try something else or escalate?"
        )

    @staticmethod
    def _extract_active_tool_names(vertex_tools: list) -> set[str]:
        """Get the set of tool names currently in the Vertex Tool object."""
        names: set[str] = set()
        for tool_obj in vertex_tools:
            if hasattr(tool_obj, "function_declarations"):
                for decl in tool_obj.function_declarations:
                    if hasattr(decl, "name"):
                        names.add(decl.name)
                    elif isinstance(decl, dict):
                        names.add(decl.get("name", ""))
            raw = getattr(tool_obj, "_raw_tool", None)
            if raw and isinstance(raw, dict):
                for decl in raw.get("function_declarations", []):
                    names.add(decl.get("name", ""))
        names.discard("")
        return names

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
6. Prefer specific typed tools (check_workflow_status, get_execution_logs,
   etc.) — they have better validation and cleaner audit trails.
7. If no typed tool fits, use the general-purpose escape hatches:
   - call_ae_api: hit any AE REST endpoint directly
   - query_database: run read-only SQL against the ops database
   - search_knowledge_base: semantic search across all KB collections
8. If none of the above help, call discover_tools to search the full
   catalog by description or category.

Available tool categories: status, logs, file, remediation, dependency,
config, notification, general, meta.
You have a subset of tools loaded. Use discover_tools to find others."""

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
