"""
Mock AutomationEdge API for local testing.
Runs a Flask server that simulates AE REST endpoints.
"""

import random
from datetime import datetime, timedelta
from flask import Flask, jsonify, request

mock_app = Flask(__name__)

WORKFLOWS = [
    {
        "id": "wf-claims",
        "name": "Claims_Processing_Daily",
        "status": "active",
        "last_run": "success",
        "schedule": "daily 06:00",
        "agenticAiToolConfiguration": {
            "toolName": "run_claims_processing",
            "toolDescription": "Run claims processing workflow for a batch date",
            "status": "active",
            "category": "claims",
            "tags": ["claims", "batch"],
            "tier": "medium_risk",
        },
        "configurationParameters": [
            {
                "name": "batch_date",
                "type": "String",
                "required": True,
                "description": "Batch date in YYYYMMDD format",
            },
            {
                "name": "validate_only",
                "type": "Boolean",
                "required": False,
                "description": "Validate inputs only",
            },
        ],
    },
    {
        "id": "wf-policy",
        "name": "Policy_Renewal_Batch",
        "status": "active",
        "last_run": "failed",
        "schedule": "daily 08:00",
        "agenticAiToolConfiguration": {
            "toolName": "trigger_policy_renewal",
            "toolDescription": "Trigger policy renewal batch with optional region",
            "status": "active",
            "category": "policy",
            "tags": ["policy", "renewal"],
            "tier": "high_risk",
        },
        "configurationParameters": [
            {
                "name": "region",
                "type": "String",
                "required": False,
                "description": "Optional region code",
            }
        ],
    },
    {
        "id": "wf-premium",
        "name": "Premium_Calculation",
        "status": "paused",
        "last_run": "success",
        "schedule": "hourly",
    },
    {
        "id": "wf-ocr",
        "name": "Document_OCR_Pipeline",
        "status": "active",
        "last_run": "running",
        "schedule": "continuous",
    },
    {
        "id": "wf-commission",
        "name": "Agent_Commission_Report",
        "status": "active",
        "last_run": "success",
        "schedule": "weekly Monday 07:00",
    },
]

EXECUTIONS = [
    {
        "execution_id": f"EX-{i:04d}",
        "workflow_name": random.choice([w["name"] for w in WORKFLOWS]),
        "status": random.choice(
            ["success", "success", "success", "failed", "running"]
        ),
        "started_at": (
            datetime.now() - timedelta(hours=random.randint(1, 72))
        ).isoformat(),
        "completed_at": (
            datetime.now() - timedelta(hours=random.randint(0, 48))
        ).isoformat(),
        "error": (
            "FileNotFoundError: Input file /data/claims/batch_20250227.csv "
            "not found"
            if random.random() < 0.3 else ""
        ),
    }
    for i in range(1, 21)
]

SESSIONS = set()


def _validate_session():
    token = request.headers.get("X-session-token", "")
    return bool(token and token in SESSIONS)


# â”€â”€ AE REST auth/execute/discovery endpoints â”€â”€

@mock_app.route("/aeengine/rest/authenticate", methods=["POST"])
def ae_authenticate():
    username = (
        request.form.get("username")
        or (request.json or {}).get("username")
        or ""
    )
    password = (
        request.form.get("password")
        or (request.json or {}).get("password")
        or ""
    )
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400
    token = f"mock-session-{random.randint(1000, 9999)}"
    SESSIONS.add(token)
    return jsonify({"token": token, "status": "success"})


@mock_app.route("/aeengine/rest/execute", methods=["POST"])
def ae_execute():
    if not _validate_session():
        return jsonify({"error": "invalid or missing session token"}), 401

    data = request.json or {}
    workflow_name = data.get("workflowName", "")
    req_id = f"REQ-{random.randint(10000, 99999)}"
    return jsonify({
        "status": "QUEUED",
        "requestId": req_id,
        "workflowName": workflow_name,
        "message": "Execution queued",
        "params": data.get("params", []),
    })


@mock_app.route("/aeengine/rest/workflows", methods=["GET"])
def ae_list_workflows():
    if not _validate_session():
        return jsonify({"error": "invalid or missing session token"}), 401

    return jsonify({
        "workflows": [
            {
                "id": wf.get("id"),
                "name": wf.get("name"),
                "workflowName": wf.get("name"),
                "status": wf.get("status"),
            }
            for wf in WORKFLOWS
        ]
    })


@mock_app.route("/aeengine/rest/workflows/<workflow_identifier>", methods=["GET"])
def ae_workflow_details(workflow_identifier):
    if not _validate_session():
        return jsonify({"error": "invalid or missing session token"}), 401

    wf = next(
        (
            item for item in WORKFLOWS
            if item.get("id") == workflow_identifier
            or item.get("name") == workflow_identifier
        ),
        None,
    )
    if not wf:
        return jsonify({"error": "workflow not found"}), 404
    return jsonify(wf)


# ── Workflow endpoints ──

@mock_app.route("/api/v1/workflows/<name>/status")
def workflow_status(name):
    wf = next((w for w in WORKFLOWS if w["name"] == name), None)
    if not wf:
        return jsonify({"error": f"Workflow '{name}' not found"}), 404
    return jsonify({
        "workflow_name": name,
        "status": wf["status"],
        "last_execution_status": wf["last_run"],
        "schedule": wf["schedule"],
        "agent": "agent-prod-01",
        "errorMessage": (
            "FileNotFoundError: Input file not found"
            if wf["last_run"] == "failed" else None
        ),
    })


@mock_app.route("/api/v1/workflows/<name>/executions")
def workflow_executions(name):
    limit = request.args.get("limit", 10, type=int)
    execs = [e for e in EXECUTIONS if e["workflow_name"] == name][:limit]
    return jsonify({"executions": execs, "total": len(execs)})


@mock_app.route("/api/v1/workflows/<name>/logs")
def workflow_logs(name):
    return jsonify({
        "workflow_name": name,
        "logs": [
            {"timestamp": datetime.now().isoformat(),
             "level": "INFO", "message": f"Step 1: Read input file"},
            {"timestamp": datetime.now().isoformat(),
             "level": "ERROR",
             "message": "FileNotFoundError: /data/claims/batch_20250227.csv"},
            {"timestamp": datetime.now().isoformat(),
             "level": "INFO", "message": "Execution terminated with errors"},
        ],
    })


@mock_app.route("/api/v1/workflows/<name>/restart", methods=["POST"])
def restart_workflow(name):
    return jsonify({
        "workflow_name": name,
        "action": "restart",
        "status": "initiated",
        "new_execution_id": f"EX-{random.randint(1000, 9999)}",
    })


@mock_app.route("/api/v1/workflows/<name>/trigger", methods=["POST"])
def trigger_workflow(name):
    params = request.json or {}
    return jsonify({
        "workflow_name": name,
        "action": "trigger",
        "status": "queued",
        "execution_id": f"EX-{random.randint(1000, 9999)}",
        "parameters": params,
    })


@mock_app.route("/api/v1/workflows/<name>/disable", methods=["POST"])
def disable_workflow(name):
    wf = next((w for w in WORKFLOWS if w["name"] == name), None)
    return jsonify({
        "workflow_name": name,
        "action": "disable",
        "status": "disabled",
        "previous_status": wf["status"] if wf else "unknown",
    })


@mock_app.route("/api/v1/workflows/<name>/dependencies")
def workflow_dependencies(name):
    return jsonify({
        "workflow_name": name,
        "upstream": ["Input_File_Generator", "Data_Validation_Check"],
        "downstream": ["Report_Aggregator", "Email_Notification"],
        "sharedResources": ["/data/claims/", "/data/policies/"],
    })


@mock_app.route("/api/v1/workflows/<name>/config")
def workflow_config(name):
    return jsonify({
        "workflow_name": name,
        "input_paths": ["/data/claims/batch_*.csv"],
        "output_paths": ["/data/output/claims_processed.csv"],
        "timeout_minutes": 120,
        "retry_count": 3,
        "parameters": {"batch_size": 500, "validate_input": True},
    })


@mock_app.route("/api/v1/workflows/<name>/schedule")
def workflow_schedule(name):
    wf = next((w for w in WORKFLOWS if w["name"] == name), None)
    return jsonify({
        "workflow_name": name,
        "cronExpression": "0 8 * * *",
        "nextRun": (datetime.now() + timedelta(hours=12)).isoformat(),
        "lastRun": (datetime.now() - timedelta(hours=12)).isoformat(),
        "timezone": "Asia/Kolkata",
        "enabled": wf["status"] != "paused" if wf else True,
    })


@mock_app.route("/api/v1/workflows/<name>/input-file")
def workflow_input_file(name):
    date = request.args.get("date", datetime.now().strftime("%Y%m%d"))
    exists = random.random() > 0.3
    return jsonify({
        "exists": exists,
        "filePath": f"/data/claims/batch_{date}.csv",
        "fileSize": random.randint(1000, 50000000) if exists else 0,
        "lastModified": datetime.now().isoformat() if exists else None,
        "formatValid": exists,
        "rowCount": random.randint(100, 5000) if exists else 0,
    })


@mock_app.route("/api/v1/workflows/<name>/output-file")
def workflow_output_file(name):
    exists = random.random() > 0.4
    return jsonify({
        "exists": exists,
        "filePath": f"/data/output/{name}_result.csv",
        "fileSize": random.randint(500, 10000000) if exists else 0,
        "lastModified": datetime.now().isoformat() if exists else None,
        "rowCount": random.randint(50, 3000) if exists else 0,
    })


# ── Execution endpoints ──

@mock_app.route("/api/v1/executions/<execution_id>/logs")
def execution_logs(execution_id):
    ex = next((e for e in EXECUTIONS if e["execution_id"] == execution_id), None)
    wf_name = ex["workflow_name"] if ex else "unknown"
    return jsonify({
        "execution_id": execution_id,
        "workflow_name": wf_name,
        "logs": [
            {"timestamp": datetime.now().isoformat(),
             "level": "INFO", "message": "Step 1: Read input file"},
            {"timestamp": datetime.now().isoformat(),
             "level": "ERROR",
             "message": "FileNotFoundError: /data/claims/batch_20250227.csv"},
            {"timestamp": datetime.now().isoformat(),
             "level": "INFO", "message": "Execution terminated with errors"},
        ],
    })


@mock_app.route("/api/v1/executions/<execution_id>/restart", methods=["POST"])
def restart_execution(execution_id):
    data = request.json or {}
    return jsonify({
        "execution_id": execution_id,
        "workflow_name": data.get("workflow_name", "unknown"),
        "action": "restart",
        "status": "initiated",
        "new_execution_id": f"EX-{random.randint(1000, 9999)}",
    })


@mock_app.route("/api/v1/executions/bulk-retry", methods=["POST"])
def bulk_retry():
    data = request.json or {}
    max_retries = data.get("max_retries", 10)
    retried = min(random.randint(1, 8), max_retries)
    return jsonify({
        "retriedCount": retried,
        "skippedCount": random.randint(0, 3),
        "errors": [],
    })


@mock_app.route("/api/v1/failures/recent")
def recent_failures():
    failures = [e for e in EXECUTIONS if e["status"] == "failed"]
    return jsonify({"failures": failures, "total": len(failures)})


# ── System / health ──

@mock_app.route("/api/v1/system/health")
def system_health():
    return jsonify({
        "status": "healthy",
        "agents": [
            {"name": "agent-prod-01", "status": "online",
             "cpu": 45, "memory": 62},
            {"name": "agent-prod-02", "status": "online",
             "cpu": 78, "memory": 85},
        ],
        "queue_depth": random.randint(0, 50),
        "active_executions": random.randint(1, 15),
    })


# ── Queue endpoints ──

@mock_app.route("/api/v1/queue/status")
def queue_status_global():
    return jsonify({
        "pending": random.randint(0, 20),
        "running": random.randint(1, 10),
        "completed_today": random.randint(50, 200),
        "failed_today": random.randint(0, 15),
    })


@mock_app.route("/api/v1/queues/<queue_name>/status")
def queue_status(queue_name):
    return jsonify({
        "queue_name": queue_name,
        "pending": random.randint(0, 20),
        "running": random.randint(1, 10),
        "completed_today": random.randint(50, 200),
        "failed_today": random.randint(0, 15),
    })


@mock_app.route("/api/v1/queues/<queue_name>/items/<item_id>/requeue",
                methods=["POST"])
def requeue_item(queue_name, item_id):
    return jsonify({
        "queue_name": queue_name,
        "item_id": item_id,
        "status": "queued",
        "action": "requeue",
    })


# ── Agent endpoints ──

@mock_app.route("/api/v1/agents/status")
def all_agents_status():
    return jsonify({
        "agents": [
            {"name": "agent-prod-01", "status": "online",
             "cpu_percent": 45, "memory_percent": 62,
             "active_workflows": 3},
            {"name": "agent-prod-02", "status": "online",
             "cpu_percent": 78, "memory_percent": 85,
             "active_workflows": 5},
        ],
    })


@mock_app.route("/api/v1/agents/<agent_name>/status")
def agent_status(agent_name):
    return jsonify({
        "agent": agent_name,
        "status": "online",
        "cpu_percent": random.randint(10, 95),
        "memory_percent": random.randint(30, 90),
        "active_workflows": random.randint(0, 8),
    })


@mock_app.route("/api/v1/agents/<agent_name>/resources")
def agent_resources(agent_name):
    return jsonify({
        "agent": agent_name,
        "cpu_percent": random.randint(10, 95),
        "memory_percent": random.randint(30, 90),
        "disk_percent": random.randint(20, 80),
        "active_workflows": random.randint(0, 8),
    })


@mock_app.route("/api/v1/agents/resources")
def all_agents_resources():
    return jsonify({
        "agents": [
            {"name": "agent-prod-01", "cpu_percent": 45,
             "memory_percent": 62, "disk_percent": 55,
             "active_workflows": 3},
            {"name": "agent-prod-02", "cpu_percent": 78,
             "memory_percent": 85, "disk_percent": 40,
             "active_workflows": 5},
        ],
    })


# ── Notification / incident endpoints ──

@mock_app.route("/api/v1/notifications/send", methods=["POST"])
def send_notification():
    data = request.json or {}
    return jsonify({
        "status": "sent",
        "channel": data.get("channel", "email"),
        "recipients": data.get("recipients", []),
        "notification_id": f"NOTIF-{random.randint(1000, 9999)}",
    })


@mock_app.route("/api/v1/incidents", methods=["POST"])
def create_incident():
    data = request.json or {}
    return jsonify({
        "incident_id": f"INC-{random.randint(10000, 99999)}",
        "title": data.get("title", "Untitled"),
        "priority": data.get("priority", "P3"),
        "status": "created",
    })


# ── Legacy / compatibility routes ──

@mock_app.route("/api/v1/files/check")
def check_file():
    path = request.args.get("path", "")
    exists = random.random() > 0.3
    return jsonify({
        "path": path,
        "exists": exists,
        "size_bytes": random.randint(1000, 50000000) if exists else 0,
        "last_modified": datetime.now().isoformat() if exists else None,
    })


if __name__ == "__main__":
    print("Starting Mock AE API on http://localhost:5051")
    mock_app.run(host="0.0.0.0", port=5051, debug=True)
