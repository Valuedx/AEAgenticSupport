"""
Metrics collector for observability.
Tracks latencies, token usage, and tool success rates.
"""
import time
import logging
import threading
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

logger = logging.getLogger("ops_agent.metrics")

@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    candidate_tokens: int = 0
    total_tokens: int = 0

@dataclass
class ToolMetric:
    tool_name: str
    latency_ms: float
    success: bool
    error: Optional[str] = None

@dataclass
class TurnMetric:
    conversation_id: str
    turn_id: str
    latency_ms: float
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    tool_calls: List[ToolMetric] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

class MetricsCollector:
    """Thread-safe collector for agent performance metrics."""
    
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(MetricsCollector, cls).__new__(cls)
                cls._instance._init_once()
            return cls._instance

    def _init_once(self):
        self.turns: List[TurnMetric] = []
        self.active_turns: Dict[str, TurnMetric] = {}
        self.max_history = 1000

    def start_turn(self, conversation_id: str, turn_id: str):
        with self._lock:
            self.active_turns[turn_id] = TurnMetric(
                conversation_id=conversation_id,
                turn_id=turn_id,
                latency_ms=0.0
            )

    def end_turn(self, turn_id: str, token_usage: Optional[TokenUsage] = None):
        with self._lock:
            if turn_id in self.active_turns:
                metric = self.active_turns.pop(turn_id)
                metric.latency_ms = (time.time() - metric.timestamp) * 1000
                if token_usage:
                    metric.token_usage = token_usage
                
                self.turns.append(metric)
                if len(self.turns) > self.max_history:
                    self.turns.pop(0)
                
                logger.info(f"Turn {turn_id} ended. Latency: {metric.latency_ms:.2f}ms. Tokens: {metric.token_usage.total_tokens}")

    def record_tool_call(self, turn_id: str, tool_name: str, latency_ms: float, success: bool, error: str = None):
        with self._lock:
            if turn_id in self.active_turns:
                self.active_turns[turn_id].tool_calls.append(
                    ToolMetric(tool_name, latency_ms, success, error)
                )

    def get_summary(self, conversation_id: Optional[str] = None) -> dict:
        """Get aggregate metrics for a conversation or globally."""
        with self._lock:
            relevant = [t for t in self.turns if conversation_id is None or t.conversation_id == conversation_id]
            if not relevant:
                return {"count": 0}
            
            total_lat = sum(t.latency_ms for t in relevant)
            total_tokens = sum(t.token_usage.total_tokens for t in relevant)
            tool_counts = {}
            tool_failures = 0
            
            for t in relevant:
                for tm in t.tool_calls:
                    tool_counts[tm.tool_name] = tool_counts.get(tm.tool_name, 0) + 1
                    if not tm.success:
                        tool_failures += 1
            
            return {
                "turn_count": len(relevant),
                "avg_latency_ms": total_lat / len(relevant),
                "total_tokens": total_tokens,
                "tool_usage": tool_counts,
                "tool_failure_rate": tool_failures / sum(tool_counts.values()) if tool_counts else 0
            }

metrics_collector = MetricsCollector()
