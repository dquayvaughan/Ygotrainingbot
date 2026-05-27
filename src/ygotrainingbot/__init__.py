"""Core interfaces for the Yu-Gi-Oh! training bot."""

from ygotrainingbot.agents import DuelAgent, FirstLegalActionAgent
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
    "Card",
    "CardSet",
    "CardType",
    "CoachingRecommendation",
    "Deck",
    "DeterministicScenarioSimulator",
    "DuelAgent",
    "DuelSimulator",
    "DuelTrace",
    "EdoproGatewayConfig",
    "EdoproInstall",
    "FirstLegalActionAgent",
    "FormatBanlist",
    "FormatDeck",
    "FormatPack",
    "FormatTrainingConfig",
    "GameAction",
    "JsonLineEdoproSimulator",
    "LearningReport",
    "MatchResult",
    "SelfPlayRunner",
    "SetExplorationPlan",
    "TraceCoach",
    "VisibleGameState",
    "build_card_sets",
    "fetch_ygoprodeck_cards",
    "load_card_database",
    "load_format_pack",
    "load_format_training_config",
    "StaticSetTrainer",
    "StaticTrainingReport",
]
