from __future__ import annotations

from pathlib import Path

from state.docs_catalog import DocsCatalogStore


def test_docs_catalog_persists_entries_and_reads_markdown(tmp_path):
    guide_path = tmp_path / "guides" / "billing.md"
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("# Billing guide\n\nStep 1", encoding="utf-8")

    store = DocsCatalogStore(
        path=str(tmp_path / "docs_catalog.json"),
        root_dir=tmp_path,
    )
    saved = store.upsert_document(
        {
            "title": "Billing recovery guide",
            "badge": "BR",
            "summary": "Used by support leads during billing incidents.",
            "audience": "Support operations",
            "path": "guides/billing.md",
            "displayOrder": 5,
            "active": True,
        }
    )

    assert saved["id"] == "billing_recovery_guide"
    assert saved["path"] == "guides/billing.md"
    assert saved["available"] is True

    listed = store.list_documents()
    billing_doc = next(item for item in listed if item["id"] == "billing_recovery_guide")
    assert billing_doc["title"] == "Billing recovery guide"
    assert billing_doc["audience"] == "Support operations"

    content = store.read_document_content("billing_recovery_guide", include_inactive=True)
    assert content["content"].startswith("# Billing guide")


def test_docs_catalog_rejects_paths_outside_workspace(tmp_path):
    store = DocsCatalogStore(
        path=str(tmp_path / "docs_catalog.json"),
        root_dir=tmp_path,
    )

    outside_path = (tmp_path.parent / "secret.md").resolve()

    try:
        store.upsert_document(
            {
                "title": "Bad doc",
                "path": str(outside_path),
            }
        )
    except ValueError as exc:
        assert "workspace" in str(exc).lower()
    else:
        raise AssertionError("Expected document path validation to fail")
