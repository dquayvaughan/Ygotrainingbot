"""Core interfaces for the Yu-Gi-Oh! training bot."""

from ygotrainingbot.agents import (
    AggressiveHeuristicAgent,
    ControlHeuristicAgent,
    DuelAgent,
    FirstLegalActionAgent,
    HeuristicActionAgent,
    RandomLegalActionAgent,
    TempoHeuristicAgent,
    create_agent,
)
from ygotrainingbot.coaching import TraceCoach
from ygotrainingbot.data import build_card_sets, fetch_ygoprodeck_cards, load_card_database
from ygotrainingbot.edopro import EdoproGatewayConfig, EdoproInstall, JsonLineEdoproSimulator
from ygotrainingbot.format_training import (
    FormatBanlist,
    FormatDeck,
    FormatPack,
    FormatTrainingConfig,
    load_format_pack,
    load_format_training_config,
)
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
from ygotrainingbot.static_training import StaticSetTrainer, StaticTrainingReport
from ygotrainingbot.training import LearningReport, SetExplorationPlan, SelfPlayRunner

__all__ = [
    "AggressiveHeuristicAgent",
    "Card",
    "CardSet",
    "CardType",
    "CoachingRecommendation",
    "ControlHeuristicAgent",
    "Deck",
    "DeterministicScenarioSimulator",
    "DuelAgent",
    "DuelSimulator",
    "DuelTrace",
    "EdoproGatewayConfig",
    "EdoproInstall",
    "FirstLegalActionAgent",
    "HeuristicActionAgent",
    "FormatBanlist",
    "FormatDeck",
    "FormatPack",
    "FormatTrainingConfig",
    "GameAction",
    "JsonLineEdoproSimulator",
    "LearningReport",
    "MatchResult",
    "RandomLegalActionAgent",
    "SelfPlayRunner",
    "SetExplorationPlan",
    "TempoHeuristicAgent",
    "TraceCoach",
    "VisibleGameState",
    "build_card_sets",
    "create_agent",
    "fetch_ygoprodeck_cards",
    "load_card_database",
    "load_format_pack",
    "load_format_training_config",
    "StaticSetTrainer",
    "StaticTrainingReport",
]
