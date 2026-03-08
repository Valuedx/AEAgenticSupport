from __future__ import annotations

from state.tool_overrides import ToolOverrideStore
from tools.base import ToolDefinition
import tools.registry as registry_module


def test_tool_registry_applies_and_resets_admin_overrides(tmp_path, monkeypatch):
    store = ToolOverrideStore(path=str(tmp_path / "tool_overrides.json"))
    monkeypatch.setattr(registry_module, "get_tool_override_store", lambda: store)

    registry = registry_module.ToolRegistry()
    registry.register(
        ToolDefinition(
            name="demo_tool",
            description="Base description",
            category="status",
            tier="read_only",
            metadata={"tags": ["base"], "active": True},
        ),
        lambda **kwargs: {"ok": True},
    )

    registry.update_tool_override(
        "demo_tool",
        {
            "description": "Updated description",
            "tier": "high_risk",
            "tags": ["ops", "override"],
            "active": False,
            "alwaysAvailable": True,
        },
    )

    inventory = registry.get_tool_inventory({})
    assert inventory[0]["description"] == "Updated description"
    assert inventory[0]["tier"] == "high_risk"
    assert inventory[0]["active"] is False
    assert inventory[0]["alwaysAvailable"] is True
    assert inventory[0]["hasOverride"] is True
    assert inventory[0]["tags"] == ["ops", "override"]

    turn_toolset = registry.build_turn_toolset(["demo_tool"], include_meta=False)
    assert turn_toolset.list_tool_names() == []

    result = registry.execute("demo_tool")
    assert result.success is False
    assert "disabled" in result.error.lower()

    registry.reset_tool_override("demo_tool")
    reset_inventory = registry.get_tool_inventory({})
    reset_tool = next(item for item in reset_inventory if item["toolName"] == "demo_tool")
    assert reset_tool["description"] == "Base description"
    assert reset_tool["tier"] == "read_only"
    assert reset_tool["active"] is True
    assert reset_tool["hasOverride"] is False
