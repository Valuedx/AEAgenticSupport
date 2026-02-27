"""
Mock AutomationEdge API for local testing.
Runs a Flask server that simulates AE REST endpoints.
"""

import random
from datetime import datetime, timedelta
from flask import Flask, jsonify, request

mock_app = Flask(__name__)

WORKFLOWS = [
    {"name": "Claims_Processing_Daily", "status": "active",
     "last_run": "success", "schedule": "daily 06:00"},
    {"name": "Policy_Renewal_Batch", "status": "active",
     "last_run": "failed", "schedule": "daily 08:00"},
    {"name": "Premium_Calculation", "status": "paused",
     "last_run": "success", "schedule": "hourly"},
    {"name": "Document_OCR_Pipeline", "status": "active",
     "last_run": "running", "schedule": "continuous"},
    {"name": "Agent_Commission_Report", "status": "active",
     "last_run": "success", "schedule": "weekly Monday 07:00"},
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
    return jsonify({
        "workflow_name": name,
        "action": "disable",
        "status": "disabled",
    })


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


@mock_app.route("/api/v1/failures/recent")
def recent_failures():
    failures = [e for e in EXECUTIONS if e["status"] == "failed"]
    return jsonify({"failures": failures, "total": len(failures)})


@mock_app.route("/api/v1/workflows/<name>/dependencies")
def workflow_dependencies(name):
    return jsonify({
        "workflow_name": name,
        "upstream": ["Input_File_Generator", "Data_Validation_Check"],
        "downstream": ["Report_Aggregator", "Email_Notification"],
        "shared_resources": ["/data/claims/", "/data/policies/"],
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


@mock_app.route("/api/v1/queue/status")
def queue_status():
    return jsonify({
        "pending": random.randint(0, 20),
        "running": random.randint(1, 10),
        "completed_today": random.randint(50, 200),
        "failed_today": random.randint(0, 15),
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


@mock_app.route("/api/v1/notifications/send", methods=["POST"])
def send_notification():
    data = request.json or {}
    return jsonify({
        "status": "sent",
        "channel": data.get("channel", "email"),
        "recipients": data.get("recipients", []),
    })


@mock_app.route("/api/v1/incidents", methods=["POST"])
def create_incident():
    data = request.json or {}
    return jsonify({
        "incident_id": f"INC-{random.randint(10000, 99999)}",
        "title": data.get("title", "Untitled"),
        "status": "created",
    })


if __name__ == "__main__":
    print("Starting Mock AE API on http://localhost:5051")
    mock_app.run(host="0.0.0.0", port=5051, debug=True)
