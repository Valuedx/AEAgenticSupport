lines = open(r'd:\AEAgenticSupport\documents\AE_AGENTIC_PROCESS_DOCUMENT.md', encoding='utf-8').readlines()
before = ''.join(lines[:1075])  # keep everything up to line 1075

diagram = r"""## Full Flow Mermaid Diagram

```mermaid
flowchart TD
    A[User sends message] --> B{First message?}
    B -- Yes --> C[ConversationState.load - fresh state]
    B -- No  --> D[ConversationState.load - from PostgreSQL DB]
    C & D --> GW[gateway/message_gateway.py]

    GW --> BusyCheck{is_agent_working?}
    BusyCheck -- YES --> IntentQ{Message intent?}
    IntentQ -- ADDITIVE --> Queue[queue_user_message to _message_queue]
    IntentQ -- CANCEL_INTERRUPT --> Interrupt[Set interrupt_requested flag]
    IntentQ -- APPROVAL --> ApprCheck[_handle_approval_response]
    BusyCheck -- NO --> E[classify_conversational_route - LLM]

    E -- ACK_SMALLTALK --> F[Warm greeting response]
    E -- GENERAL --> F2[Answer and offer ops help]
    E -- OPS --> G[RAG Engine - rag/engine.py]

    G --> G2[search SOPs top 3]
    G --> G3[search tools top 12]
    G --> G4[search KB top 3]
    G --> G5[search past_incidents top 3]
    G2 & G3 & G4 & G5 --> H[IssueTracker.classify_message]

    H -- NEW_ISSUE --> I[create_issue in issue_registry]
    H -- CONTINUE_EXISTING --> I2[Enrich existing issue]
    H -- FOLLOWUP --> I2
    H -- RECURRENCE --> REC{recurrence_count >= 3?}
    REC -- YES --> ESC[IssueStatus = ESCALATED]
    ESC --> ESC2[create_incident_ticket P1]
    ESC2 --> ESC3[send_notification to L2 team]
    ESC3 --> RCAESC[RCAAgent.generate_rca - escalation RCA]
    RCAESC --> FINAL[Final response to user]
    REC -- NO --> I3[reopen_issue - increment recurrence_count]
    I3 --> I

    I & I2 --> AWAITCHECK{phase = AWAITING_APPROVAL?}
    AWAITCHECK -- YES --> ApprCheck
    AWAITCHECK -- NO --> PARAMCHECK{param_collection active?}
    PARAMCHECK -- YES --> CP[_continue_param_collection]
    CP --> CPALL{All params collected?}
    CPALL -- YES --> RETOOL[Re-run original tool with complete params]
    CPALL -- NO --> ASKMORE[Ask user for remaining params]
    PARAMCHECK -- NO --> DA[find_free_agent usecase=diagnostic]

    DA --> DB[mark_agent_busy in agent_execution_status]
    DB --> DC[LLM selects diagnostic tools]
    DC --> DC1[check_workflow_status]
    DC --> DC2[list_recent_failures hours=24]
    DC --> DC3[t4_check_agent_status]
    DC --> DC4[get_execution_logs tail=200]
    DC --> DC5[get_execution_history]
    DC1 & DC2 & DC3 & DC4 & DC5 --> DD[add_finding and add_error_signature to Issue object]
    DD --> DE[mark_agent_idle]
    DE --> DF{Issue found?}

    DF -- NO --> GOOD1{SOP hits available?}
    GOOD1 -- YES --> GOOD2[_build_sop_fallback_response - SOP steps to user]
    GOOD1 -- NO --> GOOD3[Health confirmed - all clear response]

    DF -- YES --> AGOFF{Agent DISCONNECTED?}
    AGOFF -- YES --> AGOFF2[pending_action = restart_ae_agent tier medium_risk]
    AGOFF2 --> AGAPP[AWAITING_APPROVAL - Restart agent? yes or no]
    AGAPP -- YES --> AGOFF4[restart_ae_agent - subprocess run startup cmd]
    AGOFF4 --> AGOFF5[Poll t4_check_agent_status every 2s up to 15 times]
    AGOFF5 --> AGOFF6{Agent running?}
    AGOFF6 -- YES --> RA
    AGOFF6 -- NO --> LOGREAD
    AGAPP -- Rejected --> RJ[Cancel action and inform user]
    AGOFF -- NO --> RA

    RA[find_free_agent usecase=remediation]
    RA --> RB[mark_agent_busy]
    RB --> RC[LLM builds remediation plan from issue findings]
    RC --> RD{Missing tool params?}
    RD -- YES --> RD2[needs_user_input with missing_params list]
    RD2 --> RD3[_start_or_update_param_collection in state]
    RD3 --> ASKMORE
    RD -- NO --> RE{ApprovalGate.needs_approval?}

    RE -- NO --> RF[Execute tool via tool_registry.execute]
    RE -- YES --> RG[format_approval_prompt - ask user]
    RG --> ApprCheck
    ApprCheck --> RH{classify_approval_turn result}
    RH -- APPROVE --> RF
    RH -- REJECT_CANCEL --> RJ
    RH -- CLARIFY --> RK[format_clarification_prompt]
    RH -- NEW_REQUEST --> E
    RF --> RS{Tool success?}

    RS -- YES --> RESOLVE[tracker.resolve_issue with resolution_text]
    RESOLVE --> RCA1[RCAAgent.generate_rca - rca_agent.py L20]
    RCA1 --> RCA2[Gather: findings + workflows_involved + tool_call_log last 15]
    RCA2 --> RCA3[rag.search_past_incidents top 3 similar cases]
    RCA3 --> RCA4{state.user_role?}

    RCA4 -- business --> BIZ1[_generate_business_rca - rca_agent.py L79]
    BIZ1 --> BIZ2[Plain English - no jargon - max 500 words]
    BIZ2 --> BIZ3[Covers: what happened + business impact + why + fix + prevention]

    RCA4 -- technical --> TECH1[_generate_technical_rca - rca_agent.py L106]
    TECH1 --> TECH2[Timeline + root cause chain A to B to C + IDs + tool logs]
    TECH2 --> TECH3[Includes: workflow names + execution IDs + error strings]

    BIZ3 & TECH3 --> RCASAVE[state.rca_data saved - generated_at + report + user_role]
    RCASAVE --> RCAIDX[_index_as_past_incident - rca_agent.py L136]
    RCAIDX --> RCAEXT[LLM extracts root_cause in one sentence]
    RCAEXT --> RCASTORE[rag.index_past_incident stored in collection=past_incidents]
    RCASTORE --> RCALOOP[RAG Feedback Loop - future similar incidents surface this resolution]
    RCALOOP --> FINAL

    RS -- NO --> FAIL1[First tool failure]
    FAIL1 --> RESTCHECK[get_tool_restartability]
    RESTCHECK --> RESTDB[Check tool_restartability PostgreSQL table]
    RESTDB --> RESTCAT[Check workflow_catalog.raw_data restartable field]
    RESTCAT --> RESTDEF[Default: not restartable if no metadata found]
    RESTDEF --> RESTQ{is_restartable?}

    RESTQ -- YES_checkpoint --> CHKPT[restart_tool_execution fromCheckpoint=True]
    CHKPT --> CHKPT2{Restart success?}
    CHKPT2 -- YES --> RESOLVE
    CHKPT2 -- NO --> LOGREAD

    RESTQ -- YES_idempotent --> FULLRS[restart_tool_execution full restart]
    FULLRS --> FULLRS2{Restart success?}
    FULLRS2 -- YES --> RESOLVE
    FULLRS2 -- NO --> LOGREAD

    RESTQ -- NO --> HUMANQ{human_approval_required?}
    HUMANQ -- YES --> HUMANASK[Ask user to verify records manually then confirm]
    HUMANASK -- Confirmed --> MANUALRS[Manual restart with explicit user approval]
    MANUALRS --> RS
    HUMANASK -- Declined --> LOGREAD
    HUMANQ -- HIGH --> LOGREAD

    LOGREAD[_collect_agent_log_tail 200 lines + get_execution_logs tail=200]
    LOGREAD --> LOGANALYZ[analyze_logs_and_plan - LLM reads log tail]
    LOGANALYZ --> LOGQ{human_required?}
    LOGQ -- NO --> AUTOFIX[Execute automated fix steps via tool calls]
    AUTOFIX --> RETOOL
    LOGQ -- YES --> TICKET2[create_incident_ticket priority P2]
    TICKET2 --> HUMANSTEPS[Human remediation steps response to user]

    Queue --> Drain[_drain_queued_messages after current processing completes]
    Drain --> Combine[IssueTracker.classify_message on queued message]
    Combine -- CONTINUE_EXISTING --> DD
    Combine -- NEW_ISSUE --> I

    RETOOL --> RF
```

---

*Document version 1.2 | March 2026 | Codebase: d:\AEAgenticSupport*
*All file paths, function names, line numbers, and table names verified against actual codebase.*
"""

with open(r'd:\AEAgenticSupport\documents\AE_AGENTIC_PROCESS_DOCUMENT.md', 'w', encoding='utf-8') as f:
    f.write(before + diagram)

print('Done.')
