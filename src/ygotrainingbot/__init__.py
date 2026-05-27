"""Core interfaces for the Yu-Gi-Oh! training bot."""

from ygotrainingbot.agents import DuelAgent, FirstLegalActionAgent
from ygotrainingbot.coaching import TraceCoach
from ygotrainingbot.models import (
    Card,
    CardSet,
    CardType,
    CoachingRecommendation,
    Deck,
    DuelTrace,
    GameAction,
    MatchResult,
    VisibleGameState,
)
from ygotrainingbot.simulation import DeterministicScenarioSimulator, DuelSimulator
from ygotrainingbot.training import LearningReport, SetExplorationPlan, SelfPlayRunner

__all__ = [
    "Card",
    "CardSet",
    "CardType",
    "CoachingRecommendation",
    "Deck",
    "DeterministicScenarioSimulator",
    "DuelAgent",
    "DuelSimulator",
    "DuelTrace",
    "FirstLegalActionAgent",
    "GameAction",
    "LearningReport",
    "MatchResult",
    "SelfPlayRunner",
    "SetExplorationPlan",
    "TraceCoach",
    "VisibleGameState",
]
