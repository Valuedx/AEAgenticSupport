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
from datetime import datetime
from typing import cast, Any, List, Optional


from google.genai import types

from agents.approval_gate import ApprovalGate, ApprovalIntent
from agents.escalation import EscalationAgent
from config.llm_client import llm_client, set_current_trace
from config.metrics import metrics_collector
from config.observability import trace_context, span_context
from gateway.progress import ProgressCallback, create_noop_progress
from rag.engine import get_rag_engine
from state.app_config import get_runtime_value
from state.conversation_state import ConversationState, ConversationPhase
from state.issue_tracker import (
    IssueTracker,
    MessageClassification,
    IssueStatus,
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
                       allowed_categories: list[str] | None = None,
                       feedback_agent_id: str = "ops_orchestrator") -> str:
        if not user_message.strip():
            return "It looks like your message was empty. How can I help?"

        progress = on_progress or create_noop_progress()
        
        # Start tracking turn metrics
        import uuid
        turn_id = f"turn-{cast(Any, uuid.uuid4().hex)[:8]}"
        metrics_collector.start_turn(state.conversation_id, turn_id)

        with trace_context(
            "orchestrator_turn",
            session_id=state.conversation_id,
            user_id=getattr(state, "user_id", ""),
            input={"message": user_message[:500], "turn_id": turn_id},
            metadata={"phase": state.phase.value if hasattr(state.phase, "value") else str(state.phase)},
            tags=["orchestrator"],
        ) as trace:
            set_current_trace(trace)

            try:
                return self._handle_message_inner(
                    user_message, state, progress, allowed_categories,
                    feedback_agent_id, turn_id, trace,
                )
            finally:
                set_current_trace(None)

    def _handle_message_inner(self, user_message: str,
                              state: ConversationState,
                              progress: ProgressCallback,
                              allowed_categories: list[str] | None,
                              feedback_agent_id: str,
                              turn_id: str,
                              trace) -> str:
        """Core message handling wrapped by handle_message's trace context."""
        try:
            state.add_message("user", user_message)
            tracker = self._get_issue_tracker(state.conversation_id)

            # ── Approval flow ──
            if state.phase == ConversationPhase.AWAITING_APPROVAL:
                active = tracker.get_active_issue()
                if active:
                    active.touch()
                    tracker._persist_issue(active)

                # Ask the LLM whether the user is responding to the approval
                # or pivoting to something completely new.
                intent_result = self.approval_gate.classify_approval_turn(
                    user_message=user_message,
                    pending_action=state.pending_action,
                    pending_summary=state.pending_action_summary,
                    conversation_messages=state.messages,
                )
                if intent_result.intent == ApprovalIntent.NEW_REQUEST:
                    # Suspend the pending approval so the user can resume later
                    state.suspended_flow = {
                        "type": "approval",
                        "pending_action": dict(state.pending_action or {}),
                        "pending_action_summary": state.pending_action_summary,
                    }
                    state.pending_action = None
                    state.pending_action_summary = ""
                    state.phase = ConversationPhase.IDLE
                    self.approval_gate.log_decision(state.conversation_id, "CANCELLED")
                    logger.info(
                        "approval_suspended conversation_id=%s intent=%s",
                        state.conversation_id, intent_result.reason,
                    )
                    # Answer the new question and append a resume reminder
                    inner = self._process_message(
                        user_message, state, tracker, progress,
                        feedback_agent_id=feedback_agent_id,
                    )
                    suspended_summary = state.suspended_flow.get(
                        "pending_action_summary", "previous action"
                    )
                    reminder = (
                        f"\n\n---\n"
                        f"\u23f8\ufe0f A pending action is on hold: **{suspended_summary}**\n"
                        f"Reply **continue** to review it again, or **drop it** to cancel."
                    )
                    state.save()
                    return inner + reminder

                response = self._handle_approval_response(
                    user_message, state, tracker
                )
                state.save()
                return response

            # Conversational router (LLM-based): ACK/SMALLTALK/GENERAL/OPS.
            # Skip for specialists (allowed_categories != None) or ongoing investigations.
            conv_route = "OPS"
            if allowed_categories is None and state.phase == ConversationPhase.IDLE:
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
                    title=cast(Any, user_message)[:80],
                    description=user_message,
                )
                state.phase = ConversationPhase.IDLE
                response = self._process_message(
                    user_message,
                    state,
                    tracker,
                    progress,
                    feedback_agent_id=feedback_agent_id,
                )

            elif classification == MessageClassification.CONTINUE_EXISTING:
                tracker.switch_to_issue(issue_id or tracker.active_issue_id)
                response = self._process_message(
                    user_message,
                    state,
                    tracker,
                    progress,
                    feedback_agent_id=feedback_agent_id,
                )

            elif classification == MessageClassification.RELATED_NEW:
                parent_id = issue_id or tracker.active_issue_id
                issue = tracker.create_issue(
                    title=cast(Any, user_message)[:80],
                    description=user_message,
                )
                if parent_id:
                    tracker.link_issues(parent_id, issue.issue_id)
                response = (
                    "This looks related to the issue I'm already investigating "
                    "but appears to be a separate problem. I'll track it as a "
                    "linked issue.\n\n"
                    + self._process_message(
                        user_message,
                        state,
                        tracker,
                        progress,
                        feedback_agent_id=feedback_agent_id,
                    )
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
                        f"({cast(Any, old_issue.resolution)[:150]}) is not holding. "
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
                        f"{cast(Any, old_issue.resolution)[:200]}. "
                        f"Let me check if the same root cause applies.\n\n"
                    )
                response = recurrence_note + self._process_message(
                    user_message,
                    state,
                    tracker,
                    progress,
                    feedback_agent_id=feedback_agent_id,
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
                        + self._process_message(
                            user_message,
                            state,
                            tracker,
                            progress,
                            allowed_categories,
                            feedback_agent_id=feedback_agent_id,
                        )
                    )
                else:
                    response = self._process_message(
                        user_message,
                        state,
                        tracker,
                        progress,
                        allowed_categories,
                        feedback_agent_id=feedback_agent_id,
                    )

            elif classification == MessageClassification.STATUS_CHECK:
                summary = tracker.get_all_issues_summary()
                response = (
                    f"Here's the current session status:\n\n{summary}\n\n"
                    + self._process_message(
                        user_message,
                        state,
                        tracker,
                        progress,
                        feedback_agent_id=feedback_agent_id,
                    )
                )
            else:
                response = self._process_message(
                    user_message,
                    state,
                    tracker,
                    progress,
                    feedback_agent_id=feedback_agent_id,
                )

            state.save()
            trace.update(output={"response": response[:1000]})
            return response
        except Exception as e:
            logger.exception(f"Error in handle_message: {e}")
            trace.update(output={"error": str(e)[:500]})
            error_msg = f"I encountered a technical problem: {cast(Any, str(e))[:100]}. Please try again or contact support."
            return error_msg
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

        # Fast-path: if the message contains IDs, dates, or time ranges, it is OPS.
        import re
        id_pattern = r"\b(request|req|id|execution|exec|automation|agent|workflow|status|error|fail|issue)\s*(id|#)?\s*:?\s*\d{0,}\b"
        date_pattern = r"\b(\d{1,4}[-/]\d{1,2}[-/]\d{1,4})\b"
        if re.search(id_pattern, text, re.IGNORECASE) or re.search(date_pattern, text):
            return "OPS"
            
        # Also match standalone numeric IDs that look like request IDs or Agent IDs (e.g. 2887)
        if re.search(r"\b\d{4,}\b", text):
            return "OPS"

        active_issue = tracker.get_active_issue() if tracker else None
        # If there's an active investigation or pending action, bias towards OPS
        if active_issue and text.lower() not in {"hi", "hello", "thanks", "ok", "yes", "no"}:
            # Check if it looks like a follow-up answer (containing names or specific values)
            if len(text) > 5:
                return "OPS"
                
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
                    best_similarity = max(
                        best_similarity,
                        float(hit.get("rrf_score", hit.get("similarity", 0.0)) or 0.0),
                    )
                except Exception:
                    continue
        except Exception:
            best_similarity = 0.0

        try:
            route = llm_client.chat(
                (
                    "Classify this user message for routing into one of four categories.\n"
                "CRITICAL: YOUR OUTPUT MUST BE EXACTLY ONE WORD: 'ACK', 'SMALLTALK', 'GENERAL', or 'OPS'.\n"
                "DO NOT provide any explanation, preamble, or punctuation.\n\n"
                "ACK = brief acknowledgement, thank you, or closing of the conversation.\n"
                "SMALLTALK = greeting/chit-chat that does not contain a specific task or question.\n"
                "GENERAL = a general non-technical question that does not require workflows, tools, or SOPs.\n"
                "OPS = any operations, support, troubleshooting, or automation intent (e.g. asking about status, errors, workflows, or fixes).\n"
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

        # Fallback to similarity check if LLM response was ambiguous or failed.
        # A threshold of 0.01 is more appropriate for RRF/vector search scores in this catalog.
        return "OPS" if best_similarity >= 0.01 else "GENERAL"

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

        # Compute IST (UTC+5:30) time and derive greeting word
        from datetime import timezone, timedelta
        _IST = timezone(timedelta(hours=5, minutes=30))
        _now_ist = datetime.now(_IST)
        _hour = _now_ist.hour
        if _hour < 12:
            _greeting = "Good morning"
        elif _hour < 17:
            _greeting = "Good afternoon"
        elif _hour < 21:
            _greeting = "Good evening"
        else:
            _greeting = "Good night"
        _now_str = _now_ist.strftime("%A, %d %B %Y  %I:%M %p IST")

        try:
            return llm_client.chat(
                (
                    f"Current Date/Time (IST): {_now_str}\n"
                    f"Appropriate greeting for this time of day: {_greeting}\n\n"
                    "Respond naturally to the user's message.\n"
                    "If this is a greeting or small talk, use the appropriate greeting above naturally — "
                    "do NOT hardcode a different greeting.\n"
                    "If the message seems like an operations request, clarify that you can help but need more detail.\n"
                    "Do not use internal IDs or technical error codes in this chat phase.\n"
                    "End by reminding the user that you are ready to help with AutomationEdge issues.\n"
                    f"Route: {route}\n"
                    f'User message: "{text}"'
                ),
                system="You are a polite, concise assistant. Always use the provided IST date/time and greeting word — never guess the time of day yourself.",
                temperature=0.5,
                max_tokens=120,
            ).strip()
        except Exception:
            return (
                f"{_greeting}! If you need anything, share it and I'll help.\n"
                "I can also help with AutomationEdge issues anytime."
            )

    # =====================================================================
    # Core investigation / remediation loop
    # =====================================================================

    def _process_message(self, user_message: str,
                         state: ConversationState,
                         tracker: IssueTracker,
                         progress: ProgressCallback | None = None,
                         allowed_categories: list[str] | None = None,
                         feedback_agent_id: str = "ops_orchestrator") -> str:
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
                # Only include error signatures if the issue is NOT resolved (Loop Fix)
                if active_issue.status != IssueStatus.RESOLVED and active_issue.error_signatures:
                    context_parts.append(f"Errors: {', '.join(active_issue.error_signatures)}")
                if active_issue.execution_ids:
                    context_parts.append(f"ExecutionIDs: {', '.join(active_issue.execution_ids)}")
                if context_parts:
                    enriched_query = f"{user_message} (Context: {' '.join(context_parts)})"
                    logger.info(f"RAG enriched query: {enriched_query}")

            query_vec = None
            try:
                query_vec = rag.embed_query(enriched_query)
            except Exception as e:
                logger.warning(f"RAG embedding failed: {e}. Falling back to keyword-only search.")

            # Run four RAG searches in parallel to reduce tail latency
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
                [tool_hits[i] for i in range(min(len(tool_hits), 5))], kb_hits, sop_hits, incident_hits
            )

            rag_tool_names = [
                h.get("metadata", {}).get("tool_name", h.get("id", ""))
                for h in tool_hits
            ]
            max_rag = get_runtime_value("MAX_RAG_TOOLS", 12)
            
            # ── Category-based Tool Isolation (Feature 1.2) ──
            turn_tools = tool_registry.build_turn_toolset_filtered(
                rag_tool_names,
                query=enriched_query,
                rag_hits=tool_hits,
                max_rag_tools=max_rag,
                allowed_categories=allowed_categories,
                feedback_agent_id=feedback_agent_id,
            )
            vertex_tools = turn_tools.to_vertex_tools()
            active_tool_names = set(turn_tools.list_tool_names())

            messages = [
                types.Content(
                    role="user",
                    parts=[types.Part(text=f"{context_block}\n\nUser: {user_message}")],
                )
            ]

            active_issue = tracker.get_active_issue()
            max_iterations = get_runtime_value("MAX_AGENT_ITERATIONS", 15)

            param_followup = self._continue_param_collection(user_message, state, tracker)
            if param_followup:
                state.add_message("assistant", param_followup)
                state.is_agent_working = False
                return param_followup

            # Track whether a flow was just suspended so we can append a reminder below
            _flow_was_suspended = bool(state.suspended_flow)

            preflight = self._preflight_workflow_param_collection(
                user_message=user_message,
                state=state,
                active_issue=active_issue,
                sop_hits=sop_hits,
                feedback_agent_id=feedback_agent_id,
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
                        tool_def = turn_tools.get_tool(tool_name)
                        if not tool_def:
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

                        tool_def = turn_tools.get_tool(tool_name)
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
                        
                        result = turn_tools.execute(tool_name, **tool_args)
                        
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
                        # Cleanup param collection on success (Loop Fix)
                        if result.success and tool_name in ("trigger_workflow", "t4_execute_and_poll"):
                            state.clear_param_collection()

                        progress.on_tool_done(
                            tool_name, result.success,
                            result.error if not result.success else "",
                        )

                        if tool_name == "discover_tools":
                            found = (result.data or {}).get("tools", [])
                            found_names: List[str] = [str(t.get("name") or "") for t in cast(List[Any], found) if isinstance(t, dict)]
                            active_set: set[str] = cast(set[str], active_tool_names)
                            new_names = [n for n in found_names if n and not active_set.__contains__(n)]
                            discovered_names.extend(new_names)
                            logger.info(
                                "discover_tools result: found=%s new_to_active=%s",
                                found_names, new_names,
                            )
                            if not found_names:
                                logger.warning(
                                    "discover_tools returned EMPTY results for this query. "
                                    "LLM may fall back to SOP. Check RAG index and category mapping."
                                )

                        if active_issue:
                            active_issue.touch()
                            wf = tool_args.get("workflow_name")
                            if wf:
                                tracker.add_workflow_to_issue(
                                    active_issue.issue_id, wf
                                )
                                tracker.add_error_signature(
                                    active_issue.issue_id,
                                    cast(Any, result.error)[:100],
                                )
                            if isinstance(result.data, dict):
                                eid = result.data.get("execution_id") or result.data.get("request_id")
                                if eid:
                                    tracker.add_execution_id_to_issue(
                                        active_issue.issue_id, str(eid)
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
                    logger.info("Iter %s: Tool execution turn complete. Appending results to context.", iteration)

                    if discovered_names or needs_expansion:
                        expanded = list(active_tool_names) + discovered_names
                        turn_tools = tool_registry.build_turn_toolset(
                            expanded,
                            allowed_categories=allowed_categories,
                            include_meta=True,
                            feedback_agent_id=feedback_agent_id,
                        )
                        vertex_tools = turn_tools.to_vertex_tools()
                        active_tool_names = set(turn_tools.list_tool_names())

                if not tool_called:
                    progress.on_phase("almost_done")
                    text_parts = [
                        p.text for p in parts
                        if p.text
                    ]
                    # If no tool was called and tool relevance is weak, prefer SOP-guided resolution.
                    # CRITICAL: Only fall back if NO tools have been successfully called in the session.
                    any_tools_called = any(
                        getattr(p, 'function_call', None) or getattr(p, 'function_response', None) 
                        for m in messages for p in m.parts
                    )
                    logger.warning(
                        "LLM did not call any tool on iteration %s. "
                        "any_tools_called_in_history=%s active_tools=%s",
                        iteration,
                        any_tools_called,
                        sorted(active_tool_names),
                    )
                    use_sop = not any_tools_called and self._should_use_sop_fallback(tool_hits, sop_hits)
                    if use_sop:
                        best_tool_sim = max(
                            (float(h.get("rrf_score", h.get("similarity", 0.0)) or 0.0) for h in (tool_hits or [])),
                            default=0.0,
                        )
                        best_sop_sim = max(
                            (float(h.get("rrf_score", h.get("similarity", 0.0)) or 0.0) for h in (sop_hits or [])),
                            default=0.0,
                        )
                        logger.warning(
                            "SOP fallback triggered: best_tool_score=%.4f best_sop_score=%.4f "
                            "(tool score < 0.52 or < 0.0001 with sop > 0.3). "
                            "Falling back to SOP guidance instead of tool call.",
                            best_tool_sim, best_sop_sim,
                        )
                        final_response = self._build_sop_fallback_response(
                            user_message=user_message,
                            sop_hits=sop_hits,
                        )
                    else:
                        if text_parts:
                            final_response = "\n".join(text_parts)
                        else:
                            final_response = (
                                "I've completed my investigation. Let me know if "
                                "you need anything else."
                            )
                    final_response = self._filter_for_persona(
                        final_response, state
                    )
                    # If a previous flow was suspended, remind the user they can resume
                    if _flow_was_suspended and state.suspended_flow:
                        wf = (
                            state.suspended_flow.get("workflow_name")
                            or state.suspended_flow.get("pending_action_summary")
                            or "previous request"
                        )
                        final_response += (
                            f"\n\n---\n"
                            f"\u23f8\ufe0f The **{wf}** request is on hold.\n"
                            f"Reply **continue** to pick up where we left off, "
                            f"or **drop it** to cancel."
                        )
                    state.add_message("assistant", final_response)
                    state.is_agent_working = False
                    state.phase = ConversationPhase.IDLE

                    queued = self._drain_queued_messages(state, tracker)
                    if queued:
                        final_response += "\n\n" + queued
                    
                    logger.info("Turn successfully completed with final response (len=%s)", len(final_response))
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
                    f"now — \"{cast(Any, content)[:150]}\""
                )
                state.add_message("user", content)
                resp = self._process_message(content, state, tracker)
                parts.append(resp)
            elif hint == "additive":
                state.add_message("user", f"[Additional context] {content}")
                parts.append(
                    f"**Noted additional context:** {cast(Any, content)[:200]}"
                )
            elif hint == "new_request":
                parts.append(
                    f"**Queued request:** \"{cast(Any, content)[:150]}\" — "
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
        # Cleanup param collection on success (Loop Fix)
        if result.success and action["tool"] in ("trigger_workflow", "t4_execute_and_poll"):
            state.clear_param_collection()

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
        if not get_runtime_value("RBAC_ENABLED", False):
            return True, ""
            
        role = (state.user_role or "readonly").lower()
        role_ranks = dict(get_runtime_value("ROLE_RANK", {}))
        tier_ranks = dict(get_runtime_value("TIER_RANK", {}))
        role_rank = role_ranks.get(role, 0)
        tier_rank = tier_ranks.get(tier.lower(), 100) # Default to max rank for unknown
        
        if role_rank >= tier_rank:
            return True, ""
            
        min_role = self._get_min_role_for_tier(tier)
        return False, (
            f"Your role '{role}' is insufficient for {tier} actions. "
            f"Minimum role required: {min_role}"
        )

    def _get_min_role_for_tier(self, tier: str) -> str:
        tier_ranks = dict(get_runtime_value("TIER_RANK", {}))
        role_ranks = dict(get_runtime_value("ROLE_RANK", {}))
        tier_rank = tier_ranks.get(tier.lower(), 100)
        # Sort roles by rank to find the smallest rank that satisfies the tier
        roles = sorted(role_ranks.items(), key=lambda x: x[1])
        for role, rank in roles:
            if rank >= tier_rank:
                return role
        return "admin"

    def _format_completion_message(self, tool_name: str, data: dict) -> str:
        """Create a clean, human-readable summary of the tool result with LLM-generated suggestions."""
        report = data.get("report")
        msg = data.get("message") or f"I've successfully completed the {tool_name} action."

        if report:
            # If tool provided a detailed markdown report, use that as the primary message
            msg = report
        
        details = []
        exec_id = data.get("execution_id") or data.get("request_id")
        if exec_id:
            details.append(f"• **Request ID**: `{exec_id}`")
        
        status = data.get("status") or data.get("state")
        if status:
            details.append(f"• **Status**: {status}")

        workflow = data.get("workflow_name")
        if workflow:
            details.append(f"• **Workflow**: `{workflow}`")

        response = f"### ✅ Action Completed\n{msg}\n"
        if details:
            response += "\n" + "\n".join(details)

        # Ask the LLM to generate 2 context-aware suggestions for what the user might want to do next.
        # If there's an error/failure, we MUST suggest creating a support ticket.
        try:
            status_prev = str(status or "").lower()
            is_failure = any(term in status_prev for term in ("fail", "error", "abort", "reject", "cancel", "invalid"))
            
            # Identify if this is likely an HDFC-related process
            wf_name = str(workflow or "").lower()
            is_hdfc = "hdfc" in wf_name or "hdfc" in str(tool_name).lower()
            
            ticket_tool = "create_hdfc_ticket" if is_hdfc else "create_incident_ticket"
            
            error_instruction = ""
            if is_failure:
                error_instruction = (
                    "CRITICAL: The previous action failed or encountered an error. "
                    f"At least ONE of your suggestions MUST be for creating a support ticket using `{ticket_tool}`. "
                    f"If it's a technical error, suggest an 'Incident' type. If it's a service gap, suggest a 'Request' type."
                )
            else:
                # Even on success, maybe they want to raise a service request?
                error_instruction = (
                    f"If the user might need follow-up assistance, you can suggest using `{ticket_tool}` with a 'Request' type."
                )

            context_summary = f"Tool: {tool_name}. Status: {status or 'unknown'}. Workflow: {workflow or 'unknown'}. Result: {cast(Any, msg)[:200]}"
            raw = llm_client.chat(
                (
                    "Based on the following action just completed by an AutomationEdge support agent, "
                    "suggest exactly 2 brief, actionable next steps the user might want to take. "
                    f"{error_instruction}\n"
                    "Return ONLY 2 bullet lines starting with '- '. No preamble, no explanation.\n\n"
                    f"Context: {context_summary}"
                ),
                system="You are a concise assistant. Output exactly 2 lines, each starting with '- '.",
                temperature=0.7,
                max_tokens=500,
            )
            # Handle both "-" and "*" bullet points
            suggestions = []
            for line in (raw or "").splitlines():
                line = line.strip()
                if line.startswith(("- ", "* ")):
                    suggestions.append(line.lstrip("-* ").strip())
            
            # Defensive fallback if LLM ignored the instruction on failure
            if is_failure and suggestions and not any(term in str(suggestions).lower() for term in ("ticket", "escalat", "incident", "support")):
                # Inject a ticket creation suggestion as a second bullet
                suggestions = [suggestions[0], "Raise a support ticket for further investigation."]

            suggestions = [suggestions[i] for i in range(min(len(suggestions), 2))]
        except Exception:
            suggestions = []

        if suggestions:
            _labels = [
                "**Here are a few options:**",
                "**You might also want to:**",
                "**Suggested next actions:**",
                "**Can I help with anything else?**",
                "**What's your next step?**",
            ]
            label = _labels[hash(tool_name) % len(_labels)]
            response += f"\n\n{label}\n" + "\n".join(f"- {s}" for s in suggestions)

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
                f"Text: {cast(Any, text)[:200]}"
            )
            # Use raw chat to avoid recursion
            response = llm_client.chat(prompt, system="You are a language detector.")
            lang = str(response).strip().lower()
            return cast(Any, lang)[:2].replace(".", "") if len(lang) >= 2 else "en"
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
7. Always prioritize `check_workflow_status` for ANY query about a bot's state, performance, or history. Followed by `get_execution_logs` for deep analysis.
8. If no typed tool fits, use the general-purpose escape hatches:
   - call_ae_api: hit any AE REST endpoint directly
   - query_database: run read-only SQL against the ops database
   - search_knowledge_base: semantic search across all KB collections
9. If none of the above help, call discover_tools to search the full
   catalog by description or category.
10. **CRITICAL: TECHNICAL PRIORITIZATION**. If you call a tool and it returns technical data (workflow instances, logs, agent stats), you MUST report that specific technical data to the user. Do NOT provide placeholder SOP instructions if tool data is available. Prefer the tool's live truth over static Knowledge Base or SOP text provided in the context block.
11. **CRITICAL: NUMERIC ID RULE**. If the user provides a specific numeric request ID, or automation request ID (e.g. "request id 2501865"), you MUST call `get_execution_status` with that exact ID immediately. Do NOT ask for more information. Do NOT generate troubleshooting steps. Call the tool first, then report results. **EXCEPTION: This rule does NOT apply to Schedule IDs — see Rule 12.**
12. **CRITICAL: SCHEDULE OPERATION RULE**. If the user says anything containing "schedule" AND an action word (disable, enable, pause, resume, stop, start, halt, activate, deactivate, turn off, turn on), you MUST call the appropriate schedule tool immediately:
    - "disable / pause / stop / halt / deactivate" → call `ae.schedule.disable` with `schedule_id` from the message
    - "enable / resume / start / activate / turn on" → call `ae.schedule.enable` with `schedule_id` from the message
    - "list schedules / show schedules / all schedules" → call `ae.schedule.list_all`
    Do NOT call `get_execution_status` for schedule operations. Do NOT show SOP steps. Call the schedule tool directly.
13. **CRITICAL: PROACTIVE NEXT STEPS**. Every final response MUST end with 2-3 specific, actionable suggestions. 
    - Use a DIFFERENT heading each time — rotate naturally among: "Here are a few options:", "You might also want to:", "Suggested next actions:", "Can I help with anything else?", "What's your next step?" — NEVER repeat the same heading in consecutive turns.
    - Keep suggestions relevant to the context (e.g., after a failure: offer log analysis; after a restart: offer status monitoring).
    - Match the persona: technical users get tool-specific options; business users get plain-language options.
14. **TERMINOLOGY & STATUS-FIRST RULE**: "Bots" and "Workflows" are synonymous. If a user asks about a bot (even by a "friendly" or "natural language" name like 'Email Bot JD'), you MUST call `check_workflow_status` as your FIRST action unless they explicitly say "run", "start", or "trigger". Never assume the user wants to execute a bot just because they mentioned its name.
15. **PROACTIVE PARAMETER DISCOVERY**: When `discover_tools` returns a workflow with `[ORCHESTRATOR_MAPPING]` in its description:
    - **TECHNICAL MAPPING MANDATE**: You MUST silently cross-reference the required parameters against the conversation history before generating a response.
    - **NO REDUNDANCY**: DO NOT list a parameter in your response if its value is already present in history (even if the user used similar terms like "starts tomorrow" or typos like "lleave").
    - **DECISIVE ACTION**: If the history contains ALL required parameters, you MUST skip the conversational summary and immediately propose or prepare the `trigger_workflow` tool call. Only prompt for the values that are strictly missing.
16. **PROACTIVE DIAGNOSTIC DISCOVERY**: If the user asks for logs, status, or diagnostics but context is missing (like `agent_id` or `execution_id`), you MUST NOT ask the user for it first. Instead, call a discovery tool like `ae.agent.list_running`, `list_recent_failures`, or `check_workflow_status` to find potential targets. If only one candidate is found, proceed with it automatically.
    - **NAME RESOLUTION**: If the user provides an agent NAME, call `ae.agent.get_details` or `ae.agent.analyze_logs` with that name. Tools are designed to resolve names to IDs automatically.
    - **AMBIGUITY RESOLUTION**: If discovery returns exactly one candidate, proceed with the investigation. If multiple are found, list them clearly with their names and IDs and ask the user to choose.
17. **LOG DATE SELECTION RULE**: 
    - **Agent Host Logs (`analyze_agent_logs`)**: When requested for an `agent_id`, you MUST inform the user that logs default to the last 24 hours and ask if they want to specify a particular `from_date` or `to_date` BEFORE performing extraction.
    - **Workflow Execution Logs (`get_execution_logs`)**: When requested for a specific Request/Execution ID, you MUST NOT ask for a time range. These logs represent the entire lifecycle of that specific run and do not require date filters. Call the tool immediately.
18. **STRICT CONTEXT INHERITANCE**: If you previously listed agents, workflows, or IDs (e.g., ID 2887) and the user responds with parameters (like a date range, "yes", or "proceed"), you MUST assume they are referring to the MOST RECENT entity mentioned. NEVER ask "which agent" if only one agent was discussed or listed in the immediate history. Use the `Recent Conversation Context` block provided below as your source of truth.

Available tool categories: status, logs, file, remediation, dependency,
config, notification, general, meta.
You have a subset of tools loaded. Use discover_tools to find others.
FORBIDDEN: Never respond with SOP steps like 'Step 1: Check workflow status...' when the user has given you a specific ID to look up. Call the tool instead.
"""

        persona = ""
        if state.user_role == "business":
            persona = """
## Persona: Business User
Explain in plain English. No workflow names, request IDs, or error codes.
Focus on business impact, timing, and resolution status."""
        else:
            persona = """
## Persona: Technical Staff
Include workflow names, request IDs, error details, and timestamps.
Provide full diagnostic information."""

        issue_context = ""
        if tracker and tracker.issues:
            active = tracker.get_active_issue()
            issue_context = f"""
## Active Issues in This Session
{tracker.get_all_issues_summary()}

Currently focused issue: {active.issue_id if active else 'None'}

IMPORTANT: Scope your investigation to the currently focused issue."""

        # ── Recent Tool Context (Cross-Turn Memory) ──
        # Inject the most recent tool findings into the prompt so that when a
        # follow-up question arrives (e.g. "explain why this failed"), the agent
        # knows which execution_id or workflow was found moments earlier.
        tool_context = ""
        if state.tool_call_log:
            log_len = len(state.tool_call_log)
            recent_calls = [state.tool_call_log[i] for i in range(max(0, log_len - 4), log_len)]
            # Expanded keys to catch Agent IDs and generic IDs
            interesting_keys = {
                "execution_id", "workflow_name", "workflow", "request_id", 
                "agent_id", "agentid", "uuid", "id", "name"
            }
            extracted: dict[str, str] = {}
            for call in recent_calls:
                result_data = call.get("result") or {}
                
                # Robustify: Handle dict, list, or direct item
                items_to_scan = []
                if isinstance(result_data, list):
                    items_to_scan = result_data
                elif isinstance(result_data, dict):
                    # Check common AE list wrappers
                    items_to_scan = (
                        result_data.get("instances") or 
                        result_data.get("failures") or 
                        result_data.get("agents") or
                        [result_data]
                    )
                else:
                    items_to_scan = [result_data]

                for row in items_to_scan:
                    if not isinstance(row, dict):
                        continue
                    for k, v in row.items():
                        if k.lower() in interesting_keys and v and str(v).strip():
                            # Map aliases to canonical keys for the LLM
                            canonical_k = k.lower()
                            if canonical_k in {"agentid", "uuid"}:
                                canonical_k = "agent_id"
                            
                            # Check for 'id' mapping if the tool name suggests it's an agent tool
                            tool_called = str(call.get("tool") or call.get("tool_name") or "").lower()
                            if canonical_k == "id" and "agent" in tool_called:
                                canonical_k = "agent_id"
                            
                            extracted[canonical_k] = str(v).strip()

            if extracted:
                ctx_lines = "\n".join(f"  - {k}: {v}" for k, v in extracted.items())
                tool_context = f"""
## Recent Tool Findings (from this conversation turn)
The following technical values were found in the most recent tool calls.
Use them directly when investigating instead of asking the user to repeat them:
{ctx_lines}
CRITICAL: If an `execution_id` or `request_id` is listed above and the user asks "why" or "explain", call `get_execution_logs` immediately.
CRITICAL: If an `agent_id` is listed above and the user asks for logs, says "yes/ok", or PROVIDES A DATE RANGE, you MUST call `analyze_agent_logs` with that `agent_id` immediately. Do NOT ask for the agent ID again."""

        # Build IST (UTC+5:30) datetime and compute the appropriate greeting word
        from datetime import timezone, timedelta
        _IST = timezone(timedelta(hours=5, minutes=30))
        _now_ist = datetime.now(_IST)
        _hour = _now_ist.hour
        if _hour < 12:
            _greeting = "Good morning"
        elif _hour < 17:
            _greeting = "Good afternoon"
        elif _hour < 21:
            _greeting = "Good evening"
        else:
            _greeting = "Good night"
        _now_str = _now_ist.strftime("%A, %d %B %Y  %I:%M %p IST")
        time_context = (
            "\n## Current Date & Time (Indian Standard Time)\n"
            f"- Date/Time : {_now_str}\n"
            f"- Greeting  : {_greeting}\n"
            "Use the greeting above when the context calls for one (e.g. first turn, opening a conversation). "
            "Do NOT use a different greeting; always derive it from the Date/Time provided above.\n"
        )
        
        lang_instr = f"\n## Response Language: {state.preferred_language.upper()}\n"
        lang_instr += f"IMPORTANT: Respond to the user in {state.preferred_language.upper()} only. Keep internal reasoning (if any) or tool outputs as is, but the final text to the user MUST be in {state.preferred_language.upper()}."

        # ── Recent Conversation Context (Cross-Issue Entity Memory) ──────────
        # This block carries forward named entities (IDs, workflow names, last
        # resolved actions) from the last 5 turns so the LLM doesn't re-ask
        # for values the user already provided — even across issue boundaries.
        recent_context = ""
        try:
            recent_context = state.get_recent_context_summary(n_turns=5)
        except Exception as _rc_err:
            logger.debug("Could not build recent context summary: %s", _rc_err)

        # ── Param Collection Persistence (Memory across turns) ──
        param_hint = ""
        active_issue = tracker.get_active_issue()
        is_resolved = active_issue.status == IssueStatus.RESOLVED if active_issue else False
        
        if not is_resolved and state.param_collection and state.param_collection.get("workflow_name"):
            wf = state.param_collection.get("workflow_name", "the current workflow")
            collected = dict(state.param_collection.get("collected_params") or {})
            required_list = list(state.param_collection.get("required_params") or [])
            remaining = [p for p in required_list if p not in collected]
            param_hint = f"""
## Active Workflow Parameter Collection
Workflow: {wf}
MANDATORY Parameters (ALL ARE REQUIRED): {required_list}
Collected from Conversation: {json.dumps(collected, indent=2)}
STILL MISSING: {remaining}

CRITICAL RULES:
- FORBIDDEN: Do NOT ask for, mention, or 'fill in' any parameter not in the 'MANDATORY Parameters' list.
- NEVER assume a value for a missing parameter (e.g., do NOT guess leave_type, start_date, or employee_id).
- YOU MUST explicitly ask the user for any parameter listed in 'STILL MISSING'.
- NEVER trigger the workflow if 'STILL MISSING' is not empty.
- If you call `trigger_workflow`, use ONLY the exact keys from the collected dict above.
"""

        parts = [base_prompt, persona]
        if recent_context:
            parts.append(recent_context)
        if param_hint:
            parts.append(param_hint)
        parts.extend([issue_context, tool_context, time_context, lang_instr])
        return "\n".join(parts)

    def _preflight_workflow_param_collection(
        self,
        user_message: str,
        state: ConversationState,
        active_issue=None,
        sop_hits: list[dict] | None = None,
        feedback_agent_id: str = "ops_orchestrator",
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
            _agent_id=feedback_agent_id,
        )
        state.log_tool_call("discover_tools", {"query": msg, "category": "automationedge", "top_k": 5}, discover.data, discover.success)
        if not discover.success:
            return None

        hits = (discover.data or {}).get("tools", [])
        if not isinstance(hits, list) or not hits:
            return None

        execution_intent = self._is_execution_request(msg)

        # Only measure similarity from WF_ workflow hits — non-workflow tools
        # like ae.agent.get_details can score higher but are irrelevant here.
        best_wf_similarity = 0.0
        for hit in hits:
            if isinstance(hit, dict):
                hit_name = str(hit.get("name") or hit.get("workflow_name") or hit.get("tool_name") or "").strip()
                hit_meta = hit.get("metadata") or {}
                is_ae = (
                    hit_name.startswith("WF_") or 
                    hit.get("category") == "automationedge" or 
                    hit_meta.get("source") == "automationedge" or
                    hit_meta.get("workflow_id")
                )
                if not is_ae:
                    continue
                try:
                    best_wf_similarity = max(
                        best_wf_similarity,
                        float(hit.get("score", 0.0) or hit.get("similarity", 0.0) or 0.0),
                    )
                except Exception:
                    pass

        # AE-102: Strictly respect execution intent.
        # If the LLM says NOT_EXECUTE, don't proactively start param collection/blocking.
        if not execution_intent:
            return None

        # Pre-filter to AutomationEdge workflow hits only.
        # Previously restricted to "WF_" prefix; now allowing any AE category/source.
        scored_hits: list[tuple[float, dict]] = []
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            hit_name = str(hit.get("name") or hit.get("workflow_name") or hit.get("tool_name") or "").strip()
            hit_meta = hit.get("metadata") or {}
            
            # Allow if it has a workflow_id or is from AE source
            is_ae = (
                hit_name.startswith("WF_") or 
                hit.get("category") == "automationedge" or 
                hit_meta.get("source") == "automationedge" or
                hit_meta.get("workflow_id")
            )
            
            if not is_ae:
                continue
                
            try:
                sim = float(hit.get("score", 0.0) or hit.get("similarity", 0.0) or 0.0)
            except Exception:
                sim = 0.0
            scored_hits.append((sim, hit))

        if not scored_hits:
            return None
        scored_hits.sort(key=lambda item: item[0], reverse=True)
        selected_hit = scored_hits[0][1]

        workflow_name = str(
            selected_hit.get("workflow_name")
            or selected_hit.get("name")
            or ""
        ).strip()
        if not workflow_name:
            return None

        client = get_ae_client()
        schema = client.get_cached_workflow_parameters(workflow_name)
        hit_meta = selected_hit.get("metadata") or {}
        hit_params = hit_meta.get("parameters") or selected_hit.get("parameters") or {}
        
        # Merge hit_params and schema to get the most comprehensive list.
        # RAG index (hit_params) is often more up-to-date than the local catalog CACHE.
        
        # 1. Start with schema (DB) as the baseline
        combined_schema: dict[str, dict] = {p.get("name"): p for p in (schema or []) if p.get("name")}
        
        # 2. Layer on search hit parameters (Metadata)
        if isinstance(hit_params, dict):
            for name, p_schema in hit_params.items():
                if name not in combined_schema:
                    combined_schema[name] = {"name": name}
                if isinstance(p_schema, dict):
                    combined_schema[name].update(p_schema)
        elif isinstance(hit_params, list):
            for p in hit_params:
                if isinstance(p, dict) and p.get("name"):
                    pname = str(p.get("name")).strip()
                    if pname and pname not in combined_schema:
                        combined_schema[pname] = p
                    else:
                        target_dict = combined_schema.get(pname)
                        if isinstance(target_dict, dict):
                            target_dict.update(p)

        # AE-77: Intercept file upload parameters before collection starts.
        # File upload is not supported in agentic chat yet.
        file_params = [
            name for name, p in combined_schema.items()
            if str(p.get("type") or p.get("uiControlType") or "").strip().lower() in {"file", "attachment", "upload"}
        ]
        if file_params:
            logger.info(f"Preflight: Workflow '{workflow_name}' matches but requires file upload ({file_params}). Rejecting.")
            params_str = ", ".join(file_params)
            return (
                f"I've identified that the **{workflow_name}** bot requires a **document upload** "
                f"for the following input(s): `{params_str}`. \n\n"
                "Since document uploading is not supported in the chat interface yet, "
                "please log in to the **AutomationEdge (AE) server** to trigger this bot manually. "
                "I apologize for the inconvenience!"
            )

        required_with_desc: list[tuple[str, str]] = []
        for name, p in combined_schema.items():
            # A parameter is REQUIRED unless it is explicitly marked as optional.
            opt = p.get("optional")
            is_explicitly_optional = (
                opt is True or 
                (isinstance(opt, str) and str(opt).strip().lower() in {"true", "1", "yes", "y"}) or
                p.get("is_optional") is True or
                p.get("required") is False or
                p.get("is_required") is False
            )
            
            if not is_explicitly_optional:
                desc = str(p.get("description") or p.get("displayName") or "").strip()
                required_with_desc.append((name, desc))

        required = [name for name, _ in required_with_desc]
        # No longer need manual fallbacks here as the client handles the 'required by default' model.
        if not required:
            return None

        # ── Proactive Extraction (Fix for "Preflight Blindness") ──
        # Extract params from the current message AND history immediately.
        extracted = self._extract_params_from_user_message(
            user_message=msg,
            param_names=required,
            messages=state.messages
        )
        # Apply normalization/mapping logic here too (Fix for "Preflight Blindness")
        collected = {}
        normalized_required = {self._norm_param_key(p): p for p in required}
        for key, value in extracted.items():
            if value in (None, "", "null", "None"):
                continue
            if key in required:
                collected[key] = str(value).strip()
                continue
            # Try fuzzy mapping via normalization
            mapped = normalized_required.get(self._norm_param_key(key))
            if mapped:
                collected[mapped] = str(value).strip()

        # Validate extensions
        validation_errors = self._validate_parameter_extensions(workflow_name, collected)

        if workflow_name not in state.affected_workflows:
            state.affected_workflows.append(workflow_name)
        if active_issue:
            # keep issue tracking in sync with chosen workflow
            self._get_issue_tracker(state.conversation_id).add_workflow_to_issue(
                active_issue.issue_id, workflow_name
            )

        # Update remaining items for the request message
        remaining_required = [p for p in required if p not in collected]
        
        state.param_collection = {
            "workflow_name": workflow_name,
            "required_params": required,
            "collected_params": collected,
            "execution_tool": str(selected_hit.get("use_tool") or "trigger_workflow"),
            "execution_template": {"workflow_name": workflow_name},
            "matched_tool": selected_hit,
            "auto_execute": False,
        }

        # If everything is already collected, return None to let the main loop proceed
        if not remaining_required:
            logger.info(f"Preflight: All params for {workflow_name} found in history/current message.")
            return None

        # Only list truly missing items
        pretty_items = []
        remaining_with_desc = [combined_schema.get(n) for n in remaining_required if combined_schema.get(n)]
        for p_schema in remaining_with_desc:
            name = p_schema.get("name")
            desc = str(p_schema.get("description") or p_schema.get("displayName") or "").strip()
            p_type = p_schema.get("type") or p_schema.get("uiControlType") or ""
            p_ext = p_schema.get("extension") or ""
            
            pretty_name = self._prettify_param_name(name)
            clean_desc = self._clean_param_description(desc, name)
            
            # Enrich label with type/extension info if relevant
            label = pretty_name
            metadata_hint = []
            if p_type.lower() == "file" or p_ext:
                metadata_hint.append("File Upload")
                if p_ext:
                    ext = str(p_ext).strip()
                    if not ext.startswith("."): ext = "." + ext
                    metadata_hint.append(f"format: {ext}")
            
            if metadata_hint:
                label += f" ({', '.join(metadata_hint)})"
                
            pretty_items.append((label, clean_desc))

        sop_guidance = self._extract_sop_param_hints(
            workflow_name=workflow_name,
            required_params=required,
            sop_hits=sop_hits or [],
        )

        intro = "I can help with that."
        if validation_errors:
            intro = "\n".join(validation_errors) + "\n\n" + intro

        return self._build_param_request_message(
            workflow_name=workflow_name,
            items=pretty_items,
            intro=intro,
            sop_guidance=sop_guidance,
        )

    def _validate_parameter_extensions(self, workflow_name: str, collected: dict) -> list[str]:
        """Validate collected parameters against their required extensions.
        Returns a list of human-readable error messages.
        """
        schema = get_ae_client().get_cached_workflow_parameters(workflow_name)
        errors = []
        for p in schema:
            if not isinstance(p, dict):
                continue
            name = p.get("name")
            required_ext = p.get("extension")
            if not name or not required_ext:
                continue
            
            # Extension might be ".xlsx" or "xlsx"
            ext_suffix = str(required_ext).strip()
            if not ext_suffix.startswith("."):
                ext_suffix = "." + ext_suffix

            value = collected.get(name)
            if not value:
                continue
            
            val_str = str(value).strip()
            # Skip validation for GUIDs (already uploaded to AE)
            # Typically looks like GUID-xxxx-xxxx or just a long UUID-like string
            if val_str.upper().startswith("GUID-") or ("-" in val_str and len(val_str) > 30):
                continue
            
            if not val_str.lower().endswith(ext_suffix.lower()):
                errors.append(
                    f"The parameter '{self._prettify_param_name(name)}' requires a **{ext_suffix}** file. "
                    f"You provided '{val_str}'."
                )
                # Remove from collected so it is asked for again
                if name in collected:
                    del collected[name]
                
        return errors

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
                    "EXECUTE means user wants to run, create, or trigger a new automation workflow. "
                    "NOT_EXECUTE means the user is asking for logs, status, history, errors, or general help with an EXISTING execution or workflow."
                ),
                temperature=0.0,
                max_tokens=8,
            ).strip().upper()
            return "EXECUTE" in verdict
        except Exception:
            return False

    def _detect_context_switch(
        self,
        user_message: str,
        workflow_name: str,
        missing_params: list[str],
        conversation_messages: list[dict],
    ) -> bool:
        """
        Use the LLM to decide whether the user’s message is a new/unrelated
        request rather than an answer to the pending parameter question.

        Returns True  → context switch detected (suspend param collection).
        Returns False → message is (or might be) a param value (keep collecting).

        Deliberately avoids hard-coded keyword lists so detection is language-
        and phrasing-agnostic.
        """
        context_tail = conversation_messages[-4:] if conversation_messages else []
        history_block = "\n".join(
            f"{m.get('role', 'unknown')}: {str(m.get('content', ''))[:200]}"
            for m in context_tail
        )
        prompt = (
            f"The bot was collecting inputs for a workflow named ‘{workflow_name}’.\n"
            f"It is still waiting for: {', '.join(missing_params)}.\n"
            f"Recent conversation:\n{history_block}\n\n"
            f"The user just said: \"{user_message}\"\n\n"
            "Is the user’s message a COMPLETELY NEW, UNRELATED request or question \n"
            "(i.e. NOT providing the value(s) asked for)?\n"
            "Reply with exactly one word: YES or NO."
        )
        try:
            result = llm_client.chat(
                prompt,
                system=(
                    "You classify user intent during multi-turn parameter collection. "
                    "YES means the user has switched topic. "
                    "NO means the user is still answering the parameter question."
                ),
                temperature=0.0,
                max_tokens=5,
            ).strip().upper()
            is_switch = result.startswith("YES")
            logger.debug(
                "context_switch_check workflow=%s message=%r result=%s",
                workflow_name, user_message[:60], result,
            )
            return is_switch
        except Exception as exc:
            logger.warning("_detect_context_switch LLM call failed: %s", exc)
            return False

    def _continue_param_collection(self, user_message: str, state: ConversationState, tracker: IssueTracker | None = None) -> str | None:
        """Continue multi-turn param collection (ported from code_ref remediation flow)."""
        # ── Handle resume / drop for a previously suspended flow ──
        if state.suspended_flow:
            msg_lower = user_message.strip().lower()
            if msg_lower in {
                "continue", "yes", "resume", "yes continue",
                "pick up", "go back", "proceed",
            }:
                # Restore the suspended flow
                state.param_collection = dict(
                    state.suspended_flow.get("param_collection", {})
                )
                state.clear_suspended_flow()
                logger.info("param_collection_resumed conversation_id=%s", state.conversation_id)
                # Fall through so the next missing param is asked
            elif msg_lower in {
                "drop it", "cancel it", "skip it", "cancel",
                "forget it", "no", "don't",
            }:
                state.clear_suspended_flow()
                state.clear_param_collection()
                return "Understood — I’ve cancelled that request. How else can I help?"

        pc = state.param_collection or {}
        workflow_name = str(pc.get("workflow_name") or "").strip()
        required = [p for p in (pc.get("required_params") or []) if p]
        collected = dict(pc.get("collected_params") or {})
        if not workflow_name or not required:
            return None

        missing = [p for p in required if not collected.get(p)]
        if not missing:
            return None

        # ── LLM-based context switch detection ──
        # If the user is asking something completely unrelated, suspend the
        # param flow so _process_message can answer the new question.
        if self._detect_context_switch(
            user_message, workflow_name, missing, state.messages
        ):
            state.suspended_flow = {
                "type": "param_collection",
                "param_collection": dict(state.param_collection),
                "workflow_name": workflow_name,
            }
            state.clear_param_collection()
            logger.info(
                "param_collection_suspended workflow=%s conversation_id=%s",
                workflow_name, state.conversation_id,
            )
            return None  # let _process_message handle the new question

        extracted = self._extract_params_from_user_message(user_message, missing, state.messages)
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

        # Validate extensions
        validation_errors = self._validate_parameter_extensions(workflow_name, collected)

        remaining = [p for p in required if not collected.get(p)]
        state.param_collection = {
            **pc,
            "workflow_name": workflow_name,
            "required_params": required,
            "collected_params": collected,
        }

        if remaining:
            # Build request for missing or invalid items
            pretty_items = []
            # Use combined schema info to get descriptions if possible
            # We already have the workflow_name and remaining list.
            # Look up descriptions from the cached schema for better labeling.
            schema_list = get_ae_client().get_cached_workflow_parameters(workflow_name)
            schema_map = {p.get("name"): p for p in schema_list if isinstance(p, dict) and p.get("name")}
            
            # AE-77: Safety check even in continue phase
            file_params = [
                name for name, p in schema_map.items()
                if str(p.get("type") or p.get("uiControlType") or "").strip().lower() in {"file", "attachment", "upload"}
            ]
            if file_params:
                logger.info(f"Continue: Workflow '{workflow_name}' requires file upload. Rejecting.")
                params_str = ", ".join(file_params)
                return (
                    f"I see that the **{workflow_name}** bot requires a **document upload** "
                    f"for: `{params_str}`. \n\n"
                    "Currently, I cannot process file uploads through this chat. "
                    "Please go to the **AutomationEdge (AE) server** to trigger this bot manually. "
                    "Thank you for your understanding!"
                )
            
            for name in remaining:
                # Always include the parameter even if not in local schema
                p_schema = schema_map.get(name, {})
                p_name = self._prettify_param_name(name)
                desc = p_schema.get("description") or p_schema.get("displayName") or ""
                clean_desc = self._clean_param_description(desc, name)
                
                p_type = p_schema.get("type") or p_schema.get("uiControlType") or ""
                p_ext = p_schema.get("extension") or ""
                
                label = p_name
                metadata_hint = []
                if p_type.lower() == "file" or p_ext:
                    metadata_hint.append("File Upload")
                    if p_ext:
                        ext = str(p_ext).strip()
                        if not ext.startswith("."): ext = "." + ext
                        metadata_hint.append(f"format: {ext}")
                
                if metadata_hint:
                    label += f" ({', '.join(metadata_hint)})"

                pretty_items.append((label, clean_desc))

            intro = "Got it, thanks."
            if validation_errors:
                intro = "\n".join(validation_errors) + "\n\n" + intro

            return self._build_param_request_message(
                workflow_name=workflow_name,
                items=pretty_items,
                intro=intro,
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

    def _extract_params_from_user_message(self, user_message: str, param_names: list[str], messages: list[dict] | None = None) -> dict[str, str | None]:
        """LLM-based param extractor inspired by code_ref remediation_agent_extract_params."""
        if not param_names:
            return {}
        
        context_str = ""
        if messages:
            # Get last 5 messages for context without overwhelming the prompt
            m_len = len(messages)
            context_history = [messages[i] for i in range(max(0, m_len - 6), max(0, m_len - 1))] if m_len > 1 else []
            history_lines = []
            for m in context_history:
                role = "User" if m.get("role") == "user" else "Assistant"
                history_lines.append(f"{role}: {m.get('content')}")
            if history_lines:
                context_str = "Conversation History:\n" + "\n".join(history_lines) + "\n\n"

        prompt = (
            f"{context_str}"
            "Extract parameter values from the user message, cross-referencing with the conversation history if provided.\n"
            "CRITICAL: You MUST use the exact keys from the 'Parameters needed' list below for the JSON keys.\n"
            "CRITICAL: Do NOT extract values that are generic or part of the user's intent framing (e.g. do not extract 'leave' from 'i want to apply for leave'). Only extract specific, concrete values provided by the user (e.g. 'sick', 'annual', '2023-12-01').\n"
            "Return valid JSON only.\n\n"
            f"Parameters needed: {param_names}\n"
            f'Current User message: "{user_message}"\n\n'
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
            txt = cast(Any, txt)[:-1]
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
        # Feature: Prioritize tools so LLM sees them as the primary action source
        if tool_hits:
            tool_text = "\n".join(
                f"- {str(h.get('content', ''))}" for h in [tool_hits[i] for i in range(min(len(tool_hits), 5))]
            )
            sections.append(f"## Relevant Tools\n{tool_text}")
        if kb_hits:
            kb_text = "\n".join(
                f"- {str(h.get('content', ''))}" for h in [kb_hits[i] for i in range(min(len(kb_hits), 3))]
            )
            sections.append(f"## Knowledge Base\n{kb_text}")
        if sop_hits:
            sop_text = "\n".join(
                f"- {str(h.get('content', ''))}" for h in [sop_hits[i] for i in range(min(len(sop_hits), 3))]
            )
            sections.append(f"## SOPs\n{sop_text}")
        if incident_hits:
            inc_text = "\n".join(
                f"- {str(h.get('content', ''))}" for h in [incident_hits[i] for i in range(min(len(incident_hits), 3))]
            )
            sections.append(f"## Past Incidents & Resolutions\n{inc_text}")
        return "\n\n".join(sections) if sections else ""

    def _filter_for_persona(self, response: str,
                            state: ConversationState) -> str:
        if state.user_role != "business":
            return response

        try:
            filtered = llm_client.chat(
                f"Rewrite this for a non-technical business user. "
                f"Remove workflow names, request IDs, error codes. "
                f"Focus on impact and status. "
                f"CRITICAL: Keep the 'Next Steps' or 'What would you like to do next?' section, but ensure the suggestions themselves are also non-technical (e.g., 'Should I check the overall system health?' instead of 'Check agent CPU logs').\n\n"
                f"Original Response:\n{response}",
                system="Rewrite technical text for business audiences while preserving proactive calls to action.",
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
        """LLM-based natural prompting inspired by the provided reference code."""
        friendly_wf = self._humanize_workflow_name(workflow_name)
        param_details = "\n".join([f"- {label}: {desc}" if desc else f"- {label}" for label, desc in items])
        
        sop_str = ""
        if sop_guidance:
            sop_str = "\nGuidelines from SOPs:\n" + "\n".join([f"- {g}" for g in [sop_guidance[i] for i in range(min(len(sop_guidance), 3))]])

        prompt = (
            f"You are a helpful RPA automation assistant. You are helping the user with: '{friendly_wf}'.\n"
            f"{intro}\n\n"
            "Ask the user to provide the following required information in a warm, conversational, and premium tone.\n"
            "List each item clearly with a bullet point. If a description is provided, use it to help the user understand what is needed.\n"
            "Do NOT use technical terms like JSON or API. End with an encouraging note.\n\n"
            f"Required Information:\n{param_details}\n{sop_str}\n\n"
            "Write the message now:"
        )

        try:
            # Using temperature 0.4 for a slightly more natural/varied but still professional feel
            response = llm_client.chat(
                prompt,
                system="You are a warm, helpful automation assistant providing a premium experience.",
                temperature=0.4,
                max_tokens=1024
            )
            return response.strip()
        except Exception as e:
            logger.warning(f"LLM prompting failed: {e}. Falling back to static template.")
            # Static fallback if LLM fails
            lines = [f"- {label}: {desc}" if desc else f"- {label}" for label, desc in items]
            prefix = f"{intro} " if intro else ""
            msg = (
                f"{prefix}To continue with {friendly_wf}, please share:\n"
                + "\n".join(lines)
            )
            if sop_guidance:
                msg += "\n\nPlease follow these guidelines:\n" + "\n".join(f"- {g}" for g in [sop_guidance[i] for i in range(min(len(sop_guidance), 3))])
            return msg

    @staticmethod
    def _humanize_workflow_name(workflow_name: str) -> str:
        name = str(workflow_name or "").strip()
        if name.upper().startswith("WF_"):
            name = str(name)[3:] if len(str(name)) > 3 else ""
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

        for hit in [sop_hits[i] for i in range(min(len(sop_hits), 3))]:
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

        return [hints[i] for i in range(min(len(hints), 3))]

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
    @staticmethod
    def _should_use_sop_fallback(tool_hits: list[dict], sop_hits: list[dict], threshold: float = 0.008) -> bool:
        """Use SOP fallback when no strong tool match exists.

        NOTE: tool_hits use RRF (Reciprocal Rank Fusion) scores, NOT cosine similarity.
        RRF scores are typically in the range 0.01–0.05, not 0.0–1.0.
        Threshold is calibrated for RRF: 0.008 ≈ tool ranked in top-5 of 60.
        """
        if not sop_hits:
            return False
        best_tool_similarity = 0.0
        for hit in tool_hits or []:
            try:
                best_tool_similarity = max(
                    best_tool_similarity,
                    float(hit.get("rrf_score", hit.get("similarity", 0.0)) or 0.0),
                )
            except Exception:
                continue
        logger.debug(
            "SOP fallback check: best_tool_score=%.4f threshold=%.4f sop_hits=%d",
            best_tool_similarity, threshold, len(sop_hits),
        )
        if best_tool_similarity < 0.001:
            # RAG found nothing useful at all.
            # Only fall back to SOP if we have a moderately high-confidence SOP hit.
            best_sop_similarity = 0.0
            for hit in sop_hits or []:
                best_sop_similarity = max(best_sop_similarity, float(hit.get("rrf_score", hit.get("similarity", 0.0)) or 0.0))
            logger.debug(
                "Tool RAG score near-zero. best_sop_score=%.4f (threshold > 0.003)",
                best_sop_similarity,
            )
            return best_sop_similarity > 0.003

        return best_tool_similarity < threshold

    def _build_sop_fallback_response(self, user_message: str, sop_hits: list[dict]) -> str:
        """Provide direct SOP-based guidance when tool coverage is missing."""
        steps = self._get_sop_troubleshooting_steps(user_message)
        if not steps:
            # Secondary parse directly from provided SOP hits.
            for hit in [sop_hits[i] for i in range(min(len(sop_hits), 3))]:
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
                + "\n".join(f"- {s}" for s in [steps[i] for i in range(min(len(steps), 3))])
                + "\n\nIf you want, I can also create an incident ticket for follow-up."
            )

        return (
            "I couldn’t find a matching tool or a strong SOP for this request yet. "
            "Please share one more detail (system name, error message, or workflow name), and I’ll guide you step-by-step."
        )
