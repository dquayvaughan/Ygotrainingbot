#!/usr/bin/env node
import createCore, {
  OcgDuelMode,
  OcgLocation,
  OcgMessageType,
  OcgPosition,
  OcgProcessResult,
  OcgResponseType,
  SelectBattleCMDAction,
  SelectIdleCMDAction,
  ocgMessageTypeStrings,
  ocgProcessResultString,
  ocgPositionParse,
  ocgPositionString,
} from "ocgcore-wasm";
import Database from "better-sqlite3";
import { createInterface } from "node:readline/promises";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const DEFAULT_DECK_A = Array(40).fill(1184620); // Hunter Spider, level 4 normal monster
const DEFAULT_DECK_B = Array(40).fill(3134241); // Flying Kamakiri #1, level 4 normal monster

const args = parseArgs(process.argv.slice(2));
const edoproHome = path.resolve(args.edoproHome ?? process.env.EDOPRO_HOME ?? ".");
const maxDecisions = Number(args.maxDecisions ?? 80);
const rl = createInterface({ input: process.stdin, output: process.stdout, terminal: false });
const stdinIterator = rl[Symbol.asyncIterator]();
const scriptCache = new Map();
const scriptLoadStats = { loaded: 0, missing: 0, optional_missing: 0, errors: 0 };

let players = ["bot-a", "bot-b"];
let database;
let lib;
let handle;

async function runDuel() {
  let decisions = 0;
  let winner = null;
  let loser = null;
  let retryQueue = [];
  const lifePoints = [8000, 8000];

  for (let step = 0; step < 10000; step += 1) {
    const status = await lib.duelProcess(handle);
    const messages = lib.duelGetMessage(handle);
    emitLog({
      event: "engine_messages",
      step,
      status: ocgProcessResultString.get(status) ?? status,
      life_points: [...lifePoints],
      messages: safe(messages),
    });
    for (const message of messages) {
      if (message.type === OcgMessageType.WIN) {
        winner = playerName(message.player);
        loser = playerName(message.player === 0 ? 1 : 0);
      } else if (message.type === OcgMessageType.DAMAGE) {
        lifePoints[message.player] -= message.amount;
      } else if (message.type === OcgMessageType.RECOVER) {
        lifePoints[message.player] += message.amount;
      } else if (message.type === OcgMessageType.LPUPDATE) {
        lifePoints[message.player] = message.lp;
      }
    }

    if (status === OcgProcessResult.END) {
      emitResult({ winner, loser, turns: countTurns(messages), decisions, tags: ["edopro", "ocgcore-wasm"], script_stats: scriptLoadStats });
      return;
    }
    if (status === OcgProcessResult.CONTINUE) {
      continue;
    }
    if (status !== OcgProcessResult.WAITING) {
      throw new Error(`Unknown ocgcore process result: ${status}`);
    }

    const selectable = [...messages].reverse().find((message) => legalActionsFor(message, lifePoints).length > 0);
    if (!selectable) {
      if (messages.some((message) => message.type === OcgMessageType.RETRY)) {
        if (retryQueue.length > 0) {
          const retryAction = retryQueue.shift();
          emitLog({ event: "retry_response", action: safe(retryAction) });
          lib.duelSetResponse(handle, retryAction.response);
          continue;
        }
        const adjudicated = adjudicateByLifePoints(lifePoints);
        emitResult({
          winner: adjudicated.winner,
          loser: adjudicated.loser,
          turns: decisions,
          decisions,
          tags: ["edopro", "ocgcore-wasm", "retry-adjudication", adjudicated.tag],
          script_stats: scriptLoadStats,
        });
        return;
      }
      throw new Error(`ocgcore is waiting, but no supported selectable message was emitted: ${JSON.stringify(safe(messages))}`);
    }

    decisions += 1;
    const legalActions = legalActionsFor(selectable, lifePoints);
    const stateId = `ocgcore-${decisions}`;
    emitLog({
      event: "decision_state",
      state_id: stateId,
      selectable_type: ocgMessageTypeStrings.get(selectable.type) ?? selectable.type,
      player: selectable.player,
      legal_action_ids: legalActions.map((action) => action.action_id),
    });
    emit({
      type: "state",
      state: {
        state_id: stateId,
        turn: decisions,
        active_player: playerName(selectable.player ?? 0),
        summary: summarizeMessage(selectable, lifePoints),
        legal_actions: legalActions.map(({ response: _response, ...action }) => action),
        public_zones: visibleZonesFor(selectable.player ?? 0, lifePoints),
      },
    });

    const actionMessage = await readJsonLine();
    const actionIndex = legalActions.findIndex((candidate) => candidate.action_id === actionMessage.action_id);
    const action = legalActions[actionIndex];
    if (!action) {
      throw new Error(`Unknown action_id ${actionMessage.action_id} for state ${stateId}.`);
    }
    retryQueue = legalActions.filter((_candidate, index) => index !== actionIndex);
    emitLog({
      event: "submit_response",
      state_id: stateId,
      requested_action: safe(actionMessage),
      selected_action: safe({ ...action, response: undefined }),
      response: safe(action.response),
    });
    lib.duelSetResponse(handle, action.response);

    if (decisions >= maxDecisions) {
      const adjudicated = adjudicateByLifePoints(lifePoints);
      emitResult({
        winner: adjudicated.winner,
        loser: adjudicated.loser,
        turns: decisions,
        decisions,
        tags: ["edopro", "ocgcore-wasm", "max-decisions", adjudicated.tag],
        script_stats: scriptLoadStats,
      });
      return;
    }
  }

  throw new Error("ocgcore gateway exceeded the hard step limit.");
}

function adjudicateByLifePoints(lifePoints) {
  if (lifePoints[0] > lifePoints[1]) {
    return { winner: playerName(0), loser: playerName(1), tag: "lp-adjudication" };
  }
  if (lifePoints[1] > lifePoints[0]) {
    return { winner: playerName(1), loser: playerName(0), tag: "lp-adjudication" };
  }
  return { winner: null, loser: null, tag: "draw" };
}


function legalActionsFor(message, lifePoints = [8000, 8000]) {
  switch (message.type) {
    case OcgMessageType.SELECT_IDLECMD:
      return idleActions(message, lifePoints);
    case OcgMessageType.SELECT_BATTLECMD:
      return battleActions(message, lifePoints);
    case OcgMessageType.SELECT_CHAIN:
      return chainActions(message);
    case OcgMessageType.SELECT_CARD:
      return message.selects.map((card, index) => ({
        action_id: `select-card-${index}`,
        label: `Select ${cardName(card.code)}`,
        tags: ["select-card", ...effectTagsForCard(card.code)],
        response: { type: OcgResponseType.SELECT_CARD, indicies: [index] },
      }));
    case OcgMessageType.SELECT_UNSELECT_CARD:
      return selectUnselectActions(message);
    case OcgMessageType.SELECT_TRIBUTE:
      return message.selects.map((card, index) => ({
        action_id: `select-tribute-${index}`,
        label: `Tribute ${cardName(card.code)}`,
        tags: ["tribute"],
        response: { type: OcgResponseType.SELECT_TRIBUTE, indicies: [index] },
      }));
    case OcgMessageType.SELECT_POSITION:
      return ocgPositionParse(message.positions).map((position) => ({
        action_id: `position-${position}`,
        label: `Choose ${ocgPositionString.get(position) ?? position}`,
        tags: ["position"],
        response: { type: OcgResponseType.SELECT_POSITION, position },
      }));
    case OcgMessageType.SELECT_OPTION:
      return message.options.map((_option, index) => ({
        action_id: `option-${index}`,
        label: `Choose option ${index + 1}`,
        tags: ["option"],
        response: { type: OcgResponseType.SELECT_OPTION, index },
      }));
    case OcgMessageType.SELECT_PLACE:
      return fieldPlaceActions(message, OcgResponseType.SELECT_PLACE);
    case OcgMessageType.SELECT_DISFIELD:
      return fieldPlaceActions(message, OcgResponseType.SELECT_DISFIELD);
    case OcgMessageType.SELECT_YESNO:
      return [
        { action_id: "yes", label: "Yes", tags: ["yes-no"], response: { type: OcgResponseType.SELECT_YESNO, yes: true } },
        { action_id: "no", label: "No", tags: ["yes-no"], response: { type: OcgResponseType.SELECT_YESNO, yes: false } },
      ];
    case OcgMessageType.SELECT_EFFECTYN: {
      const tags = ["effect", ...effectTagsForCard(message.code)];
      return [
        {
          action_id: "activate-effect",
          label: `Activate ${cardName(message.code)} - ${shortCardText(message.code)}`,
          expected_value: expectedValueForTags(tags),
          tags,
          response: { type: OcgResponseType.SELECT_EFFECTYN, yes: true },
        },
        { action_id: "decline-effect", label: "Do not activate", tags: ["effect", "decline"], response: { type: OcgResponseType.SELECT_EFFECTYN, yes: false } },
      ];
    }
    default:
      return [];
  }
}

function idleActions(message, lifePoints) {
  const actions = [];
  // Put safe phase actions first for baseline agents. More ambitious agents can
  // still choose summons, sets, and activations from the full legal action list.
  if (message.to_ep) {
    actions.push({ action_id: "to-end-phase", label: "Go to End Phase", tags: ["phase"], response: { type: OcgResponseType.SELECT_IDLECMD, action: SelectIdleCMDAction.TO_EP, index: null } });
  }
  if (message.to_bp) {
    actions.push({ action_id: "to-battle-phase", label: "Go to Battle Phase", tags: ["phase"], response: { type: OcgResponseType.SELECT_IDLECMD, action: SelectIdleCMDAction.TO_BP, index: null } });
  }
  message.summons.forEach((card, index) => actions.push({
    action_id: `normal-summon-${index}`,
    label: `Normal Summon ${cardName(card.code)}`,
    tags: ["normal-summon"],
    response: { type: OcgResponseType.SELECT_IDLECMD, action: SelectIdleCMDAction.SELECT_SUMMON, index },
  }));
  message.special_summons.forEach((card, index) => actions.push({
    action_id: `special-summon-${index}`,
    label: `Special Summon ${cardName(card.code)}`,
    tags: ["special-summon"],
    response: { type: OcgResponseType.SELECT_IDLECMD, action: SelectIdleCMDAction.SELECT_SPECIAL_SUMMON, index },
  }));
  message.activates.forEach((card, index) => {
    const tags = ["activate", ...effectTagsForCard(card.code)];
    actions.push({
      action_id: `activate-${index}`,
      label: `Activate ${cardName(card.code)} - ${shortCardText(card.code)}`,
      expected_value: expectedValueForTags(tags),
      tags,
      response: { type: OcgResponseType.SELECT_IDLECMD, action: SelectIdleCMDAction.SELECT_ACTIVATE, index },
    });
  });
  message.monster_sets.forEach((card, index) => {
    const tags = ["set-monster", ...effectTagsForCard(card.code)];
    actions.push({
      action_id: `set-monster-${index}`,
      label: `Set ${cardName(card.code)} - ${shortCardText(card.code)}`,
      expected_value: expectedValueForTags(tags),
      tags,
      response: { type: OcgResponseType.SELECT_IDLECMD, action: SelectIdleCMDAction.SELECT_MONSTER_SET, index },
    });
  });
  message.spell_sets.forEach((card, index) => {
    const tags = ["set-spell", ...effectTagsForCard(card.code)];
    actions.push({
      action_id: `set-spell-${index}`,
      label: `Set ${cardName(card.code)} - ${shortCardText(card.code)}`,
      expected_value: expectedValueForTags(tags),
      tags,
      response: { type: OcgResponseType.SELECT_IDLECMD, action: SelectIdleCMDAction.SELECT_SPELL_SET, index },
    });
  });
  return actions;
}


function battleActions(message, lifePoints) {
  const actions = [];
  if (message.to_ep) {
    actions.push({ action_id: "to-end-phase", label: "Go to End Phase", tags: ["phase"], response: { type: OcgResponseType.SELECT_BATTLECMD, action: SelectBattleCMDAction.TO_EP, index: null } });
  }
  if (message.to_m2) {
    actions.push({ action_id: "to-main-phase-2", label: "Go to Main Phase 2", tags: ["phase"], response: { type: OcgResponseType.SELECT_BATTLECMD, action: SelectBattleCMDAction.TO_M2, index: null } });
  }
  message.attacks.forEach((card, index) => {
    const attack = Math.max(0, cardAttack(card.code));
    const opponent = message.player === 0 ? 1 : 0;
    const damage = card.can_direct ? attack : Math.floor(attack / 2);
    const tags = ["attack", `opp-lp:${lifePoints[opponent]}`];
    if (card.can_direct) {
      tags.push("direct-attack", `damage:${damage}`, `lp-swing:${damage}`);
    }
    if (damage >= lifePoints[opponent]) {
      tags.push("lethal");
    }
    actions.push({
      action_id: `attack-${index}`,
      label: card.can_direct
        ? `Direct attack with ${cardName(card.code)} for ${damage}`
        : `Attack with ${cardName(card.code)}`,
      expected_value: damage / 100,
      tags,
      response: { type: OcgResponseType.SELECT_BATTLECMD, action: SelectBattleCMDAction.SELECT_BATTLE, index },
    });
  });
  message.chains.forEach((card, index) => {
    const tags = ["chain", "battle-chain", ...effectTagsForCard(card.code)];
    actions.push({
      action_id: `battle-chain-${index}`,
      label: `Activate ${cardName(card.code)} - ${shortCardText(card.code)}`,
      expected_value: expectedValueForTags(tags),
      tags,
      response: { type: OcgResponseType.SELECT_BATTLECMD, action: SelectBattleCMDAction.SELECT_CHAIN, index },
    });
  });
  return actions;
}


function selectUnselectActions(message) {
  const actions = [];
  if (message.can_cancel) {
    actions.push({
      action_id: "cancel-selection",
      label: "Cancel selection",
      tags: ["select-unselect", "decline"],
      response: { type: OcgResponseType.SELECT_UNSELECT_CARD, index: null },
    });
  }
  if (message.can_finish) {
    actions.push({
      action_id: "finish-selection",
      label: "Finish selection",
      tags: ["select-unselect", "finish"],
      response: { type: OcgResponseType.SELECT_UNSELECT_CARD, index: null },
    });
  }
  message.select_cards.forEach((card, index) => {
    const tags = ["select-unselect", "select-card", ...effectTagsForCard(card.code)];
    actions.push({
      action_id: `select-unselect-card-${index}`,
      label: `Select ${cardName(card.code)} - ${shortCardText(card.code)}`,
      expected_value: expectedValueForTags(tags),
      tags,
      response: { type: OcgResponseType.SELECT_UNSELECT_CARD, index },
    });
  });
  message.unselect_cards.forEach((card, index) => {
    const responseIndex = message.select_cards.length + index;
    actions.push({
      action_id: `unselect-card-${index}`,
      label: `Unselect ${cardName(card.code)}`,
      tags: ["select-unselect", "unselect"],
      response: { type: OcgResponseType.SELECT_UNSELECT_CARD, index: responseIndex },
    });
  });
  return actions;
}


function fieldPlaceActions(message, responseType) {
  const places = decodeFieldMask(message.field_mask, message.count, message.player);
  return places.map((place) => {
    const locationName = place.location === OcgLocation.SZONE ? "spell/trap zone" : "monster zone";
    return {
      action_id: `place-${locationName.replaceAll("/", "-").replaceAll(" ", "-")}-${place.player}-${place.sequence}`,
      label: `Choose ${playerName(place.player)} ${locationName} ${place.sequence + 1}`,
      tags: ["zone", place.location === OcgLocation.SZONE ? "szone" : "mzone"],
      response: {
        type: responseType,
        places: [place],
      },
    };
  });
}

function decodeFieldMask(fieldMask, count, activePlayer) {
  const places = [];
  const unsignedMask = fieldMask >>> 0;
  const opponent = activePlayer === 0 ? 1 : 0;
  // ocgcore field masks are relative to the selecting player: low MZONE/SZONE
  // bits are the active player's zones, high MZONE/SZONE bits are opponent zones.
  const ranges = [
    { player: activePlayer, location: OcgLocation.MZONE, offset: 0, length: 7 },
    { player: activePlayer, location: OcgLocation.SZONE, offset: 8, length: 8 },
    { player: opponent, location: OcgLocation.MZONE, offset: 16, length: 7 },
    { player: opponent, location: OcgLocation.SZONE, offset: 24, length: 8 },
  ];

  for (const range of ranges) {
    for (let sequence = 0; sequence < range.length; sequence += 1) {
      const bit = range.offset + sequence;
      const isUnavailable = ((unsignedMask >>> bit) & 1) === 1;
      if (!isUnavailable) {
        places.push({ player: range.player, location: range.location, sequence });
      }
    }
  }

  if (places.length === 0) {
    emitLog({ event: "field_mask_decode_empty", field_mask: unsignedMask, count, activePlayer });
  }
  return places.slice(0, Math.max(1, count || 1));
}


function chainActions(message) {
  const actions = [];
  if (!message.forced) {
    actions.push({ action_id: "decline-chain", label: "Do not chain", tags: ["chain", "decline"], response: { type: OcgResponseType.SELECT_CHAIN, index: null } });
  }
  message.selects.forEach((card, index) => {
    const tags = ["chain", ...effectTagsForCard(card.code)];
    if (message.forced) {
      tags.push("forced");
    }
    actions.push({
      action_id: `chain-${index}`,
      label: `Chain ${cardName(card.code)} - ${shortCardText(card.code)}`,
      expected_value: expectedValueForTags(tags),
      tags,
      response: { type: OcgResponseType.SELECT_CHAIN, index },
    });
  });
  return actions;
}

function effectTagsForCard(code) {
  const text = `${cardName(code)} ${cardText(code)}`.toLowerCase();
  const type = database.type(code);
  const tags = [];
  if ((type & 0x4) !== 0) tags.push("trap");
  if ((type & 0x2) !== 0) tags.push("spell");
  if ((type & 0x1) !== 0) tags.push("monster");
  if (matchesAny(text, ["destroy", "破坏", "破壞"])) tags.push("removal", "destroy-monster");
  if (matchesAny(text, ["negate", "无效", "無效"])) tags.push("negate");
  if (matchesAny(text, ["attack", "攻击", "攻擊", "battle"])) tags.push("battle-trap");
  if (matchesAny(text, ["banish", "除外"])) tags.push("banish", "removal");
  if (matchesAny(text, ["draw", "抽卡", "抽" ])) tags.push("draw");
  if (matchesAny(text, ["add 1", "from your deck", "从卡组", "從牌組", "加入手卡", "加入手牌"])) tags.push("search");
  if (matchesAny(text, ["special summon", "特殊召唤", "特殊召喚"])) tags.push("special-summon");
  if (matchesAny(text, ["cannot be destroyed", "不会被破坏", "不會被破壞", "代替破坏", "代替破壞"])) tags.push("protect");
  return [...new Set(tags)];
}

function expectedValueForTags(tags) {
  let value = 0;
  if (tags.includes("lethal")) value += 100;
  if (tags.includes("negate")) value += 4;
  if (tags.includes("removal")) value += 3.5;
  if (tags.includes("battle-trap")) value += 3;
  if (tags.includes("protect")) value += 2.5;
  if (tags.includes("search")) value += 2;
  if (tags.includes("draw")) value += 2;
  if (tags.includes("banish")) value += 1.5;
  return value || null;
}

function matchesAny(text, needles) {
  return needles.some((needle) => text.includes(needle));
}

function shortCardText(code) {
  const text = cardText(code).replace(/\s+/g, " ").trim();
  return text ? text.slice(0, 120) : "no effect text";
}


async function addDeck(core, duelHandle, team, cards) {
  await Promise.all(cards.map((code, sequence) => core.duelNewCard(duelHandle, {
    team,
    duelist: 0,
    code,
    controller: team,
    location: OcgLocation.DECK,
    sequence,
    position: OcgPosition.FACEDOWN_DEFENSE,
  })));
}

function readScript(scriptName) {
  if (scriptCache.has(scriptName)) {
    return scriptCache.get(scriptName);
  }
  const scriptPath = resolveScriptPath(scriptName);
  if (!scriptPath) {
    const optional = isOptionalMissingScript(scriptName);
    if (optional) {
      scriptLoadStats.optional_missing += 1;
      emitLog({ event: "script_optional_missing", script: scriptName });
    } else {
      scriptLoadStats.missing += 1;
      emitLog({ event: "script_missing", script: scriptName });
    }
    scriptCache.set(scriptName, null);
    return null;
  }
  try {
    const content = readFileSync(scriptPath, "utf8");
    scriptLoadStats.loaded += 1;
    emitLog({ event: "script_loaded", script: scriptName, path: path.relative(edoproHome, scriptPath) });
    scriptCache.set(scriptName, content);
    return content;
  } catch (error) {
    scriptLoadStats.errors += 1;
    emitLog({ event: "script_read_error", script: scriptName, error: String(error) });
    scriptCache.set(scriptName, null);
    return null;
  }
}

function resolveScriptPath(scriptName) {
  const candidates = /^c\d+\.lua$/.test(scriptName)
    ? [
        path.join(edoproHome, "script", "official", scriptName),
        path.join(edoproHome, "script", scriptName),
      ]
    : [
        path.join(edoproHome, "script", scriptName),
        path.join(edoproHome, "script", "official", scriptName),
      ];
  return candidates.find((candidate) => existsSync(candidate)) ?? null;
}

function isOptionalMissingScript(scriptName) {
  if (scriptName === "c0.lua") {
    return true;
  }
  const match = scriptName.match(/^c(\d+)\.lua$/);
  if (!match || !database) {
    return false;
  }
  const row = database.row(Number(match[1]));
  return row ? isVanillaMonster(row.type) : false;
}

function validateDeckScripts(deckA, deckB) {
  const uniqueCodes = [...new Set([...deckA, ...deckB])];
  const missingData = [];
  const missingScripts = [];
  const scriptlessNormalMonsters = [];
  for (const code of uniqueCodes) {
    const row = database.row(code);
    if (!row) {
      missingData.push(code);
      continue;
    }
    const scriptName = `c${code}.lua`;
    if (resolveScriptPath(scriptName)) {
      continue;
    }
    if (isVanillaMonster(row.type)) {
      scriptlessNormalMonsters.push(code);
    } else {
      missingScripts.push(code);
    }
  }
  emitLog({
    event: "deck_script_validation",
    total_unique_cards: uniqueCodes.length,
    missing_data: missingData,
    missing_scripts: missingScripts,
    scriptless_normal_monsters: scriptlessNormalMonsters,
  });
  if (missingData.length || missingScripts.length) {
    throw new Error(`Deck script validation failed: missingData=${missingData.join(",")} missingScripts=${missingScripts.join(",")}`);
  }
}

function isVanillaMonster(type) {
  const monster = (type & 0x1) !== 0;
  const normal = (type & 0x10) !== 0;
  const effectLikeBits = type & ~0x11;
  return monster && normal && effectLikeBits === 0;
}


function summarizeMessage(message, lifePoints = [8000, 8000]) {
  return `EDOPro ${ocgMessageTypeStrings.get(message.type) ?? message.type} decision | LP ${playerName(0)}:${lifePoints[0]} ${playerName(1)}:${lifePoints[1]}`;
}

function cardName(code) {
  return database.name(code);
}

function cardAttack(code) {
  return database.attack(code);
}

function cardText(code) {
  return database.text(code);
}

function playerName(playerIndex) {
  return players[playerIndex] ?? `player-${playerIndex}`;
}

function visibleZonesFor(activePlayer, lifePoints) {
  const opponent = activePlayer === 0 ? 1 : 0;
  return {
    life_points: lifePoints.map((lp, index) => `${playerName(index)}:${lp}`),
    known_to_agent: [
      `active_player:${playerName(activePlayer)}`,
      "legal_actions:actionable own/public choices only",
      "opponent_card_identities:not_exposed",
    ],
    hidden_zones: [
      `${playerName(activePlayer)}.hand:own_cards_known_to_engine_actions`,
      `${playerName(opponent)}.hand:hidden`,
      `${playerName(activePlayer)}.deck:hidden_order`,
      `${playerName(opponent)}.deck:hidden_order`,
      `${playerName(activePlayer)}.extra:hidden_until_public`,
      `${playerName(opponent)}.extra:hidden_until_public`,
    ],
    public_zones: [
      "graveyards:public_when_reported_by_legal_actions",
      "banished:public_when_reported_by_legal_actions",
    ],
  };
}


function countTurns(messages) {
  const turns = messages.filter((message) => message.type === OcgMessageType.NEW_TURN).length;
  return Math.max(1, turns);
}

function parseDeck(value, fallback) {
  if (!value) {
    return fallback;
  }
  const parsed = String(value).split(",").map((item) => Number(item.trim())).filter(Boolean);
  if (parsed.length < 40) {
    throw new Error("Deck lists must contain at least 40 comma-separated card IDs.");
  }
  return parsed;
}

function parseArgs(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--edopro-home") {
      parsed.edoproHome = argv[++index];
    } else if (arg === "--max-decisions") {
      parsed.maxDecisions = argv[++index];
    } else if (arg === "--deck-a") {
      parsed.deckA = argv[++index];
    } else if (arg === "--deck-b") {
      parsed.deckB = argv[++index];
    }
  }
  return parsed;
}

async function readJsonLine() {
  const { value, done } = await stdinIterator.next();
  if (done) {
    throw new Error("stdin closed before the gateway received a message.");
  }
  return JSON.parse(value);
}

function emit(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function emitLog(line) {
  emit({ type: "log", message: typeof line === "string" ? line : safe(line) });
}

function emitResult(result) {
  emit({ type: "result", ...result });
}

function safe(value) {
  return JSON.parse(JSON.stringify(value, (_key, item) => (typeof item === "bigint" ? item.toString() : item)));
}

function splitSetcode(value) {
  const codes = [];
  let remaining = BigInt(value || 0);
  while (remaining > 0n) {
    const code = Number(remaining & 0xffffn);
    if (code) {
      codes.push(code);
    }
    remaining >>= 16n;
  }
  return codes;
}

class CardDatabase {
  constructor(databasePath) {
    this.database = new Database(databasePath, { readonly: true });
    this.dataStatement = this.database.prepare("select * from datas where id = ?");
    this.nameStatement = this.database.prepare("select name from texts where id = ?");
    this.textStatement = this.database.prepare("select desc from texts where id = ?");
    this.names = new Map();
    this.texts = new Map();
  }

  row(code) {
    return this.dataStatement.get(code);
  }

  readCard(code) {
    const row = this.row(code);
    if (!row) {
      return null;
    }
    this.names.set(code, this.name(code));
    const level = row.level ?? 0;
    return {
      code: row.id,
      alias: row.alias ?? 0,
      setcodes: splitSetcode(row.setcode),
      type: row.type ?? 0,
      level: level & 0xff,
      attribute: row.attribute ?? 0,
      race: BigInt(row.race ?? 0),
      attack: row.atk ?? 0,
      defense: row.def ?? 0,
      lscale: (level >> 24) & 0xff,
      rscale: (level >> 16) & 0xff,
      link_marker: row.type & 0x4000000 ? row.def : 0,
    };
  }

  name(code) {
    if (!this.names.has(code)) {
      this.names.set(code, this.nameStatement.get(code)?.name ?? String(code));
    }
    return this.names.get(code);
  }

  attack(code) {
    return this.row(code)?.atk ?? 0;
  }

  type(code) {
    return this.row(code)?.type ?? 0;
  }

  text(code) {
    if (!this.texts.has(code)) {
      this.texts.set(code, this.textStatement.get(code)?.desc ?? "");
    }
    return this.texts.get(code);
  }
}

async function main() {
  const startMessage = await readJsonLine();
  if (startMessage.type !== "start_duel") {
    throw new Error(`Expected start_duel message, received ${startMessage.type}`);
  }

  players = startMessage.players ?? ["bot-a", "bot-b"];
  const deckA = parseDeck(startMessage.deck_a ?? args.deckA, DEFAULT_DECK_A);
  const deckB = parseDeck(startMessage.deck_b ?? args.deckB, DEFAULT_DECK_B);

  database = new CardDatabase(path.join(edoproHome, "cards.cdb"));
  validateDeckScripts(deckA, deckB);
  lib = await createCore({
    sync: true,
    print: () => {},
    printErr: (line) => emitLog(line),
  });

  handle = await lib.createDuel({
    flags: OcgDuelMode.MODE_MR5,
    seed: [1n, 2n, 3n, 4n],
    team1: { startingLP: 8000, startingDrawCount: 5, drawCountPerTurn: 1 },
    team2: { startingLP: 8000, startingDrawCount: 5, drawCountPerTurn: 1 },
    cardReader: (code) => database.readCard(code),
    scriptReader: readScript,
    errorHandler: (_type, text) => emitLog(text),
  });
  if (!handle) {
    throw new Error("ocgcore failed to create a duel.");
  }

  try {
    await addDeck(lib, handle, 0, deckA);
    await addDeck(lib, handle, 1, deckB);
    await lib.startDuel(handle);
    await runDuel();
  } finally {
    lib.destroyDuel(handle);
    rl.close();
  }
}

await main();
