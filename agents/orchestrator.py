"""
Main orchestrator agent.
Routes messages, manages the investigation/remediation loop,
coordinates tool calls via RAG-selected tools, and handles
issue lifecycle (new, continue, recurrence, escalation).
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import re

from google.genai import types

from agents.approval_gate import ApprovalGate, ApprovalIntent
from agents.escalation import EscalationAgent
from config.llm_client import llm_client
from config.metrics import metrics_collector
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
                       on_progress: ProgressCallback | None = None,
                       allowed_categories: list[str] | None = None) -> str:
        if not user_message.strip():
            return "It looks like your message was empty. How can I help?"

        progress = on_progress or create_noop_progress()
        
        # Start tracking turn metrics
        import uuid
        turn_id = f"turn-{uuid.uuid4().hex[:8]}"
        metrics_collector.start_turn(state.conversation_id, turn_id)
        
        try:
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

            # Conversational router (LLM-based): ACK/SMALLTALK/GENERAL/OPS.
            conv_route = self._classify_conversational_route(user_message, tracker)
            if conv_route in {"ACK", "SMALLTALK", "GENERAL"}:
                response = self._build_conversational_response(
                    user_message=user_message,
                    route=conv_route,
                    tracker=tracker,
                )
                state.phase = ConversationPhase.IDLE
                state.is_agent_working = False
                state.add_message("assistant", response)
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
                        + self._process_message(user_message, state, tracker, progress, allowed_categories)
                    )
                else:
                    response = self._process_message(user_message, state, tracker, progress, allowed_categories)

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
        finally:
            metrics_collector.end_turn(turn_id)

    def _classify_conversational_route(
        self,
        user_message: str,
        tracker: IssueTracker | None = None,
    ) -> str:
        """LLM router for conversational turns. Returns: ACK, SMALLTALK, GENERAL, or OPS."""
        text = str(user_message or "").strip()
        if not text:
            return "GENERAL"

        active_issue = tracker.get_active_issue() if tracker else None
        active_status = active_issue.status.value if active_issue else "none"

        best_similarity = 0.0
        try:
            rag = get_rag_engine()
            query_vec = rag.embed_query(text)
            tool_hits = rag.search_tools(text, top_k=5, query_embedding=query_vec)
            for hit in tool_hits or []:
                if not isinstance(hit, dict):
                    continue
                try:
                    best_similarity = max(best_similarity, float(hit.get("similarity", 0.0) or 0.0))
                except Exception:
                    continue
        except Exception:
            best_similarity = 0.0

        try:
            route = llm_client.chat(
                (
                    "Classify this user message for routing.\n"
                    "Return exactly one token: ACK, SMALLTALK, GENERAL, or OPS.\n"
                    "ACK = brief acknowledgement or thanks.\n"
                    "SMALLTALK = greeting/chit-chat without a task.\n"
                    "GENERAL = non-ops/general question not requiring workflows/tools/SOP.\n"
                    "OPS = any operations/support/troubleshooting/automation workflow intent.\n"
                    f"Active issue status: {active_status}\n"
                    f"Tool relevance score (0-1): {best_similarity:.3f}\n"
                    f'User message: "{text}"'
                ),
                system="Be strict and output one token only.",
                temperature=0.0,
                max_tokens=8,
            ).strip().upper()
            if route in {"ACK", "SMALLTALK", "GENERAL", "OPS"}:
                return route
        except Exception:
            pass

        return "OPS" if best_similarity >= 0.52 else "GENERAL"

    def _build_conversational_response(
        self,
        *,
        user_message: str,
        route: str,
        tracker: IssueTracker | None = None,
    ) -> str:
        text = str(user_message or "").strip()
        if route == "ACK":
            active = tracker.get_active_issue() if tracker else None
            if active and active.status == IssueStatus.RESOLVED:
                return "You're welcome. This issue is resolved. If it comes back, share the details and I'll check."
            return "You're welcome. Share any issue when ready and I'll help."

        if route == "SMALLTALK":
            return "Hi. I can help with AutomationEdge and IT ops issues. Tell me what you need."

        try:
            return llm_client.chat(
                (
                    "Respond naturally and briefly to the user's general message.\n"
                    "Do not mention tools, workflows, SOPs, or incidents.\n"
                    "End with one short line that you can also help with AutomationEdge issues.\n"
                    f"Route: {route}\n"
                    f'User message: "{text}"'
                ),
                system="You are a polite, concise assistant.",
                temperature=0.5,
                max_tokens=120,
            ).strip()
        except Exception:
            return (
                "Got it. If you need anything else, tell me.\n"
                "I can also help with AutomationEdge issues anytime."
            )

    # =====================================================================
    # Core investigation / remediation loop
    # =====================================================================

    def _process_message(self, user_message: str,
                         state: ConversationState,
                         tracker: IssueTracker,
                         progress: ProgressCallback | None = None,
                         allowed_categories: list[str] | None = None) -> str:
        progress = progress or create_noop_progress()
        state.is_agent_working = True
        state.phase = ConversationPhase.INVESTIGATING
        progress.on_phase("investigating")

        # Feature 2.2: Language Detection
        if len(state.messages) <= 3:
            detected = self._detect_language(user_message)
            if detected != state.preferred_language:
                state.preferred_language = detected
                logger.info(f"Language switch detected: {detected}")
                state.save()

        try:
            active_issue = tracker.get_active_issue()
            system_prompt = self._build_system_prompt(state, tracker)
            rag = get_rag_engine()

            # ── Context-Aware RAG Enrichment (Feature 1.1) ──
            enriched_query = user_message
            if active_issue:
                context_parts = []
                if active_issue.workflows_involved:
                    context_parts.append(f"Workflows: {', '.join(active_issue.workflows_involved)}")
                if active_issue.error_signatures:
                    context_parts.append(f"Errors: {', '.join(active_issue.error_signatures)}")
                if context_parts:
                    enriched_query = f"{user_message} (Context: {' '.join(context_parts)})"
                    logger.info(f"RAG enriched query: {enriched_query}")

            query_vec = rag.embed_query(enriched_query)
            # Run four RAG searches in parallel to reduce tail latency (same inputs/outputs)
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                f_tools = ex.submit(
                    rag.search_tools, enriched_query, 12, query_vec
                )
                f_kb = ex.submit(
                    rag.search_kb, enriched_query, 3, query_vec
                )
                f_sops = ex.submit(
                    rag.search_sops, enriched_query, 3, query_vec
                )
                f_incidents = ex.submit(
                    rag.search_past_incidents, enriched_query, 3, query_vec
                )
                tool_hits = f_tools.result()
                kb_hits = f_kb.result()
                sop_hits = f_sops.result()
                incident_hits = f_incidents.result()

            context_block = self._format_rag_context(
                tool_hits[:5], kb_hits, sop_hits, incident_hits
            )

            rag_tool_names = [
                h.get("metadata", {}).get("tool_name", h.get("id", ""))
                for h in tool_hits
            ]
            max_rag = CONFIG.get("MAX_RAG_TOOLS", 12)
            
            # ── Category-based Tool Isolation (Feature 1.2) ──
            vertex_tools = tool_registry.get_vertex_tools_filtered(
                rag_tool_names, 
                max_rag_tools=max_rag,
                allowed_categories=allowed_categories
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
                sop_hits=sop_hits,
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
                                        state.conversation_id,
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
                        
                        import time
                        tool_start = time.time()
                        
                        result = tool_registry.execute(tool_name, **tool_args)
                        
                        tool_lat = (time.time() - tool_start) * 1000
                        
                        # Find the active turn_id for this conversation
                        with metrics_collector._lock:
                            tid = next((tid for tid, m in metrics_collector.active_turns.items() 
                                       if m.conversation_id == state.conversation_id), None)
                        if tid:
                            metrics_collector.record_tool_call(tid, tool_name, tool_lat, result.success, result.error)

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
                            allowed_categories=allowed_categories,
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
                    # If no tool was called and tool relevance is weak, prefer SOP-guided resolution.
                    if self._should_use_sop_fallback(tool_hits, sop_hits):
                        final_response = self._build_sop_fallback_response(
                            user_message=user_message,
                            sop_hits=sop_hits,
                        )
                    else:
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
            self.approval_gate.log_decision(state.conversation_id, "CANCELLED")
            state.phase = ConversationPhase.IDLE
            state.pending_action = None
            state.pending_action_summary = ""
            state.param_collection = {}
            return "Understood. I cancelled the pending action. What should I do next?"

        if intent in (ApprovalIntent.REJECT, ApprovalIntent.NEW_REQUEST):
            self.approval_gate.log_decision(state.conversation_id, "REJECTED")
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

        rbac_ok, rbac_err = self._check_rbac(state, action.get("tier", "high_risk"))
        if not rbac_ok:
            return rbac_err

        self.approval_gate.log_decision(state.conversation_id, "APPROVED", state.user_id or "user")
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

        return self._build_action_failure_response(
            action_tool=str(action.get("tool") or ""),
            action_args=action.get("args") or {},
            error_text=result.error,
        )

    def _check_rbac(self, state: ConversationState, tier: str) -> tuple[bool, str]:
        """Verify the user's role allows actions of the given risk tier."""
        if not CONFIG.get("RBAC_ENABLED", False):
            return True, ""
            
        role = (state.user_role or "readonly").lower()
        role_rank = CONFIG["ROLE_RANK"].get(role, 0)
        tier_rank = CONFIG["TIER_RANK"].get(tier.lower(), 100) # Default to max rank for unknown
        
        if role_rank >= tier_rank:
            return True, ""
            
        min_role = self._get_min_role_for_tier(tier)
        return False, (
            f"Your role '{role}' is insufficient for {tier} actions. "
            f"Minimum role required: {min_role}"
        )

    def _get_min_role_for_tier(self, tier: str) -> str:
        tier_rank = CONFIG["TIER_RANK"].get(tier.lower(), 100)
        # Sort roles by rank to find the smallest rank that satisfies the tier
        roles = sorted(CONFIG["ROLE_RANK"].items(), key=lambda x: x[1])
        for role, rank in roles:
            if rank >= tier_rank:
                return role
        return "admin"

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

    def _detect_language(self, text: str) -> str:
        """Detect the ISO 639-1 language code of the text using LLM."""
        if not text or len(text.strip()) < 5:
            return "en"
        try:
            prompt = (
                "Detect the language of the following text. "
                "Return ONLY the ISO 639-1 language code (e.g. 'en', 'es', 'fr', 'hi', 'zh'). "
                "If unsure, return 'en'.\n\n"
                f"Text: {text[:200]}"
            )
            # Use raw chat to avoid recursion
            response = llm_client.chat(prompt, system="You are a language detector.")
            lang = str(response).strip().lower()
            return lang[:2].replace(".", "") if len(lang) >= 2 else "en"
        except Exception:
            return "en"

    # =====================================================================
    # Prompt building
    # =====================================================================

    def _build_system_prompt(self, state: ConversationState,
                             tracker: IssueTracker) -> str:
        base_prompt = """You are an AutomationEdge operations support agent.
You help investigate and resolve issues with RPA workflows.

Rules:
1. Always verify before acting - never guess based on symptoms alone.
2. Check input files early - 800+ workflows are file-based.
3. When multiple failures exist, trace to the upstream root cause.
4. Adjust detail level based on user role.
5. Every tool call is audited.
6. Read tool descriptions carefully. They may include use/avoid guidance,
   required parameters, and example arguments. Follow those hints exactly.
7. Prefer specific typed tools (check_workflow_status, get_execution_logs,
   etc.) - they have better validation and cleaner audit trails.
8. If no typed tool fits, use the general-purpose escape hatches:
   - call_ae_api: hit any AE REST endpoint directly
   - query_database: run read-only SQL against the ops database
   - search_knowledge_base: semantic search across all KB collections
9. If none of the above help, call discover_tools to search the full
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

        lang_instr = f"\n## Response Language: {state.preferred_language.upper()}\n"
        lang_instr += f"IMPORTANT: Respond to the user in {state.preferred_language.upper()} only. Keep internal reasoning (if any) or tool outputs as is, but the final text to the user MUST be in {state.preferred_language.upper()}."

        return f"{base_prompt}\n{persona}\n{issue_context}\n{lang_instr}"

    def _preflight_workflow_param_collection(
        self,
        user_message: str,
        state: ConversationState,
        active_issue=None,
        sop_hits: list[dict] | None = None,
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
                    best_similarity = max(
                        best_similarity,
                        float(hit.get("score", 0.0) or hit.get("similarity", 0.0) or 0.0),
                    )
                except Exception:
                    pass

        if not execution_intent and best_similarity < 0.5:
            return None

        scored_hits: list[tuple[float, dict]] = []
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            try:
                sim = float(hit.get("score", 0.0) or hit.get("similarity", 0.0) or 0.0)
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
        sop_guidance = self._extract_sop_param_hints(
            workflow_name=workflow_name,
            required_params=required,
            sop_hits=sop_hits or [],
        )
        return self._build_param_request_message(
            workflow_name=workflow_name,
            items=pretty_items,
            intro="I can help with that.",
            sop_guidance=sop_guidance,
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

            return self._build_action_failure_response(
                action_tool=tool_name,
                action_args=action_args,
                error_text=result.error,
            )

        # Otherwise move to approval flow.
        tool_def = tool_registry.get_tool(tool_name)
        actual_tier = tool_def.tier if tool_def else "medium_risk"
        state.pending_action = {
            "tool": tool_name,
            "args": action_args,
            "tier": actual_tier,
            "authorized_users": [],
        }
        state.pending_action_summary = f"{tool_name} on {workflow_name}"
        state.phase = ConversationPhase.AWAITING_APPROVAL
        return self.approval_gate.format_approval_prompt(
            self.approval_gate.create_approval_request(
                state.conversation_id,
                tool_name,
                actual_tier,
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
        sop_guidance: list[str] | None = None,
    ) -> str:
        friendly_wf = self._humanize_workflow_name(workflow_name)
        lines = []
        for label, desc in items:
            if desc:
                lines.append(f"- {label}: {desc}")
            else:
                lines.append(f"- {label}")
        prefix = f"{intro} " if intro else ""
        msg = (
            f"{prefix}To continue with {friendly_wf}, please share:\n"
            + "\n".join(lines)
        )
        if sop_guidance:
            msg += "\n\nPlease follow these guidelines:\n" + "\n".join(f"- {g}" for g in sop_guidance[:3])
        return msg

    @staticmethod
    def _humanize_workflow_name(workflow_name: str) -> str:
        name = str(workflow_name or "").strip()
        if name.upper().startswith("WF_"):
            name = name[3:]
        name = name.replace("_", " ").replace("-", " ").strip()
        return name.title() if name else "this workflow"

    def _extract_sop_param_hints(
        self,
        *,
        workflow_name: str,
        required_params: list[str],
        sop_hits: list[dict],
    ) -> list[str]:
        """Extract concise SOP-backed hints tied to required params."""
        if not sop_hits or not required_params:
            return []

        wf_norm = workflow_name.lower()
        hints: list[str] = []
        required_norm = {self._norm_param_key(p): p for p in required_params}

        for hit in sop_hits[:3]:
            content = str(hit.get("content") or "")
            if not content:
                continue
            lower = content.lower()
            # Prefer SOPs clearly related to this workflow when detectable.
            if wf_norm not in lower and "leave" not in lower and "payslip" not in lower and "employee" not in lower:
                continue
            for raw_line in content.splitlines():
                line = raw_line.strip(" -*\t")
                if not line:
                    continue
                line_lower = line.lower()
                # Keep only lines that likely guide input formatting or required values.
                if not any(tok in line_lower for tok in ("format", "date", "required", "must", "value", "start", "end", "id", "type")):
                    continue
                for p_norm, p_name in required_norm.items():
                    if p_norm and p_norm in self._norm_param_key(line_lower):
                        cleaned = line.strip()
                        if cleaned and cleaned not in hints:
                            hints.append(cleaned)
                        break

        return hints[:3]

    def _build_action_failure_response(self, *, action_tool: str, action_args: dict, error_text: str) -> str:
        """Natural fallback message with SOP-guided steps."""
        workflow_name = str(
            (action_args or {}).get("workflow_name")
            or (action_args or {}).get("workflow")
            or ""
        ).strip()
        wf_label = self._humanize_workflow_name(workflow_name) if workflow_name else "this request"

        if action_tool == "create_incident_ticket":
            title = str((action_args or {}).get("title") or "Support Incident").strip()
            guidance = self._get_sop_troubleshooting_steps(f"{title} {error_text}")
            msg = (
                "I couldn't reach the incident system automatically, but I can still help you resolve this.\n"
                f"Issue: {title}"
            )
            if guidance:
                msg += "\n\nRecommended troubleshooting steps:\n" + "\n".join(f"- {g}" for g in guidance)
            msg += (
                "\n\nIf you'd like, I can retry ticket creation or prepare an escalation summary for manual handoff."
            )
            return msg

        guidance = self._get_sop_troubleshooting_steps(f"{workflow_name or ''} {error_text}")
        msg = f"I couldn't complete the action for {wf_label} automatically."
        if guidance:
            msg += "\n\nRecommended troubleshooting steps:\n" + "\n".join(f"- {g}" for g in guidance)
        msg += "\n\nWould you like me to retry, create an incident ticket, or escalate?"
        return msg

    def _get_sop_troubleshooting_steps(self, query: str) -> list[str]:
        """Extract short SOP-like action steps for user-facing recovery guidance."""
        try:
            rag = get_rag_engine()
            hits = rag.search_sops(query, top_k=3)
        except Exception:
            return []

        steps: list[str] = []
        for hit in hits:
            content = str(hit.get("content") or "")
            for raw in content.splitlines():
                line = raw.strip(" -*\t")
                if not line:
                    continue
                lower = line.lower()
                if not any(k in lower for k in ("check", "verify", "ensure", "restart", "retry", "validate", "confirm", "collect", "contact")):
                    continue
                if len(line) < 18:
                    continue
                if line not in steps:
                    steps.append(line)
                if len(steps) >= 3:
                    return steps
        return steps

    @staticmethod
    def _should_use_sop_fallback(tool_hits: list[dict], sop_hits: list[dict], threshold: float = 0.52) -> bool:
        """Use SOP fallback when no strong tool match exists."""
        if not sop_hits:
            return False
        best_tool_similarity = 0.0
        for hit in tool_hits or []:
            try:
                best_tool_similarity = max(best_tool_similarity, float(hit.get("similarity", 0.0) or 0.0))
            except Exception:
                continue
        return best_tool_similarity < threshold

    def _build_sop_fallback_response(self, user_message: str, sop_hits: list[dict]) -> str:
        """Provide direct SOP-based guidance when tool coverage is missing."""
        steps = self._get_sop_troubleshooting_steps(user_message)
        if not steps:
            # Secondary parse directly from provided SOP hits.
            for hit in sop_hits[:3]:
                content = str(hit.get("content") or "")
                for raw in content.splitlines():
                    line = raw.strip(" -*\t")
                    if not line:
                        continue
                    if len(line) < 18:
                        continue
                    if any(k in line.lower() for k in ("check", "verify", "ensure", "restart", "retry", "validate", "contact")):
                        steps.append(line)
                    if len(steps) >= 3:
                        break
                if len(steps) >= 3:
                    break

        if steps:
            return (
                "I couldn’t find a direct automation tool for this request, but here are SOP-based steps to resolve it:\n"
                + "\n".join(f"- {s}" for s in steps[:3])
                + "\n\nIf you want, I can also create an incident ticket for follow-up."
            )

        return (
            "I couldn’t find a matching tool or a strong SOP for this request yet. "
            "Please share one more detail (system name, error message, or workflow name), and I’ll guide you step-by-step."
        )
