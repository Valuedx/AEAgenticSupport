import React, {
  useDeferredValue,
  useEffect,
  useState,
  startTransition,
} from "react";
import { createRoot } from "react-dom/client";
import htm from "htm";

const html = htm.bind(React.createElement);

const TABS = [
  {
    id: "overview",
    title: "Overview",
    blurb: "See health, approvals, monitoring state, and the latest activity in one place.",
  },
  {
    id: "settings",
    title: "Application Settings",
    blurb: "Choose the user experience, operational rules, approvals, monitoring, and connection defaults.",
  },
  {
    id: "agents",
    title: "Agents",
    blurb: "Manage the agent catalog and review which specialist agents are currently registered.",
  },
  {
    id: "tools",
    title: "Tools",
    blurb: "Search the tool inventory, understand ownership, and resync from AutomationEdge.",
  },
  {
    id: "knowledge",
    title: "Knowledge",
    blurb: "Search SOP guidance and maintain the SOP library behind assistant recommendations.",
  },
  {
    id: "activity",
    title: "Activity",
    blurb: "Review recent interactions, scheduler tasks, approval queues, and metrics.",
  },
];

function requestJson(path, { token = "", method = "GET", body } = {}) {
  const headers = {};
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (token) {
    headers["X-Admin-Token"] = token;
  }
  return fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(async (response) => {
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload.error || payload.message || `HTTP ${response.status}`;
      throw new Error(message);
    }
    return payload;
  });
}

function toDraftValue(field, value) {
  if (field.type === "boolean") {
    return Boolean(value);
  }
  if (field.type === "string_list") {
    return Array.isArray(value) ? value.join("\n") : "";
  }
  if (field.type === "map_number") {
    if (!value || typeof value !== "object") {
      return "";
    }
    return Object.entries(value)
      .map(([key, rawVal]) => `${key}=${rawVal}`)
      .join("\n");
  }
  if (field.type === "number") {
    return value === undefined || value === null ? "" : String(value);
  }
  return value === undefined || value === null ? "" : String(value);
}

function buildDraftSections(schema, config) {
  const drafts = {};
  (schema || []).forEach((section) => {
    const current = (config || {})[section.id] || {};
    drafts[section.id] = {};
    (section.fields || []).forEach((field) => {
      drafts[section.id][field.key] = toDraftValue(field, current[field.key]);
    });
  });
  return drafts;
}

function formatTimestamp(value) {
  if (!value) {
    return "Not available";
  }
  try {
    return new Date(value).toLocaleString();
  } catch (error) {
    return String(value);
  }
}

function compactNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "0";
  }
  return new Intl.NumberFormat().format(Number(value));
}

function shortenText(value, maxLength = 160) {
  const text = String(value || "").trim();
  if (!text) {
    return "";
  }
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function splitCommaList(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function emptyAgentForm() {
  return {
    agentId: "",
    name: "",
    usecase: "",
    status: "active",
    persona: "technical",
    capabilitiesText: "",
    domainsText: "",
    priority: 50,
    version: "1.0.0",
    description: "",
    tagsText: "",
    linkedTools: [],
  };
}

function agentToForm(agent) {
  return {
    agentId: agent.agentId || "",
    name: agent.name || "",
    usecase: agent.usecase || "",
    status: agent.status || "active",
    persona: agent.persona || "technical",
    capabilitiesText: Array.isArray(agent.capabilities) ? agent.capabilities.join(", ") : "",
    domainsText: Array.isArray(agent.domains) ? agent.domains.join(", ") : "",
    priority: agent.priority ?? 50,
    version: agent.version || "1.0.0",
    description: agent.description || "",
    tagsText: Array.isArray(agent.tags) ? agent.tags.join(", ") : "",
    linkedTools: Array.isArray(agent.linkedTools) ? agent.linkedTools : [],
  };
}

function emptySopForm() {
  return {
    id: "",
    title: "",
    reference_id: "",
    tagsText: "",
    content: "",
  };
}

function sopToForm(item) {
  return {
    id: item.id || "",
    title: item.title || "",
    reference_id: item.reference_id || "",
    tagsText: Array.isArray(item.tags) ? item.tags.join(", ") : "",
    content: item.content || "",
  };
}

function emptyReferenceDocForm() {
  return {
    id: "",
    title: "",
    badge: "DOC",
    summary: "",
    audience: "",
    path: "",
    displayOrder: 100,
    active: true,
    available: false,
    updatedAt: "",
  };
}

function referenceDocToForm(item) {
  return {
    id: item.id || "",
    title: item.title || "",
    badge: item.badge || "DOC",
    summary: item.summary || "",
    audience: item.audience || "",
    path: item.path || "",
    displayOrder: item.displayOrder ?? 100,
    active: item.active !== false,
    available: Boolean(item.available),
    updatedAt: item.updatedAt || "",
  };
}

function emptyToolForm() {
  return {
    toolName: "",
    toolTitle: "",
    description: "",
    category: "",
    tier: "read_only",
    safety: "",
    tagsText: "",
    useWhen: "",
    avoidWhen: "",
    active: true,
    alwaysAvailable: false,
    allowedAgentsText: "",
    source: "",
    workflowName: "",
    linkedAgents: [],
    hasOverride: false,
    llmCallable: true,
  };
}

function toolToForm(tool) {
  return {
    toolName: tool.toolName || "",
    toolTitle: tool.toolTitle || "",
    description: tool.description || "",
    category: tool.category || "",
    tier: tool.tier || "read_only",
    safety: tool.safety || "",
    tagsText: Array.isArray(tool.tags) ? tool.tags.join(", ") : "",
    useWhen: tool.useWhen || "",
    avoidWhen: tool.avoidWhen || "",
    active: tool.active !== false,
    alwaysAvailable: Boolean(tool.alwaysAvailable),
    allowedAgentsText: Array.isArray(tool.allowedAgents) ? tool.allowedAgents.join(", ") : "",
    source: tool.source || "",
    workflowName: tool.workflowName || "",
    linkedAgents: Array.isArray(tool.linkedAgents) ? tool.linkedAgents : [],
    hasOverride: Boolean(tool.hasOverride),
    llmCallable: tool.llmCallable !== false,
  };
}

function emptySchedulerTaskForm() {
  return {
    taskId: "",
    name: "",
    description: "",
    scheduleType: "interval",
    intervalSeconds: 300,
    cronHour: 8,
    cronMinute: 0,
    handlerName: "health_check",
    handlerArgsText: "{}",
    enabled: true,
    isSystem: false,
  };
}

function schedulerTaskToForm(task) {
  return {
    taskId: task.task_id || "",
    name: task.name || "",
    description: task.description || "",
    scheduleType: task.schedule_type || "interval",
    intervalSeconds: task.interval_seconds ?? 300,
    cronHour: task.cron_hour ?? 8,
    cronMinute: task.cron_minute ?? 0,
    handlerName: task.handler_name || "health_check",
    handlerArgsText: JSON.stringify(task.handler_args || {}, null, 2),
    enabled: task.enabled !== false,
    isSystem: Boolean(task.is_system),
  };
}

function parseJsonObject(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed) {
    return {};
  }
  const parsed = JSON.parse(trimmed);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Handler arguments must be a JSON object.");
  }
  return parsed;
}

function extractSuggestedSteps(text) {
  return String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((line) => {
      const lower = line.toLowerCase();
      return [
        "check",
        "verify",
        "ensure",
        "restart",
        "retry",
        "validate",
        "confirm",
        "contact",
      ].some((verb) => lower.includes(verb));
    });
}

function StatusPill({ children, tone = "neutral" }) {
  return html`<span className=${`pill ${tone}`}>${children}</span>`;
}

function StatCard({ label, value, detail, tone = "neutral" }) {
  const toneClass = tone === "danger" ? "danger" : tone === "warn" ? "warn" : "neutral";
  return html`
    <article className="stat-card">
      <div className="stat-label">${label}</div>
      <div className="stat-value">${value}</div>
      <div className="stat-detail">
        <span className=${`pill ${toneClass}`}>${detail}</span>
      </div>
    </article>
  `;
}

function FieldEditor({ field, value, onChange }) {
  if (field.type === "boolean") {
    return html`
      <div className="switch-row">
        <div className="switch-copy">
          <strong>${field.label}</strong>
          <div className="field-hint">${field.help}</div>
        </div>
        <input
          className="toggle"
          type="checkbox"
          checked=${Boolean(value)}
          onChange=${(event) => onChange(event.target.checked)}
        />
      </div>
    `;
  }

  if (field.type === "textarea" || field.type === "string_list" || field.type === "map_number") {
    const className =
      field.type === "string_list" || field.type === "map_number"
        ? "text-area codeish"
        : "text-area";
    return html`
      <label className="field full">
        <span className="field-label">${field.label}</span>
        <textarea
          className=${className}
          value=${value}
          onInput=${(event) => onChange(event.target.value)}
        ></textarea>
        <span className="field-hint">${field.help}</span>
      </label>
    `;
  }

  if (field.type === "enum") {
    return html`
      <label className="field">
        <span className="field-label">${field.label}</span>
        <select
          className="select-input"
          value=${value}
          onChange=${(event) => onChange(event.target.value)}
        >
          ${(field.options || []).map(
            (option) => html`<option key=${option} value=${option}>${option}</option>`
          )}
        </select>
        <span className="field-hint">${field.help}</span>
      </label>
    `;
  }

  return html`
    <label className="field">
      <span className="field-label">${field.label}</span>
      <input
        className="text-input"
        type=${field.type === "number" ? "number" : "text"}
        value=${value}
        onInput=${(event) => onChange(event.target.value)}
      />
      <span className="field-hint">${field.help}</span>
    </label>
  `;
}

function SettingsSection({
  section,
  draft,
  onFieldChange,
  onSave,
  onReset,
  saving,
}) {
  const workspacePreview =
    section.id === "workspace"
      ? html`
          <div className="preview-card">
            <div className="section-heading">
              <div>
                <h3>End-user preview</h3>
                <p className="section-copy">
                  This is how the public web chat will read after you save the
                  current changes.
                </p>
              </div>
              <${StatusPill}>Live preview</${StatusPill}>
            </div>
            <div className="preview-shell">
              <div className="preview-header">
                <div>
                  <strong>${draft.assistantName || "AutomationEdge Ops Agent"}</strong>
                  <div className="muted">
                    ${draft.businessRoleLabel || "Business user"} and
                    ${" "}
                    ${draft.technicalRoleLabel || "Operations / IT"} audiences
                  </div>
                </div>
                <${StatusPill} tone="warn">Chat experience</${StatusPill}>
              </div>
              <div className="preview-bubble">
                <strong>Business welcome</strong>
                <div className="muted" style=${{ marginTop: "8px" }}>
                  ${draft.businessWelcomeMessage}
                </div>
              </div>
              <div className="preview-bubble">
                <strong>Technical welcome</strong>
                <div className="muted" style=${{ marginTop: "8px" }}>
                  ${draft.technicalWelcomeMessage}
                </div>
              </div>
              <div>
                <div className="field-label" style=${{ marginBottom: "8px" }}>
                  Quick actions
                </div>
                <div className="chip-row">
                  ${(draft.quickActions || "")
                    .split("\n")
                    .map((item) => item.trim())
                    .filter(Boolean)
                    .map((item, index) => html`<span className="chip" key=${`${item}-${index}`}>${item}</span>`)}
                </div>
              </div>
            </div>
          </div>
        `
      : null;

  return html`
    <section className="control-card">
      <div className="section-heading">
        <div>
          <div className="eyebrow">${section.title}</div>
          <h2>${section.title}</h2>
          <p className="section-copy">${section.summary}</p>
        </div>
        <${StatusPill} tone=${section.requiresRestart ? "warn" : "neutral"}>
          ${section.saveHint}
        </${StatusPill}>
      </div>
      <div className="field-grid">
        ${(section.fields || []).map((field) =>
          html`<${FieldEditor}
            key=${field.key}
            field=${field}
            value=${draft[field.key]}
            onChange=${(nextValue) => onFieldChange(section.id, field.key, nextValue)}
          />`
        )}
      </div>
      ${workspacePreview}
      <div className="section-actions">
        <div className="field-hint">${section.saveHint}</div>
        <div className="action-group">
          <button
            className="button ghost"
            disabled=${saving}
            onClick=${() => onReset(section.id)}
          >
            Reset section
          </button>
          <button
            className="button primary"
            disabled=${saving}
            onClick=${() => onSave(section.id)}
          >
            ${saving ? "Saving..." : "Save changes"}
          </button>
        </div>
      </div>
    </section>
  `;
}

function OverviewTab({ overview, onRefresh, onSchedulerAction, links }) {
  const schedulerRunning = Boolean(overview.scheduler?.running);
  const approvalCount = (overview.pendingApprovals || []).length;
  const interactionCount = (overview.interactions || []).length;
  const turnCount = overview.metrics?.turn_count || 0;
  const healthStatus = overview.health?.status || "unknown";
  const specialists = overview.multiAgents?.agents || [];

  return html`
    <div className="tab-shell">
      <section className="hero-panel">
        <div className="hero-copy">
          <div className="eyebrow">Control plane</div>
          <h1>Run the assistant from business language instead of code edits.</h1>
          <p>
            Update the experience people see, the approval and escalation
            guardrails, and the platform defaults that shape new requests. The
            overview below keeps day-to-day operators grounded in what is live.
          </p>
          <div className="hero-actions">
            <button className="button primary" onClick=${onRefresh}>Refresh snapshot</button>
            <button
              className="button secondary"
              onClick=${() => onSchedulerAction(schedulerRunning ? "stop" : "start")}
            >
              ${schedulerRunning ? "Pause scheduler" : "Start scheduler"}
            </button>
            <a className="button ghost link-inline" href=${links.documentation}>Open knowledge library</a>
            <a className="button ghost link-inline" href=${links.legacyTools}>Open legacy console</a>
          </div>
        </div>
        <div className="hero-side">
          <div className="side-note">
            <strong>Service health</strong>
            <div className="muted">
              The agent API is currently reporting <strong>${healthStatus}</strong>.
            </div>
          </div>
          <div className="side-note">
            <strong>Scheduler</strong>
            <div className="muted">
              ${schedulerRunning
                ? "Background monitoring is active."
                : "Background monitoring is currently paused."}
            </div>
          </div>
        </div>
      </section>

      <div className="stat-grid">
        <${StatCard}
          label="Service health"
          value=${healthStatus.toUpperCase()}
          detail=${schedulerRunning ? "Scheduler active" : "Scheduler paused"}
          tone=${healthStatus === "ok" ? "neutral" : "warn"}
        />
        <${StatCard}
          label="Pending approvals"
          value=${compactNumber(approvalCount)}
          detail=${approvalCount ? "Needs a decision" : "Queue is clear"}
          tone=${approvalCount ? "warn" : "neutral"}
        />
        <${StatCard}
          label="Recent tracked turns"
          value=${compactNumber(turnCount)}
          detail=${overview.metrics ? "Metrics are recording" : "No metrics yet"}
        />
        <${StatCard}
          label="Recent tool events"
          value=${compactNumber(interactionCount)}
          detail=${specialists.length ? `${specialists.length} specialist agents registered` : "No specialist data"}
        />
      </div>

      <div className="two-column">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>What is live right now</h2>
              <p>
                A quick narrative snapshot for operations managers and support
                leads.
              </p>
            </div>
          </div>
          <div className="list-stack">
            <div className="side-note">
              <strong>Scheduler state</strong>
              <div className="muted">
                ${schedulerRunning
                  ? `Running with ${(overview.scheduler?.tasks || []).length} configured task(s).`
                  : "Stopped. Tasks are still defined, but no timers are currently running."}
              </div>
            </div>
            <div className="side-note">
              <strong>Approval posture</strong>
              <div className="muted">
                ${approvalCount
                  ? `${approvalCount} approval request(s) are waiting for a decision.`
                  : "No approval requests are waiting right now."}
              </div>
            </div>
            <div className="side-note">
              <strong>Metrics snapshot</strong>
              <div className="muted">
                Average latency: ${overview.metrics?.avg_latency_ms
                  ? `${Math.round(overview.metrics.avg_latency_ms)} ms`
                  : "No completed turns yet"}
                . Total tokens tracked: ${compactNumber(overview.metrics?.total_tokens || 0)}.
              </div>
            </div>
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Latest tool activity</h2>
              <p>
                Recent tool execution events help operations teams spot drift or
                unexpected volume.
              </p>
            </div>
          </div>
          ${overview.interactions && overview.interactions.length
            ? html`
                <div className="table-wrap">
                  <table className="table-shell">
                    <thead>
                      <tr>
                        <th>Time</th>
                        <th>Agent</th>
                        <th>Tool</th>
                        <th>Outcome</th>
                      </tr>
                    </thead>
                    <tbody>
                      ${overview.interactions.slice(0, 8).map(
                        (item) => html`
                          <tr key=${`${item.timestamp}-${item.toolName}`}>
                            <td>${formatTimestamp(item.timestamp)}</td>
                            <td>${item.agentId || "Unknown"}</td>
                            <td><code>${item.toolName || "-"}</code></td>
                            <td>
                              <${StatusPill} tone=${item.success ? "neutral" : "danger"}>
                                ${item.success ? "Success" : "Failed"}
                              </${StatusPill}>
                            </td>
                          </tr>
                        `
                      )}
                    </tbody>
                  </table>
                </div>
              `
            : html`<div className="empty-state">No tool activity has been captured yet.</div>`}
        </section>
      </div>
    </div>
  `;
}

function SettingsTab({
  schema,
  drafts,
  onFieldChange,
  onSave,
  onReset,
  savingSections,
}) {
  return html`
    <div className="tab-shell">
      <section className="hero-panel">
        <div className="hero-copy">
          <div className="eyebrow">Application settings</div>
          <h1>Shape the assistant in language your operators understand.</h1>
          <p>
            Each section below controls a different layer of the experience:
            what users see, how the assistant decides, who can approve change,
            how monitoring behaves, and what technical endpoints it talks to.
          </p>
        </div>
        <div className="hero-side">
          <div className="side-note">
            <strong>Immediate effect</strong>
            <div className="muted">
              Conversation wording, approval rules, and most monitoring changes
              apply to new requests as soon as you save.
            </div>
          </div>
          <div className="side-note">
            <strong>Secret boundary</strong>
            <div className="muted">
              Secrets such as API keys and passwords stay in environment or
              secret storage. This screen only manages non-secret defaults.
            </div>
          </div>
        </div>
      </section>
      <div className="settings-grid">
        ${(schema || []).map(
          (section) => html`
            <${SettingsSection}
              key=${section.id}
              section=${section}
              draft=${drafts[section.id] || {}}
              onFieldChange=${onFieldChange}
              onSave=${onSave}
              onReset=${onReset}
              saving=${Boolean(savingSections[section.id])}
            />
          `
        )}
      </div>
    </div>
  `;
}

function AgentsTab({
  loaded,
  agents,
  specialists,
  toolOptions,
  form,
  onSelectAgent,
  onChangeForm,
  onSaveAgent,
  onDeleteAgent,
  onResetForm,
  saving,
}) {
  if (!loaded) {
    return html`<div className="loader">Loading agent data...</div>`;
  }

  return html`
    <div className="tab-shell">
      <div className="agent-grid">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Catalog agents</h2>
              <p>
                These are the agent definitions and tool associations managed by
                the control center.
              </p>
            </div>
          </div>
          <div className="stack-split">
            <div className="list-stack">
              ${agents.length
                ? agents.map(
                    (agent) => html`
                      <button
                        key=${agent.agentId}
                        className=${`agent-card ${form.agentId === agent.agentId ? "active" : ""}`}
                        onClick=${() => onSelectAgent(agent)}
                      >
                        <strong>${agent.name || agent.agentId}</strong>
                        <div className="muted mono">${agent.agentId}</div>
                        <div className="muted">${agent.usecase || "No use case yet"}</div>
                        <div className="muted">
                          Priority ${agent.priority ?? 50} | Version ${agent.version || "1.0.0"}
                        </div>
                        <div className="chip-row" style=${{ marginTop: "10px" }}>
                          <${StatusPill}>${agent.status || "active"}</${StatusPill}>
                          <${StatusPill} tone="warn">${agent.persona || "technical"}</${StatusPill}>
                          ${Array.isArray(agent.capabilities)
                            ? agent.capabilities.slice(0, 2).map(
                                (capability) =>
                                  html`<span className="chip" key=${`${agent.agentId}-${capability}`}>${capability}</span>`
                              )
                            : null}
                        </div>
                      </button>
                    `
                  )
                : html`<div className="empty-state">No catalog agents are defined yet.</div>`}
            </div>
            <div>
              <div className="field-grid">
                <label className="field">
                  <span className="field-label">Agent ID</span>
                  <input className="text-input" value=${form.agentId} onInput=${(e) => onChangeForm("agentId", e.target.value)} />
                  <span className="field-hint">Leave blank for a new record and the server will generate one.</span>
                </label>
                <label className="field">
                  <span className="field-label">Display name</span>
                  <input className="text-input" value=${form.name} onInput=${(e) => onChangeForm("name", e.target.value)} />
                  <span className="field-hint">Business-friendly name for this agent.</span>
                </label>
                <label className="field">
                  <span className="field-label">Use case</span>
                  <input className="text-input" value=${form.usecase} onInput=${(e) => onChangeForm("usecase", e.target.value)} />
                  <span className="field-hint">The operational scenario this agent supports.</span>
                </label>
                <label className="field">
                  <span className="field-label">Status</span>
                  <select className="select-input" value=${form.status} onChange=${(e) => onChangeForm("status", e.target.value)}>
                    <option value="active">active</option>
                    <option value="paused">paused</option>
                    <option value="disabled">disabled</option>
                  </select>
                  <span className="field-hint">Controls whether the catalog entry should be offered for routing and operator use.</span>
                </label>
                <label className="field">
                  <span className="field-label">Audience style</span>
                  <select className="select-input" value=${form.persona} onChange=${(e) => onChangeForm("persona", e.target.value)}>
                    <option value="technical">technical</option>
                    <option value="business">business</option>
                  </select>
                  <span className="field-hint">Tells the assistant which tone this agent is tuned for.</span>
                </label>
                <label className="field">
                  <span className="field-label">Capabilities</span>
                  <input className="text-input" value=${form.capabilitiesText} onInput=${(e) => onChangeForm("capabilitiesText", e.target.value)} />
                  <span className="field-hint">Comma-separated routing strengths such as orchestration, diagnostics, or remediation.</span>
                </label>
                <label className="field">
                  <span className="field-label">Domains</span>
                  <input className="text-input" value=${form.domainsText} onInput=${(e) => onChangeForm("domainsText", e.target.value)} />
                  <span className="field-hint">Comma-separated business areas such as operations, claims, finance, or network.</span>
                </label>
                <label className="field">
                  <span className="field-label">Priority</span>
                  <input className="text-input" type="number" value=${form.priority} onInput=${(e) => onChangeForm("priority", e.target.value)} />
                  <span className="field-hint">Lower numbers are considered first when multiple agents can handle the same work.</span>
                </label>
                <label className="field">
                  <span className="field-label">Version</span>
                  <input className="text-input" value=${form.version} onInput=${(e) => onChangeForm("version", e.target.value)} />
                  <span className="field-hint">Use a simple version marker so operators know which playbook is active.</span>
                </label>
                <label className="field full">
                  <span className="field-label">Description</span>
                  <textarea className="text-area" value=${form.description} onInput=${(e) => onChangeForm("description", e.target.value)}></textarea>
                  <span className="field-hint">Explain what this agent does in language your operators can understand.</span>
                </label>
                <label className="field">
                  <span className="field-label">Tags</span>
                  <input className="text-input" value=${form.tagsText} onInput=${(e) => onChangeForm("tagsText", e.target.value)} />
                  <span className="field-hint">Comma-separated hints such as claims, remediation, or priority.</span>
                </label>
                <label className="field full">
                  <span className="field-label">Linked tools</span>
                  <select
                    className="multi-select"
                    multiple
                    value=${form.linkedTools}
                    onChange=${(event) =>
                      onChangeForm(
                        "linkedTools",
                        Array.from(event.target.selectedOptions).map((option) => option.value)
                      )}
                  >
                    ${toolOptions.map(
                      (toolName) => html`<option key=${toolName} value=${toolName}>${toolName}</option>`
                    )}
                  </select>
                  <span className="field-hint">Choose which tools this catalog agent should be associated with.</span>
                </label>
              </div>
              <div className="section-actions">
                <div className="field-hint">Changes here update the agent catalog immediately.</div>
                <div className="action-group">
                  <button className="button ghost" onClick=${onResetForm}>Clear form</button>
                  <button className="button danger" disabled=${!form.agentId} onClick=${onDeleteAgent}>Delete</button>
                  <button className="button primary" disabled=${saving} onClick=${onSaveAgent}>
                    ${saving ? "Saving..." : "Save agent"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Registered specialist agents</h2>
              <p>
                These runtime specialists come from the Python agent registry
                and show what is currently available for routing.
              </p>
            </div>
          </div>
          <div className="specialist-list">
            ${specialists.length
              ? specialists.map(
                  (agent) => html`
                    <div className="specialist-card" key=${agent.agent_id}>
                      <strong>${agent.name || agent.agent_id}</strong>
                      <div className="muted mono">${agent.agent_id}</div>
                      <p className="muted">${agent.description || "No description provided."}</p>
                      <div className="chip-row">
                        <${StatusPill}>${agent.status || "active"}</${StatusPill}>
                        ${Array.isArray(agent.capabilities)
                          ? agent.capabilities.map(
                              (capability) =>
                                html`<span className="chip" key=${`${agent.agent_id}-${capability}`}>${capability}</span>`
                            )
                          : null}
                      </div>
                    </div>
                  `
                )
              : html`<div className="empty-state">No specialist agent metadata is available yet.</div>`}
          </div>
        </section>
      </div>
    </div>
  `;
}

function ToolsTab({
  loaded,
  tools,
  query,
  onQueryChange,
  onSync,
  syncing,
  form,
  onSelectTool,
  onChangeForm,
  onSaveTool,
  onResetTool,
  saving,
}) {
  if (!loaded) {
    return html`<div className="loader">Loading tool inventory...</div>`;
  }

  return html`
    <div className="tab-shell">
      <div className="agent-grid">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Tool inventory</h2>
              <p>
                Search across typed tools, dynamically discovered tools, and
                knowledge-backed tool cards. Select a tool to tune how it should
                appear and behave.
              </p>
            </div>
            <div className="toolbar-row">
              <input
                className="text-input"
                style=${{ minWidth: "280px" }}
                placeholder="Search by tool, workflow, category, or tag"
                value=${query}
                onInput=${(event) => onQueryChange(event.target.value)}
              />
              <button className="button primary" disabled=${syncing} onClick=${onSync}>
                ${syncing ? "Syncing..." : "Sync from AutomationEdge"}
              </button>
            </div>
          </div>
          <div className="table-wrap">
            <table className="table-shell">
              <thead>
                <tr>
                  <th>Tool</th>
                  <th>Workflow</th>
                  <th>Category</th>
                  <th>Source</th>
                  <th>Status</th>
                  <th>Linked agents</th>
                </tr>
              </thead>
              <tbody>
                ${tools.length
                  ? tools.map(
                      (tool) => html`
                        <tr
                          key=${tool.toolName}
                          className="row-clickable"
                          onClick=${() => onSelectTool(tool)}
                        >
                          <td>
                            <strong>${tool.toolTitle || tool.toolName}</strong>
                            <div className="muted mono">${tool.toolName}</div>
                            <div className="muted mono">${Array.isArray(tool.tags) ? tool.tags.join(", ") : ""}</div>
                          </td>
                          <td>${tool.workflowName || "-"}</td>
                          <td>
                            ${tool.category || "-"}
                            <div className="muted">${tool.tier || "-"}</div>
                          </td>
                          <td>
                            <${StatusPill} tone=${tool.source === "automationedge" ? "warn" : "neutral"}>
                              ${tool.source || "static"}
                            </${StatusPill}>
                            ${tool.hasOverride ? html`<div style=${{ marginTop: "6px" }}><${StatusPill}>Admin override</${StatusPill}></div>` : null}
                          </td>
                          <td>
                            <${StatusPill} tone=${tool.active ? "neutral" : "danger"}>
                              ${tool.active ? "Active" : "Inactive"}
                            </${StatusPill}>
                            ${tool.alwaysAvailable ? html`<div style=${{ marginTop: "6px" }}><${StatusPill}>Always available</${StatusPill}></div>` : null}
                          </td>
                          <td>
                            <div className="chip-row">
                              ${Array.isArray(tool.linkedAgents) && tool.linkedAgents.length
                                ? tool.linkedAgents.map(
                                    (agentId) => html`<span className="chip" key=${`${tool.toolName}-${agentId}`}>${agentId}</span>`
                                  )
                                : html`<span className="muted">None linked</span>`}
                            </div>
                          </td>
                        </tr>
                      `
                    )
                  : html`
                      <tr>
                        <td colSpan="6">
                          <div className="empty-state">No tools match the current search.</div>
                        </td>
                      </tr>
                    `}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Tool configuration</h2>
              <p>
                Adjust naming, safety cues, and availability in business-friendly
                language. These changes stay outside the Python source.
              </p>
            </div>
          </div>

          ${form.toolName
            ? html`
                <div className="field-grid">
                  <label className="field full">
                    <span className="field-label">Tool ID</span>
                    <input className="text-input mono" value=${form.toolName} disabled />
                    <span className="field-hint">System identifier used by the application.</span>
                  </label>
                  <label className="field">
                    <span className="field-label">Display title</span>
                    <input className="text-input" value=${form.toolTitle} onInput=${(e) => onChangeForm("toolTitle", e.target.value)} />
                    <span className="field-hint">Short, business-friendly name shown in the control center.</span>
                  </label>
                  <label className="field">
                    <span className="field-label">Category</span>
                    <input className="text-input" value=${form.category} onInput=${(e) => onChangeForm("category", e.target.value)} />
                    <span className="field-hint">Examples: status, remediation, dependency, notification.</span>
                  </label>
                  <label className="field">
                    <span className="field-label">Risk tier</span>
                    <select className="select-input" value=${form.tier} onChange=${(e) => onChangeForm("tier", e.target.value)}>
                      <option value="read_only">read_only</option>
                      <option value="low_risk">low_risk</option>
                      <option value="medium_risk">medium_risk</option>
                      <option value="high_risk">high_risk</option>
                    </select>
                    <span className="field-hint">Used by approvals and safety controls.</span>
                  </label>
                  <label className="field">
                    <span className="field-label">Allowed agents</span>
                    <input className="text-input" value=${form.allowedAgentsText} onInput=${(e) => onChangeForm("allowedAgentsText", e.target.value)} />
                    <span className="field-hint">Optional comma-separated agent IDs that may use this tool directly.</span>
                  </label>
                  <label className="field full">
                    <span className="field-label">Description</span>
                    <textarea className="text-area" value=${form.description} onInput=${(e) => onChangeForm("description", e.target.value)}></textarea>
                    <span className="field-hint">Plain-language explanation of what this tool does.</span>
                  </label>
                  <label className="field full">
                    <span className="field-label">Safety guidance</span>
                    <textarea className="text-area" value=${form.safety} onInput=${(e) => onChangeForm("safety", e.target.value)}></textarea>
                    <span className="field-hint">Explain what operators should know before using the tool.</span>
                  </label>
                  <label className="field full">
                    <span className="field-label">Use when</span>
                    <textarea className="text-area" value=${form.useWhen} onInput=${(e) => onChangeForm("useWhen", e.target.value)}></textarea>
                    <span className="field-hint">Describe when this is the right tool for the job.</span>
                  </label>
                  <label className="field full">
                    <span className="field-label">Avoid when</span>
                    <textarea className="text-area" value=${form.avoidWhen} onInput=${(e) => onChangeForm("avoidWhen", e.target.value)}></textarea>
                    <span className="field-hint">Describe when operators should avoid this tool and choose another path.</span>
                  </label>
                  <label className="field full">
                    <span className="field-label">Tags</span>
                    <input className="text-input" value=${form.tagsText} onInput=${(e) => onChangeForm("tagsText", e.target.value)} />
                    <span className="field-hint">Comma-separated search hints such as workflow, diagnostics, finance, restart.</span>
                  </label>
                  <div className="field full">
                    <div className="switch-row">
                      <div className="switch-copy">
                        <strong>Keep this tool active</strong>
                        <div className="field-hint">Turn this off to hide the tool from assistant use without removing its definition.</div>
                      </div>
                      <input className="toggle" type="checkbox" checked=${Boolean(form.active)} onChange=${(e) => onChangeForm("active", e.target.checked)} />
                    </div>
                    <div className="switch-row" style=${{ marginTop: "14px" }}>
                      <div className="switch-copy">
                        <strong>Always include this tool</strong>
                        <div className="field-hint">Useful for core diagnostics that should stay available even when the toolset is trimmed.</div>
                      </div>
                      <input className="toggle" type="checkbox" checked=${Boolean(form.alwaysAvailable)} onChange=${(e) => onChangeForm("alwaysAvailable", e.target.checked)} />
                    </div>
                  </div>
                </div>
                <div className="preview-card" style=${{ marginTop: "18px" }}>
                  <div className="list-stack">
                    <div className="side-note">
                      <strong>Source</strong>
                      <div className="muted">${form.source || "Unknown"}</div>
                    </div>
                    <div className="side-note">
                      <strong>Workflow</strong>
                      <div className="muted">${form.workflowName || "Not tied to a workflow"}</div>
                    </div>
                    <div className="side-note">
                      <strong>Linked catalog agents</strong>
                      <div className="chip-row" style=${{ marginTop: "8px" }}>
                        ${form.linkedAgents.length
                          ? form.linkedAgents.map(
                              (agentId) => html`<span className="chip" key=${`${form.toolName}-${agentId}`}>${agentId}</span>`
                            )
                          : html`<span className="muted">No linked catalog agents</span>`}
                      </div>
                    </div>
                    <div className="side-note">
                      <strong>Exposure</strong>
                      <div className="muted">
                        ${form.llmCallable
                          ? "This tool is available as a direct assistant tool when selected."
                          : "This tool is cataloged but not exposed as a direct assistant function."}
                      </div>
                    </div>
                  </div>
                </div>
                <div className="section-actions">
                  <div className="field-hint">
                    ${form.hasOverride
                      ? "This tool currently has an admin override saved."
                      : "Saving creates a non-code override for this tool."}
                  </div>
                  <div className="action-group">
                    <button className="button ghost" disabled=${saving} onClick=${onResetTool}>
                      Reset override
                    </button>
                    <button className="button primary" disabled=${saving} onClick=${onSaveTool}>
                      ${saving ? "Saving..." : "Save tool settings"}
                    </button>
                  </div>
                </div>
              `
            : html`<div className="empty-state">Select a tool from the inventory to edit its settings.</div>`}
        </section>
      </div>
    </div>
  `;
}

function KnowledgeTab({
  loaded,
  query,
  onQueryChange,
  onRunSearch,
  searching,
  searchHits,
  suggestedSteps,
  sops,
  form,
  onSelectSop,
  onChangeForm,
  onSaveSop,
  saving,
  referenceDocs,
  docForm,
  onSelectDoc,
  onChangeDocForm,
  onSaveDoc,
  onDeleteDoc,
  onResetDocForm,
  docSaving,
  docDeleting,
  documentationLink,
}) {
  if (!loaded) {
    return html`<div className="loader">Loading SOP knowledge base...</div>`;
  }

  return html`
    <div className="tab-shell">
      <div className="knowledge-grid">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>SOP assistant</h2>
              <p>
                Search SOP content the same way the assistant does, then review
                the exact snippets and extracted action steps.
              </p>
            </div>
            <div className="toolbar-row">
              <input
                className="text-input"
                style=${{ minWidth: "280px" }}
                placeholder="Describe an issue such as wifi connection failure"
                value=${query}
                onInput=${(event) => onQueryChange(event.target.value)}
              />
              <button className="button primary" disabled=${searching} onClick=${onRunSearch}>
                ${searching ? "Searching..." : "Find SOP steps"}
              </button>
            </div>
          </div>

          ${suggestedSteps.length
            ? html`
                <div className="preview-card">
                  <div className="field-label" style=${{ marginBottom: "10px" }}>
                    Suggested next steps
                  </div>
                  <div className="list-stack">
                    ${suggestedSteps.map(
                      (step, index) => html`
                        <div className="side-note" key=${`${step}-${index}`}>${step}</div>
                      `
                    )}
                  </div>
                </div>
              `
            : null}

          <div className="table-wrap" style=${{ marginTop: "18px" }}>
            <table className="table-shell">
              <thead>
                <tr>
                  <th>Similarity</th>
                  <th>Snippet</th>
                </tr>
              </thead>
              <tbody>
                ${searchHits.length
                  ? searchHits.map(
                      (hit, index) => html`
                        <tr key=${`${hit.similarity}-${index}`}>
                          <td>${hit.similarity ?? "-"}</td>
                          <td>${hit.content || "-"}</td>
                        </tr>
                      `
                    )
                  : html`
                      <tr>
                        <td colSpan="2">
                          <div className="empty-state">
                            No SOP search has been run yet or there were no meaningful matches.
                          </div>
                        </td>
                      </tr>
                    `}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>SOP library</h2>
              <p>
                Add or update SOP entries so operators and the assistant see the
                same playbook.
              </p>
            </div>
          </div>

          <div className="table-wrap" style=${{ marginBottom: "18px", maxHeight: "280px" }}>
            <table className="table-shell">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Tags</th>
                </tr>
              </thead>
              <tbody>
                ${sops.length
                  ? sops.map(
                      (item) => html`
                        <tr
                          key=${item.id}
                          className="row-clickable"
                          onClick=${() => onSelectSop(item)}
                        >
                          <td>
                            <strong>${item.title || item.id}</strong>
                            <div className="muted mono">${item.id}</div>
                          </td>
                          <td>${Array.isArray(item.tags) ? item.tags.join(", ") : ""}</td>
                        </tr>
                      `
                    )
                  : html`
                      <tr>
                        <td colSpan="2">
                          <div className="empty-state">No SOPs are stored yet.</div>
                        </td>
                      </tr>
                    `}
              </tbody>
            </table>
          </div>

          <div className="field-grid">
            <label className="field">
              <span className="field-label">SOP ID</span>
              <input className="text-input" value=${form.id} onInput=${(e) => onChangeForm("id", e.target.value)} />
              <span className="field-hint">Leave blank for a new SOP ID based on the title.</span>
            </label>
            <label className="field">
              <span className="field-label">Reference ID</span>
              <input className="text-input" value=${form.reference_id} onInput=${(e) => onChangeForm("reference_id", e.target.value)} />
              <span className="field-hint">Optional external or document reference.</span>
            </label>
            <label className="field full">
              <span className="field-label">Title</span>
              <input className="text-input" value=${form.title} onInput=${(e) => onChangeForm("title", e.target.value)} />
              <span className="field-hint">Use a clear title that makes sense to support teams.</span>
            </label>
            <label className="field full">
              <span className="field-label">Tags</span>
              <input className="text-input" value=${form.tagsText} onInput=${(e) => onChangeForm("tagsText", e.target.value)} />
              <span className="field-hint">Comma-separated tags such as wifi, network, recovery.</span>
            </label>
            <label className="field full">
              <span className="field-label">SOP content</span>
              <textarea className="text-area codeish" value=${form.content} onInput=${(e) => onChangeForm("content", e.target.value)}></textarea>
              <span className="field-hint">Plain text steps are fine. One action per line is easiest to review later.</span>
            </label>
          </div>

          <div className="section-actions">
            <div className="field-hint">Saving updates the knowledge base immediately.</div>
            <button className="button primary" disabled=${saving} onClick=${onSaveSop}>
              ${saving ? "Saving..." : "Save SOP"}
            </button>
          </div>
        </section>
      </div>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Reference document catalog</h2>
            <p>
              Decide which guides appear in the public documentation library and
              how they are labeled for end users.
            </p>
          </div>
          ${documentationLink
            ? html`<a className="button ghost link-inline" href=${documentationLink} target="_blank" rel="noreferrer">Open public library</a>`
            : null}
        </div>

        <div className="stack-split">
          <div>
            <div className="table-wrap" style=${{ maxHeight: "320px" }}>
              <table className="table-shell">
                <thead>
                  <tr>
                    <th>Document</th>
                    <th>Audience</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  ${referenceDocs.length
                    ? referenceDocs.map(
                        (item) => html`
                          <tr
                            key=${item.id}
                            className="row-clickable"
                            onClick=${() => onSelectDoc(item)}
                          >
                            <td>
                              <strong>${item.title || item.id}</strong>
                              <div className="muted mono">${item.path}</div>
                            </td>
                            <td>${item.audience || "General support teams"}</td>
                            <td>
                              <div className="chip-row">
                                <${StatusPill} tone=${item.active ? "neutral" : "warn"}>
                                  ${item.active ? "Visible" : "Hidden"}
                                </${StatusPill}>
                                <${StatusPill} tone=${item.available ? "neutral" : "danger"}>
                                  ${item.available ? "File ready" : "File missing"}
                                </${StatusPill}>
                              </div>
                            </td>
                          </tr>
                        `
                      )
                    : html`
                        <tr>
                          <td colSpan="3">
                            <div className="empty-state">
                              No reference documents are configured yet.
                            </div>
                          </td>
                        </tr>
                      `}
                </tbody>
              </table>
            </div>
          </div>

          <div>
            <div className="field-grid">
              <label className="field">
                <span className="field-label">Document ID</span>
                <input className="text-input" value=${docForm.id} onInput=${(e) => onChangeDocForm("id", e.target.value)} />
                <span className="field-hint">Leave blank to generate one from the title.</span>
              </label>
              <label className="field">
                <span className="field-label">Display order</span>
                <input className="text-input" type="number" value=${docForm.displayOrder} onInput=${(e) => onChangeDocForm("displayOrder", e.target.value)} />
                <span className="field-hint">Lower numbers appear earlier in the public library.</span>
              </label>
              <label className="field full">
                <span className="field-label">Document title</span>
                <input className="text-input" value=${docForm.title} onInput=${(e) => onChangeDocForm("title", e.target.value)} />
                <span className="field-hint">Use a title that makes sense to support leads and business reviewers.</span>
              </label>
              <label className="field">
                <span className="field-label">Badge</span>
                <input className="text-input" value=${docForm.badge} onInput=${(e) => onChangeDocForm("badge", e.target.value)} />
                <span className="field-hint">Short label shown in the document list, such as SOP or FAQ.</span>
              </label>
              <label className="field">
                <span className="field-label">Primary audience</span>
                <input className="text-input" value=${docForm.audience} onInput=${(e) => onChangeDocForm("audience", e.target.value)} />
                <span className="field-hint">Example: Business operations, administrators, platform team.</span>
              </label>
              <label className="field full">
                <span className="field-label">Markdown file path</span>
                <input className="text-input mono" value=${docForm.path} onInput=${(e) => onChangeDocForm("path", e.target.value)} />
                <span className="field-hint">Relative path within the project, such as docs/runbooks/payment_incidents.md.</span>
              </label>
              <label className="field full">
                <span className="field-label">Library summary</span>
                <textarea className="text-area" value=${docForm.summary} onInput=${(e) => onChangeDocForm("summary", e.target.value)}></textarea>
                <span className="field-hint">Short description shown so users know when this document is useful.</span>
              </label>
              <div className="field full">
                <div className="switch-row">
                  <div className="switch-copy">
                    <strong>Show in public library</strong>
                    <div className="field-hint">
                      Turn this off to keep the document in the catalog without showing it to end users.
                    </div>
                  </div>
                  <input className="toggle" type="checkbox" checked=${Boolean(docForm.active)} onChange=${(e) => onChangeDocForm("active", e.target.checked)} />
                </div>
              </div>
            </div>

            <div className="preview-card" style=${{ marginTop: "18px" }}>
              <div className="section-heading">
                <div>
                  <h3>Document readiness</h3>
                  <p className="section-copy">
                    Review whether the linked file is present before publishing
                    the document in the public library.
                  </p>
                </div>
                <${StatusPill} tone=${docForm.available ? "neutral" : "danger"}>
                  ${docForm.available ? "File found" : "File missing"}
                </${StatusPill}>
              </div>
              <div className="muted">
                ${docForm.updatedAt
                  ? `Last saved ${formatTimestamp(docForm.updatedAt)}`
                  : "This document has not been saved yet."}
              </div>
            </div>
          </div>
        </div>

        <div className="section-actions">
          <div className="field-hint">
            Saving updates the public documentation library without editing the page code.
          </div>
          <div className="action-group">
            <button className="button ghost" onClick=${onResetDocForm}>
              New document
            </button>
            <button
              className="button danger"
              disabled=${!docForm.id || docDeleting}
              onClick=${onDeleteDoc}
            >
              ${docDeleting ? "Deleting..." : "Delete document"}
            </button>
            <button className="button primary" disabled=${docSaving} onClick=${onSaveDoc}>
              ${docSaving ? "Saving..." : docForm.id ? "Save document" : "Create document"}
            </button>
          </div>
        </div>
      </section>
    </div>
  `;
}

function ActivityTab({
  loaded,
  interactions,
  interactionFilter,
  onInteractionFilter,
  pendingApprovals,
  onDecision,
  approvalsBusy,
  scheduler,
  schedulerLog,
  metrics,
  onSchedulerAction,
  taskForm,
  onSelectTask,
  onTaskFormChange,
  onSaveTask,
  onDeleteTask,
  onResetTask,
  taskSaving,
  taskDeleting,
  onToggleTask,
  historyQuery,
  onHistoryQueryChange,
  onRunHistorySearch,
  onClearHistorySearch,
  historySearching,
  historyResults,
  historyDetail,
  onSelectConversation,
  onRefreshHistorySummary,
  onMarkHistoryHandoff,
  historyActionBusy,
  historyExportFormat,
  onHistoryExportFormatChange,
  historyExportLoading,
  historyExportContent,
}) {
  if (!loaded) {
    return html`<div className="loader">Loading activity and approvals...</div>`;
  }

  return html`
    <div className="tab-shell">
      <div className="activity-grid">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Recent interactions</h2>
              <p>
                Review which catalog agents and tools have been active recently.
              </p>
            </div>
            <input
              className="text-input"
              style=${{ minWidth: "240px" }}
              placeholder="Filter by agent ID"
              value=${interactionFilter}
              onInput=${(event) => onInteractionFilter(event.target.value)}
            />
          </div>
          <div className="table-wrap">
            <table className="table-shell">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Agent</th>
                  <th>Tool</th>
                  <th>Outcome</th>
                  <th>Error</th>
                </tr>
              </thead>
              <tbody>
                ${interactions.length
                  ? interactions.map(
                      (item) => html`
                        <tr key=${`${item.timestamp}-${item.toolName}-${item.agentId}`}>
                          <td>${formatTimestamp(item.timestamp)}</td>
                          <td>${item.agentId || "-"}</td>
                          <td><code>${item.toolName || "-"}</code></td>
                          <td>
                            <${StatusPill} tone=${item.success ? "neutral" : "danger"}>
                              ${item.success ? "Success" : "Failed"}
                            </${StatusPill}>
                          </td>
                          <td>${item.error || "-"}</td>
                        </tr>
                      `
                    )
                  : html`
                      <tr>
                        <td colSpan="5">
                          <div className="empty-state">No interactions match the current filter.</div>
                        </td>
                      </tr>
                    `}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Approvals and scheduler</h2>
              <p>
                Decide pending approvals, review live monitoring jobs, and
                create additional automation tasks without editing server code.
              </p>
            </div>
            <div className="toolbar-row">
              <button
                className="button secondary"
                onClick=${() => onSchedulerAction(scheduler?.running ? "stop" : "start")}
              >
                ${scheduler?.running ? "Pause scheduler" : "Start scheduler"}
              </button>
            </div>
          </div>

          <div className="preview-card">
            <div className="field-label" style=${{ marginBottom: "10px" }}>Pending approvals</div>
            ${pendingApprovals.length
              ? html`
                  <div className="list-stack">
                    ${pendingApprovals.map(
                      (item) => html`
                        <div className="side-note" key=${item.id || item.conversation_id}>
                          <strong>${item.tool_name}</strong>
                          <div className="muted">${item.summary || "No summary provided."}</div>
                          <div className="muted mono" style=${{ marginTop: "6px" }}>
                            ${item.conversation_id}
                          </div>
                          <div className="inline-actions" style=${{ marginTop: "12px" }}>
                            <button
                              className="button primary"
                              disabled=${Boolean(approvalsBusy[item.conversation_id])}
                              onClick=${() => onDecision(item.conversation_id, "approve")}
                            >
                              Approve
                            </button>
                            <button
                              className="button danger"
                              disabled=${Boolean(approvalsBusy[item.conversation_id])}
                              onClick=${() => onDecision(item.conversation_id, "reject")}
                            >
                              Reject
                            </button>
                          </div>
                        </div>
                      `
                    )}
                  </div>
                `
              : html`<div className="empty-state">No approvals are waiting right now.</div>`}
          </div>

          <div className="preview-card">
            <div className="field-label" style=${{ marginBottom: "10px" }}>Scheduler tasks</div>
            ${scheduler?.tasks?.length
              ? html`
                  <div className="table-wrap">
                    <table className="table-shell">
                      <thead>
                        <tr>
                          <th>Task</th>
                          <th>Schedule</th>
                          <th>Status</th>
                          <th>Runs</th>
                          <th>Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        ${scheduler.tasks.map(
                          (task) => html`
                            <tr
                              key=${task.task_id}
                              className="row-clickable"
                              onClick=${() => onSelectTask(task)}
                            >
                              <td>
                                <strong>${task.name}</strong>
                                <div className="muted mono">${task.task_id}</div>
                                <div className="muted">
                                  ${task.managed_by === "system"
                                    ? "Managed by application settings"
                                    : "Custom task"}
                                </div>
                              </td>
                              <td>
                                ${task.schedule_type === "cron_like"
                                  ? `Daily at ${String(task.cron_hour).padStart(2, "0")}:${String(task.cron_minute).padStart(2, "0")}`
                                  : `Every ${task.interval_seconds || 0} sec`}
                              </td>
                              <td>
                                <${StatusPill} tone=${task.enabled ? "neutral" : "warn"}>
                                  ${task.enabled ? "Enabled" : "Disabled"}
                                </${StatusPill}>
                              </td>
                              <td>${task.run_count || 0}</td>
                              <td>
                                <button
                                  className="button ghost"
                                  onClick=${(event) => {
                                    event.stopPropagation();
                                    onToggleTask(task);
                                  }}
                                >
                                  ${task.enabled ? "Pause" : "Enable"}
                                </button>
                              </td>
                            </tr>
                          `
                        )}
                      </tbody>
                    </table>
                  </div>
                `
              : html`<div className="empty-state">No scheduler tasks are registered yet.</div>`}
          </div>

          <div className="preview-card">
            <div className="section-heading">
              <div>
                <h3>Custom task editor</h3>
                <p className="section-copy">
                  Create recurring checks or one-time automation jobs using the
                  scheduler handlers already available in the platform.
                </p>
              </div>
              ${taskForm.isSystem ? html`<${StatusPill} tone="warn">System task</${StatusPill}>` : html`<${StatusPill}>Custom task</${StatusPill}>`}
            </div>
            ${taskForm.isSystem
              ? html`
                  <div className="empty-state">
                    This task is managed by application settings. Change health
                    checks, workflow monitoring, or daily summary behavior in
                    Application Settings.
                  </div>
                `
              : null}
            <div className="field-grid">
              <label className="field">
                <span className="field-label">Task name</span>
                <input className="text-input" value=${taskForm.name} onInput=${(e) => onTaskFormChange("name", e.target.value)} disabled=${taskForm.isSystem} />
                <span className="field-hint">Short operational label for this task.</span>
              </label>
              <label className="field">
                <span className="field-label">Handler</span>
                <select className="select-input" value=${taskForm.handlerName} onChange=${(e) => onTaskFormChange("handlerName", e.target.value)} disabled=${taskForm.isSystem}>
                  ${(scheduler?.handlers || []).map(
                    (handler) => html`<option key=${handler.name} value=${handler.name}>${handler.name}</option>`
                  )}
                </select>
                <span className="field-hint">The underlying scheduler action to run.</span>
              </label>
              <label className="field full">
                <span className="field-label">Description</span>
                <input className="text-input" value=${taskForm.description} onInput=${(e) => onTaskFormChange("description", e.target.value)} disabled=${taskForm.isSystem} />
                <span className="field-hint">Explain what outcome this task supports for the business.</span>
              </label>
              <label className="field">
                <span className="field-label">Schedule type</span>
                <select className="select-input" value=${taskForm.scheduleType} onChange=${(e) => onTaskFormChange("scheduleType", e.target.value)} disabled=${taskForm.isSystem}>
                  <option value="interval">interval</option>
                  <option value="cron_like">cron_like</option>
                  <option value="one_shot">one_shot</option>
                </select>
                <span className="field-hint">Choose recurring interval, daily time, or one-time delay.</span>
              </label>
              ${taskForm.scheduleType === "cron_like"
                ? html`
                    <label className="field">
                      <span className="field-label">Hour</span>
                      <input className="text-input" type="number" value=${taskForm.cronHour} onInput=${(e) => onTaskFormChange("cronHour", e.target.value)} disabled=${taskForm.isSystem} />
                      <span className="field-hint">24-hour clock. Use `-1` for every hour.</span>
                    </label>
                    <label className="field">
                      <span className="field-label">Minute</span>
                      <input className="text-input" type="number" value=${taskForm.cronMinute} onInput=${(e) => onTaskFormChange("cronMinute", e.target.value)} disabled=${taskForm.isSystem} />
                      <span className="field-hint">Minute within the hour to run.</span>
                    </label>
                  `
                : html`
                    <label className="field">
                      <span className="field-label">Interval seconds</span>
                      <input className="text-input" type="number" value=${taskForm.intervalSeconds} onInput=${(e) => onTaskFormChange("intervalSeconds", e.target.value)} disabled=${taskForm.isSystem} />
                      <span className="field-hint">Delay for interval and one-shot schedules.</span>
                    </label>
                  `}
              <div className="field full">
                <div className="switch-row">
                  <div className="switch-copy">
                    <strong>Enable immediately</strong>
                    <div className="field-hint">If turned on, the scheduler can run this task as soon as it is saved.</div>
                  </div>
                  <input className="toggle" type="checkbox" checked=${Boolean(taskForm.enabled)} onChange=${(e) => onTaskFormChange("enabled", e.target.checked)} disabled=${taskForm.isSystem} />
                </div>
              </div>
              <label className="field full">
                <span className="field-label">Handler arguments</span>
                <textarea className="text-area codeish" value=${taskForm.handlerArgsText} onInput=${(e) => onTaskFormChange("handlerArgsText", e.target.value)} disabled=${taskForm.isSystem}></textarea>
                <span className="field-hint">JSON object passed into the handler. Example: {"hours": 2}</span>
              </label>
            </div>
            <div className="section-actions">
              <div className="field-hint">
                ${taskForm.taskId
                  ? "Saving updates this custom task in the persisted scheduler catalog."
                  : "Saving creates a new persisted custom task."}
              </div>
              <div className="action-group">
                <button className="button ghost" onClick=${onResetTask}>
                  New task
                </button>
                <button className="button danger" disabled=${taskForm.isSystem || !taskForm.taskId || taskDeleting} onClick=${onDeleteTask}>
                  ${taskDeleting ? "Deleting..." : "Delete task"}
                </button>
                <button className="button primary" disabled=${taskForm.isSystem || taskSaving} onClick=${onSaveTask}>
                  ${taskSaving ? "Saving..." : taskForm.taskId ? "Save task" : "Create task"}
                </button>
              </div>
            </div>
          </div>
        </section>
      </div>

      <div className="two-column">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Metrics summary</h2>
              <p>High-level performance indicators collected from recent turns.</p>
            </div>
          </div>
          <div className="list-stack">
            <div className="side-note">
              <strong>Turn count</strong>
              <div className="muted">${compactNumber(metrics?.turn_count || 0)}</div>
            </div>
            <div className="side-note">
              <strong>Average latency</strong>
              <div className="muted">
                ${metrics?.avg_latency_ms
                  ? `${Math.round(metrics.avg_latency_ms)} ms`
                  : "No completed turns yet"}
              </div>
            </div>
            <div className="side-note">
              <strong>Tool failure rate</strong>
              <div className="muted">
                ${metrics?.tool_failure_rate !== undefined
                  ? `${Math.round((metrics.tool_failure_rate || 0) * 100)}%`
                  : "No data"}
              </div>
            </div>
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Scheduler execution log</h2>
              <p>Useful for checking whether proactive jobs are actually running.</p>
            </div>
          </div>
          ${schedulerLog.length
            ? html`
                <div className="table-wrap">
                  <table className="table-shell">
                    <thead>
                      <tr>
                        <th>Time</th>
                        <th>Task</th>
                        <th>Message</th>
                      </tr>
                    </thead>
                    <tbody>
                      ${schedulerLog.map(
                        (entry, index) => html`
                          <tr key=${`${entry.timestamp || index}-${index}`}>
                            <td>${formatTimestamp(entry.timestamp)}</td>
                            <td>${entry.task_id || entry.task || "-"}</td>
                            <td>${entry.message || entry.result || "-"}</td>
                          </tr>
                        `
                      )}
                    </tbody>
                  </table>
                </div>
              `
            : html`<div className="empty-state">No scheduler executions have been logged yet.</div>`}
        </section>
      </div>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Conversation history</h2>
            <p>
              Search past support conversations, refresh executive summaries,
              and mark cases for human follow-up.
            </p>
          </div>
          <div className="toolbar-row">
            <input
              className="text-input"
              style=${{ minWidth: "260px" }}
              placeholder="Search by workflow, symptom, user, or conversation ID"
              value=${historyQuery}
              onInput=${(event) => onHistoryQueryChange(event.target.value)}
            />
            <button className="button primary" disabled=${historySearching} onClick=${onRunHistorySearch}>
              ${historySearching ? "Searching..." : "Find conversations"}
            </button>
            <button className="button ghost" disabled=${historySearching} onClick=${onClearHistorySearch}>
              Clear
            </button>
          </div>
        </div>

        <div className="stack-split">
          <div>
            <div className="table-wrap" style=${{ maxHeight: "420px" }}>
              <table className="table-shell">
                <thead>
                  <tr>
                    <th>Conversation</th>
                    <th>Summary</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  ${historyResults.length
                    ? historyResults.map(
                        (item) => html`
                          <tr
                            key=${item.conversation_id}
                            className=${`row-clickable ${historyDetail?.conversation_id === item.conversation_id ? "row-selected" : ""}`}
                            onClick=${() => onSelectConversation(item)}
                          >
                            <td>
                              <strong>${item.conversation_id}</strong>
                              <div className="muted">${item.user_id || "Unknown user"} · ${item.user_role || "technical"}</div>
                              <div className="muted">${formatTimestamp(item.updated_at || item.last_message_at)}</div>
                            </td>
                            <td>
                              <div>${shortenText(item.summary || item.match_excerpt || item.last_message || "No summary yet.", 120)}</div>
                              <div className="muted" style=${{ marginTop: "6px" }}>
                                ${shortenText(item.match_excerpt || item.last_message || "", 110)}
                              </div>
                            </td>
                            <td>
                              <div className="chip-row">
                                <${StatusPill} tone=${item.is_human_handoff ? "warn" : "neutral"}>
                                  ${item.is_human_handoff ? "Needs human follow-up" : "Agent-managed"}
                                </${StatusPill}>
                                <${StatusPill}>${item.phase || "idle"}</${StatusPill}>
                              </div>
                            </td>
                          </tr>
                        `
                      )
                    : html`
                        <tr>
                          <td colSpan="3">
                            <div className="empty-state">
                              No conversations matched the current search.
                            </div>
                          </td>
                        </tr>
                      `}
                </tbody>
              </table>
            </div>
          </div>

          <div>
            ${historyDetail
              ? html`
                  <div className="preview-card">
                    <div className="section-heading">
                      <div>
                        <h3>Selected conversation</h3>
                        <p className="section-copy">
                          ${historyDetail.conversation_id}
                        </p>
                      </div>
                      <div className="chip-row">
                        <${StatusPill} tone=${historyDetail.is_human_handoff ? "warn" : "neutral"}>
                          ${historyDetail.is_human_handoff ? "Human follow-up requested" : "No handoff requested"}
                        </${StatusPill}>
                        <${StatusPill}>${historyDetail.phase || "idle"}</${StatusPill}>
                      </div>
                    </div>

                    <div className="list-stack">
                      <div className="side-note">
                        <strong>Conversation summary</strong>
                        <div className="muted">
                          ${historyDetail.summary || "No summary has been generated yet."}
                        </div>
                      </div>
                      <div className="side-note">
                        <strong>Customer and channel context</strong>
                        <div className="muted">
                          ${historyDetail.user_id || "Unknown user"} · ${historyDetail.user_role || "technical"} audience ·
                          ${" "}Messages: ${compactNumber(historyDetail.message_count || 0)} · Findings: ${compactNumber(historyDetail.finding_count || 0)}
                        </div>
                      </div>
                    </div>

                    <div className="section-actions">
                      <div className="field-hint">
                        Use summary refresh after a long investigation, or mark the case for a human owner when automation should stop here.
                      </div>
                      <div className="action-group">
                        <button className="button secondary" disabled=${historyActionBusy} onClick=${onRefreshHistorySummary}>
                          ${historyActionBusy ? "Working..." : "Refresh summary"}
                        </button>
                        <button className="button warn" disabled=${historyActionBusy || historyDetail.is_human_handoff} onClick=${onMarkHistoryHandoff}>
                          ${historyDetail.is_human_handoff ? "Handoff requested" : "Request human handoff"}
                        </button>
                      </div>
                    </div>
                  </div>

                  <div className="preview-card">
                    <div className="section-heading">
                      <div>
                        <h3>Recent message timeline</h3>
                        <p className="section-copy">
                          Last ${Math.min((historyDetail.messages || []).length, 10)} message(s) from the selected conversation.
                        </p>
                      </div>
                    </div>
                    ${(historyDetail.messages || []).length
                      ? html`
                          <div className="list-stack">
                            ${(historyDetail.messages || []).slice(-10).map(
                              (message, index) => html`
                                <div className="side-note" key=${`${message.timestamp || index}-${index}`}>
                                  <strong>${String(message.role || "unknown").toUpperCase()}</strong>
                                  <div className="muted" style=${{ marginTop: "4px" }}>
                                    ${formatTimestamp(message.timestamp)}
                                  </div>
                                  <div style=${{ marginTop: "10px" }}>
                                    ${message.content || ""}
                                  </div>
                                </div>
                              `
                            )}
                          </div>
                        `
                      : html`<div className="empty-state">No messages are stored for this conversation yet.</div>`}
                  </div>

                  <div className="preview-card">
                    <div className="section-heading">
                      <div>
                        <h3>Export preview</h3>
                        <p className="section-copy">
                          Review the generated handoff or archive output before copying it into a ticket or report.
                        </p>
                      </div>
                      <div className="toolbar-row">
                        <select className="select-input" value=${historyExportFormat} onChange=${(event) => onHistoryExportFormatChange(event.target.value)}>
                          <option value="markdown">markdown</option>
                          <option value="json">json</option>
                        </select>
                        <${StatusPill} tone=${historyExportLoading ? "warn" : "neutral"}>
                          ${historyExportLoading ? "Refreshing export" : "Live preview"}
                        </${StatusPill}>
                      </div>
                    </div>
                    <textarea className="text-area codeish" readOnly value=${historyExportContent || ""}></textarea>
                  </div>
                `
              : html`
                  <div className="empty-state">
                    Select a conversation from the left to review its summary,
                    messages, and export preview.
                  </div>
                `}
          </div>
        </div>
      </section>
    </div>
  `;
}

function App() {
  const [token, setToken] = useState(() => window.localStorage.getItem("ae-admin-token") || "");
  const [activeTab, setActiveTab] = useState("overview");
  const [bootstrap, setBootstrap] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [flash, setFlash] = useState(null);
  const [savingSections, setSavingSections] = useState({});

  const [overview, setOverview] = useState({
    health: null,
    scheduler: null,
    pendingApprovals: [],
    interactions: [],
    multiAgents: null,
    metrics: null,
  });

  const [agentsLoaded, setAgentsLoaded] = useState(false);
  const [agents, setAgents] = useState([]);
  const [specialists, setSpecialists] = useState([]);
  const [agentForm, setAgentForm] = useState(emptyAgentForm());
  const [agentSaving, setAgentSaving] = useState(false);

  const [toolsLoaded, setToolsLoaded] = useState(false);
  const [tools, setTools] = useState([]);
  const [toolQuery, setToolQuery] = useState("");
  const deferredToolQuery = useDeferredValue(toolQuery);
  const [toolSyncing, setToolSyncing] = useState(false);
  const [toolForm, setToolForm] = useState(emptyToolForm());
  const [toolSaving, setToolSaving] = useState(false);

  const [knowledgeLoaded, setKnowledgeLoaded] = useState(false);
  const [sops, setSops] = useState([]);
  const [knowledgeQuery, setKnowledgeQuery] = useState("");
  const [knowledgeSearching, setKnowledgeSearching] = useState(false);
  const [knowledgeHits, setKnowledgeHits] = useState([]);
  const [suggestedSteps, setSuggestedSteps] = useState([]);
  const [sopForm, setSopForm] = useState(emptySopForm());
  const [sopSaving, setSopSaving] = useState(false);
  const [referenceDocs, setReferenceDocs] = useState([]);
  const [docForm, setDocForm] = useState(emptyReferenceDocForm());
  const [docSaving, setDocSaving] = useState(false);
  const [docDeleting, setDocDeleting] = useState(false);

  const [activityLoaded, setActivityLoaded] = useState(false);
  const [interactions, setInteractions] = useState([]);
  const [pendingApprovals, setPendingApprovals] = useState([]);
  const [schedulerState, setSchedulerState] = useState(null);
  const [schedulerLog, setSchedulerLog] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [interactionFilter, setInteractionFilter] = useState("");
  const deferredInteractionFilter = useDeferredValue(interactionFilter);
  const [approvalsBusy, setApprovalsBusy] = useState({});
  const [taskForm, setTaskForm] = useState(emptySchedulerTaskForm());
  const [taskSaving, setTaskSaving] = useState(false);
  const [taskDeleting, setTaskDeleting] = useState(false);
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyResults, setHistoryResults] = useState([]);
  const [historyDetail, setHistoryDetail] = useState(null);
  const [historySearching, setHistorySearching] = useState(false);
  const [historyActionBusy, setHistoryActionBusy] = useState(false);
  const [historyExportFormat, setHistoryExportFormat] = useState("markdown");
  const [historyExportContent, setHistoryExportContent] = useState("");
  const [historyExportLoading, setHistoryExportLoading] = useState(false);

  useEffect(() => {
    window.localStorage.setItem("ae-admin-token", token);
  }, [token]);

  useEffect(() => {
    if (!flash) {
      return undefined;
    }
    const timer = window.setTimeout(() => setFlash(null), 5000);
    return () => window.clearTimeout(timer);
  }, [flash]);

  function syncToolSelection(nextTools) {
    setToolForm((current) => {
      if (!Array.isArray(nextTools) || !nextTools.length) {
        return emptyToolForm();
      }
      const match = nextTools.find((tool) => tool.toolName === current.toolName);
      return toolToForm(match || nextTools[0]);
    });
  }

  function syncTaskSelection(nextScheduler) {
    const tasks = nextScheduler?.tasks || [];
    setTaskForm((current) => {
      if (!tasks.length) {
        return emptySchedulerTaskForm();
      }
      const match = tasks.find((task) => task.task_id === current.taskId);
      return match ? schedulerTaskToForm(match) : current.taskId ? emptySchedulerTaskForm() : schedulerTaskToForm(tasks[0]);
    });
  }

  function syncReferenceDocSelection(nextDocs) {
    setDocForm((current) => {
      if (!Array.isArray(nextDocs) || !nextDocs.length) {
        return emptyReferenceDocForm();
      }
      const match = nextDocs.find((item) => item.id === current.id);
      return match ? referenceDocToForm(match) : current.id ? emptyReferenceDocForm() : referenceDocToForm(nextDocs[0]);
    });
  }

  async function loadBootstrap(currentToken = token) {
    try {
      const payload = await requestJson("/api/admin/bootstrap", { token: currentToken });
      setBootstrap(payload);
      setDrafts(buildDraftSections(payload.schema, payload.config));
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Failed to load control center." });
    }
  }

  async function loadOverview(currentToken = token) {
    const [
      healthRes,
      schedulerRes,
      approvalsRes,
      interactionsRes,
      multiAgentsRes,
      metricsRes,
    ] = await Promise.allSettled([
      requestJson("/health"),
      requestJson("/api/scheduler/status", { token: currentToken }),
      requestJson("/api/approvals/pending", { token: currentToken }),
      requestJson("/api/interactions?limit=8", { token: currentToken }),
      requestJson("/api/multi-agents", { token: currentToken }),
      requestJson("/api/metrics", { token: currentToken }),
    ]);

    setOverview({
      health: healthRes.status === "fulfilled" ? healthRes.value : null,
      scheduler: schedulerRes.status === "fulfilled" ? schedulerRes.value : null,
      pendingApprovals: approvalsRes.status === "fulfilled" ? approvalsRes.value.pending || [] : [],
      interactions: interactionsRes.status === "fulfilled" ? interactionsRes.value.interactions || [] : [],
      multiAgents: multiAgentsRes.status === "fulfilled" ? multiAgentsRes.value : null,
      metrics: metricsRes.status === "fulfilled" ? metricsRes.value : null,
    });
  }

  async function loadAgentsData(currentToken = token) {
    try {
      const [agentsRes, toolsRes, specialistsRes] = await Promise.all([
        requestJson("/api/agents", { token: currentToken }),
        requestJson("/api/tools", { token: currentToken }),
        requestJson("/api/multi-agents", { token: currentToken }),
      ]);
      setAgents(agentsRes.agents || []);
      setTools(toolsRes.tools || []);
      syncToolSelection(toolsRes.tools || []);
      setToolsLoaded(true);
      setSpecialists(specialistsRes.agents || []);
      setAgentsLoaded(true);
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Failed to load agent data." });
    }
  }

  async function loadToolsData(currentToken = token) {
    try {
      const toolsRes = await requestJson("/api/tools", { token: currentToken });
      setTools(toolsRes.tools || []);
      syncToolSelection(toolsRes.tools || []);
      setToolsLoaded(true);
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Failed to load tools." });
    }
  }

  async function loadKnowledgeData(currentToken = token) {
    try {
      const [sopRes, docsRes] = await Promise.all([
        requestJson("/api/sops?limit=300", { token: currentToken }),
        requestJson("/api/docs/catalog", { token: currentToken }),
      ]);
      setSops(sopRes.sops || []);
      setReferenceDocs(docsRes.documents || []);
      syncReferenceDocSelection(docsRes.documents || []);
      setKnowledgeLoaded(true);
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Failed to load knowledge resources." });
    }
  }

  async function loadConversationExport(
    conversationId,
    format = historyExportFormat,
    currentToken = token
  ) {
    if (!conversationId) {
      setHistoryExportContent("");
      return "";
    }
    setHistoryExportLoading(true);
    try {
      const payload = await requestJson(
        `/api/history/export/${encodeURIComponent(conversationId)}?format=${encodeURIComponent(format)}`,
        { token: currentToken }
      );
      const content = payload.content || "";
      setHistoryExportContent(content);
      return content;
    } finally {
      setHistoryExportLoading(false);
    }
  }

  async function loadConversationDetail(
    conversationId,
    currentToken = token,
    format = historyExportFormat
  ) {
    if (!conversationId) {
      setHistoryDetail(null);
      setHistoryExportContent("");
      return null;
    }
    const payload = await requestJson(
      `/api/history/conversations/${encodeURIComponent(conversationId)}`,
      { token: currentToken }
    );
    setHistoryDetail(payload.conversation || null);
    await loadConversationExport(conversationId, format, currentToken);
    return payload.conversation || null;
  }

  async function loadHistoryData(
    currentToken = token,
    searchQuery = historyQuery,
    preferredConversationId = historyDetail?.conversation_id || ""
  ) {
    setHistorySearching(true);
    try {
      const params = new URLSearchParams({ limit: "25" });
      if (String(searchQuery || "").trim()) {
        params.set("q", String(searchQuery).trim());
      }
      const payload = await requestJson(`/api/history/conversations?${params.toString()}`, {
        token: currentToken,
      });
      const results = payload.results || [];
      setHistoryResults(results);

      const nextConversationId =
        preferredConversationId && results.some((item) => item.conversation_id === preferredConversationId)
          ? preferredConversationId
          : results[0]?.conversation_id || "";

      if (nextConversationId) {
        await loadConversationDetail(nextConversationId, currentToken, historyExportFormat);
      } else {
        setHistoryDetail(null);
        setHistoryExportContent("");
      }
      return results;
    } finally {
      setHistorySearching(false);
    }
  }

  async function loadActivityData(currentToken = token) {
    try {
      const [interactionsRes, pendingRes, schedulerRes, schedulerLogRes, metricsRes] =
        await Promise.all([
          requestJson("/api/interactions?limit=200", { token: currentToken }),
          requestJson("/api/approvals/pending", { token: currentToken }),
          requestJson("/api/scheduler/status", { token: currentToken }),
          requestJson("/api/scheduler/logs?limit=20", { token: currentToken }),
          requestJson("/api/metrics", { token: currentToken }),
        ]);
      setInteractions(interactionsRes.interactions || []);
      setPendingApprovals(pendingRes.pending || []);
      setSchedulerState(schedulerRes);
      syncTaskSelection(schedulerRes);
      setSchedulerLog(schedulerLogRes.log || []);
      setMetrics(metricsRes);
      await loadHistoryData(currentToken, historyQuery, historyDetail?.conversation_id || "");
      setActivityLoaded(true);
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Failed to load activity data." });
    }
  }

  useEffect(() => {
    loadBootstrap(token);
    loadOverview(token).catch((error) => {
      setFlash({ tone: "error", message: error.message || "Failed to load overview." });
    });
  }, [token]);

  useEffect(() => {
    if (activeTab === "agents" && !agentsLoaded) {
      loadAgentsData(token);
    }
    if (activeTab === "tools" && !toolsLoaded) {
      loadToolsData(token);
    }
    if (activeTab === "knowledge" && !knowledgeLoaded) {
      loadKnowledgeData(token);
    }
    if (activeTab === "activity" && !activityLoaded) {
      loadActivityData(token);
    }
  }, [activeTab, token]);

  useEffect(() => {
    if (activeTab !== "activity" || !historyDetail?.conversation_id) {
      return;
    }
    loadConversationExport(historyDetail.conversation_id, historyExportFormat, token).catch((error) => {
      setFlash({ tone: "error", message: error.message || "Unable to refresh the export preview." });
    });
  }, [historyExportFormat]);

  function setSectionField(sectionId, fieldKey, nextValue) {
    setDrafts((current) => ({
      ...current,
      [sectionId]: {
        ...(current[sectionId] || {}),
        [fieldKey]: nextValue,
      },
    }));
  }

  async function saveSection(sectionId) {
    setSavingSections((current) => ({ ...current, [sectionId]: true }));
    try {
      const payload = await requestJson(`/api/admin/config/${sectionId}`, {
        token,
        method: "PUT",
        body: drafts[sectionId] || {},
      });
      const nextConfig = {
        ...(bootstrap?.config || {}),
        [sectionId]: payload.config,
      };
      setBootstrap((current) => (current ? { ...current, config: nextConfig } : current));
      setDrafts((current) => ({
        ...current,
        [sectionId]: buildDraftSections(bootstrap?.schema || [], nextConfig)[sectionId],
      }));
      setFlash({ tone: "success", message: `${sectionId.replaceAll("_", " ")} saved.` });
      if (sectionId === "workspace") {
        await loadBootstrap(token);
      }
      if (sectionId === "monitoring") {
        await Promise.all([loadOverview(token), loadActivityData(token)]);
      }
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to save settings." });
    } finally {
      setSavingSections((current) => ({ ...current, [sectionId]: false }));
    }
  }

  async function resetSection(sectionId) {
    setSavingSections((current) => ({ ...current, [sectionId]: true }));
    try {
      const payload = await requestJson(`/api/admin/config/${sectionId}/reset`, {
        token,
        method: "POST",
      });
      const nextConfig = {
        ...(bootstrap?.config || {}),
        [sectionId]: payload.config,
      };
      setBootstrap((current) => (current ? { ...current, config: nextConfig } : current));
      setDrafts((current) => ({
        ...current,
        [sectionId]: buildDraftSections(bootstrap?.schema || [], nextConfig)[sectionId],
      }));
      setFlash({ tone: "success", message: `${sectionId.replaceAll("_", " ")} reset.` });
      if (sectionId === "workspace") {
        await loadBootstrap(token);
      }
      if (sectionId === "monitoring") {
        await Promise.all([loadOverview(token), loadActivityData(token)]);
      }
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to reset settings." });
    } finally {
      setSavingSections((current) => ({ ...current, [sectionId]: false }));
    }
  }

  function updateAgentForm(field, value) {
    setAgentForm((current) => ({ ...current, [field]: value }));
  }

  async function saveAgent() {
    if (!agentForm.name.trim() || !agentForm.usecase.trim()) {
      setFlash({ tone: "error", message: "Agent name and use case are required." });
      return;
    }
    setAgentSaving(true);
    try {
      const payload = {
        agentId: agentForm.agentId.trim(),
        name: agentForm.name.trim(),
        usecase: agentForm.usecase.trim(),
        status: agentForm.status,
        persona: agentForm.persona,
        capabilities: splitCommaList(agentForm.capabilitiesText),
        domains: splitCommaList(agentForm.domainsText),
        priority: Number(agentForm.priority) || 50,
        version: agentForm.version.trim() || "1.0.0",
        description: agentForm.description.trim(),
        tags: splitCommaList(agentForm.tagsText),
        linkedTools: agentForm.linkedTools,
      };
      const isUpdate = Boolean(payload.agentId);
      await requestJson(
        isUpdate ? `/api/agents/${encodeURIComponent(payload.agentId)}` : "/api/agents",
        {
          token,
          method: isUpdate ? "PUT" : "POST",
          body: payload,
        }
      );
      await loadAgentsData(token);
      await loadOverview(token);
      setAgentForm(emptyAgentForm());
      setFlash({ tone: "success", message: "Agent saved." });
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to save agent." });
    } finally {
      setAgentSaving(false);
    }
  }

  async function deleteAgent() {
    if (!agentForm.agentId) {
      return;
    }
    if (!window.confirm(`Delete agent "${agentForm.agentId}"?`)) {
      return;
    }
    try {
      await requestJson(`/api/agents/${encodeURIComponent(agentForm.agentId)}`, {
        token,
        method: "DELETE",
      });
      await loadAgentsData(token);
      await loadOverview(token);
      setAgentForm(emptyAgentForm());
      setFlash({ tone: "success", message: "Agent deleted." });
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to delete agent." });
    }
  }

  function updateToolForm(field, value) {
    setToolForm((current) => ({ ...current, [field]: value }));
  }

  async function syncTools() {
    setToolSyncing(true);
    try {
      await requestJson("/api/tools/sync", {
        token,
        method: "POST",
        body: { includeInactive: false },
      });
      await Promise.all([
        loadToolsData(token),
        agentsLoaded ? loadAgentsData(token) : Promise.resolve(),
      ]);
      setFlash({ tone: "success", message: "Tool inventory refreshed from AutomationEdge." });
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to sync tools." });
    } finally {
      setToolSyncing(false);
    }
  }

  async function saveToolConfig() {
    if (!toolForm.toolName) {
      setFlash({ tone: "error", message: "Select a tool before saving settings." });
      return;
    }
    setToolSaving(true);
    try {
      const payload = await requestJson(`/api/tools/${encodeURIComponent(toolForm.toolName)}/config`, {
        token,
        method: "PUT",
        body: {
          title: toolForm.toolTitle.trim(),
          description: toolForm.description.trim(),
          category: toolForm.category.trim(),
          tier: toolForm.tier,
          safety: toolForm.safety.trim(),
          tags: splitCommaList(toolForm.tagsText),
          useWhen: toolForm.useWhen.trim(),
          avoidWhen: toolForm.avoidWhen.trim(),
          alwaysAvailable: Boolean(toolForm.alwaysAvailable),
          active: Boolean(toolForm.active),
          allowedAgents: splitCommaList(toolForm.allowedAgentsText),
        },
      });
      const updatedTool = payload.tool;
      setTools((current) =>
        current.map((tool) => (tool.toolName === updatedTool.toolName ? updatedTool : tool))
      );
      setToolForm(toolToForm(updatedTool));
      setFlash({ tone: "success", message: "Tool settings saved." });
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to save tool settings." });
    } finally {
      setToolSaving(false);
    }
  }

  async function resetToolConfig() {
    if (!toolForm.toolName) {
      return;
    }
    setToolSaving(true);
    try {
      const payload = await requestJson(`/api/tools/${encodeURIComponent(toolForm.toolName)}/config/reset`, {
        token,
        method: "POST",
      });
      const updatedTool = payload.tool;
      setTools((current) =>
        current.map((tool) => (tool.toolName === updatedTool.toolName ? updatedTool : tool))
      );
      setToolForm(toolToForm(updatedTool));
      setFlash({ tone: "success", message: "Tool override reset." });
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to reset the tool override." });
    } finally {
      setToolSaving(false);
    }
  }

  async function runKnowledgeSearch() {
    if (!knowledgeQuery.trim()) {
      setFlash({ tone: "error", message: "Enter a short issue description before searching SOPs." });
      return;
    }
    setKnowledgeSearching(true);
    try {
      const payload = await requestJson("/api/tools/search_knowledge_base/test", {
        token,
        method: "POST",
        body: {
          args: { query: knowledgeQuery.trim(), collection: "sops", top_k: 5 },
        },
      });
      const hits = payload.data?.results || [];
      setKnowledgeHits(hits);
      const steps = [];
      hits.forEach((hit) => {
        extractSuggestedSteps(hit.content || "").forEach((step) => {
          if (!steps.includes(step)) {
            steps.push(step);
          }
        });
      });
      setSuggestedSteps(steps.slice(0, 5));
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to search SOPs." });
    } finally {
      setKnowledgeSearching(false);
    }
  }

  function updateSopForm(field, value) {
    setSopForm((current) => ({ ...current, [field]: value }));
  }

  async function selectSop(item) {
    try {
      const payload = await requestJson(`/api/sops/${encodeURIComponent(item.id)}`, {
        token,
      });
      setSopForm(sopToForm(payload));
    } catch (error) {
      setSopForm(sopToForm(item));
      setFlash({ tone: "error", message: error.message || "Unable to load this SOP." });
    }
  }

  async function saveSop() {
    if (!sopForm.content.trim()) {
      setFlash({ tone: "error", message: "SOP content is required." });
      return;
    }
    setSopSaving(true);
    try {
      await requestJson("/api/sops", {
        token,
        method: "POST",
        body: {
          id: sopForm.id.trim(),
          title: sopForm.title.trim(),
          reference_id: sopForm.reference_id.trim(),
          tags: splitCommaList(sopForm.tagsText),
          content: sopForm.content.trim(),
        },
      });
      await loadKnowledgeData(token);
      setSopForm(emptySopForm());
      setFlash({ tone: "success", message: "SOP saved." });
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to save SOP." });
    } finally {
      setSopSaving(false);
    }
  }

  function updateDocForm(field, value) {
    setDocForm((current) => ({ ...current, [field]: value }));
  }

  async function saveReferenceDoc() {
    if (!docForm.title.trim()) {
      setFlash({ tone: "error", message: "Document title is required." });
      return;
    }
    if (!docForm.path.trim()) {
      setFlash({ tone: "error", message: "Markdown file path is required." });
      return;
    }
    setDocSaving(true);
    try {
      const payload = await requestJson("/api/docs/catalog", {
        token,
        method: "POST",
        body: {
          id: docForm.id.trim(),
          title: docForm.title.trim(),
          badge: docForm.badge.trim(),
          summary: docForm.summary.trim(),
          audience: docForm.audience.trim(),
          path: docForm.path.trim(),
          displayOrder: Number(docForm.displayOrder) || 100,
          active: Boolean(docForm.active),
        },
      });
      await loadKnowledgeData(token);
      setDocForm(referenceDocToForm(payload.document));
      setFlash({ tone: "success", message: "Reference document saved." });
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to save the reference document." });
    } finally {
      setDocSaving(false);
    }
  }

  async function deleteReferenceDoc() {
    if (!docForm.id) {
      return;
    }
    if (!window.confirm(`Delete document "${docForm.title || docForm.id}"?`)) {
      return;
    }
    setDocDeleting(true);
    try {
      await requestJson(`/api/docs/catalog/${encodeURIComponent(docForm.id)}`, {
        token,
        method: "DELETE",
      });
      setDocForm(emptyReferenceDocForm());
      await loadKnowledgeData(token);
      setFlash({ tone: "success", message: "Reference document removed." });
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to delete the reference document." });
    } finally {
      setDocDeleting(false);
    }
  }

  async function runHistorySearch() {
    try {
      await loadHistoryData(token, historyQuery, historyDetail?.conversation_id || "");
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to search conversation history." });
    }
  }

  async function clearHistorySearch() {
    setHistoryQuery("");
    try {
      await loadHistoryData(token, "", historyDetail?.conversation_id || "");
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to reload recent conversations." });
    }
  }

  async function selectHistoryConversation(item) {
    try {
      await loadConversationDetail(item.conversation_id, token, historyExportFormat);
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to load this conversation." });
    }
  }

  async function refreshHistorySummary() {
    if (!historyDetail?.conversation_id) {
      return;
    }
    setHistoryActionBusy(true);
    try {
      await requestJson(`/api/history/summary/${encodeURIComponent(historyDetail.conversation_id)}`, {
        token,
        method: "POST",
        body: {},
      });
      await loadHistoryData(token, historyQuery, historyDetail.conversation_id);
      setFlash({ tone: "success", message: "Conversation summary refreshed." });
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to refresh the conversation summary." });
    } finally {
      setHistoryActionBusy(false);
    }
  }

  async function markHistoryHandoff() {
    if (!historyDetail?.conversation_id) {
      return;
    }
    setHistoryActionBusy(true);
    try {
      await requestJson(`/api/history/handoff/${encodeURIComponent(historyDetail.conversation_id)}`, {
        token,
        method: "POST",
        body: {},
      });
      await loadHistoryData(token, historyQuery, historyDetail.conversation_id);
      setFlash({ tone: "success", message: "Conversation marked for human handoff." });
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to mark the conversation for handoff." });
    } finally {
      setHistoryActionBusy(false);
    }
  }

  async function handleApprovalDecision(conversationId, decision) {
    setApprovalsBusy((current) => ({ ...current, [conversationId]: true }));
    try {
      await requestJson("/api/approvals/decision", {
        token,
        method: "POST",
        body: {
          conversation_id: conversationId,
          decision,
          approver_id: "admin-control-center",
        },
      });
      await Promise.all([loadOverview(token), loadActivityData(token)]);
      setFlash({ tone: "success", message: `Approval decision recorded: ${decision}.` });
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to record approval decision." });
    } finally {
      setApprovalsBusy((current) => ({ ...current, [conversationId]: false }));
    }
  }

  async function handleSchedulerAction(action) {
    try {
      await requestJson(`/api/scheduler/${action}`, {
        token,
        method: "POST",
        body: {},
      });
      await Promise.all([
        loadOverview(token),
        activityLoaded ? loadActivityData(token) : Promise.resolve(),
      ]);
      setFlash({
        tone: "success",
        message: action === "start" ? "Scheduler started." : "Scheduler stopped.",
      });
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to change scheduler state." });
    }
  }

  function updateTaskForm(field, value) {
    setTaskForm((current) => ({ ...current, [field]: value }));
  }

  async function saveSchedulerTask() {
    if (!taskForm.name.trim()) {
      setFlash({ tone: "error", message: "Task name is required." });
      return;
    }
    if (!taskForm.handlerName.trim()) {
      setFlash({ tone: "error", message: "Choose a scheduler handler before saving." });
      return;
    }
    setTaskSaving(true);
    try {
      const handlerArgs = parseJsonObject(taskForm.handlerArgsText);
      const body = {
        name: taskForm.name.trim(),
        description: taskForm.description.trim(),
        schedule_type: taskForm.scheduleType,
        interval_seconds: Number(taskForm.intervalSeconds) || 300,
        cron_hour: Number(taskForm.cronHour),
        cron_minute: Number(taskForm.cronMinute),
        handler_name: taskForm.handlerName,
        handler_args: handlerArgs,
        enabled: Boolean(taskForm.enabled),
      };
      if (taskForm.taskId) {
        await requestJson(`/api/scheduler/tasks/${encodeURIComponent(taskForm.taskId)}`, {
          token,
          method: "PUT",
          body,
        });
      } else {
        await requestJson("/api/scheduler/tasks", {
          token,
          method: "POST",
          body,
        });
      }
      await Promise.all([loadOverview(token), loadActivityData(token)]);
      setFlash({ tone: "success", message: taskForm.taskId ? "Task updated." : "Task created." });
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to save the scheduler task." });
    } finally {
      setTaskSaving(false);
    }
  }

  async function deleteSchedulerTask() {
    if (!taskForm.taskId) {
      return;
    }
    if (!window.confirm(`Delete task "${taskForm.name || taskForm.taskId}"?`)) {
      return;
    }
    setTaskDeleting(true);
    try {
      await requestJson(`/api/scheduler/tasks/${encodeURIComponent(taskForm.taskId)}`, {
        token,
        method: "DELETE",
      });
      setTaskForm(emptySchedulerTaskForm());
      await Promise.all([loadOverview(token), loadActivityData(token)]);
      setFlash({ tone: "success", message: "Task deleted." });
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to delete the task." });
    } finally {
      setTaskDeleting(false);
    }
  }

  async function toggleSchedulerTask(task) {
    const action = task.enabled ? "disable" : "enable";
    try {
      await requestJson(`/api/scheduler/tasks/${encodeURIComponent(task.task_id)}/${action}`, {
        token,
        method: "POST",
        body: {},
      });
      await Promise.all([loadOverview(token), loadActivityData(token)]);
      setFlash({
        tone: "success",
        message: task.enabled ? "Task paused." : "Task enabled.",
      });
    } catch (error) {
      setFlash({ tone: "error", message: error.message || "Unable to change the task state." });
    }
  }

  const filteredTools = tools.filter((tool) => {
    const query = deferredToolQuery.trim().toLowerCase();
    if (!query) {
      return true;
    }
    const haystack = [
      tool.toolName,
      tool.workflowName,
      tool.category,
      Array.isArray(tool.tags) ? tool.tags.join(" ") : "",
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });

  const filteredInteractions = interactions.filter((item) => {
    if (!deferredInteractionFilter.trim()) {
      return true;
    }
    return String(item.agentId || "")
      .toLowerCase()
      .includes(deferredInteractionFilter.trim().toLowerCase());
  });

  const shellTitle =
    drafts.workspace?.adminConsoleTitle ||
    bootstrap?.config?.workspace?.adminConsoleTitle ||
    "Operations Control Center";

  const shellSubtitle =
    drafts.workspace?.adminConsoleSubtitle ||
    bootstrap?.config?.workspace?.adminConsoleSubtitle ||
    "Configure the support assistant without editing code.";

  function refreshOverviewSnapshot() {
    return loadOverview(token).catch((error) => {
      setFlash({ tone: "error", message: error.message || "Failed to refresh overview." });
    });
  }

  useEffect(() => {
    document.title = shellTitle;
  }, [shellTitle]);

  let currentTab = html`<div className="loader">Loading workspace...</div>`;
  if (bootstrap) {
    if (activeTab === "overview") {
      currentTab = html`
        <${OverviewTab}
          overview=${overview}
          onRefresh=${refreshOverviewSnapshot}
          onSchedulerAction=${handleSchedulerAction}
          links=${bootstrap.links}
        />
      `;
    }
    if (activeTab === "settings") {
      currentTab = html`
        <${SettingsTab}
          schema=${bootstrap.schema}
          drafts=${drafts}
          onFieldChange=${setSectionField}
          onSave=${saveSection}
          onReset=${resetSection}
          savingSections=${savingSections}
        />
      `;
    }
    if (activeTab === "agents") {
      currentTab = html`
        <${AgentsTab}
          loaded=${agentsLoaded}
          agents=${agents}
          specialists=${specialists}
          toolOptions=${tools.map((tool) => tool.toolName).sort()}
          form=${agentForm}
          onSelectAgent=${(agent) => setAgentForm(agentToForm(agent))}
          onChangeForm=${updateAgentForm}
          onSaveAgent=${saveAgent}
          onDeleteAgent=${deleteAgent}
          onResetForm=${() => setAgentForm(emptyAgentForm())}
          saving=${agentSaving}
        />
      `;
    }
    if (activeTab === "tools") {
      currentTab = html`
        <${ToolsTab}
          loaded=${toolsLoaded}
          tools=${filteredTools}
          query=${toolQuery}
          onQueryChange=${setToolQuery}
          onSync=${syncTools}
          syncing=${toolSyncing}
          form=${toolForm}
          onSelectTool=${(tool) => setToolForm(toolToForm(tool))}
          onChangeForm=${updateToolForm}
          onSaveTool=${saveToolConfig}
          onResetTool=${resetToolConfig}
          saving=${toolSaving}
        />
      `;
    }
    if (activeTab === "knowledge") {
      currentTab = html`
        <${KnowledgeTab}
          loaded=${knowledgeLoaded}
          query=${knowledgeQuery}
          onQueryChange=${setKnowledgeQuery}
          onRunSearch=${runKnowledgeSearch}
          searching=${knowledgeSearching}
          searchHits=${knowledgeHits}
          suggestedSteps=${suggestedSteps}
          sops=${sops}
          form=${sopForm}
          onSelectSop=${selectSop}
          onChangeForm=${updateSopForm}
          onSaveSop=${saveSop}
          saving=${sopSaving}
          referenceDocs=${referenceDocs}
          docForm=${docForm}
          onSelectDoc=${(item) => setDocForm(referenceDocToForm(item))}
          onChangeDocForm=${updateDocForm}
          onSaveDoc=${saveReferenceDoc}
          onDeleteDoc=${deleteReferenceDoc}
          onResetDocForm=${() => setDocForm(emptyReferenceDocForm())}
          docSaving=${docSaving}
          docDeleting=${docDeleting}
          documentationLink=${bootstrap?.links?.documentation}
        />
      `;
    }
    if (activeTab === "activity") {
      currentTab = html`
        <${ActivityTab}
          loaded=${activityLoaded}
          interactions=${filteredInteractions}
          interactionFilter=${interactionFilter}
          onInteractionFilter=${setInteractionFilter}
          pendingApprovals=${pendingApprovals}
          onDecision=${handleApprovalDecision}
          approvalsBusy=${approvalsBusy}
          scheduler=${schedulerState}
          schedulerLog=${schedulerLog}
          metrics=${metrics}
          onSchedulerAction=${handleSchedulerAction}
          taskForm=${taskForm}
          onSelectTask=${(task) => setTaskForm(schedulerTaskToForm(task))}
          onTaskFormChange=${updateTaskForm}
          onSaveTask=${saveSchedulerTask}
          onDeleteTask=${deleteSchedulerTask}
          onResetTask=${() => setTaskForm(emptySchedulerTaskForm())}
          taskSaving=${taskSaving}
          taskDeleting=${taskDeleting}
          onToggleTask=${toggleSchedulerTask}
          historyQuery=${historyQuery}
          onHistoryQueryChange=${setHistoryQuery}
          onRunHistorySearch=${runHistorySearch}
          onClearHistorySearch=${clearHistorySearch}
          historySearching=${historySearching}
          historyResults=${historyResults}
          historyDetail=${historyDetail}
          onSelectConversation=${selectHistoryConversation}
          onRefreshHistorySummary=${refreshHistorySummary}
          onMarkHistoryHandoff=${markHistoryHandoff}
          historyActionBusy=${historyActionBusy}
          historyExportFormat=${historyExportFormat}
          onHistoryExportFormatChange=${setHistoryExportFormat}
          historyExportLoading=${historyExportLoading}
          historyExportContent=${historyExportContent}
        />
      `;
    }
  }

  return html`
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-copy">
          <div className="eyebrow">Admin workspace</div>
          <h1>${shellTitle}</h1>
          <p>${shellSubtitle}</p>
        </div>
        <div className="topbar-actions">
          <div className="token-stack">
            <label for="adminToken">Admin token</label>
            <input
              id="adminToken"
              className="text-input mono"
              type="password"
              placeholder="Enter the admin token if your server requires one"
              value=${token}
              onInput=${(event) => setToken(event.target.value)}
            />
          </div>
          <button
            className="button ghost"
            onClick=${() => {
              loadBootstrap(token);
              refreshOverviewSnapshot();
            }}
          >
            Reload
          </button>
        </div>
      </header>

      <div className="layout">
        <aside className="sidebar">
          <div className="nav-title">Workspace</div>
          ${TABS.map(
            (tab) => html`
              <button
                key=${tab.id}
                className=${`nav-btn ${activeTab === tab.id ? "active" : ""}`}
                onClick=${() => startTransition(() => setActiveTab(tab.id))}
              >
                <strong>${tab.title}</strong>
                <span>${tab.blurb}</span>
              </button>
            `
          )}
        </aside>

        <main className="content">
          ${flash
            ? html`
                <div className=${`banner ${flash.tone === "success" ? "success" : "error"}`}>
                  ${flash.message}
                </div>
              `
            : null}
          ${currentTab}
        </main>
      </div>
    </div>
  `;
}

createRoot(document.getElementById("root")).render(html`<${App} />`);
