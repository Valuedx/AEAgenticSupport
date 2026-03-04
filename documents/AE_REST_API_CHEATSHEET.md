> **Documentation Update (2026-03-04)**  
> Patch release notes included in this version:
> - Added tenant-validated T4 request/response behavior for auth, workflow list, and workflow details.
> - Added method-level configuration notes (`AE_WORKFLOWS_METHOD`, `AE_WORKFLOW_DETAILS_METHOD`).
> - Added token-field compatibility note (`sessionToken` vs `token`).
>
# AutomationEdge REST API Cheat Sheet

## Base URL
- `AE_BASE_URL`: `https://<host>:<port>`
- `AE_REST_BASE_PATH`: `/aeengine/rest`
- Effective REST root: `https://<host>:<port>/aeengine/rest`

## Authentication
- Endpoint: `POST /aeengine/rest/authenticate`
- Content type: `application/x-www-form-urlencoded`
- Body fields:
  - `username`
  - `password`
- Expected response field for token:
  - default: `token` (`AE_TOKEN_FIELD`)
- Session header for subsequent calls:
  - default: `X-session-token` (`AE_SESSION_HEADER`)

Example:
```http
POST /aeengine/rest/authenticate
Content-Type: application/x-www-form-urlencoded

username=ops_user&password=*****
```

Example success body:
```json
{
  "token": "abc123-session-token",
  "status": "success"
}
```

Tenant note (T4 validation on March 4, 2026):
- Response token field is `sessionToken` (not `token`).
- Set `AE_TOKEN_FIELD=sessionToken`.

## Execute Workflow
- Endpoint: `POST /aeengine/rest/execute`
- Header: `X-session-token: <token>`
- Body contract used by `AutomationEdgeClient.execute_workflow(...)`:
```json
{
  "orgCode": "ORG1",
  "workflowName": "My_Workflow",
  "userId": "ops_agent",
  "source": "ae-agentic-support",
  "params": [
    { "name": "input_file", "value": "/data/input.csv", "type": "String" },
    { "name": "retry", "value": true, "type": "Boolean" }
  ]
}
```

Typical response fields:
- `status` / `requestStatus`
- `requestId` / `id`
- `message` / `details`

Tenant note (T4 validation on March 4, 2026):
- Endpoint responds with fields like:
  - `success`
  - `responseCode`
  - `errorDetails`
  - `automationRequestId`
- Example non-destructive validation call with invalid workflow returned:
  - `success: false`
  - `responseCode: InvalidWorkflowConfiguration`

## Workflow Discovery
- List workflows endpoint (configurable):
  - default: `GET /aeengine/rest/workflows` (`AE_WORKFLOWS_ENDPOINT`)
- Workflow details endpoint (configurable):
  - default: `GET /aeengine/rest/workflows/{workflow_identifier}` (`AE_WORKFLOW_DETAILS_ENDPOINT`)

Used for dynamic tool discovery:
- Reads `Agentic AI Tool Configuration`
- Requires:
  - `toolName` (no spaces)
  - `status`/`active` to be enabled

Tenant note (T4 validation on March 4, 2026):
- Workflow list works with `POST /aeengine/rest/workflows` (GET returned `AE-1002`).
- Workflow details works with `GET /aeengine/rest/workflows/{workflowId}`.
- Using workflow name in detail path returned server error; use numeric workflow id.
- Current validated tenant returned 10 workflows in default list page.

## Environment Variables
- `AE_BASE_URL`
- `AE_REST_BASE_PATH`
- `AE_AUTH_ENDPOINT`
- `AE_EXECUTE_ENDPOINT`
- `AE_WORKFLOWS_ENDPOINT`
- `AE_WORKFLOW_DETAILS_ENDPOINT`
- `AE_USERNAME`
- `AE_PASSWORD`
- `AE_SESSION_HEADER`
- `AE_TOKEN_FIELD`
- `AE_TOKEN_TTL_SECONDS`
- `AE_ORG_CODE`
- `AE_DEFAULT_USERID`
- `AE_ENABLE_DYNAMIC_TOOLS`
- `AE_WORKFLOWS_METHOD` (use `POST` for T4)
- `AE_WORKFLOW_DETAILS_METHOD` (use `GET` for T4)
