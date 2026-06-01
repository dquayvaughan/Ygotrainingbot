import json
from pathlib import Path

from ygotrainingbot.duel_logs import samples_from_game_log_payload
from ygotrainingbot.learning import (
    LearnedPolicy,
    _outcome_weight_adjustments,
    _update_policy,
    _why_best_line_samples,
    is_learnable_policy_tag,
    learn_from_report,
)
from ygotrainingbot.policy_validation import validate_protagonist_policy_update
from ygotrainingbot.progress import should_stop_on_regression


def test_samples_from_game_log_include_outcome_flags() -> None:
    payload = {
        "meta": {"goes_first": "bot-01", "home_bot_id": "bot-01", "away_bot_id": "bot-02"},
        "result": {"winner": "bot-01", "loser": "bot-02", "turns": 9},
        "traces": [
            {
                "agent": "bot-01",
                "turn": 1,
                "selected_action": "attack-0",
                "selected_label": "Attack",
                "selected_tags": ["attack"],
            },
            {
                "agent": "bot-02",
                "turn": 2,
                "selected_action": "decline-effect",
                "selected_label": "Decline",
                "selected_tags": ["decline"],
            },
        ],
    }
    samples = samples_from_game_log_payload(payload)
    yugi = next(sample for sample in samples if sample["agent"] == "bot-01")
    joey = next(sample for sample in samples if sample["agent"] == "bot-02")
    assert yugi["game_won"] is True
    assert yugi["bot_goes_first"] is True
    assert yugi["game_turns"] == 9
    assert joey["game_won"] is False


def test_outcome_weight_adjustments_boost_winning_tags() -> None:
    samples = [
        {"game_won": True, "selected_tags": ["attack"]},
        {"game_won": True, "selected_tags": ["attack"]},
        {"game_won": True, "selected_tags": ["attack"]},
        {"game_won": False, "selected_tags": ["phase"]},
        {"game_won": False, "selected_tags": ["phase"]},
        {"game_won": False, "selected_tags": ["phase"]},
    ]
    adjustments = _outcome_weight_adjustments(samples)
    assert adjustments["attack"] > 0
    assert adjustments["phase"] < 0


def test_outcome_weight_adjustments_ignore_state_descriptor_tags() -> None:
    samples = [
        {"game_won": True, "selected_tags": ["damage:3000", "opp-lp:700", "lethal"]},
        {"game_won": True, "selected_tags": ["damage:0", "lp-swing:0", "lethal"]},
        {"game_won": True, "selected_tags": ["damage:2800", "lethal", "removal"]},
        {"game_won": True, "selected_tags": ["removal"]},
        {"game_won": True, "selected_tags": ["removal"]},
        {"game_won": True, "selected_tags": ["direct-attack"]},
        {"game_won": True, "selected_tags": ["direct-attack"]},
        {"game_won": True, "selected_tags": ["direct-attack"]},
        {"game_won": False, "selected_tags": ["damage:3000", "phase"]},
        {"game_won": False, "selected_tags": ["damage:0", "phase"]},
        {"game_won": False, "selected_tags": ["damage:2800", "phase"]},
    ]
    adjustments = _outcome_weight_adjustments(samples)
    assert "damage:3000" not in adjustments
    assert "damage:0" not in adjustments
    assert "opp-lp:700" not in adjustments
    assert "lp-swing:0" not in adjustments
    assert adjustments["lethal"] > 0
    assert adjustments["removal"] > 0
    assert adjustments["direct-attack"] > 0
    assert adjustments["phase"] < 0


def test_update_policy_drops_state_descriptor_weights() -> None:
    previous = LearnedPolicy(
        tag_weights={"damage:0": 2.0, "removal": 1.0, "opp-lp:300": -1.5},
        observations=10,
    )
    analysis = {
        "total_games": 1,
        "draws": 0,
        "total_decisions": 10,
        "top_tags": [("removal", 5)],
        "mistake_adjustments": {},
        "outcome_adjustments": {},
    }
    learned = _update_policy(previous, analysis)
    assert "damage:0" not in learned.tag_weights
    assert "opp-lp:300" not in learned.tag_weights
    assert learned.tag_weights["removal"] > 1.0


def test_why_best_line_samples_include_card_labels_for_select_card() -> None:
    evaluation = (
        "selected_score=240.00; top_alternatives=["
        "{'action_id': 'select-card-0', 'label': 'Select Ash Blossom & Joyous Spring', 'score': 240.0, 'tags': ['select-card', 'spell']}, "
        "{'action_id': 'select-card-1', 'label': 'Select Maxx \"C\"', 'score': 30.0, 'tags': ['select-card', 'spell']}"
        "]"
    )
    samples = [
        {
            "duel_turn": 3,
            "decision_index": 13,
            "summary": "EDOPro select_card decision",
            "agent": "bot-a",
            "selected_action": "select-card-0",
            "selected_label": "Select Ash Blossom & Joyous Spring",
            "selected_tags": ["select-card", "spell"],
            "evaluation": evaluation,
        }
    ]
    insights = _why_best_line_samples(samples)
    assert len(insights) == 1
    assert "select-card-0" in insights[0]
    assert "Ash Blossom" in insights[0]
    assert "select-card-1" in insights[0]
    assert "Maxx" in insights[0]


def test_why_best_line_samples_accepts_small_lethal_gaps() -> None:
    evaluation = (
        "selected_score=20828.80; top_alternatives=["
        "{'action_id': 'attack-0', 'label': 'Direct attack', 'score': 20828.8, 'tags': ['attack', 'lethal', 'direct-attack']}, "
        "{'action_id': 'attack-1', 'label': 'Direct attack alt', 'score': 20824.8, 'tags': ['attack', 'lethal', 'direct-attack']}"
        "]"
    )
    samples = [
        {
            "turn": 4,
            "duel_turn": 4,
            "decision_index": 154,
            "summary": "Battle phase",
            "agent": "bot-a",
            "selected_action": "attack-0",
            "selected_tags": ["attack", "lethal", "direct-attack"],
            "evaluation": evaluation,
        }
    ]
    insights = _why_best_line_samples(samples)
    assert len(insights) == 1
    assert "duel turn 4" in insights[0]
    assert "attack-0" in insights[0]
    assert "attack-1" in insights[0]


def test_is_learnable_policy_tag() -> None:
    assert is_learnable_policy_tag("removal")
    assert not is_learnable_policy_tag("damage:3000")
    assert not is_learnable_policy_tag("opp-lp:700")
    assert not is_learnable_policy_tag("monster")


def test_update_policy_scales_league_learning() -> None:
    previous = LearnedPolicy(tag_weights={"attack": 1.0}, observations=10)
    analysis = {
        "total_games": 1,
        "draws": 0,
        "total_decisions": 10,
        "top_tags": [("attack", 5)],
        "mistake_adjustments": {},
        "outcome_adjustments": {},
    }
    full = _update_policy(previous, analysis, update_scale=1.0)
    partial = _update_policy(previous, analysis, update_scale=0.35)
    assert partial.tag_weights["attack"] < full.tag_weights["attack"]


def test_learn_from_report_applies_outcome_weights(tmp_path: Path) -> None:
    game = tmp_path / "game.json"
    game.write_text(
        json.dumps(
            {
                "meta": {"goes_first": "bot-01"},
                "result": {"winner": "bot-01"},
                "traces": [
                    {
                        "agent": "bot-01",
                        "turn": 1,
                        "selected_action": "attack-0",
                        "selected_label": "Attack",
                        "selected_tags": ["attack"],
                    }
                ]
                * 6
                + [
                    {
                        "agent": "bot-01",
                        "turn": 2,
                        "selected_action": "to-end-phase",
                        "selected_label": "End",
                        "selected_tags": ["phase"],
                    }
                ]
                * 6,
            }
        ),
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "format": "test",
                "games": 2,
                "draws": 0,
                "bot_agent": "bot-01",
                "game_log_paths": [str(game)],
            }
        ),
        encoding="utf-8",
    )
    policy_path = tmp_path / "policy.json"
    learn_from_report(report_path, policy_path)
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    assert payload["tag_weights"]["attack"] > 0


def test_validate_protagonist_policy_accepts_when_candidate_wins() -> None:
    from ygotrainingbot.format_training import FormatDeck
    from ygotrainingbot.league_tournament import BotSeasonState

    deck = FormatDeck(name="test", archetype="test", source="test", main=(1, 2, 3))
    protagonist = BotSeasonState(
        bot_id="bot-01",
        name="Yugi",
        policy="control",
        characteristics="",
        policy_path=Path("candidate.json"),
        archetype="test",
        pack_path=Path("pack.json"),
        deck=deck,
    )
    opponent = BotSeasonState(
        bot_id="bot-02",
        name="Joey",
        policy="tempo",
        characteristics="",
        policy_path=Path("opponent.json"),
        archetype="test",
        pack_path=Path("pack.json"),
        deck=deck,
    )

    def fake_play(_gateway: str, **kwargs) -> dict[str, object]:
        first = kwargs["first_agent"]
        second = kwargs["second_agent"]
        if first == "bot-01":
            protag_weights = kwargs["first_weights"]
        elif second == "bot-01":
            protag_weights = kwargs["second_weights"]
        else:
            return {"wins_by_agent": {"bot-02": 1}}
        winner = "bot-01" if str(protag_weights) == "candidate.json" else "bot-02"
        return {"wins_by_agent": {winner: 1}}

    result = validate_protagonist_policy_update(
        protagonist=protagonist,
        opponents=[opponent],
        backup_weights=Path("backup.json"),
        candidate_weights=Path("candidate.json"),
        play_duel=fake_play,
        gateway_command="node gateway.mjs",
        games_per_matchup=2,
        timeout_seconds=30.0,
        format_name="test",
    )
    assert result["accepted"] is True
    assert result["candidate_wins"] >= result["baseline_wins"]


def test_should_stop_on_regression() -> None:
    progress = {
        "cycles": [
            {"status": "ok", "series_win_rate": 0.6},
            {"status": "ok", "series_win_rate": 0.5},
        ]
    }
    stop, reason = should_stop_on_regression(progress, tolerance=0.05)
    assert stop is True
    assert reason is not None
