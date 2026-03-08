# Tool Architecture Target

**Status:** Target architecture and migration plan; core catalog, ranking, hydrator, and executor slices implemented  
**Date:** 2026-03-07

Implemented in the current codebase:

- Searchable tool metadata is now stored separately from runtime handler hydration.
- Long-tail MCP tools are cataloged and lazily hydrated on demand.
- Dynamic AE workflow-backed tools are cataloged and now default to generic-runner exposure through `trigger_workflow`; an allowlist can keep selected workflows as direct tools.
- The orchestrator now uses a turn-local hydrated tool set for the main LLM investigation loop.
- Tool discovery and turn-local hydration now share a catalog-aware ranking step that blends retrieval score with source, risk, latency, mutation, and direct-callability signals.
- The ranking step now also folds in observed success/failure history from the existing tool interaction log, weighted toward recent events and scoped to the current agent when matching feedback exists.
- Hydration and runtime execution are now implemented in dedicated modules: `tools/hydrator.py` and `tools/executor.py`.
- The existing `ToolRegistry` API remains in place as a compatibility facade.

## 1. Why This Refactor Exists

The repo already supports three different tool sources:

- Custom/static tools in `tools/*.py`
- MCP-backed tools bridged through `tools/mcp_tools.py`, sourced from `mcp_server/tool_specs.py`
- AutomationEdge workflow-backed dynamic tools loaded via `tools/registry.py`

This works at moderate scale, but it becomes unstable once the combined catalog reaches hundreds of tools:

- Too many tools are globally registered up front.
- Discovery, execution, and prompt exposure are coupled too tightly.
- Workflow-backed tools are treated too much like hand-authored tools.
- MCP long-tail tools compete with high-value custom composite tools.
- The model still gets a flatter tool surface than it should for large catalogs.

The target state is:

1. One unified searchable catalog across all sources.
2. A very small always-on executable tool surface.
3. Turn-local hydration of only the few tools relevant to the current request.
4. A clean separation between metadata, execution, and prompt exposure.


## 2. Design Principles

### 2.1 Catalog is not execution

A tool being searchable does not mean it should be exposed to the model as a live callable function.

### 2.2 Exposure is turn-local

The set of function declarations passed to `llm_client.chat_with_tools()` should be computed per turn and discarded after the turn.

### 2.3 Tool sources are unified, not identical

Custom tools, MCP tools, and AE workflow-backed tools should share the same metadata model and discovery path, but they should not all hydrate the same way.

### 2.4 Composite beats primitive

The system should prefer a few opinionated high-value tools over many low-level operations, especially for MCP.

### 2.5 Risk and latency are ranking signals

Tool ranking should consider not only semantic similarity, but also risk tier, required identifiers, latency class, and prior success.


## 3. Current State in This Repo

Today the code has the right pieces, but not yet the right separation:

- `tools/registry.py`
  - Holds the global tool registry.
  - Registers custom tools and dynamic workflow tools.
  - Also contains `discover_tools`.
- `tools/mcp_tools.py`
  - Bulk-registers bridged MCP handlers into the main registry.
- `agents/orchestrator.py`
  - Already narrows the visible tool set per turn using RAG plus `discover_tools`.
- `main.py`
  - Loads static tools, MCP tools, dynamic workflow tools, then indexes the resulting set into RAG.

This means the prompt exposure is smaller than the total catalog, but the system still pays the complexity cost of large up-front registration.


## 4. Target Architecture

### 4.1 Layer A: Tool Catalog

Purpose: store metadata for every possible tool, regardless of whether it is currently executable.

Proposed responsibilities:

- Normalize tool metadata from all sources.
- Provide inventory and filtering APIs.
- Feed RAG indexing and `discover_tools`.
- Store source-specific hydration hints.

Proposed new module:

- `tools/catalog.py`

Proposed core model:

```python
@dataclass
class ToolCatalogEntry:
    tool_name: str
    source: str  # custom | mcp | automationedge
    source_ref: str  # import path, workflow id/name, mcp dispatch key
    category: str
    tier: str
    description: str
    parameters: dict
    required_params: list[str]
    use_when: str
    avoid_when: str
    input_examples: list[dict]
    tags: list[str]
    always_available: bool
    hydration_mode: str  # eager | lazy | execute_via_generic_runner
    latency_class: str  # fast | medium | slow | polling
    mutating: bool
    allowed_agents: list[str]
    metadata: dict
```

Notes by source:

- Custom tools:
  - Usually cataloged as `source="custom"`.
  - Best composite and safe diagnostic tools can stay `always_available=True`.
- MCP tools:
  - Catalog all of them.
  - Only a curated subset should be `always_available=True`.
  - Most should be `hydration_mode="lazy"`.
- AE workflow-backed tools:
  - Catalog all eligible workflows.
  - Most should be `hydration_mode="execute_via_generic_runner"`.
  - Only a small curated subset should be hydrated as first-class direct tools.


### 4.2 Layer B: Tool Hydrator

Purpose: turn selected catalog entries into live callable tool definitions for one turn.

Proposed new module:

- `tools/hydrator.py`

Responsibilities:

- Accept a ranked list of selected `ToolCatalogEntry` records.
- Convert only those entries into `ToolDefinition` and callable handlers.
- Build a turn-local function declaration list for the LLM.
- Apply agent/category/risk filters before hydration.

Proposed interface:

```python
class ToolHydrator:
    def hydrate_for_turn(
        self,
        entries: list[ToolCatalogEntry],
        *,
        allowed_categories: list[str] | None,
        max_tools: int,
    ) -> TurnToolSet:
        ...
```

```python
@dataclass
class TurnToolSet:
    entries: list[ToolCatalogEntry]
    definitions: dict[str, ToolDefinition]
    handlers: dict[str, Callable]

    def to_vertex_tools(self) -> list[dict]:
        ...
```

Source-specific behavior:

- Custom tool entry:
  - Hydrate by binding to the existing Python handler.
- MCP tool entry:
  - Hydrate by creating the bridge handler only when selected.
  - Do not bulk-register every MCP handler globally.
- AE workflow tool entry:
  - Usually do not create a unique handler.
  - Hydrate as either:
    - a direct workflow-backed tool for top workflows, or
    - a generic executor with workflow metadata passed in.


### 4.3 Layer C: Execution Layer

Purpose: execute a hydrated tool safely and consistently.

Proposed modules:

- `tools/executor.py`
- Keep low-level source adapters where they already live:
  - `tools/mcp_tools.py`
  - `tools/automationedge_client.py`
  - custom tool modules

Responsibilities:

- Execute only hydrated handlers for the current turn.
- Preserve audit logging and metrics.
- Preserve RBAC and approval checks.
- Return normalized `ToolResult`.

This keeps execution concerns out of the catalog layer.


### 4.4 Layer D: Discovery and Ranking

Purpose: choose which few tools to hydrate for the current turn.

Keep:

- `discover_tools`
- RAG tool search

Improve:

- Add ranking features beyond semantic similarity:
  - exact workflow/request/entity match
  - source preference
  - tool success history
  - latency class
  - mutation penalty unless the user intent is clearly action-oriented
  - specialist-agent compatibility

Proposed new module:

- `tools/ranker.py`

Proposed ranking flow:

1. Retrieve candidate catalog entries from RAG.
2. Add exact-match candidates from structured lookups where possible.
3. Score candidates with a reranker.
4. Hydrate only the top `N`.


## 5. Request Flow in the Target State

### 5.1 Startup

1. Load static custom-tool metadata into the catalog.
2. Load MCP tool metadata into the catalog.
3. Load AE workflow metadata into the catalog.
4. Index catalog metadata into RAG.
5. Do not globally register long-tail MCP or workflow handlers.

### 5.2 Start of a user turn

1. Build the small always-on tool set:
   - `discover_tools`
   - a few composite diagnostic tools
   - a few safe high-value status tools
   - generic workflow executor
   - generic escape hatches only if still justified
2. Run retrieval and ranking for the user turn.
3. Hydrate the top relevant tools only.
4. Send the union of:
   - always-on tools
   - top hydrated turn-local tools
   to the LLM.

### 5.3 Mid-turn expansion

If the model calls `discover_tools`:

1. Search the catalog, not the global runtime registry.
2. Return ranked tool cards with:
   - required params
   - examples
   - source
   - risk tier
   - latency class
   - use/avoid guidance
3. Hydrate only the newly selected tools.
4. Re-run `chat_with_tools()` with the expanded turn-local set.

### 5.4 End of turn

1. Drop the turn-local hydrated set.
2. Keep only:
   - persisted conversation state
   - metrics
   - tool interaction telemetry


## 6. How Each Tool Source Should Be Handled

### 6.1 Custom tools

These should be the backbone of the always-on surface.

Keep always available:

- High-signal status tools
- High-signal log tools
- Composite diagnostic tools
- Approval-safe coordination tools

Reduce exposure of:

- Redundant low-level wrappers if an MCP or composite tool already covers the same task

Rule:

- If a custom tool mainly exists to stitch together several lower-level calls, keep it.
- If it is a thin wrapper around one low-level operation that MCP already exposes, consider moving it out of the always-on set.


### 6.2 MCP tools

MCP is where tool count can grow fastest.

Recommended handling:

- Catalog every MCP tool.
- Keep only a curated support subset always available.
- Make the rest lazy-hydrated.
- Push users toward MCP composite tools first.

Examples of good always-on MCP candidates:

- `ae.support.*`
- a few `ae.request.*` diagnostic reads
- a few `ae.agent.*` health reads

Examples of poor always-on candidates:

- every mutation tool
- every low-level list/read variant
- every niche administrative operation


### 6.3 AE workflow-backed tools

These should not all become direct function declarations.

Recommended handling:

- Keep all workflow metadata in the catalog.
- Treat most workflows as discoverable executable assets, not hand-authored tools.
- Route most execution through one generic workflow execution path.

Promotion rules for direct first-class workflow tools:

- The workflow is high-frequency.
- The workflow has stable parameters.
- The workflow is operationally important enough to justify a dedicated prompt surface.
- The workflow benefits from custom guidance, validation, or approval policy.

Everything else should be:

- discoverable in the catalog
- executable via generic workflow runner


## 7. Concrete Changes to Existing Files

### 7.1 `tools/base.py`

Current role:

- `ToolDefinition`

Target role:

- Keep `ToolDefinition` as the runtime hydrated definition.
- Do not use it as the only model for catalog storage.

Add:

- `ToolCatalogEntry` in `tools/catalog.py`, not here.


### 7.2 `tools/registry.py`

Current role:

- Acts as catalog, execution registry, and discovery entry point.

Target role:

- Be split into:
  - `ToolCatalog`
  - `ToolHydrator`
  - `ToolExecutor`

Migration note:

- `ToolRegistry` can remain as a compatibility facade during transition.


### 7.3 `tools/mcp_tools.py`

Current role:

- Bridges the shared MCP spec catalog into the main registry and hydrates selected MCP handlers on demand.

Target role:

- Keep using the shared MCP spec registry as the source of truth.
- Export MCP catalog metadata builder.
- Export MCP handler hydrator for a selected tool name.
- Continue avoiding duplicate hand-maintained dispatch metadata.


### 7.4 `tools/ae_dynamic_tools.py`

Current role:

- Converts workflow metadata into `ToolDefinition` plus handler behavior.

Target role:

- Convert workflow metadata into `ToolCatalogEntry` or a workflow-specific catalog DTO.
- Leave runtime hydration decisions to the hydrator.


### 7.5 `agents/orchestrator.py`

Current role:

- Builds the turn-local visible tool set using `get_vertex_tools_filtered()`.

Target role:

- Ask the catalog/ranker for candidates.
- Ask the hydrator for a turn-local tool set.
- Keep only the turn-local set for execution during the loop.


### 7.6 `main.py`

Current role:

- Imports tool modules, loads MCP and dynamic workflow tools, indexes all registered tools into RAG.

Target role:

- Initialize the catalog.
- Index catalog documents into RAG.
- Initialize only the always-on executable core.


## 8. Migration Plan

### Phase 1: Normalize metadata

Goal:

- Ensure all tool sources produce the same metadata shape.

Tasks:

- Add `ToolCatalogEntry`.
- Build custom-tool metadata adapter.
- Build MCP metadata adapter.
- Build workflow metadata adapter.

Outcome:

- One searchable inventory across all tool sources.


### Phase 2: Separate discovery from execution

Goal:

- Make `discover_tools` operate on catalog entries, not on whatever happens to be globally executable.

Tasks:

- Move tool-card generation to catalog layer.
- Index catalog entries into RAG directly.

Outcome:

- Discovery becomes stable even if runtime exposure changes.


### Phase 3: Introduce turn-local hydration

Goal:

- Hydrate only top-ranked tools per turn.

Tasks:

- Add `ToolHydrator`.
- Add `TurnToolSet`.
- Update orchestrator to execute only turn-local handlers.

Outcome:

- Large catalogs no longer force broad runtime registration.


### Phase 4: Move workflows to generic execution by default

Goal:

- Stop treating every workflow as a first-class direct tool.

Tasks:

- Keep workflow metadata in catalog.
- Route long-tail workflow execution through generic runner.
- Promote only selected workflows to direct tools.

Outcome:

- Workflow scale stops dominating prompt and registry complexity.


### Phase 5: Prune the always-on set

Goal:

- Keep the prompt surface small and high-signal.

Tasks:

- Measure tool usage frequency.
- Keep only the best custom composites and a curated MCP subset always available.
- Remove redundant low-level tools from the always-on set.

Outcome:

- Better tool choice and lower prompt noise.


## 9. Metrics to Validate the Refactor

Track these before and after:

- average number of tools exposed per turn
- number of tools globally executable at startup
- first tool-call latency
- rate of `discover_tools` usage
- success rate after tool discovery
- approval dead-end rate
- average tools used per successful resolution
- prompt token count for tool declarations
- percentage of workflow executions handled by generic runner vs direct tool


## 10. Recommended First Implementation Slice

Do not start with a full rewrite.

Start here:

1. Add a catalog model separate from `ToolDefinition`.
2. Make MCP tools cataloged but not all globally executable.
3. Make dynamic AE workflows cataloged first, generic-runner by default.
4. Keep `ToolRegistry` as a facade so the orchestrator does not need a flag-day rewrite.
5. Add turn-local hydration for newly discovered tools first, then expand it to the initial tool set.

This gives the main scalability benefit without breaking the entire execution path in one pass.


## 11. Explicit Non-Goals for the First Refactor

Not required in phase one:

- a code-execution planner
- a fully separate microservice for tool execution
- replacing RAG
- replacing approval and RBAC logic
- replacing the existing custom tools

The first refactor should be about separation of concerns and prompt-surface control, not about re-platforming the agent.
