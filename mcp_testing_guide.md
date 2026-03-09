# Testing MCP Tools via Agent Server (Port 5050)

Since the Agent Server uses an internal bridge, you can test it directly through its own interface.

### 1. Verify Tools are Registered
Open your browser to:
[http://localhost:5050/api/tools](http://localhost:5050/api/tools)

Search (Ctrl+F) for `ae.request.search`. If you see it, the tools are correctly loaded.

### 2. Test via Webchat (Recommended)
Open the integrated webchat:
[http://localhost:5050/webchat](http://localhost:5050/webchat)

Ask a question that requires MCP data, for example:
> "Search for requests that failed in the last 2 hours"

### 3. Test via API (cURL)
You can also trigger a tool call directly via the `/chat` endpoint:

```bash
curl -X POST http://localhost:5050/chat \
     -H "Content-Type: application/json" \
     -d '{"message": "summarize the status of requests for workflow WF_Sample", "session_id": "test-123"}'
```

### 4. Verify in Logs
Watch the terminal where `agent_server.py` is running. You will see lines like:
`INFO:ops_agent.tools.mcp_tools:MCP tool ae.request.search executed successfully`
