"""TCG banlist periods (2010–present) and top-5 meta deck assignments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# Each period: id, year, month, human label
BANLIST_PERIODS: tuple[tuple[str, int, int, str], ...] = (
    ("2010-03", 2010, 3, "March 2010 (Edison)"),
    ("2010-09", 2010, 9, "September 2010"),
    ("2011-03", 2011, 3, "March 2011"),
    ("2011-09", 2011, 9, "September 2011"),
    ("2012-03", 2012, 3, "March 2012"),
    ("2012-09", 2012, 9, "September 2012"),
    ("2013-03", 2013, 3, "March 2013"),
    ("2013-09", 2013, 9, "September 2013"),
    ("2014-04", 2014, 4, "April 2014"),
    ("2014-10", 2014, 10, "October 2014"),
    ("2015-04", 2015, 4, "April 2015"),
    ("2015-11", 2015, 11, "November 2015"),
    ("2016-04", 2016, 4, "April 2016"),
    ("2016-10", 2016, 10, "October 2016"),
    ("2017-04", 2017, 4, "April 2017"),
    ("2017-10", 2017, 10, "October 2017"),
    ("2018-05", 2018, 5, "May 2018"),
    ("2018-12", 2018, 12, "December 2018"),
    ("2019-07", 2019, 7, "July 2019"),
    ("2020-01", 2020, 1, "January 2020"),
    ("2020-04", 2020, 4, "April 2020"),
    ("2020-09", 2020, 9, "September 2020"),
    ("2021-05", 2021, 5, "May 2021"),
    ("2021-10", 2021, 10, "October 2021"),
    ("2022-04", 2022, 4, "April 2022"),
    ("2022-10", 2022, 10, "October 2022"),
    ("2023-05", 2023, 5, "May 2023"),
    ("2023-11", 2023, 11, "November 2023"),
    ("2024-05", 2024, 5, "May 2024"),
    ("2024-11", 2024, 11, "November 2024"),
    ("2025-05", 2025, 5, "May 2025"),
    ("2026-01", 2026, 1, "January 2026"),
)

# Top-5 competitive archetypes per banlist period (TCG meta snapshots).
TOP5_BY_PERIOD: dict[str, tuple[str, ...]] = {
    "2010-03": ("Quickdraw Dandywarrior", "Frog Monarch", "Machina Gadget", "X-Saber", "Gravekeeper"),
    "2010-09": ("Quickdraw Dandywarrior", "Frog Monarch", "Plant Synchro", "Blackwing", "X-Saber"),
    "2011-03": ("Agent Fairy", "Six Samurai", "T.G. Stun", "Karakuri", "HERO Beat"),
    "2011-09": ("Agent Fairy", "Six Samurai", "Dino Rabbit", "Karakuri", "T.G. Stun"),
    "2012-03": ("Dino Rabbit", "Inzektor", "Wind-Up", "Hero Beat", "Geargia"),
    "2012-09": ("Wind-Up", "Dino Rabbit", "Inzektor", "Mermail", "Chaos Dragon"),
    "2013-03": ("Dragon Ruler", "Spellbook", "Evilswarm", "Mermail", "Fire Fist"),
    "2013-09": ("Dragon Ruler", "Mermail", "Fire Fist", "Bujin", "Geargia"),
    "2014-04": ("Shaddoll", "Burning Abyss", "HAT", "Bujin", "Satellarknight"),
    "2014-10": ("Shaddoll", "Burning Abyss", "Satellarknight", "HAT", "Nekroz"),
    "2015-04": ("Nekroz", "Qliphort", "Burning Abyss", "Shaddoll", "HERO"),
    "2015-11": ("Nekroz", "Qliphort", "Kozmo", "Burning Abyss", "Ritual Beast"),
    "2016-04": ("Monarch", "Kozmo", "Blue-Eyes", "Pendulum Magician", "ABC"),
    "2016-10": ("ABC", "Pendulum Magician", "Kozmo", "Monarch", "Mermail Atlantean"),
    "2017-04": ("Zoodiac", "True Draco", "SPYRAL", "Pendulum Magician", "Invoked"),
    "2017-10": ("Zoodiac", "True Draco", "SPYRAL", "Invoked", "Dinosaur"),
    "2018-05": ("Sky Striker", "Trickstar", "Altergeist", "Thunder Dragon", "Gouki"),
    "2018-12": ("Sky Striker", "Orcust", "Thunder Dragon", "Altergeist", "Gouki"),
    "2019-07": ("Salamangreat", "Orcust", "Sky Striker", "Thunder Dragon", "Danger Thunder"),
    "2020-01": ("Salamangreat", "Orcust", "Sky Striker", "Altergeist", "True Draco"),
    "2020-04": ("Eldlich", "Salamangreat", "Dragon Link", "Adamancipator", "Altergeist"),
    "2020-09": ("Eldlich", "Dragon Link", "Salamangreat", "Invoked Dogmatika", "Adamancipator"),
    "2021-05": ("Tri-Brigade", "Drytron", "Prank-Kids", "Swordsoul", "Eldlich"),
    "2021-10": ("Tri-Brigade", "Drytron", "Swordsoul", "Branded Despia", "Prank-Kids"),
    "2022-04": ("Tearlaments", "Branded Despia", "Spright", "Floowandereeze", "Swordsoul Tenyi"),
    "2022-10": ("Tearlaments", "Branded Despia", "Spright", "Labrynth", "Runick Control"),
    "2023-05": ("Kashtira", "Tearlaments", "Branded Despia", "Labrynth", "Purrely"),
    "2023-11": ("Kashtira", "Labrynth", "Snake-Eye", "Branded Despia", "Rescue-ACE"),
    "2024-05": ("Snake-Eye", "Ryzeal", "Fire King Snake-Eye", "Tearlaments Horus", "Labrynth"),
    "2024-11": ("Ryzeal", "Snake-Eye", "Yubel Fiendsmith", "Fire King Snake-Eye", "Voiceless Voice"),
    "2025-05": ("Ryzeal", "Memento", "Vanquish Soul", "Dracotail Branded", "Yummy"),
    "2026-01": ("Ryzeal", "Memento", "Yummy", "Dracotail Branded", "Vanquish Soul"),
}

# Which static pack supplies banlist card-ID metadata for a period.
BANLIST_INHERIT: dict[str, str] = {
    "2010-03": "edison-2010",
    "2010-09": "edison-2010",
    "2011-03": "edison-2010",
    "2011-09": "edison-2010",
}


@dataclass(frozen=True, slots=True)
class BanlistPeriod:
    period_id: str
    year: int
    month: int
    label: str
    top5: tuple[str, ...]
    inherit_pack: str | None

    @property
    def sort_key(self) -> int:
        return self.year * 100 + self.month

    @property
    def pack_name(self) -> str:
        return f"banlist-{self.period_id}"


def banlist_periods() -> list[BanlistPeriod]:
    items: list[BanlistPeriod] = []
    for period_id, year, month, label in BANLIST_PERIODS:
        top5 = TOP5_BY_PERIOD.get(period_id)
        if top5 is None:
            raise KeyError(f"missing top-5 deck list for banlist period {period_id!r}")
        inherit = BANLIST_INHERIT.get(period_id)
        if inherit is None and year <= 2011:
            inherit = "edison-2010"
        items.append(
            BanlistPeriod(
                period_id=period_id,
                year=year,
                month=month,
                label=label,
                top5=top5,
                inherit_pack=inherit,
            )
        )
    return items


def top5_for_period(period_id: str) -> Sequence[str]:
    return TOP5_BY_PERIOD[period_id]
