"""Convert duel traces into player-facing coaching recommendations."""

from __future__ import annotations

from ygotrainingbot.models import CoachingRecommendation, MatchResult


class TraceCoach:
    """Derive simple coaching notes from traced decisions."""

    def recommend(self, result: MatchResult) -> tuple[CoachingRecommendation, ...]:
        recommendations: list[CoachingRecommendation] = []

        for trace in result.traces:
            alternatives = [
                action
                for action in trace.state.legal_actions
                if action != trace.action and action.expected_value is not None
            ]
            if trace.action.expected_value is None or not alternatives:
                continue

            best_alternative = max(alternatives, key=lambda action: action.expected_value or 0.0)
            value_gap = best_alternative.expected_value - trace.action.expected_value
            if value_gap <= 0:
                continue

            recommendations.append(
                CoachingRecommendation(
                    title=f"Consider {best_alternative.label}",
                    scenario=trace.state.summary,
                    recommendation=(
                        f"{trace.agent_name} chose {trace.action.label}, but "
                        f"{best_alternative.label} projected better in this state."
                    ),
                    evidence=(
                        f"Chosen expected value: {trace.action.expected_value:.2f}",
                        f"Alternative expected value: {best_alternative.expected_value:.2f}",
                    ),
                    confidence=min(1.0, value_gap),
                )
            )

        return tuple(recommendations)
