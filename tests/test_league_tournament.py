import json
import random
from pathlib import Path

from ygotrainingbot.format_training import FormatDeck
from ygotrainingbot.league_tournament import (
    Bo3SeriesResult,
    apply_post_season_learning,
    BotSeasonState,
    compute_standings,
    generate_duel_seed,
    merge_training_reports,
    pick_first_player_for_game,
    play_bo3_series,
    resolve_year_deck,
    run_season_year,
    run_yearly_bracket_tournament,
)


def test_play_bo3_series_first_to_two_wins() -> None:
    calls = {"count": 0}
    seeds: list[tuple[int, ...]] = []

    def fake_play_duel(*_args, **kwargs):
        calls["count"] += 1
        seeds.append(kwargs["seed"])
        winner = kwargs["first_agent"] if calls["count"] % 2 == 1 else kwargs["second_agent"]
        return {"games": 1, "draws": 0, "wins_by_agent": {winner: 1}, "decision_samples": []}

    deck = FormatDeck(name="d", archetype="a", source=None, main=(1,) * 40)
    home = type(
        "S",
        (),
        {
            "bot_id": "bot-a",
            "name": "A",
            "policy": "aggressive",
            "policy_path": Path("a.json"),
            "deck": deck,
            "archetype": "test",
        },
    )()
    away = type(
        "S",
        (),
        {
            "bot_id": "bot-b",
            "name": "B",
            "policy": "control",
            "policy_path": Path("b.json"),
            "deck": deck,
            "archetype": "test",
        },
    )()

    series = play_bo3_series(
        play_duel=fake_play_duel,
        gateway_command="cmd",
        home=home,
        away=away,
        timeout_seconds=1.0,
        format_name="test",
        rng=random.Random(7),
    )
    assert series.series_winner in {"bot-a", "bot-b"}
    assert series.home_series_wins + series.away_series_wins == 2
    assert series.games_played >= 2
    assert len({seed for seed in seeds}) == series.games_played


def test_pick_first_player_follows_bo3_rules() -> None:
    rng = random.Random(1)
    first = pick_first_player_for_game(1, home_bot_id="a", away_bot_id="b", previous_game_loser=None, rng=rng)
    assert first in {"a", "b"}
    second = pick_first_player_for_game(2, home_bot_id="a", away_bot_id="b", previous_game_loser="a", rng=random.Random(2))
    assert second in {"a", "b"}


def test_resolve_year_deck_uses_distinct_2010_assignments() -> None:
    ethan_pack, ethan_deck, ethan_arch = resolve_year_deck("bot-01", 2010, {})
    marcus_pack, marcus_deck, _ = resolve_year_deck("bot-02", 2010, {})
    tyler_pack, tyler_deck, _ = resolve_year_deck("bot-04", 2010, {})
    assert ethan_arch == "Frog Monarch"
    assert "Frog Monarch" in ethan_deck.name
    assert "Machina" not in ethan_deck.name or ethan_pack != marcus_pack or ethan_deck.name != marcus_deck.name
    assert "Quickdraw" in tyler_deck.name
    assert ethan_deck.name != tyler_deck.name


def test_compute_standings_ranks_by_series_wins() -> None:
    from ygotrainingbot.league_tournament import BotSeasonState

    deck = FormatDeck(name="d", archetype="a", source=None, main=(1,) * 40)
    bots = [
        BotSeasonState("bot-a", "A", "aggressive", "", Path("a.json"), "x", Path("p"), deck),
        BotSeasonState("bot-b", "B", "control", "", Path("b.json"), "y", Path("p"), deck),
    ]
    series = [
        Bo3SeriesResult("bot-a", "bot-b", "bot-a", 2, 0, 2, ({"wins_by_agent": {"bot-a": 1}, "draws": 0},)),
        Bo3SeriesResult("bot-b", "bot-a", "bot-a", 0, 2, 2, ({"wins_by_agent": {"bot-a": 1}, "draws": 0},)),
    ]
    standings = compute_standings(bots, series)
    assert standings[0]["bot_id"] == "bot-a"
    assert standings[0]["series_wins"] == 2


def test_run_yearly_bracket_tournament_smoke(tmp_path: Path) -> None:
    profiles = [
        {
            "bot_id": "bot-01",
            "name": "Yugi",
            "policy": "control",
            "characteristics": "underdog",
            "initial_weights": {"attack": 0.5},
        },
        {
            "bot_id": "bot-02",
            "name": "Joey",
            "policy": "tempo",
            "characteristics": "tempo",
            "initial_weights": {"attack": 1.0},
        },
    ]

    def fake_play_duel(*_args, **kwargs):
        first = kwargs["first_agent"]
        return {
            "games": 1,
            "draws": 0,
            "traced_decisions": 1,
            "wins_by_agent": {first: 1},
            "tags": {"attack": 1},
            "action_counts": {},
            "decision_samples": [{"agent": first, "selected_tags": ["attack"]}],
            "duel_seed": list(kwargs["seed"]),
        }

    learned: list[str] = []

    def fake_learn(report_path: Path, policy_path: Path):
        learned.append(f"{report_path.name}:{policy_path.name}")
        policy_path.write_text(
            json.dumps({"tag_weights": {"attack": 1.5}, "observations": 2}) + "\n",
            encoding="utf-8",
        )
        return {}, "ok"

    report = run_yearly_bracket_tournament(
        profiles=profiles,
        output_dir=tmp_path / "bracket",
        start_year=2010,
        end_year=2010,
        series_per_opponent=1,
        ethan_bot_id="bot-01",
        play_duel=fake_play_duel,
        build_gateway_command=lambda: "cmd",
        materialize_pack=lambda _p, _d, dest: dest,
        learn_fn=fake_learn,
        combine_weights_fn=lambda main, others: {"attack": 1.0},
        write_policy_fn=lambda path, weights: path.write_text(json.dumps({"tag_weights": weights}) + "\n"),
        load_policy_weights_fn=lambda _path: {"attack": 1.0},
        write_initial_policy_fn=lambda path, weights: path.write_text(
            json.dumps({"tag_weights": weights, "observations": 0}) + "\n",
            encoding="utf-8",
        ),
        timeout_seconds=1.0,
    )
    assert report["seasons"][0]["year"] == 2010
    assert (tmp_path / "bracket" / "2010" / "bracket-results.json").exists()
    assert (tmp_path / "bracket" / "2010" / "standings.md").exists()
    assert any("league-wide-report" in item for item in learned)


def test_merge_training_reports_aggregates_games() -> None:
    merged = merge_training_reports(
        [
            {"games": 1, "draws": 0, "traced_decisions": 2, "wins_by_agent": {"a": 1}, "tags": {"x": 1}},
            {"games": 1, "draws": 1, "traced_decisions": 3, "wins_by_agent": {"b": 1}, "tags": {"y": 2}},
        ],
        format_name="season",
    )
    assert merged["games"] == 2
    assert merged["draws"] == 1
    assert merged["traced_decisions"] == 5


def test_merge_training_reports_preserves_bot_agent() -> None:
    merged = merge_training_reports(
        [{"games": 1, "draws": 0, "traced_decisions": 1, "wins_by_agent": {"bot-01": 1}, "tags": {}}],
        format_name="season-2010:bot-01",
        bot_agent="bot-01",
    )
    assert merged["bot_agent"] == "bot-01"


def test_apply_post_season_learning_promotes_protagonist(tmp_path: Path) -> None:
    from ygotrainingbot.policy_runtime import backup_policy, raw_tag_weights, should_accept_policy_update, write_policy_file

    bot_dir = tmp_path / "bots" / "bot-01"
    bot_dir.mkdir(parents=True)
    policy_path = bot_dir / "policy.json"
    write_policy_file(policy_path, {"attack": 0.5, "removal": 1.0}, observations=500, parent_observations=100)

    other_dir = tmp_path / "bots" / "bot-02"
    other_dir.mkdir(parents=True)
    other_policy = other_dir / "policy.json"
    write_policy_file(other_policy, {"attack": 2.0, "removal": 3.0}, observations=50)

    deck = FormatDeck(name="deck", archetype="test", source="test", main=(1,) * 40)
    bots = [
        BotSeasonState(
            bot_id="bot-01",
            name="Yugi",
            policy="heuristic",
            characteristics="",
            policy_path=policy_path,
            archetype="test",
            pack_path=tmp_path / "pack.json",
            deck=deck,
        ),
        BotSeasonState(
            bot_id="bot-02",
            name="Joey",
            policy="heuristic",
            characteristics="",
            policy_path=other_policy,
            archetype="test",
            pack_path=tmp_path / "pack2.json",
            deck=FormatDeck(name="deck2", archetype="test", source="test", main=(1,) * 40),
        ),
    ]

    bot_report = merge_training_reports(
        [
            {
                "games": 10,
                "draws": 0,
                "traced_decisions": 100,
                "wins_by_agent": {"bot-01": 6},
                "tags": {"attack": 50, "removal": 20},
                "action_counts": {"attack-0": 10},
                "decision_samples": [],
            }
        ],
        format_name="season-2010:bot-01",
        bot_agent="bot-01",
    )

    def fake_learn(report_path: Path, learn_policy_path: Path) -> tuple[dict, str]:
        weights = raw_tag_weights(learn_policy_path)
        weights["attack"] = weights.get("attack", 0.0) + 1.5
        write_policy_file(learn_policy_path, weights, observations=650)
        return {}, "ok"

    summary = apply_post_season_learning(
        year=2010,
        bots=bots,
        league_report={"games": 20, "traced_decisions": 200, "tags": {"attack": 80}, "wins_by_agent": {}},
        bot_reports={"bot-01": bot_report, "bot-02": bot_report},
        ethan_bot_id="bot-01",
        learn_fn=fake_learn,
        combine_weights_fn=lambda main, others: {"attack": 1.25, "removal": 2.0},
        write_policy_fn=lambda path, weights: write_policy_file(path, weights),
        load_policy_weights_fn=lambda path: raw_tag_weights(path),
        backup_policy_fn=backup_policy,
        accept_policy_update_fn=should_accept_policy_update,
    )

    protagonist = summary["bots"]["bot-01"]
    assert protagonist["promoted"] is True
    assert protagonist["reverted"] is False
    assert protagonist["weight_delta"]["tags_changed"] > 0
    final = raw_tag_weights(policy_path)
    assert final["attack"] > 0.5


def test_generate_duel_seed_is_unique_per_call() -> None:
    rng = random.Random(99)
    assert generate_duel_seed(rng) != generate_duel_seed(rng)
