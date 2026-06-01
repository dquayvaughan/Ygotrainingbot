"""Representative topping-style deck shells keyed by archetype name."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ygotrainingbot.deck_composition import (
    EXTRA_MAX,
    KNOWN_EXTRA_MONSTER_IDS,
    MAIN_MAX,
    MAIN_MIN,
    SIDE_MAX,
    build_side_deck,
    extra_staples_for_era,
    main_staples_for_era,
    normalize_deck_dict,
    pad_zone,
    side_staples_for_era,
)
# Era staple pools — main-only for padding main deck (never put extra-deck monsters in main).
STAPLES_EDISON = main_staples_for_era(modern=False)
STAPLES_SYNCHRO = STAPLES_EDISON
STAPLES_MODERN = main_staples_for_era(modern=True)

# Signature cards: (card_id, copies). Extra is optional per archetype.
ARCHETYPE_SIGNATURES: dict[str, dict[str, Any]] = {
    "Quickdraw Dandywarrior": {
        "signatures": (
            (20932152, 3), (15341821, 2), (48686504, 2), (14943837, 2),
            (21502796, 2), (5220687, 2), (85087012, 1), (71564252, 1),
            (84290642, 1), (11819616, 1),
        ),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094, 7391448, 23693634, 26593852, 60800381, 73580471),
    },
    "Frog Monarch": {
        "signatures": (
            (9126351, 3), (46239604, 3), (1357146, 2), (20663556, 3),
            (93369354, 1), (73125233, 2), (22123627, 3), (98045062, 2),
        ),
        "staples": STAPLES_SYNCHRO,
        "extra": (52687916, 50321796, 26593852, 44508094, 7391448, 23693634, 76774528, 73580471),
    },
    "Machina Gadget": {
        "signatures": (
            (13803966, 3), (70278545, 3), (4294110, 3), (4294110, 0),
            (31560081, 2), (63749102, 2), (23205979, 1), (34853266, 2),
        ),
        "staples": STAPLES_EDISON,
        "extra": (50321796, 52687916, 44508094, 7391448, 26593852),
    },
    "X-Saber": {
        "signatures": (
            (90640901, 3), (42308877, 2), (53804307, 2), (44519536, 2),
            (26489347, 2), (33198837, 1), (26489347, 0),
        ),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094, 7391448, 26593852, 23693634),
    },
    "Gravekeeper": {
        "signatures": (
            (81954330, 3), (38033191, 3), (24306, 2), (24306, 0),
            (58482831, 2), (58482831, 0), (14883228, 2), (14883228, 0),
        ),
        "staples": STAPLES_EDISON,
        "extra": (),
    },
    "Plant Synchro": {
        "signatures": (
            (33171767, 3), (33171767, 0), (48654267, 2), (48654267, 0),
            (91110378, 2), (91110378, 0), (15341821, 2), (48686504, 2),
        ),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094, 7391448, 26593852, 23693634, 60800381),
    },
    "Blackwing": {
        "signatures": (
            (19036354, 3), (19036354, 0), (22859805, 3), (22859805, 0),
            (72359851, 2), (72359851, 0), (55343236, 1),
        ),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094, 7391448, 26593852),
    },
    "Goat Control": {"reference": "goat-2005", "archetype": "Goat Control"},
    "Chaos Warrior": {"reference": "goat-2005", "archetype": "Chaos Warrior"},
    "Agent Fairy": {
        "signatures": ((56433456, 2), (91110378, 0), (64734920, 3), (64734920, 0), (32854013, 2)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094),
    },
    "Six Samurai": {
        "signatures": ((49702399, 3), (49702399, 0), (29981986, 2), (29981986, 0), (53610683, 2)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916),
    },
    "T.G. Stun": {
        "signatures": ((30308789, 3), (30308789, 0), (9742784, 2), (9742784, 0), (5821478, 2)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094),
    },
    "Karakuri": {
        "signatures": ((62340868, 3), (62340868, 0), (62340868, 0), (62340868, 0), (51100308, 2)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916),
    },
    "HERO Beat": {
        "signatures": ((89943723, 2), (612115, 2), (612115, 0), (35836417, 2), (22061412, 2)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916),
    },
    "Dino Rabbit": {
        "signatures": ((80925836, 3), (80925836, 0), (41777209, 2), (41777209, 0), (41777209, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094),
    },
    "Inzektor": {
        "signatures": ((66220810, 3), (66220810, 0), (66220810, 0), (90311554, 2), (90311554, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094, 7391448),
    },
    "Wind-Up": {
        "signatures": ((80925836, 0), (64734920, 0), (58577036, 3), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094),
    },
    "Geargia": {
        "signatures": ((76843130, 3), (76843130, 0), (76843130, 0), (76843130, 0), (76843130, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916),
    },
    "Mermail": {
        "signatures": ((22702034, 3), (22702034, 0), (46411259, 0), (46411259, 0), (71921856, 2)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094, 7391448),
    },
    "Chaos Dragon": {
        "signatures": ((9596126, 2), (9596126, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094, 7391448, 26593852),
    },
    "Dragon Ruler": {
        "signatures": ((89333528, 2), (89333528, 0), (89333528, 0), (89333528, 0), (89333528, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094, 7391448, 26593852, 23693634),
    },
    "Spellbook": {
        "signatures": ((21593977, 0), (21593977, 0), (21593977, 0), (21593977, 0), (21593977, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916),
    },
    "Evilswarm": {
        "signatures": ((9128265, 3), (9128265, 0), (9128265, 0), (9128265, 0), (9128265, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916),
    },
    "Fire Fist": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094),
    },
    "Bujin": {
        "signatures": ((58488461, 3), (58488461, 0), (58488461, 0), (58488461, 0), (58488461, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094),
    },
    "Shaddoll": {
        "signatures": ((6417578, 3), (6417578, 0), (6417578, 0), (6417578, 0), (6417578, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094, 7391448),
    },
    "Burning Abyss": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916),
    },
    "HAT": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916),
    },
    "Satellarknight": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916),
    },
    "Nekroz": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094),
    },
    "Qliphort": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916),
    },
    "HERO": {
        "signatures": ((612115, 2), (612115, 0), (35836417, 2), (22061412, 2), (89943723, 2)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916),
    },
    "Kozmo": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Ritual Beast": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Monarch": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Blue-Eyes": {
        "signatures": ((89631139, 3), (89631139, 0), (89631139, 0), (89631139, 0), (89631139, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094, 7391448),
    },
    "Pendulum Magician": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094),
    },
    "ABC": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Mermail Atlantean": {
        "signatures": ((22702034, 3), (22702034, 0), (22702034, 0), (22702034, 0), (71921856, 2)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094),
    },
    "Zoodiac": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "True Draco": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "SPYRAL": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Invoked": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094),
    },
    "Dinosaur": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Paleozoic": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Sky Striker": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Trickstar": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Altergeist": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Thunder Dragon": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094),
    },
    "Gouki": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Orcust": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094),
    },
    "Salamangreat": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Danger Thunder": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094),
    },
    "Eldlich": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Dragon Link": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094, 7391448),
    },
    "Adamancipator": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Invoked Dogmatika": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094),
    },
    "Tri-Brigade": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Drytron": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094),
    },
    "Prank-Kids": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Swordsoul": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094),
    },
    "Branded Despia": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094, 7391448),
    },
    "Tearlaments": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Spright": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Floowandereeze": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Swordsoul Tenyi": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094),
    },
    "Labrynth": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Runick Control": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Kashtira": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Purrely": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Snake-Eye": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094),
    },
    "Rescue-ACE": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Fire King Snake-Eye": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094),
    },
    "Tearlaments Horus": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Ryzeal": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Yubel Fiendsmith": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094),
    },
    "Voiceless Voice": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Memento": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Vanquish Soul": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Dracotail Branded": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094),
    },
    "Yummy": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Ryzeal Fiendsmith": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916, 44508094),
    },
    "Mathmech": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Phantom Knights": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Infernity": {
        "signatures": ((33198884, 3), (64988958, 2), (64988958, 0), (64988958, 0), (64988958, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916, 44508094),
    },
    "Infernoid": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_MODERN,
        "extra": (50321796, 52687916),
    },
    "Dark World": {
        "signatures": ((58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0), (58577036, 0)),
        "staples": STAPLES_SYNCHRO,
        "extra": (50321796, 52687916),
    },
}

_REFERENCE_CACHE: dict[str, dict[str, dict[str, Any]]] = {}


def _load_reference_pack(repo_root: Path, pack_stem: str) -> dict[str, dict[str, Any]]:
    if pack_stem not in _REFERENCE_CACHE:
        path = repo_root / "configs" / "format-packs" / f"{pack_stem}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        _REFERENCE_CACHE[pack_stem] = {deck["archetype"]: deck for deck in payload["decks"]}
    return _REFERENCE_CACHE[pack_stem]


def _signature_rows(
    archetype: str,
    template: dict[str, Any],
    *,
    repo_root: Path,
) -> tuple[tuple[int, int], ...]:
    rows = template.get("signatures", ())
    if any(int(copies) > 0 for _, copies in rows):
        return tuple((int(card_id), int(copies)) for card_id, copies in rows)
    from ygotrainingbot.ygoprodeck_decks import signature_card_ids

    key_ids = signature_card_ids(archetype, repo_root=repo_root)
    if not key_ids:
        return rows
    return tuple((int(card_id), 3) for card_id in key_ids[:3])


def _synthesize_shell(archetype: str, template: dict[str, Any], *, repo_root: Path) -> dict[str, Any]:
    modern = bool(template.get("modern"))
    main: list[int] = []
    extra_from_sigs: list[int] = []
    for card_id, copies in _signature_rows(archetype, template, repo_root=repo_root):
        if copies <= 0:
            continue
        card_id = int(card_id)
        if card_id in KNOWN_EXTRA_MONSTER_IDS:
            extra_from_sigs.extend([card_id] * int(copies))
        else:
            main.extend([card_id] * int(copies))
    staples = template.get("staples", main_staples_for_era(modern=modern))
    safe_staples = [int(card_id) for card_id in staples if card_id not in KNOWN_EXTRA_MONSTER_IDS]
    pad_zone(main, safe_staples or list(main_staples_for_era(modern=modern)), target=MAIN_MIN, maximum=MAIN_MAX)

    extra = extra_from_sigs + [int(card_id) for card_id in template.get("extra", ())]
    pad_zone(extra, extra_staples_for_era(modern=modern), target=EXTRA_MAX, maximum=EXTRA_MAX)

    side = list(template.get("side", ())) or list(build_side_deck(modern=modern))
    pad_zone(side, side_staples_for_era(modern=modern), target=SIDE_MAX, maximum=SIDE_MAX)

    payload = {
        "name": f"{archetype} representative top shell",
        "archetype": archetype,
        "source": f"Representative {archetype} topping shell for training; normalized to card IDs.",
        "main": main,
        "extra": extra,
        "side": side,
    }
    return normalize_deck_dict(payload, modern=modern)


def build_deck_shell(
    archetype: str,
    *,
    repo_root: Path,
    period_id: str | None = None,
    modern: bool = False,
) -> dict[str, Any]:
    from ygotrainingbot.banlist_catalog import banlist_periods
    from ygotrainingbot.ygoprodeck_decks import DEFAULT_CACHE_PATH, load_deck_cache

    template = ARCHETYPE_SIGNATURES.get(archetype)
    if template is None:
        template = {"signatures": ((58577036, 3),), "staples": STAPLES_MODERN, "extra": ()}

    period = None
    if period_id is not None:
        period = next((item for item in banlist_periods() if item.period_id == period_id), None)
        if period is not None:
            modern = period.year >= 2017
    cache_path = repo_root / DEFAULT_CACHE_PATH
    cache = load_deck_cache(cache_path)
    if archetype in cache:
        cached = dict(cache[archetype])
        cached["archetype"] = archetype
        from ygotrainingbot.ygoprodeck_decks import trusted_cache_entry

        if trusted_cache_entry(archetype, cached, cache=cache, repo_root=repo_root):
            return normalize_deck_dict(
                cached,
                modern=modern,
                require_side=False,
                pad_zones=False,
            )

    if "reference" in template:
        pack = _load_reference_pack(repo_root, str(template["reference"]))
        ref_name = str(template.get("archetype", archetype))
        if ref_name in pack:
            return normalize_deck_dict(dict(pack[ref_name]), modern=False)
    return _synthesize_shell(archetype, template, repo_root=repo_root)


def build_period_decks(
    archetypes: tuple[str, ...],
    *,
    repo_root: Path,
    period_id: str | None = None,
    modern: bool = False,
) -> list[dict[str, Any]]:
    decks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for archetype in archetypes:
        if archetype in seen:
            continue
        seen.add(archetype)
        decks.append(
            build_deck_shell(
                archetype,
                repo_root=repo_root,
                period_id=period_id,
                modern=modern,
            )
        )
    return decks
