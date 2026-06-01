"""Resolve persistent training/runtime directories for local and cloud deploys."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TrainingPaths:
    """Locations for jobs, policies, EDOPro cache, and bracket outputs."""

    repo_root: Path
    data_dir: Path
    jobs_dir: Path
    bots_dir: Path
    custom_decks_dir: Path
    learned_policy_path: Path
    edopro_home: Path
    human_catalog_dir: Path
    card_cache_path: Path
    bracket_output_dir: Path

    @classmethod
    def resolve(
        cls,
        repo_root: Path,
        *,
        data_dir: Path | None = None,
        edopro_home: Path | None = None,
        human_catalog_dir: Path | None = None,
        card_cache_path: Path | None = None,
    ) -> TrainingPaths:
        root = repo_root.resolve()
        env_data = os.environ.get("YGOTRAIN_DATA_DIR")
        if data_dir is not None:
            resolved_data = data_dir.resolve()
        elif env_data:
            resolved_data = Path(env_data).resolve()
        else:
            resolved_data = (root / ".ygotrain").resolve()

        centralized = env_data is not None or data_dir is not None
        if human_catalog_dir is not None:
            catalog = human_catalog_dir.resolve()
        elif centralized:
            catalog = resolved_data / "human-duels"
        else:
            catalog = (root / "data" / "human-duels").resolve()

        if card_cache_path is not None:
            card_cache = card_cache_path.resolve()
        elif centralized:
            card_cache = resolved_data / "cards.json"
        else:
            card_cache = (root / "data" / "cards.json").resolve()

        if edopro_home is not None:
            edopro = edopro_home.resolve()
        elif centralized:
            edopro = resolved_data / "edopro-home"
        else:
            edopro = (root / ".ygotrain" / "edopro-home").resolve()

        if centralized:
            bracket_dir = resolved_data / "bracket"
        else:
            bracket_dir = (root / "data").resolve()

        return cls(
            repo_root=root,
            data_dir=resolved_data,
            jobs_dir=resolved_data / "jobs",
            bots_dir=resolved_data / "bots",
            custom_decks_dir=resolved_data / "custom-decks",
            learned_policy_path=resolved_data / "learned-policy.json",
            edopro_home=edopro,
            human_catalog_dir=catalog,
            card_cache_path=card_cache,
            bracket_output_dir=bracket_dir,
        )

    def ensure_directories(self) -> None:
        for path in (
            self.jobs_dir,
            self.bots_dir,
            self.custom_decks_dir,
            self.human_catalog_dir,
            self.bracket_output_dir,
            self.edopro_home,
        ):
            path.mkdir(parents=True, exist_ok=True)
