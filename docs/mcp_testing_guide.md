# Testing MCP Tools via Agent Server (Port 5050)

The Agent Server can use either:

- a co-located MCP bridge that imports the local `mcp_server` package, or
- a remote MCP bridge that talks to `AE_MCP_SERVER_URL` over MCP.

In both cases, you can test MCP-backed tools through the agent server interface.

### 1. Verify Tools are Registered
Open your browser to:
[http://localhost:5050/api/tools](http://localhost:5050/api/tools)

Search (Ctrl+F) for `ae.request.search`. If you see it, the tools are correctly loaded.
If you open the tool entry, check `mcp_connection_mode` in metadata:

- `local` means the app is using the co-located shared-spec bridge.
- `remote` means the app is using the remote MCP client bridge.

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
Watch the terminal where `agent_server.py` is running. Successful executions are recorded by the tool executor/audit logs, for example:

- `TOOL_CALL tool=ae.request.search ...`
- `TOOL_OK tool=ae.request.search`

If remote MCP mode is enabled and discovery fails, `tools/mcp_tools.py` will log a warning that includes the configured `AE_MCP_SERVER_URL`.
