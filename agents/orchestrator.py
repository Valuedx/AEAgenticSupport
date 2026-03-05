"""
Main orchestrator agent.
Routes messages, manages the investigation/remediation loop,
coordinates tool calls via RAG-selected tools, and handles
issue lifecycle (new, continue, recurrence, escalation).
"""
from __future__ import annotations

import json
import logging

from google.genai import types

from agents.approval_gate import ApprovalGate, ApprovalIntent
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
from tools.base import get_ae_client
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
            response = self._handle_approval_response(
                user_message, state, tracker
            )
            state.save()
            return response

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
                state.phase = ConversationPhase.ESCALATED
                response = (
                    f"This issue has now recurred {old_issue.recurrence_count} "
                    f"times. Previous resolution "
                    f"({old_issue.resolution[:150]}) is not holding. "
                    f"I'm escalating to the operations team for a permanent fix."
                )
                state.save()
                return response

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
            incident_hits = rag.search_past_incidents(user_message, top_k=3,
                                                       query_embedding=query_vec)

            context_block = self._format_rag_context(
                tool_hits[:5], kb_hits, sop_hits, incident_hits
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
                types.Content(
                    role="user",
                    parts=[types.Part(text=f"{context_block}\n\nUser: {user_message}")],
                )
            ]

            active_issue = tracker.get_active_issue()
            max_iterations = CONFIG.get("MAX_AGENT_ITERATIONS", 15)

            param_followup = self._continue_param_collection(user_message, state, tracker)
            if param_followup:
                state.add_message("assistant", param_followup)
                state.is_agent_working = False
                return param_followup

            preflight = self._preflight_workflow_param_collection(
                user_message=user_message,
                state=state,
                active_issue=active_issue,
            )
            if preflight:
                state.add_message("assistant", preflight)
                state.is_agent_working = False
                state.phase = ConversationPhase.IDLE
                return preflight

            for iteration in range(max_iterations):
                if state.interrupt_requested:
                    state.interrupt_requested = False
                    state.is_agent_working = False
                    state.phase = ConversationPhase.IDLE
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
                    p.function_call for p in parts
                    if p.function_call
                ]
                tool_called = bool(fn_calls)

                if tool_called:
                    needs_expansion = False
                    for fc in fn_calls:
                        tool_name = fc.name
                        tool_args = dict(fc.args) if fc.args else {}
                        tool_def = tool_registry.get_tool(tool_name)

                        if tool_def:
                            # Check for missing required parameters first. 
                            # If params are missing, we don't ask for approval yet.
                            # We let the tool run (dynamic tools return a friendly prompt)
                            # or the agent will naturally realize it needs them.
                            missing = [
                                p for p in tool_def.required_params
                                if p not in tool_args
                                or tool_args.get(p) in (None, "", {}, [])
                            ]
                            
                            if not missing and self.approval_gate.needs_approval(
                                tool_name, tool_def.tier, tool_args
                            ):
                                state.pending_action = {
                                    "tool": tool_name,
                                    "args": tool_args,
                                    "tier": tool_def.tier,
                                    "authorized_users": tool_args.get(
                                        "authorized_users", []
                                    ),
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

                    for fc in fn_calls:
                        tool_name = fc.name
                        tool_args = dict(fc.args) if fc.args else {}

                        tool_def = tool_registry.get_tool(tool_name)
                        if not tool_def:
                            tool_def = tool_registry.resolve_discovered_tool(
                                tool_name
                            )
                        if not tool_def:
                            response_parts.append(
                                types.Part(
                                    function_response=types.FunctionResponse(
                                        name=tool_name,
                                        response={
                                            "error": f"unknown tool '{tool_name}'"
                                        },
                                    )
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

                        # Handle "Ask Again" pattern from Tool Result
                        result_payload = (
                            {"result": result.data}
                            if result.success or (isinstance(result.data, dict) and result.data.get("needs_user_input"))
                            else {"error": result.error}
                        )

                        if isinstance(result.data, dict) and result.data.get("needs_user_input"):
                            workflow_name = str(result.data.get("workflow_name") or tool_args.get("workflow_name") or "").strip()
                            missing_params = result.data.get("missing_params") or []
                            if workflow_name and isinstance(missing_params, list) and missing_params:
                                self._start_or_update_param_collection(
                                    state=state,
                                    workflow_name=workflow_name,
                                    missing_params=[str(p) for p in missing_params if p],
                                    tool_name=tool_name,
                                    tool_args=tool_args,
                                    auto_execute=False,
                                )
                            pretty_items = [(self._prettify_param_name(str(p)), "") for p in missing_params]
                            question = self._build_param_request_message(
                                workflow_name=workflow_name or "this workflow",
                                items=pretty_items,
                                intro="I need a few more details.",
                            )
                            state.add_message("assistant", question)
                            state.is_agent_working = False
                            # We stop here and ask the user
                            return question

                        response_parts.append(
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name=tool_name,
                                    response=result_payload,
                                )
                            )
                        )

                    messages.append(types.Content(role="model", parts=response_parts))

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
                        if p.text
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
                    state.phase = ConversationPhase.IDLE

                    queued = self._drain_queued_messages(state, tracker)
                    if queued:
                        final_response += "\n\n" + queued
                    return final_response

            state.is_agent_working = False
            state.phase = ConversationPhase.IDLE
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
            state.phase = ConversationPhase.IDLE
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
        classification = self.approval_gate.classify_approval_turn(
            user_message=user_message,
            pending_action=state.pending_action,
            pending_summary=state.pending_action_summary,
            conversation_messages=state.messages,
        )
        intent = classification.intent

        if intent == ApprovalIntent.CLARIFY:
            return self.approval_gate.format_clarification_prompt(
                state.pending_action,
                state.pending_action_summary,
            )

        if intent == ApprovalIntent.CANCEL:
            state.phase = ConversationPhase.IDLE
            state.pending_action = None
            state.pending_action_summary = ""
            state.param_collection = {}
            return "Understood. I cancelled the pending action. What should I do next?"

        if intent in (ApprovalIntent.REJECT, ApprovalIntent.NEW_REQUEST):
            state.phase = ConversationPhase.IDLE
            state.pending_action = None
            state.pending_action_summary = ""
            state.param_collection = {}
            if intent == ApprovalIntent.NEW_REQUEST:
                return (
                    "Understood. I will not execute the pending action.\n\n"
                    + self._process_message(user_message, state, tracker)
                )
            return "Action rejected. What would you like me to do instead?"

        if intent != ApprovalIntent.APPROVE:
            return (
                "I couldn't confidently tell whether you want to approve, "
                "reject, or ask a question. Please say what you want in "
                "natural language, for example: 'yes proceed', "
                "'no don't do this', or ask a question."
            )

        action = state.pending_action
        if not action:
            state.phase = ConversationPhase.IDLE
            state.param_collection = {}
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

        if isinstance(result.data, dict) and result.data.get("needs_user_input"):
            workflow_name = str(result.data.get("workflow_name") or action.get("args", {}).get("workflow_name") or "").strip()
            missing_params = result.data.get("missing_params") or []
            if workflow_name and isinstance(missing_params, list):
                self._start_or_update_param_collection(
                    state=state,
                    workflow_name=workflow_name,
                    missing_params=[str(p) for p in missing_params if p],
                    tool_name=action.get("tool", "trigger_workflow"),
                    tool_args=action.get("args", {}),
                    auto_execute=True,
                )
            pretty_items = [(self._prettify_param_name(str(p)), "") for p in missing_params]
            question = self._build_param_request_message(
                workflow_name=workflow_name or "this workflow",
                items=pretty_items,
                intro="I need a few more details.",
            )
            state.add_message("assistant", question)
            return question

        active_issue = tracker.get_active_issue()
        if result.success:
            state.param_collection = {}
            if active_issue:
                tracker.resolve_issue(
                    active_issue.issue_id,
                    f"Approved and executed: {action_summary}",
                )
            state.phase = ConversationPhase.RESOLVED
            return self._format_completion_message(action["tool"], result.data)

        return (
            f"The action failed: {result.error}\n"
            f"Would you like me to try something else or escalate?"
        )

    @staticmethod
    def _format_completion_message(tool_name: str, data: dict) -> str:
        """Create a clean, human-readable summary of the tool result."""
        msg = data.get("message") or f"Action {tool_name} completed successfully."
        
        details = []
        exec_id = data.get("execution_id") or data.get("request_id")
        if exec_id:
            details.append(f"• **Execution ID**: `{exec_id}`")
        
        status = data.get("status") or data.get("state")
        if status:
            details.append(f"• **Status**: {status}")

        workflow = data.get("workflow_name")
        if workflow:
            details.append(f"• **Workflow**: `{workflow}`")

        response = f"**Done!** {msg}\n"
        if details:
            response += "\n" + "\n".join(details)
            
        return response

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

    def _preflight_workflow_param_collection(
        self,
        user_message: str,
        state: ConversationState,
        active_issue=None,
    ) -> str | None:
        """For workflow-execution intents, ask for all required params up front.

        This avoids free-text one-by-one prompting when the workflow schema is known.
        """
        msg = (user_message or "").strip()
        if not msg:
            return None

        discover = tool_registry.execute(
            "discover_tools",
            query=msg,
            category="automationedge",
            top_k=5,
        )
        state.log_tool_call("discover_tools", {"query": msg, "category": "automationedge", "top_k": 5}, discover.data, discover.success)
        if not discover.success:
            return None

        hits = (discover.data or {}).get("tools", [])
        if not isinstance(hits, list) or not hits:
            return None

        execution_intent = self._is_execution_request(msg)
        best_similarity = 0.0
        for hit in hits:
            if isinstance(hit, dict):
                try:
                    best_similarity = max(best_similarity, float(hit.get("similarity", 0.0) or 0.0))
                except Exception:
                    pass

        # If intent classifier misses but workflow match is strong, still treat as execution intent.
        if not execution_intent and best_similarity < 0.5:
            return None

        scored_hits: list[tuple[float, dict]] = []
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            try:
                sim = float(hit.get("similarity", 0.0) or 0.0)
            except Exception:
                sim = 0.0
            has_wf = 1.0 if str(hit.get("workflow_name") or "").strip() else 0.0
            scored_hits.append((sim + has_wf, hit))

        if not scored_hits:
            return None
        scored_hits.sort(key=lambda item: item[0], reverse=True)
        selected_hit = scored_hits[0][1]

        workflow_name = str(
            selected_hit.get("workflow_name")
            or selected_hit.get("name")
            or ""
        ).strip()
        if not workflow_name or not workflow_name.startswith("WF_"):
            for _, hit in scored_hits:
                wf = str(hit.get("workflow_name") or hit.get("name") or "").strip()
                if wf.startswith("WF_"):
                    workflow_name = wf
                    selected_hit = hit
                    break
        if not workflow_name:
            return None

        schema = get_ae_client().get_cached_workflow_parameters(workflow_name)
        # Prefer matched-tool metadata first; fallback to DB schema.
        hit_params = selected_hit.get("parameters")
        required_with_desc: list[tuple[str, str]] = []
        if isinstance(hit_params, list):
            for p in hit_params:
                if not isinstance(p, dict):
                    continue
                name = str(p.get("name") or "").strip()
                if not name:
                    continue
                is_required = (
                    p.get("required")
                    or p.get("is_required")
                    or p.get("optional") is False
                    or (
                        isinstance(p.get("optional"), str)
                        and p.get("optional").strip().lower() in {"false", "0", "no", "n"}
                    )
                )
                if is_required:
                    desc = str(p.get("description") or p.get("displayName") or "").strip()
                    required_with_desc.append((name, desc))

        if not required_with_desc:
            for p in schema:
                if not isinstance(p, dict):
                    continue
                name = str(p.get("name") or "").strip()
                if not name:
                    continue
                is_required = (
                    p.get("required")
                    or p.get("is_required")
                    or p.get("optional") is False
                    or (
                        isinstance(p.get("optional"), str)
                        and p.get("optional").strip().lower() in {"false", "0", "no", "n"}
                    )
                )
                if is_required:
                    desc = str(p.get("description") or p.get("displayName") or "").strip()
                    required_with_desc.append((name, desc))

        required = [name for name, _ in required_with_desc]
        if not required:
            return None

        if workflow_name not in state.affected_workflows:
            state.affected_workflows.append(workflow_name)
        if active_issue:
            # keep issue tracking in sync with chosen workflow
            self._get_issue_tracker(state.conversation_id).add_workflow_to_issue(
                active_issue.issue_id, workflow_name
            )

        pretty_items = []
        for name, desc in required_with_desc:
            pretty_name = self._prettify_param_name(name)
            clean_desc = self._clean_param_description(desc, name)
            pretty_items.append((pretty_name, clean_desc))

        state.param_collection = {
            "workflow_name": workflow_name,
            "required_params": required,
            "collected_params": {},
            "execution_tool": str(selected_hit.get("use_tool") or "trigger_workflow"),
            "execution_template": {"workflow_name": workflow_name},
            "matched_tool": selected_hit,
            "auto_execute": False,
        }
        return self._build_param_request_message(
            workflow_name=workflow_name,
            items=pretty_items,
            intro="I can help with that.",
        )

    def _is_execution_request(self, user_message: str) -> bool:
        """LLM-based intent check to avoid hardcoded workflow-action keyword lists."""
        try:
            verdict = llm_client.chat(
                (
                    "Classify the user request intent.\n"
                    "Return exactly one token: EXECUTE or NOT_EXECUTE.\n"
                    f'User message: "{user_message}"'
                ),
                system=(
                    "EXECUTE means user wants to run/create/trigger an automation workflow task. "
                    "NOT_EXECUTE means status/help/info/troubleshooting/general chat."
                ),
                temperature=0.0,
                max_tokens=8,
            ).strip().upper()
            return "EXECUTE" in verdict
        except Exception:
            return False

    def _continue_param_collection(self, user_message: str, state: ConversationState, tracker: IssueTracker | None = None) -> str | None:
        """Continue multi-turn param collection (ported from code_ref remediation flow)."""
        pc = state.param_collection or {}
        workflow_name = str(pc.get("workflow_name") or "").strip()
        required = [p for p in (pc.get("required_params") or []) if p]
        collected = dict(pc.get("collected_params") or {})
        if not workflow_name or not required:
            return None

        missing = [p for p in required if not collected.get(p)]
        if not missing:
            return None

        extracted = self._extract_params_from_user_message(user_message, missing)
        normalized_required = {self._norm_param_key(p): p for p in required}
        for key, value in extracted.items():
            if not value:
                continue
            if key in required:
                collected[key] = str(value).strip()
                continue
            mapped = normalized_required.get(self._norm_param_key(key))
            if mapped:
                collected[mapped] = str(value).strip()

        remaining = [p for p in required if not collected.get(p)]
        state.param_collection = {
            **pc,
            "workflow_name": workflow_name,
            "required_params": required,
            "collected_params": collected,
        }

        if remaining:
            pretty_items = [(self._prettify_param_name(p), "") for p in remaining]
            return self._build_param_request_message(
                workflow_name=workflow_name,
                items=pretty_items,
                intro="Got it, thanks.",
            )

        tool_name = str(pc.get("execution_tool") or "trigger_workflow")
        action_args = self._build_action_args_for_collection(pc, workflow_name, collected)

        # If this param collection was created after an already-approved action,
        # execute automatically once all params are available.
        if pc.get("auto_execute"):
            result = tool_registry.execute(tool_name, **action_args)
            state.log_tool_call(tool_name, action_args, result.data, result.success)

            if isinstance(result.data, dict) and result.data.get("needs_user_input"):
                missing_again = result.data.get("missing_params") or []
                self._start_or_update_param_collection(
                    state=state,
                    workflow_name=workflow_name,
                    missing_params=[str(p) for p in missing_again if p],
                    tool_name=tool_name,
                    tool_args=action_args,
                    auto_execute=True,
                )
                return result.data.get("question", "I still need a few details.")

            if result.success:
                state.param_collection = {}
                active_issue = tracker.get_active_issue() if tracker else None
                if active_issue and tracker:
                    tracker.resolve_issue(
                        active_issue.issue_id,
                        f"Executed {tool_name} for {workflow_name}",
                    )
                state.phase = ConversationPhase.RESOLVED
                return self._format_completion_message(tool_name, result.data or {})

            return (
                f"The action failed: {result.error}\n"
                "Would you like me to try something else or escalate?"
            )

        # Otherwise move to approval flow.
        state.pending_action = {
            "tool": tool_name,
            "args": action_args,
            "tier": "medium_risk",
            "authorized_users": [],
        }
        state.pending_action_summary = f"{tool_name} on {workflow_name}"
        state.phase = ConversationPhase.AWAITING_APPROVAL
        return self.approval_gate.format_approval_prompt(
            self.approval_gate.create_approval_request(
                tool_name,
                "medium_risk",
                action_args,
                state.pending_action_summary,
            )
        )

    def _extract_params_from_user_message(self, user_message: str, param_names: list[str]) -> dict[str, str | None]:
        """LLM-based param extractor inspired by code_ref remediation_agent_extract_params."""
        if not param_names:
            return {}
        prompt = (
            "Extract parameter values from the user message.\n"
            "Return valid JSON only.\n\n"
            f"Parameters needed: {param_names}\n"
            f'User message: "{user_message}"\n\n'
            'Return format: {"param_name": "value_or_null", ...}'
        )
        try:
            raw = llm_client.chat(
                prompt,
                system="Extract only the listed parameters. Return strict JSON.",
                temperature=0.0,
                max_tokens=512,
            )
            raw_clean = raw.strip()
            if raw_clean.startswith("```"):
                raw_clean = raw_clean.strip("`")
                raw_clean = raw_clean.replace("json", "", 1).strip()
            parsed = json.loads(raw_clean)
            if isinstance(parsed, dict):
                return {
                    str(k): (str(v).strip() if v not in (None, "", "null", "None") else None)
                    for k, v in parsed.items()
                }
            return {}
        except Exception:
            return {}

    @staticmethod
    def _norm_param_key(value: str) -> str:
        txt = "".join(ch for ch in str(value).lower() if ch.isalnum())
        if txt.endswith("s") and len(txt) > 3:
            txt = txt[:-1]
        return txt

    def _start_or_update_param_collection(
        self,
        *,
        state: ConversationState,
        workflow_name: str,
        missing_params: list[str],
        tool_name: str,
        tool_args: dict,
        auto_execute: bool,
    ) -> None:
        """Merge workflow params from prior state + latest tool args to avoid loops."""
        existing = state.param_collection or {}
        same_workflow = str(existing.get("workflow_name") or "").strip() == workflow_name
        collected = dict(existing.get("collected_params") or {}) if same_workflow else {}

        schema = get_ae_client().get_cached_workflow_parameters(workflow_name)
        required = [
            p.get("name")
            for p in schema
            if isinstance(p, dict)
            and p.get("name")
            and (
                p.get("required")
                or p.get("is_required")
                or p.get("optional") is False
                or (
                    isinstance(p.get("optional"), str)
                    and p.get("optional").strip().lower() in {"false", "0", "no", "n"}
                )
            )
        ]
        if not required:
            required = list(dict.fromkeys(missing_params))

        # Seed from args payload.
        for carrier in ("parameters", "params"):
            payload = tool_args.get(carrier)
            if isinstance(payload, dict):
                collected.update({k: v for k, v in payload.items() if v not in (None, "", {}, [])})

        for p in required:
            if tool_args.get(p) not in (None, "", {}, []):
                collected[p] = tool_args.get(p)

        state.param_collection = {
            "workflow_name": workflow_name,
            "required_params": required,
            "collected_params": collected,
            "execution_tool": tool_name or "trigger_workflow",
            "execution_template": tool_args or {},
            "auto_execute": bool(auto_execute),
        }

    def _build_action_args_for_collection(self, pc: dict, workflow_name: str, collected: dict) -> dict:
        tool_name = str(pc.get("execution_tool") or "trigger_workflow")
        template = dict(pc.get("execution_template") or {})

        if tool_name == "t4_execute_and_poll":
            args = {
                "workflow_name": workflow_name,
                "workflow_id": template.get("workflow_id") or get_ae_client().get_cached_workflow_id(workflow_name) or workflow_name,
                "params": {},
            }
            if isinstance(template.get("params"), dict):
                args["params"].update(template.get("params"))
            args["params"].update(collected)
            return args

        # Default path for trigger_workflow and most execution tools.
        args = {
            "workflow_name": workflow_name,
            "parameters": {},
        }
        if isinstance(template.get("parameters"), dict):
            args["parameters"].update(template.get("parameters"))
        args["parameters"].update(collected)
        return args

    def _format_rag_context(self, tool_hits, kb_hits, sop_hits, incident_hits=None) -> str:
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
        if incident_hits:
            inc_text = "\n".join(
                f"- {h.get('content', '')[:250]}" for h in incident_hits[:3]
            )
            sections.append(f"## Past Incidents & Resolutions\n{inc_text}")
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

    @staticmethod
    def _prettify_param_name(name: str) -> str:
        text = str(name or "").strip().replace("_", " ")
        if text.lower() == "emp id":
            return "employee ID"
        return text

    @staticmethod
    def _clean_param_description(desc: str, name: str) -> str:
        value = str(desc or "").strip()
        if not value:
            return ""
        if value.lower() in {"none", "null", "n/a", "na"}:
            return ""
        if value.strip().lower() == str(name or "").strip().lower():
            return ""
        return value

    def _build_param_request_message(
        self,
        *,
        workflow_name: str,
        items: list[tuple[str, str]],
        intro: str = "",
    ) -> str:
        friendly_wf = self._humanize_workflow_name(workflow_name)
        lines = []
        for label, desc in items:
            if desc:
                lines.append(f"- {label}: {desc}")
            else:
                lines.append(f"- {label}")
        prefix = f"{intro} " if intro else ""
        return (
            f"{prefix}To continue with {friendly_wf}, please share:\n"
            + "\n".join(lines)
        )

    @staticmethod
    def _humanize_workflow_name(workflow_name: str) -> str:
        name = str(workflow_name or "").strip()
        if name.upper().startswith("WF_"):
            name = name[3:]
        name = name.replace("_", " ").replace("-", " ").strip()
        return name.title() if name else "this workflow"
