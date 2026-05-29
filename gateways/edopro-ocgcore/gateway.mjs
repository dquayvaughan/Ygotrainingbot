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
  cardMatchesOpcode,
  ocgAttributeParse,
  ocgAttributeString,
  ocgMessageTypeStrings,
  ocgProcessResultString,
  ocgPositionParse,
  ocgPositionString,
  ocgRaceParse,
  ocgRaceString,
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
const CORE_LUA_PRELUDES = ["constant.lua", "utility.lua"];
const scriptLoadStats = {
  loaded: 0,
  missing: 0,
  optional_missing: 0,
  errors: 0,
  runtime_errors: 0,
  prelude_loaded: [],
};

let players = ["bot-a", "bot-b"];
let database;
let duelDeckCards = [];
let lib;
let handle;
let currentDuelSeed = [1n, 2n, 3n, 4n];
let duelMode = OcgDuelMode.MODE_MR3;

const HARD_STEP_LIMIT = 500_000;
const STALL_STEP_THRESHOLD = 8_000;
const RETRY_STALL_THRESHOLD = 2_500;
const LOG_ENGINE_EVERY_STEPS = 10_000;

async function runDuel() {
  let decisions = 0;
  let winner = null;
  let loser = null;
  let endReason = null;
  let retryQueue = [];
  let retryOnlySteps = 0;
  const lifePoints = [8000, 8000];
  let lastLpKey = `${lifePoints[0]}/${lifePoints[1]}`;
  let lastLpChangeStep = 0;
  let continueOnlyStreak = 0;
  for (let step = 0; step < HARD_STEP_LIMIT; step += 1) {
    const status = await lib.duelProcess(handle);
    const messages = lib.duelGetMessage(handle);
    const lpKey = `${lifePoints[0]}/${lifePoints[1]}`;
    if (lpKey !== lastLpKey) {
      lastLpKey = lpKey;
      lastLpChangeStep = step;
      retryOnlySteps = 0;
    }
    if (step === 0 || step % LOG_ENGINE_EVERY_STEPS === 0) {
      emitLog({
        event: "engine_progress",
        step,
        status: ocgProcessResultString.get(status) ?? status,
        life_points: [...lifePoints],
        decisions,
        stall_steps: step - lastLpChangeStep,
      });
    }
    let winMessage = null;
    for (const message of messages) {
      if (message.type === OcgMessageType.DAMAGE) {
        lifePoints[message.player] -= message.amount;
      } else if (message.type === OcgMessageType.RECOVER) {
        lifePoints[message.player] += message.amount;
      } else if (message.type === OcgMessageType.LPUPDATE) {
        lifePoints[message.player] = message.lp;
      } else if (message.type === OcgMessageType.WIN) {
        winMessage = message;
      }
    }
    if (winMessage) {
      if (isDeckoutWinReason(winMessage.reason)) {
        winner = playerName(winMessage.player);
        loser = playerName(winMessage.player === 0 ? 1 : 0);
        endReason = "deckout";
      } else if (isLpWinReason(winMessage.reason) || outcomeFromLifePoints(lifePoints)) {
        winner = playerName(winMessage.player);
        loser = playerName(winMessage.player === 0 ? 1 : 0);
        endReason = "lp";
      } else {
        emitLog({
          event: decisions === 0 ? "ignored_premature_win" : "ignored_win",
          reason: winMessage.reason,
          life_points: [...lifePoints],
          player: winMessage.player,
        });
      }
    }
    if (winner !== null && loser !== null) {
      emitResult({
        winner,
        loser,
        turns: countTurns(messages),
        decisions,
        end_reason: endReason ?? "lp",
        life_points: [...lifePoints],
        tags: ["edopro", "ocgcore-wasm", endReason ?? "lp"],
        script_stats: scriptLoadStats,
      });
      return;
    }

    const lifeOutcome = outcomeFromLifePoints(lifePoints);
    if (lifeOutcome) {
      emitResult({
        winner: lifeOutcome.winner,
        loser: lifeOutcome.loser,
        turns: countTurns(messages),
        decisions,
        end_reason: "lp",
        life_points: [...lifePoints],
        tags: ["edopro", "ocgcore-wasm", "lp"],
        script_stats: scriptLoadStats,
      });
      return;
    }

    const deckOutcome = outcomeFromDeckCounts();
    if (deckOutcome) {
      emitResult({
        winner: deckOutcome.winner,
        loser: deckOutcome.loser,
        turns: countTurns(messages),
        decisions,
        end_reason: "deckout",
        life_points: [...lifePoints],
        tags: ["edopro", "ocgcore-wasm", "deckout"],
        script_stats: scriptLoadStats,
      });
      return;
    }

    if (status === OcgProcessResult.END) {
      throw new Error(
        `ocgcore duel ended without WIN or zero life points (LP ${lifePoints[0]}/${lifePoints[1]}).`,
      );
    }
    if (status === OcgProcessResult.CONTINUE) {
      continueOnlyStreak += 1;
      const stallSteps = step - lastLpChangeStep;
      if (stallSteps >= STALL_STEP_THRESHOLD && continueOnlyStreak >= 500) {
        throw new Error(
          `ocgcore CONTINUE stall at LP ${lifePoints[0]}/${lifePoints[1]} (stall_steps=${stallSteps}).`,
        );
      }
      continue;
    }
    continueOnlyStreak = 0;
    if (status !== OcgProcessResult.WAITING) {
      throw new Error(`Unknown ocgcore process result: ${status}`);
    }

    const stallSteps = step - lastLpChangeStep;
    const forceProactive = stallSteps >= STALL_STEP_THRESHOLD;

    const selectable = [...messages].reverse().find((message) => legalActionsFor(message, lifePoints).length > 0);
    if (!selectable) {
      if (messages.some((message) => message.type === OcgMessageType.RETRY)) {
        retryOnlySteps += 1;
        if (retryOnlySteps >= STALL_STEP_THRESHOLD) {
          throw new Error(
            `ocgcore RETRY stall at LP ${lifePoints[0]}/${lifePoints[1]} (retry_only_steps=${retryOnlySteps}).`,
          );
        }
        if (retryQueue.length > 0) {
          const retryAction = retryQueue.shift();
          emitLog({ event: "retry_response", action: safe(retryAction) });
          lib.duelSetResponse(handle, retryAction.response);
          continue;
        }
        const fallback = pickSafeRetryResponse(messages, {
          preferYes: forceProactive || retryOnlySteps > 200,
        });
        if (fallback) {
          emitLog({ event: "retry_safe_fallback", response: safe({ ...fallback, response: undefined }) });
          lib.duelSetResponse(handle, fallback.response);
          continue;
        }
        throw new Error(
          `ocgcore requested RETRY but no alternate responses remain: ${JSON.stringify(safe(messages))}`,
        );
      }
      throw new Error(`ocgcore is waiting, but no supported selectable message was emitted: ${JSON.stringify(safe(messages))}`);
    }
    retryOnlySteps = 0;

    const legalActions = legalActionsFor(selectable, lifePoints);
    if (forceProactive) {
      const stallBreaker = chooseProactiveAction(legalActions)
        ?? chooseLeastPassiveAction(legalActions, { skipPhasePass: true });
      emitLog({
        event: "stall_breaker",
        stall_steps: stallSteps,
        selectable_type: ocgMessageTypeStrings.get(selectable.type) ?? selectable.type,
        selected_action: safe({ ...stallBreaker, response: undefined }),
      });
      lib.duelSetResponse(handle, stallBreaker.response);
      continue;
    }
    const autoPass = chooseAutoPassAction(legalActions);
    if (autoPass && !forceProactive) {
      emitLog({
        event: "auto_pass_turn",
        selectable_type: ocgMessageTypeStrings.get(selectable.type) ?? selectable.type,
        selected_action: safe({ ...autoPass, response: undefined }),
      });
      lib.duelSetResponse(handle, autoPass.response);
      continue;
    }

    if (decisions >= maxDecisions) {
      const forced = outcomeFromLifePoints(lifePoints) ?? {
        winner: playerName(0),
        loser: playerName(1),
      };
      emitLog({
        event: "max_decisions_reached",
        max_decisions: maxDecisions,
        life_points: [...lifePoints],
      });
      emitResult({
        winner: forced.winner,
        loser: forced.loser,
        turns: countTurns(messages),
        decisions,
        end_reason: "max_decisions",
        life_points: [...lifePoints],
        tags: ["edopro", "ocgcore-wasm", "max_decisions"],
        script_stats: scriptLoadStats,
      });
      return;
    }

    decisions += 1;
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
    const requestedAction = legalActions[actionIndex];
    if (!requestedAction) {
      throw new Error(`Unknown action_id ${actionMessage.action_id} for state ${stateId}.`);
    }
    const action = forceLpPressureAction(legalActions, requestedAction) ?? requestedAction;
    const effectiveIndex = legalActions.findIndex((candidate) => candidate.action_id === action.action_id);
    retryQueue = legalActions.filter((_candidate, index) => index !== effectiveIndex);
    emitLog({
      event: "submit_response",
      state_id: stateId,
      requested_action: safe(actionMessage),
      selected_action: safe({ ...action, response: undefined }),
      overridden_for_lp_pressure: action.action_id !== requestedAction.action_id,
      response: safe(action.response),
    });
    lib.duelSetResponse(handle, action.response);
  }

  throw new Error(
    `ocgcore gateway exceeded the hard step limit (${HARD_STEP_LIMIT}) without a natural win at LP ${lifePoints[0]}/${lifePoints[1]} (stall_steps=${HARD_STEP_LIMIT - lastLpChangeStep}).`,
  );
}

function outcomeFromDeckCounts() {
  if (!lib || !handle) {
    return null;
  }
  for (let team = 0; team < 2; team += 1) {
    const main = lib.duelQueryCount(handle, team, OcgLocation.DECK);
    const extra = lib.duelQueryCount(handle, team, OcgLocation.EXTRA);
    if (main + extra <= 0) {
      return {
        winner: playerName(team === 0 ? 1 : 0),
        loser: playerName(team),
      };
    }
  }
  return null;
}

function chooseProactiveAction(legalActions) {
  const predicates = [
    (action) => (action.tags ?? []).includes("lethal"),
    (action) => action.action_id.startsWith("attack-"),
    (action) => (action.tags ?? []).includes("direct-attack"),
    (action) => action.action_id.startsWith("normal-summon-"),
    (action) => action.action_id.startsWith("special-summon-"),
    (action) => action.action_id.startsWith("activate-"),
    (action) => action.action_id.startsWith("set-monster-"),
    (action) => action.action_id === "activate-effect",
    (action) => action.action_id === "to-battle-phase",
    (action) => action.action_id === "yes",
  ];
  for (const predicate of predicates) {
    const match = legalActions.find(predicate);
    if (match) {
      return match;
    }
  }
  return null;
}

function chooseLeastPassiveAction(legalActions, { skipPhasePass = false } = {}) {
  const passiveTags = new Set(["phase", "decline"]);
  const candidates = skipPhasePass
    ? legalActions.filter(
      (action) => action.action_id !== "to-end-phase" && action.action_id !== "to-main-phase-2",
    )
    : legalActions;
  const nonPassive = candidates.filter((action) => {
    const tags = action.tags ?? [];
    return tags.some((tag) => !passiveTags.has(tag)) && !action.action_id.startsWith("decline-");
  });
  return (
    nonPassive.find((action) => action.action_id.startsWith("attack-"))
    ?? nonPassive.find((action) => action.action_id.startsWith("normal-summon-"))
    ?? nonPassive[0]
    ?? candidates.find((action) => action.action_id === "to-battle-phase")
    ?? candidates.find((action) => action.action_id === "to-end-phase")
    ?? candidates[0]
    ?? legalActions[0]
  );
}

function forceLpPressureAction(legalActions, requestedAction) {
  const attack = legalActions.find((action) => action.action_id.startsWith("attack-"));
  if (attack && requestedAction.action_id !== attack.action_id) {
    return attack;
  }
  const toBattle = legalActions.find((action) => action.action_id === "to-battle-phase");
  if (toBattle && requestedAction.action_id === "to-end-phase") {
    return toBattle;
  }
  const summon = legalActions.find((action) => action.action_id.startsWith("normal-summon-"));
  if (summon && requestedAction.action_id.startsWith("set-")) {
    return summon;
  }
  return null;
}

// ocgcore WIN reason flags (Fluorohydride/ygopro duelconstants.h).
const WIN_REASON_DECKOUT = 0x04;
const WIN_REASON_LOSE_LP = 0x10;

function isDeckoutWinReason(reason) {
  const code = Number(reason ?? 0);
  return Boolean(code & WIN_REASON_DECKOUT);
}

function isLpWinReason(reason) {
  const code = Number(reason ?? 0);
  return Boolean(code & WIN_REASON_LOSE_LP);
}

function outcomeFromLifePoints(lifePoints) {
  if (lifePoints[0] <= 0 && lifePoints[1] > 0) {
    return { winner: playerName(1), loser: playerName(0) };
  }
  if (lifePoints[1] <= 0 && lifePoints[0] > 0) {
    return { winner: playerName(0), loser: playerName(1) };
  }
  return null;
}

function pickSafeRetryResponse(messages, { preferYes = false } = {}) {
  for (const message of messages) {
    const actions = legalActionsFor(message, [8000, 8000]);
    if (preferYes) {
      const yes = actions.find((action) => action.action_id === "yes" || action.action_id === "activate-effect");
      if (yes) {
        return yes;
      }
    }
    const proactive = chooseProactiveAction(actions);
    if (proactive) {
      return proactive;
    }
    const fallbackActions = actions.filter((action) => (action.tags ?? []).includes("decline"));
    if (fallbackActions.length > 0) {
      return fallbackActions[0];
    }
  }
  return {
    action_id: "retry-decline-effect",
    label: "Decline effect (retry fallback)",
    tags: ["effect", "decline"],
    response: { type: OcgResponseType.SELECT_EFFECTYN, yes: false },
  };
}

function chooseAutoPassAction(legalActions) {
  if (!legalActions.length) {
    return null;
  }
  const passiveTags = new Set(["phase", "decline", "shuffle", "yes-no", "select-unselect"]);
  const passivePrefixes = [
    "to-",
    "decline-",
    "cancel-",
    "finish-",
  ];
  const hasProactiveAction = legalActions.some((action) => {
    const tags = action.tags ?? [];
    const hasActiveTag = tags.some((tag) => !passiveTags.has(tag));
    const hasActivePrefix = !passivePrefixes.some((prefix) => action.action_id.startsWith(prefix));
    return hasActiveTag && hasActivePrefix;
  });
  if (hasProactiveAction) {
    return null;
  }
  return (
    legalActions.find((action) => action.action_id === "to-end-phase")
    ?? legalActions.find((action) => action.action_id === "to-main-phase-2")
    ?? legalActions.find((action) => action.action_id === "to-battle-phase")
    ?? legalActions.find((action) => action.action_id.startsWith("decline-"))
    ?? legalActions.find((action) => action.action_id === "finish-selection")
    ?? legalActions.find((action) => action.action_id === "cancel-selection")
    ?? legalActions[0]
  );
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
      return selectCardActions(message);
    case OcgMessageType.SELECT_UNSELECT_CARD:
      return selectUnselectActions(message);
    case OcgMessageType.SELECT_TRIBUTE:
      return selectTributeActions(message);
    case OcgMessageType.SELECT_POSITION:
      return ocgPositionParse(message.positions).map((position) => ({
        action_id: `position-${position}`,
        label: `Choose ${ocgPositionString.get(position) ?? position}`,
        tags: ["position"],
        response: { type: OcgResponseType.SELECT_POSITION, position },
      }));
    case OcgMessageType.SELECT_OPTION:
      return selectOptionActions(message);
    case OcgMessageType.ANNOUNCE_NUMBER:
      return announceNumberActions(message);
    case OcgMessageType.ANNOUNCE_RACE:
      return announceRaceActions(message);
    case OcgMessageType.ANNOUNCE_ATTRIB:
      return announceAttributeActions(message);
    case OcgMessageType.ANNOUNCE_CARD:
      return announceCardActions(message);
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

function selectOptionActions(message) {
  return message.options.map((_option, index) => ({
    action_id: `option-${index}`,
    label: `Choose option ${index + 1}`,
    tags: ["option"],
    response: { type: OcgResponseType.SELECT_OPTION, index },
  }));
}

function announceNumberActions(message) {
  return message.options.map((option, index) => {
    const value = Number(option);
    return {
      action_id: `announce-number-${index}`,
      label: `Announce ${Number.isFinite(value) ? value : String(option)}`,
      tags: ["announce-number", "option"],
      response: { type: OcgResponseType.ANNOUNCE_NUMBER, value },
    };
  });
}

function announceRaceActions(message) {
  const available = BigInt(message.available ?? 0);
  const races = ocgRaceParse(available);
  const pickCount = Math.max(1, Number(message.count ?? 1));
  if (pickCount === 1) {
    return races.map((race, index) => ({
      action_id: `announce-race-${index}`,
      label: `Announce ${ocgRaceString.get(race) ?? race}`,
      tags: ["announce-race", "option"],
      response: { type: OcgResponseType.ANNOUNCE_RACE, races: [race] },
    }));
  }
  const subset = races.slice(0, pickCount);
  return [{
    action_id: "announce-race-combo",
    label: `Announce ${subset.map((race) => ocgRaceString.get(race) ?? race).join(", ")}`,
    tags: ["announce-race", "option"],
    response: { type: OcgResponseType.ANNOUNCE_RACE, races: subset },
  }];
}

function announceAttributeActions(message) {
  const available = Number(message.available ?? 0);
  const attributes = ocgAttributeParse(available);
  const pickCount = Math.max(1, Number(message.count ?? 1));
  if (pickCount === 1) {
    return attributes.map((attribute, index) => ({
      action_id: `announce-attribute-${index}`,
      label: `Announce ${ocgAttributeString.get(attribute) ?? attribute}`,
      tags: ["announce-attribute", "option"],
      response: { type: OcgResponseType.ANNOUNCE_ATTRIB, attributes: [attribute] },
    }));
  }
  const subset = attributes.slice(0, pickCount);
  return [{
    action_id: "announce-attribute-combo",
    label: `Announce ${subset.map((attribute) => ocgAttributeString.get(attribute) ?? attribute).join(", ")}`,
    tags: ["announce-attribute", "option"],
    response: { type: OcgResponseType.ANNOUNCE_ATTRIB, attributes: subset },
  }];
}

function announceCardActions(message) {
  const opcodes = message.opcodes ?? [];
  const matches = [];
  for (const code of duelDeckCards) {
    const card = database?.readCard(code);
    if (!card) {
      continue;
    }
    if (opcodes.every((opcode) => cardMatchesOpcode(card, [opcode]))) {
      matches.push(code);
    }
  }
  if (matches.length === 0) {
    return [{
      action_id: "announce-card-fallback",
      label: "Announce card (fallback)",
      tags: ["announce-card", "fallback"],
      response: { type: OcgResponseType.ANNOUNCE_CARD, card: 0 },
    }];
  }
  return matches.map((code, index) => ({
    action_id: `announce-card-${index}`,
    label: `Announce ${cardName(code)}`,
    tags: ["announce-card", ...effectTagsForCard(code)],
    response: { type: OcgResponseType.ANNOUNCE_CARD, card: code },
  }));
}

function selectCardActions(message) {
  const actions = [];
  if (message.can_cancel) {
    actions.push({
      action_id: "cancel-select-card",
      label: "Cancel selection",
      tags: ["select-card", "decline"],
      response: { type: OcgResponseType.SELECT_CARD, indicies: null },
    });
  }
  const min = Number(message.min ?? 1);
  const pickCount = Math.max(1, Math.min(min, message.selects.length));
  message.selects.forEach((card, index) => actions.push({
    action_id: `select-card-${index}`,
    label: `Select ${cardName(card.code)}`,
    tags: ["select-card", ...effectTagsForCard(card.code)],
    response: {
      type: OcgResponseType.SELECT_CARD,
      indicies: buildSelectionIndices(index, pickCount, message.selects.length),
    },
  }));
  return actions;
}

function selectTributeActions(message) {
  const actions = [];
  if (message.can_cancel) {
    actions.push({
      action_id: "cancel-select-tribute",
      label: "Cancel tribute selection",
      tags: ["tribute", "decline"],
      response: { type: OcgResponseType.SELECT_TRIBUTE, indicies: null },
    });
  }
  const min = Number(message.min ?? 1);
  const pickCount = Math.max(1, Math.min(min, message.selects.length));
  message.selects.forEach((card, index) => actions.push({
    action_id: `select-tribute-${index}`,
    label: `Tribute ${cardName(card.code)}`,
    tags: ["tribute"],
    response: {
      type: OcgResponseType.SELECT_TRIBUTE,
      indicies: buildSelectionIndices(index, pickCount, message.selects.length),
    },
  }));
  return actions;
}

function buildSelectionIndices(preferredIndex, count, total) {
  const picks = [preferredIndex];
  for (let offset = 1; picks.length < count && offset < total; offset += 1) {
    const candidate = (preferredIndex + offset) % total;
    if (!picks.includes(candidate)) {
      picks.push(candidate);
    }
  }
  return picks;
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

async function addExtraDeck(core, duelHandle, team, cards) {
  if (!cards.length) {
    return;
  }
  await Promise.all(cards.map((code, sequence) => core.duelNewCard(duelHandle, {
    team,
    duelist: 0,
    code,
    controller: team,
    location: OcgLocation.EXTRA,
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
  const scriptRoot = path.join(edoproHome, "script");
  const candidates = [];
  if (/^c\d+\.lua$/.test(scriptName)) {
    candidates.push(
      path.join(scriptRoot, "official", scriptName),
      path.join(scriptRoot, scriptName),
    );
  } else if (/proc_unofficial\.lua$/.test(scriptName)) {
    candidates.push(path.join(scriptRoot, "unofficial", scriptName));
  } else {
    candidates.push(
      path.join(scriptRoot, scriptName),
      path.join(scriptRoot, "official", scriptName),
      path.join(scriptRoot, "unofficial", scriptName),
    );
  }
  return candidates.find((candidate) => existsSync(candidate)) ?? null;
}

function loadCoreLuaPreludes(core, duelHandle) {
  for (const scriptName of CORE_LUA_PRELUDES) {
    const scriptPath = path.join(edoproHome, "script", scriptName);
    if (!existsSync(scriptPath)) {
      throw new Error(
        `Missing EDOPro core Lua prelude ${scriptName} at ${scriptPath}. ` +
          "Re-run scripts/bootstrap_edopro_home.sh or verify ProjectIgnis/script/.",
      );
    }
    const content = readFileSync(scriptPath, "utf8");
    const loaded = core.loadScript(duelHandle, scriptName, content);
    if (!loaded) {
      throw new Error(`ocgcore failed to load core Lua prelude: ${scriptName}`);
    }
    scriptLoadStats.prelude_loaded.push(scriptName);
    emitLog({ event: "script_prelude_loaded", script: scriptName, path: path.relative(edoproHome, scriptPath) });
  }
}

function isScriptRuntimeError(text) {
  const message = String(text ?? "");
  return (
    message.includes("GetID") ||
    message.includes("CallCardFunction") ||
    message.includes("attempt to call a nil value") ||
    message.includes("attempt to call an error function")
  );
}

function trackScriptRuntimeError(text) {
  if (!isScriptRuntimeError(text)) {
    return;
  }
  scriptLoadStats.runtime_errors += 1;
  emitLog({ event: "script_runtime_error", message: String(text).slice(0, 240) });
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

function validateCoreLuaPreludes() {
  const missing = CORE_LUA_PRELUDES.filter(
    (scriptName) => !existsSync(path.join(edoproHome, "script", scriptName)),
  );
  if (missing.length) {
    throw new Error(
      `EDOPro home is missing core Lua preludes: ${missing.join(", ")} under ${path.join(edoproHome, "script")}`,
    );
  }
}

function validateDeckScripts(mainA, mainB, extraA = [], extraB = []) {
  validateDeckCardScripts([...mainA, ...extraA], [...mainB, ...extraB]);
}

function validateDeckCardScripts(deckA, deckB) {
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

function parseDeckList(value, { minimum = 0, fallback = null } = {}) {
  if (value === undefined || value === null) {
    if (fallback !== null) {
      return [...fallback];
    }
    return [];
  }
  if (Array.isArray(value)) {
    const parsed = value.map((item) => Number(item)).filter(Boolean);
    if (minimum > 0 && parsed.length < minimum) {
      throw new Error(`Deck lists must contain at least ${minimum} card IDs.`);
    }
    return parsed;
  }
  const parsed = String(value).split(",").map((item) => Number(item.trim())).filter(Boolean);
  if (minimum > 0 && parsed.length < minimum) {
    throw new Error(`Deck lists must contain at least ${minimum} comma-separated card IDs.`);
  }
  return parsed;
}

function parseDeck(value, fallback) {
  return parseDeckList(value, { minimum: 40, fallback });
}

function parseExtraDeck(value) {
  const parsed = parseDeckList(value, { minimum: 0 });
  if (parsed.length > 15) {
    throw new Error("Extra deck lists may contain at most 15 card IDs.");
  }
  return parsed;
}

function parseSideDeck(value) {
  const parsed = parseDeckList(value, { minimum: 0 });
  if (parsed.length > 15) {
    throw new Error("Side deck lists may contain at most 15 card IDs.");
  }
  return parsed;
}

function parsePlayerDecks(message, teamKey, fallbackMain) {
  const deckValue = message[teamKey];
  const extraKey = teamKey === "deck_a" ? "extra_a" : "extra_b";
  const sideKey = teamKey === "deck_a" ? "side_a" : "side_b";
  if (deckValue && typeof deckValue === "object" && !Array.isArray(deckValue)) {
    return {
      main: parseDeckList(deckValue.main, { minimum: 40, fallback: fallbackMain }),
      extra: parseExtraDeck(deckValue.extra),
      side: parseSideDeck(deckValue.side),
    };
  }
  return {
    main: parseDeck(deckValue, fallbackMain),
    extra: parseExtraDeck(message[extraKey]),
    side: parseSideDeck(message[sideKey]),
  };
}

function parseSeed(value) {
  if (!value) {
    return null;
  }
  if (!Array.isArray(value) || value.length !== 4) {
    throw new Error("seed must be an array of four unsigned 64-bit integers.");
  }
  return value.map((part) => BigInt(part));
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
    } else if (arg === "--duel-mode") {
      parsed.duelMode = argv[++index];
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

function resolveDuelMode(startMessage) {
  const requested = String(startMessage?.duel_mode ?? args.duelMode ?? "mr3").toLowerCase();
  if (requested === "mr5") {
    return OcgDuelMode.MODE_MR5;
  }
  if (requested === "mr4") {
    return OcgDuelMode.MODE_MR4;
  }
  if (requested === "mr2") {
    return OcgDuelMode.MODE_MR2;
  }
  if (requested === "mr1") {
    return OcgDuelMode.MODE_MR1;
  }
  return OcgDuelMode.MODE_MR3;
}

function resolveDatabasePath(home) {
  const candidates = [
    path.join(home, "cards.cdb"),
    path.join(home, "expansions", "cards.cdb"),
  ];
  for (const candidate of candidates) {
    if (existsSync(candidate)) {
      return candidate;
    }
  }
  throw new Error(
    `No card database found under ${home}. Expected cards.cdb or expansions/cards.cdb.`,
  );
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
  const decksA = parsePlayerDecks(startMessage, "deck_a", DEFAULT_DECK_A);
  const decksB = parsePlayerDecks(startMessage, "deck_b", DEFAULT_DECK_B);
  duelDeckCards = [...new Set([...decksA.main, ...decksA.extra, ...decksB.main, ...decksB.extra])];
  currentDuelSeed = parseSeed(startMessage.seed) ?? [1n, 2n, 3n, 4n];

  const databasePath = resolveDatabasePath(edoproHome);
  emitLog({ event: "database_loaded", path: path.relative(edoproHome, databasePath) });
  database = new CardDatabase(databasePath);
  validateDeckScripts(decksA.main, decksB.main, decksA.extra, decksB.extra);
  validateCoreLuaPreludes();
  emitLog({
    event: "deck_loaded",
    deck_a_main: decksA.main.length,
    deck_a_extra: decksA.extra.length,
    deck_a_side: decksA.side.length,
    deck_b_main: decksB.main.length,
    deck_b_extra: decksB.extra.length,
    deck_b_side: decksB.side.length,
  });
  lib = await createCore({
    sync: true,
    print: () => {},
    printErr: (line) => emitLog(line),
  });

  duelMode = resolveDuelMode(startMessage);
  emitLog({ event: "duel_mode", mode: String(duelMode) });

  handle = await lib.createDuel({
    flags: duelMode,
    seed: currentDuelSeed,
    team1: { startingLP: 8000, startingDrawCount: 5, drawCountPerTurn: 1 },
    team2: { startingLP: 8000, startingDrawCount: 5, drawCountPerTurn: 1 },
    cardReader: (code) => database.readCard(code),
    scriptReader: readScript,
    errorHandler: (_type, text) => {
      trackScriptRuntimeError(text);
      emitLog(text);
    },
  });
  if (!handle) {
    throw new Error("ocgcore failed to create a duel.");
  }

  loadCoreLuaPreludes(lib, handle);

  try {
    await addDeck(lib, handle, 0, decksA.main);
    await addExtraDeck(lib, handle, 0, decksA.extra);
    await addDeck(lib, handle, 1, decksB.main);
    await addExtraDeck(lib, handle, 1, decksB.extra);
    await lib.startDuel(handle);
    await runDuel();
  } finally {
    lib.destroyDuel(handle);
    rl.close();
  }
}

await main();
