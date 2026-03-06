"""
Tests for Feature 2.5: Observability & Diagnostics.
"""
import time
import pytest
from config.metrics import MetricsCollector, TokenUsage

def test_metrics_collection():
    collector = MetricsCollector()
    collector._init_once()  # Reset for test
    
    turn_id = "turn-1"
    collector.start_turn("conv-1", turn_id)
    time.sleep(0.1)
    
    collector.record_tool_call(turn_id, "test_tool", 50.0, True)
    collector.end_turn(turn_id, TokenUsage(100, 50, 150))
    
    summary = collector.get_summary("conv-1")
    assert summary["turn_count"] == 1
    assert summary["total_tokens"] == 150
    assert summary["tool_usage"]["test_tool"] == 1
    assert summary["avg_latency_ms"] >= 100

def test_singleton_metrics():
    c1 = MetricsCollector()
    c2 = MetricsCollector()
    assert c1 is c2

if __name__ == "__main__":
    pytest.main([__file__])
