"""
run_rag_index.py
================
Standalone script to re-index ALL documents into the RAG database in one shot.

Covers:
    1. Registered MCP / static tools  (tools collection)
    2. T4 live workflows from AE API  (tools collection + workflow_catalog table)
    3. SOPs                           (sops collection)
    4. KB articles                    (kb_articles collection)
    5. Past incidents                 (past_incidents collection)

Usage:
    python run_rag_index.py                    # index everything
    python run_rag_index.py --only tools       # only MCP/static tools
    python run_rag_index.py --only t4          # only live T4 workflows
    python run_rag_index.py --only sops
    python run_rag_index.py --only kb
    python run_rag_index.py --only incidents
    python run_rag_index.py --skip t4          # everything except T4 fetch
"""

import argparse
import logging
import sys
import os

# ── Make sure the project root is on sys.path ──────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── Logging setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("run_rag_index")


# ── Import after path setup ────────────────────────────────────────────────
def _import_rag():
    from rag.engine import get_rag_engine  # noqa: F401
    return get_rag_engine()


def index_mcp_tools():
    """Index all registered MCP/static tools into the 'tools' RAG collection."""
    logger.info("=== Indexing MCP / static tools ===")
    from rag.engine import get_rag_engine
    from tools.registry import tool_registry

    docs = tool_registry.get_all_rag_documents()
    if not docs:
        logger.warning("No tool documents found in registry — is tool_registry populated?")
        return 0

    get_rag_engine().index_documents(docs, collection="tools")
    logger.info("  ✔ Indexed %d MCP / static tool documents.", len(docs))
    return len(docs)


def index_mcp_server_tools() -> int:
    """Fetch live tools from the remote MCP server and index them into the 'tools' RAG collection.

    Uses the same discovery path as mcp_tools._register_remote_mcp_tools() so the
    embeddings reflect exactly what the server currently exposes.
    Requires AE_MCP_SERVER_URL to be set in .env.
    """
    logger.info("=== Indexing remote MCP server tools ===")
    from config.settings import CONFIG
    from rag.engine import get_rag_engine
    from tools.base import ToolDefinition

    server_url = str(CONFIG.get("AE_MCP_SERVER_URL", "") or "").strip()
    if not server_url:
        logger.warning("  ⚠ AE_MCP_SERVER_URL not set — skipping remote MCP tool index.")
        return 0

    try:
        from tools.mcp_tools import (
            _discover_remote_mcp_tools,
            _get_remote_tool_meta,
            _serialize_annotations,
            _derive_remote_tool_category,
            _derive_remote_tool_tier,
            _coerce_input_examples,
        )
    except ImportError as exc:
        logger.error("  ✗ Cannot import mcp_tools: %s", exc)
        return 0

    try:
        remote_tools = _discover_remote_mcp_tools()
        logger.info("  Found %d tools from MCP server at %s", len(remote_tools), server_url)
    except Exception as exc:
        logger.error("  ✗ Failed to discover tools from MCP server: %s", exc)
        return 0

    docs = []
    for rt in remote_tools:
        meta = _get_remote_tool_meta(rt)
        annotations = _serialize_annotations(getattr(rt, "annotations", None))
        category = _derive_remote_tool_category(meta, rt.name, annotations)
        tier = _derive_remote_tool_tier(meta, annotations)
        parameters = dict(getattr(rt, "inputSchema", {}) or {})
        use_when = str(meta.get("use_when", meta.get("useWhen", "")) or "")
        input_examples = _coerce_input_examples(meta.get("input_examples", meta.get("inputExamples", [])))

        definition = ToolDefinition(
            name=rt.name,
            description=str(getattr(rt, "description", "") or f"Remote MCP tool {rt.name}"),
            category=category,
            tier=tier,
            parameters=dict(parameters.get("properties", {}) or {}),
            required_params=list(parameters.get("required", []) or []),
            always_available=bool(meta.get("always_available", False)),
            use_when=use_when,
            input_examples=input_examples,
            metadata={
                "source": "mcp_remote",
                "tags": list(meta.get("tags", []) or []),
                "mcp_server_url": server_url,
            },
        )
        docs.append(definition.to_rag_document())

    if not docs:
        logger.warning("  ⚠ No documents generated from MCP tools.")
        return 0

    get_rag_engine().index_documents(docs, collection="tools")
    logger.info("  ✔ Indexed %d remote MCP tool documents.", len(docs))
    return len(docs)


def index_t4_workflows():
    """Fetch live T4 workflows and index them (DB + RAG)."""
    logger.info("=== Indexing T4 live workflows ===")
    try:
        from tools.automationedge_client import get_automationedge_client
        from config.settings import CONFIG

        if not CONFIG.get("AE_USERNAME") and not CONFIG.get("AE_API_KEY"):
            logger.warning("  ⚠ AE_USERNAME / AE_API_KEY not set — skipping T4 workflow index.")
            return 0

        client = get_automationedge_client()
        result = client.sync_and_index_workflows()
        db_n = result.get("db_synced", 0)
        rag_n = result.get("rag_indexed", 0)
        logger.info("  ✔ T4 workflows — DB synced: %d, RAG indexed: %d", db_n, rag_n)
        return rag_n
    except Exception as exc:
        logger.error("  ✗ T4 workflow indexing failed: %s", exc)
        return 0


def index_from_dir(directory: str, collection: str, label: str) -> int:
    """Generic helper: load files from a dir and push to RAG collection."""
    logger.info("=== Indexing %s from %s ===", label, directory)
    if not os.path.isdir(directory):
        logger.warning("  ⚠ Directory not found — skipping: %s", directory)
        return 0

    from rag.engine import get_rag_engine
    from rag.document_processor import DocumentProcessor
    import glob

    processor = DocumentProcessor()
    all_chunks = []
    for ext in ("*.pdf", "*.md", "*.json"):
        for filepath in glob.glob(os.path.join(directory, "**", ext), recursive=True):
            chunks = processor.process_file(filepath)
            for chunk in chunks:
                all_chunks.append({
                    "id": chunk.id,
                    "content": chunk.content,
                    "metadata": chunk.metadata,
                })

    if not all_chunks:
        logger.warning("  ⚠ No documents found in %s", directory)
        return 0

    get_rag_engine().index_documents(all_chunks, collection=collection)
    logger.info("  ✔ Indexed %d %s segments.", len(all_chunks), label)
    return len(all_chunks)


def main():
    parser = argparse.ArgumentParser(description="Re-index all RAG documents.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--only",
        choices=["tools", "mcp", "t4", "sops", "kb", "incidents"],
        help="Index only this category. 'tools'=static registry, 'mcp'=live MCP server.",
    )
    group.add_argument(
        "--skip",
        choices=["tools", "mcp", "t4", "sops", "kb", "incidents"],
        help="Skip this category and run everything else.",
    )
    args = parser.parse_args()

    only = args.only
    skip = args.skip

    data_dir = os.path.join(PROJECT_ROOT, "rag", "data")
    totals: dict[str, int] = {}

    def should_run(name: str) -> bool:
        if only:
            return name == only
        return name != skip

    if should_run("tools"):
        totals["MCP tools (registry)"] = index_mcp_tools()

    if should_run("mcp"):
        totals["MCP server tools"] = index_mcp_server_tools()

    if should_run("t4"):
        totals["T4 workflows"] = index_t4_workflows()

    if should_run("sops"):
        totals["SOPs"] = index_from_dir(
            os.path.join(data_dir, "sops"), "sops", "SOPs"
        )

    if should_run("kb"):
        totals["KB articles"] = index_from_dir(
            os.path.join(data_dir, "kb_articles"), "kb_articles", "KB articles"
        )

    if should_run("incidents"):
        totals["Past incidents"] = index_from_dir(
            os.path.join(data_dir, "past_incidents"), "past_incidents", "Past incidents"
        )

    logger.info("=" * 55)
    logger.info("RAG index complete. Summary:")
    for name, count in totals.items():
        logger.info("  %-22s  %d documents", name, count)
    logger.info("=" * 55)


if __name__ == "__main__":
    main()
