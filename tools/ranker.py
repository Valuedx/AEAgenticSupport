"""
Catalog-aware ranking for tool discovery and turn-local hydration.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from tools.catalog import ToolCatalogEntry

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_SOURCE_BOOST = {
    "static": 0.05,
    "custom": 0.05,
    "mcp": 0.04,
    "automationedge": 0.02,
    "meta": 0.0,
}
_RISK_ADJUST = {
    "read_only": 0.03,
    "low_risk": 0.01,
    "medium_risk": -0.02,
    "high_risk": -0.05,
}
_LATENCY_ADJUST = {
    "fast": 0.02,
    "medium": 0.0,
    "slow": -0.03,
    "polling": -0.04,
}
_ACTION_HINTS = {
    "approve",
    "cancel",
    "disable",
    "enable",
    "execute",
    "fix",
    "kill",
    "pause",
    "process",
    "rerun",
    "resolve",
    "restart",
    "resume",
    "retry",
    "rollback",
    "run",
    "start",
    "stop",
    "trigger",
    "unlock",
    "update",
}


@dataclass(frozen=True)
class RankedToolCandidate:
    entry: ToolCatalogEntry
    score: float
    retrieval_score: float = 0.0


class ToolRanker:
    def rank(
        self,
        query: str,
        entries: list[ToolCatalogEntry],
        *,
        retrieval_scores: dict[str, float] | None = None,
        retrieval_ranks: dict[str, int] | None = None,
        feedback_stats: dict[str, dict] | None = None,
    ) -> list[RankedToolCandidate]:
        if not entries:
            return []

        score_map = {
            str(name): self._coerce_score(raw)
            for name, raw in (retrieval_scores or {}).items()
        }
        rank_map = {str(name): int(rank) for name, rank in (retrieval_ranks or {}).items()}
        max_raw = max(score_map.values(), default=0.0)
        hit_count = max(len(rank_map), len(score_map), 1)
        query_text = str(query or "").strip().lower()
        query_terms = self._tokenize(query_text)
        action_intent = bool(query_terms & _ACTION_HINTS)

        ranked: list[RankedToolCandidate] = []
        for entry in entries:
            ranked.append(
                RankedToolCandidate(
                    entry=entry,
                    score=self._score_entry(
                        query_text,
                        query_terms,
                        entry,
                        retrieval_score=score_map.get(entry.name, 0.0),
                        retrieval_rank=rank_map.get(entry.name),
                        hit_count=hit_count,
                        max_raw=max_raw,
                        action_intent=action_intent,
                        feedback=(
                            feedback_stats.get(entry.name, {})
                            if feedback_stats
                            else {}
                        ),
                    ),
                    retrieval_score=score_map.get(entry.name, 0.0),
                )
            )

        ranked.sort(
            key=lambda item: (
                item.score,
                item.entry.hydration_mode != "execute_via_generic_runner",
                item.entry.definition.always_available,
                item.entry.name,
            ),
            reverse=True,
        )
        return ranked

    @staticmethod
    def _coerce_score(value: float | int | str | None) -> float:
        try:
            return max(0.0, float(value or 0.0))
        except Exception:
            return 0.0

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return set(_TOKEN_RE.findall(text.lower()))

    def _score_entry(
        self,
        query_text: str,
        query_terms: set[str],
        entry: ToolCatalogEntry,
        *,
        retrieval_score: float,
        retrieval_rank: int | None,
        hit_count: int,
        max_raw: float,
        action_intent: bool,
        feedback: dict,
    ) -> float:
        retrieval_component = self._retrieval_component(
            retrieval_score,
            retrieval_rank,
            hit_count,
            max_raw,
        )
        exact_component = self._exact_match_component(query_text, entry)
        overlap_component = self._overlap_component(query_terms, entry)
        feedback_component = self._feedback_component(feedback)
        source_component = _SOURCE_BOOST.get(entry.source, 0.01)
        callable_component = 0.04 if entry.hydration_mode != "execute_via_generic_runner" else -0.03
        risk_component = _RISK_ADJUST.get(entry.definition.tier, 0.0)
        latency_component = _LATENCY_ADJUST.get(entry.latency_class, 0.0)
        availability_component = 0.02 if entry.definition.always_available else 0.0

        mutation_component = 0.0
        if entry.mutating:
            mutation_component = 0.06 if action_intent else -0.08
        elif action_intent:
            mutation_component = -0.01

        total = (
            retrieval_component
            + exact_component
            + overlap_component
            + feedback_component
            + source_component
            + callable_component
            + risk_component
            + latency_component
            + availability_component
            + mutation_component
        )
        return round(max(0.0, min(total, 1.0)), 3)

    def _retrieval_component(
        self,
        raw_score: float,
        retrieval_rank: int | None,
        hit_count: int,
        max_raw: float,
    ) -> float:
        if raw_score <= 0.0 and retrieval_rank is None:
            return 0.0
        absolute = min(raw_score, 1.0) * 0.2
        relative = ((raw_score / max_raw) if max_raw > 0.0 else 0.0) * 0.2
        rank_score = 0.0
        if retrieval_rank is not None:
            denom = max(hit_count - 1, 1)
            rank_score = max(0.0, 1.0 - (retrieval_rank / denom)) * 0.1
        return absolute + relative + rank_score

    def _exact_match_component(self, query_text: str, entry: ToolCatalogEntry) -> float:
        if not query_text:
            return 0.0
        name = entry.name.lower()
        workflow_name = str(entry.definition.metadata.get("workflow_name", "") or "").strip().lower()
        if workflow_name and workflow_name in query_text:
            return 0.22
        if name and name in query_text:
            return 0.18
        return 0.0

    def _overlap_component(self, query_terms: set[str], entry: ToolCatalogEntry) -> float:
        if not query_terms:
            return 0.0
        match_terms: set[str] = set()
        match_terms.update(self._tokenize(entry.name))
        match_terms.update(self._tokenize(str(entry.definition.metadata.get("workflow_name", "") or "")))
        match_terms.update(
            self._tokenize(" ".join(str(tag) for tag in entry.definition.metadata.get("tags", []) or []))
        )
        match_terms.update(self._tokenize(" ".join(entry.definition.required_params)))
        overlap = len(query_terms & match_terms)
        return min(0.16, 0.04 * overlap)

    def _feedback_component(self, feedback: dict) -> float:
        total = int(feedback.get("total_count", 0) or 0)
        if total <= 0:
            return 0.0
        success_count = int(feedback.get("success_count", 0) or 0)
        smoothed_rate = (success_count + 1) / (total + 2)
        confidence = min(total / 8.0, 1.0)
        return (smoothed_rate - 0.5) * 0.24 * confidence
