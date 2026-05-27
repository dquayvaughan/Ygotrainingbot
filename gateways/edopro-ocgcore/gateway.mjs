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
      emitResult({ winner, loser, turns: countTurns(messages), decisions, tags: ["edopro", "ocgcore-wasm"] });
      return;
    }
    if (status === OcgProcessResult.CONTINUE) {
      continue;
    }
    if (status !== OcgProcessResult.WAITING) {
      throw new Error(`Unknown ocgcore process result: ${status}`);
    }

    const selectable = [...messages].reverse().find((message) => legalActionsFor(message).length > 0);
    if (!selectable) {
      if (messages.some((message) => message.type === OcgMessageType.RETRY)) {
        if (retryQueue.length > 0) {
          lib.duelSetResponse(handle, retryQueue.shift().response);
          continue;
        }
        const adjudicated = adjudicateByLifePoints(lifePoints);
        emitResult({
          winner: adjudicated.winner,
          loser: adjudicated.loser,
          turns: decisions,
          decisions,
          tags: ["edopro", "ocgcore-wasm", "retry-adjudication", adjudicated.tag],
        });
        return;
      }
      throw new Error(`ocgcore is waiting, but no supported selectable message was emitted: ${JSON.stringify(safe(messages))}`);
    }

    decisions += 1;
    const legalActions = legalActionsFor(selectable);
    const stateId = `ocgcore-${decisions}`;
    emit({
      type: "state",
      state: {
        state_id: stateId,
        turn: decisions,
        active_player: playerName(selectable.player ?? 0),
        summary: summarizeMessage(selectable),
        legal_actions: legalActions.map(({ response: _response, ...action }) => action),
        public_zones: {},
      },
    });

    const actionMessage = await readJsonLine();
    const actionIndex = legalActions.findIndex((candidate) => candidate.action_id === actionMessage.action_id);
    const action = legalActions[actionIndex];
    if (!action) {
      throw new Error(`Unknown action_id ${actionMessage.action_id} for state ${stateId}.`);
    }
    retryQueue = legalActions.filter((_candidate, index) => index !== actionIndex);
    lib.duelSetResponse(handle, action.response);

    if (decisions >= maxDecisions) {
      const adjudicated = adjudicateByLifePoints(lifePoints);
      emitResult({
        winner: adjudicated.winner,
        loser: adjudicated.loser,
        turns: decisions,
        decisions,
        tags: ["edopro", "ocgcore-wasm", "max-decisions", adjudicated.tag],
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


function legalActionsFor(message) {
  switch (message.type) {
    case OcgMessageType.SELECT_IDLECMD:
      return idleActions(message);
    case OcgMessageType.SELECT_BATTLECMD:
      return battleActions(message);
    case OcgMessageType.SELECT_CHAIN:
      return chainActions(message);
    case OcgMessageType.SELECT_CARD:
      return message.selects.map((card, index) => ({
        action_id: `select-card-${index}`,
        label: `Select ${cardName(card.code)}`,
        tags: ["select-card"],
        response: { type: OcgResponseType.SELECT_CARD, indicies: [index] },
      }));
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
    case OcgMessageType.SELECT_EFFECTYN:
      return [
        { action_id: "activate-effect", label: `Activate ${cardName(message.code)}`, tags: ["effect"], response: { type: OcgResponseType.SELECT_EFFECTYN, yes: true } },
        { action_id: "decline-effect", label: "Do not activate", tags: ["effect"], response: { type: OcgResponseType.SELECT_EFFECTYN, yes: false } },
      ];
    default:
      return [];
  }
}

function idleActions(message) {
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
  message.activates.forEach((card, index) => actions.push({
    action_id: `activate-${index}`,
    label: `Activate ${cardName(card.code)}`,
    tags: ["activate"],
    response: { type: OcgResponseType.SELECT_IDLECMD, action: SelectIdleCMDAction.SELECT_ACTIVATE, index },
  }));
  message.monster_sets.forEach((card, index) => actions.push({
    action_id: `set-monster-${index}`,
    label: `Set ${cardName(card.code)}`,
    tags: ["set-monster"],
    response: { type: OcgResponseType.SELECT_IDLECMD, action: SelectIdleCMDAction.SELECT_MONSTER_SET, index },
  }));
  message.spell_sets.forEach((card, index) => actions.push({
    action_id: `set-spell-${index}`,
    label: `Set ${cardName(card.code)}`,
    tags: ["set-spell"],
    response: { type: OcgResponseType.SELECT_IDLECMD, action: SelectIdleCMDAction.SELECT_SPELL_SET, index },
  }));
  return actions;
}


function battleActions(message) {
  const actions = [];
  if (message.to_ep) {
    actions.push({ action_id: "to-end-phase", label: "Go to End Phase", tags: ["phase"], response: { type: OcgResponseType.SELECT_BATTLECMD, action: SelectBattleCMDAction.TO_EP, index: null } });
  }
  if (message.to_m2) {
    actions.push({ action_id: "to-main-phase-2", label: "Go to Main Phase 2", tags: ["phase"], response: { type: OcgResponseType.SELECT_BATTLECMD, action: SelectBattleCMDAction.TO_M2, index: null } });
  }
  message.attacks.forEach((card, index) => actions.push({
    action_id: `attack-${index}`,
    label: `Attack with ${cardName(card.code)}`,
    tags: ["attack"],
    response: { type: OcgResponseType.SELECT_BATTLECMD, action: SelectBattleCMDAction.SELECT_BATTLE, index },
  }));
  message.chains.forEach((card, index) => actions.push({
    action_id: `battle-chain-${index}`,
    label: `Activate ${cardName(card.code)}`,
    tags: ["chain"],
    response: { type: OcgResponseType.SELECT_BATTLECMD, action: SelectBattleCMDAction.SELECT_CHAIN, index },
  }));
  return actions;
}


function fieldPlaceActions(message, responseType) {
  const location = OcgLocation.MZONE;
  const places = [];
  for (let sequence = 0; sequence < 5; sequence += 1) {
    places.push({
      action_id: `place-mzone-${sequence}`,
      label: `Choose monster zone ${sequence + 1}`,
      tags: ["zone"],
      response: {
        type: responseType,
        places: [{ player: message.player, location, sequence }],
      },
    });
  }
  return places;
}


function chainActions(message) {
  const actions = [];
  if (!message.forced) {
    actions.push({ action_id: "decline-chain", label: "Do not chain", tags: ["chain"], response: { type: OcgResponseType.SELECT_CHAIN, index: null } });
  }
  message.selects.forEach((card, index) => actions.push({
    action_id: `chain-${index}`,
    label: `Chain ${cardName(card.code)}`,
    tags: ["chain"],
    response: { type: OcgResponseType.SELECT_CHAIN, index },
  }));
  return actions;
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
  const scriptPath = /^c\d+\.lua$/.test(scriptName)
    ? path.join(edoproHome, "script", "official", scriptName)
    : path.join(edoproHome, "script", scriptName);
  if (!existsSync(scriptPath)) {
    return null;
  }
  return readFileSync(scriptPath, "utf8");
}

function summarizeMessage(message) {
  return `EDOPro ${ocgMessageTypeStrings.get(message.type) ?? message.type} decision`;
}

function cardName(code) {
  return database.name(code);
}

function playerName(playerIndex) {
  return players[playerIndex] ?? `player-${playerIndex}`;
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
  emit({ type: "log", message: String(line) });
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
    this.names = new Map();
  }

  readCard(code) {
    const row = this.dataStatement.get(code);
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
