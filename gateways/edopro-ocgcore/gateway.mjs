#!/usr/bin/env node
import createCore, {
  OcgDuelMode,
  OcgLocation,
  OcgMessageType,
  OcgPosition,
  OcgProcessResult,
  OcgQueryFlags,
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
import { randomBytes } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import process from "node:process";

const DEFAULT_DECK_A = Array(40).fill(1184620); // Hunter Spider, level 4 normal monster
const DEFAULT_DECK_B = Array(40).fill(3134241); // Flying Kamakiri #1, level 4 normal monster

const args = parseArgs(process.argv.slice(2));
const edoproHome = path.resolve(args.edoproHome ?? process.env.EDOPRO_HOME ?? ".");
let maxDecisions = Number(args.maxDecisions ?? 0);
let maxDuelTurns = Number(args.maxDuelTurns ?? 0);
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
let lastDuelOutboundAt = Date.now();

const HARD_STEP_LIMIT = 500_000;
const STALL_STEP_THRESHOLD = 2_000;
const LP_STALL_STEP_LIMIT = 2_500;
const CONTINUE_STALL_STREAK = 4_000;
const CONTINUE_ONLY_STREAK_LIMIT = 4_000;
const ENGINE_SILENCE_MS = 90_000;
const SOFT_MAX_DECISIONS_UNLIMITED = 800;
const MAX_IGNORED_WIN_MESSAGES = 6;
const RETRY_PENDING_SPIN_LIMIT = 64;
const STALL_IDLE_AGENT_THRESHOLD = 600;
const RETRY_STALL_THRESHOLD = 250;
const MAX_SELECT_CARD_RETRIES_PER_PROMPT = 64;
const MAX_PROMPT_RETRIES_PER_PROMPT = 48;
const LOG_ENGINE_EVERY_STEPS = 10_000;

const OCGCORE_SELECT_PATCH_MARKER = "t.i32(0),t.i32(e.indicies.length);for(let r of e.indicies)t.i32(r)";

const PROMPT_MESSAGE_TYPES = new Set([
  OcgMessageType.SELECT_BATTLECMD,
  OcgMessageType.SELECT_IDLECMD,
  OcgMessageType.SELECT_EFFECTYN,
  OcgMessageType.SELECT_YESNO,
  OcgMessageType.SELECT_OPTION,
  OcgMessageType.SELECT_CARD,
  OcgMessageType.SELECT_CHAIN,
  OcgMessageType.SELECT_PLACE,
  OcgMessageType.SELECT_POSITION,
  OcgMessageType.SELECT_TRIBUTE,
  OcgMessageType.SORT_CHAIN,
  OcgMessageType.SELECT_COUNTER,
  OcgMessageType.SELECT_SUM,
  OcgMessageType.SELECT_DISFIELD,
  OcgMessageType.SORT_CARD,
  OcgMessageType.SELECT_UNSELECT_CARD,
  OcgMessageType.ANNOUNCE_RACE,
  OcgMessageType.ANNOUNCE_ATTRIB,
  OcgMessageType.ANNOUNCE_CARD,
  OcgMessageType.ANNOUNCE_NUMBER,
]);

async function runDuel() {
  let decisions = 0;
  let duelTurn = 1;
  let winner = null;
  let loser = null;
  let endReason = null;
  let retryQueue = [];
  let retryOnlySteps = 0;
  let lastPromptContext = null;
  let lastObservableMessages = [];
  const lifePoints = [8000, 8000];
  let lastLpKey = `${lifePoints[0]}/${lifePoints[1]}`;
  let lastLpChangeStep = 0;
  let continueOnlyStreak = 0;
  let ignoredWinStreak = 0;
  lastDuelOutboundAt = Date.now();
  for (let step = 0; step < HARD_STEP_LIMIT; step += 1) {
    const status = await lib.duelProcess(handle);
    const messages = lib.duelGetMessage(handle);
    const observableMessages = messages.filter((message) => message.type !== OcgMessageType.RETRY);
    if (observableMessages.length > 0) {
      lastObservableMessages = messages;
    }
    const promptMessages = resolvePromptMessages(messages, lastObservableMessages);
    const lpKey = `${lifePoints[0]}/${lifePoints[1]}`;
    if (lpKey !== lastLpKey) {
      lastLpKey = lpKey;
      lastLpChangeStep = step;
      retryOnlySteps = 0;
    }
    const stallSteps = step - lastLpChangeStep;
    if (stallSteps >= LP_STALL_STEP_LIMIT) {
      finishEngineStall({
        lifePoints,
        decisions,
        duelTurn,
        stallSteps,
        continueOnlyStreak,
        silenceMs: Date.now() - lastDuelOutboundAt,
        reason: "lp_stall",
      });
      return;
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
      if (message.type === OcgMessageType.NEW_TURN) {
        duelTurn += 1;
      } else if (message.type === OcgMessageType.DAMAGE) {
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
        ignoredWinStreak = 0;
        winner = playerName(winMessage.player);
        loser = playerName(winMessage.player === 0 ? 1 : 0);
        endReason = "deckout";
      } else if (isLpWinReason(winMessage.reason) || outcomeFromLifePoints(lifePoints)) {
        ignoredWinStreak = 0;
        winner = playerName(winMessage.player);
        loser = playerName(winMessage.player === 0 ? 1 : 0);
        endReason = "lp";
      } else {
        ignoredWinStreak += 1;
        emitLog({
          event: decisions === 0 ? "ignored_premature_win" : "ignored_win",
          reason: winMessage.reason,
          life_points: [...lifePoints],
          player: winMessage.player,
          ignored_win_streak: ignoredWinStreak,
        });
        if (ignoredWinStreak >= MAX_IGNORED_WIN_MESSAGES) {
          const forced = outcomeFromTurnLimit(lifePoints);
          emitLog({
            event: "ignored_win_cap",
            ignored_win_streak: ignoredWinStreak,
            life_points: [...lifePoints],
          });
          emitResult({
            winner: forced.winner,
            loser: forced.loser,
            turns: Math.max(1, duelTurn),
            decisions,
            end_reason: "engine_stall",
            life_points: [...lifePoints],
            tags: ["edopro", "ocgcore-wasm", "ignored_win"],
            script_stats: scriptLoadStats,
          });
          return;
        }
      }
    } else {
      ignoredWinStreak = 0;
    }
    if (winner !== null && loser !== null) {
      emitResult({
        winner,
        loser,
        turns: Math.max(1, duelTurn),
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
        turns: Math.max(1, duelTurn),
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
        turns: Math.max(1, duelTurn),
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
      const silenceMs = Date.now() - lastDuelOutboundAt;
      if (silenceMs >= ENGINE_SILENCE_MS) {
        finishEngineStall({
          lifePoints,
          decisions,
          duelTurn,
          stallSteps,
          continueOnlyStreak,
          silenceMs,
        });
        return;
      }
      if (continueOnlyStreak % 2_000 === 0) {
        emitLog({
          event: "engine_continue",
          step,
          continue_only_streak: continueOnlyStreak,
          stall_steps: stallSteps,
          silence_ms: silenceMs,
        });
      }
      if (continueOnlyStreak >= CONTINUE_ONLY_STREAK_LIMIT) {
        finishEngineStall({
          lifePoints,
          decisions,
          duelTurn,
          stallSteps,
          continueOnlyStreak,
          silenceMs,
          reason: "continue_only_streak",
        });
        return;
      }
      if (stallSteps >= STALL_STEP_THRESHOLD && continueOnlyStreak >= CONTINUE_STALL_STREAK) {
        finishEngineStall({
          lifePoints,
          decisions,
          duelTurn,
          stallSteps,
          continueOnlyStreak,
          silenceMs,
          reason: "continue_stall",
        });
        return;
      }
      continue;
    }
    continueOnlyStreak = 0;
    if (status !== OcgProcessResult.WAITING) {
      throw new Error(`Unknown ocgcore process result: ${status}`);
    }

    const forceProactive = stallSteps >= STALL_STEP_THRESHOLD;
    const hasRetry = messages.some((message) => message.type === OcgMessageType.RETRY);
    const retryPending = retryQueue.length > 0;

    if (hasRetry) {
      const activeRetryPrompt = activePromptFromMessages(promptMessages);
      if (
        activeRetryPrompt
        && lastPromptContext
        && activeRetryPrompt.type !== lastPromptContext.messageType
      ) {
        retryQueue = [];
        lastPromptContext = refreshPromptContext(lastPromptContext, promptMessages, lifePoints);
      }
      const autoDuringRetry = tryAutoRespond(promptMessages, lifePoints, { retryPass: true });
      if (autoDuringRetry) {
        emitLog({
          event: "retry_auto_response",
          reason: autoDuringRetry.reason,
          action: safe({ ...autoDuringRetry.action, response: undefined }),
        });
        lib.duelSetResponse(handle, autoDuringRetry.action.response);
        continue;
      }
      if (retryQueue.length > 0 && lastPromptContext?.messageType !== OcgMessageType.SELECT_CARD) {
        const retryAction = retryQueue.shift();
        lastPromptContext.triedKeys ??= new Set();
        lastPromptContext.triedKeys.add(responseKey(retryAction));
        emitLog({
          event: "retry_agent_alternate",
          prompt_type: lastPromptContext.messageType,
          remaining: retryQueue.length,
          action: safe({ ...retryAction, response: undefined }),
        });
        lib.duelSetResponse(handle, retryAction.response);
        continue;
      }
      const selectCardRetry = lastPromptContext?.messageType === OcgMessageType.SELECT_CARD;
      if (selectCardRetry && lastPromptContext) {
        const remainingSelect = remainingRetryActions(lastPromptContext);
        if (remainingSelect.length > 0) {
          lastPromptContext.selectRetries = (lastPromptContext.selectRetries ?? 0) + 1;
          const retryAction = remainingSelect[0];
          lastPromptContext.triedKeys.add(responseKey(retryAction));
          emitLog({
            event: "retry_select_card",
            remaining: remainingSelect.length - 1,
            attempt: lastPromptContext.selectRetries,
            action: safe({ ...retryAction, response: undefined }),
          });
          lib.duelSetResponse(handle, retryAction.response);
          continue;
        }
        const codeRetry = nextSelectCardCodeRetry(lastPromptContext);
        if (codeRetry) {
          emitLog({ event: "retry_code_response", action: safe({ ...codeRetry, response: undefined }) });
          lib.duelSetResponse(handle, codeRetry.response);
          continue;
        }
        if (!lastPromptContext.forceCancelTried) {
          lastPromptContext.forceCancelTried = true;
          emitLog({
            event: "retry_select_card_cancel",
            retries: lastPromptContext.selectRetries ?? 0,
            can_cancel: Boolean(lastPromptContext.selectable?.can_cancel),
          });
          lib.duelSetResponse(handle, { type: OcgResponseType.SELECT_CARD, indicies: null });
          continue;
        }
        emitLog({
          event: "retry_stuck_end",
          retry_only_steps: retryOnlySteps,
          decisions,
          duel_turn: duelTurn,
          last_prompt_type: lastPromptContext.messageType,
          last_select_card: {
            pick_count: selectCardPickCount(enrichSelectCardMessage(lastPromptContext.selectable)),
            cards: (lastPromptContext.selects ?? []).map((card, index) => ({
              index,
              code: Number(card.code ?? 0),
              name: cardName(Number(card.code ?? 0)),
            })),
            tried_index_responses: lastPromptContext.triedKeys?.size ?? 0,
            tried_code_responses: lastPromptContext.codeTriedKeys?.size ?? 0,
          },
        });
        const lpOutcome = outcomeFromLifePoints(lifePoints);
        emitResult({
          winner: lpOutcome?.winner ?? null,
          loser: lpOutcome?.loser ?? null,
          turns: Math.max(1, duelTurn),
          decisions,
          end_reason: "retry_stuck",
          life_points: [...lifePoints],
          tags: ["edopro", "ocgcore-wasm", "retry_stuck"],
          script_stats: scriptLoadStats,
        });
        return;
      }

      if (lastPromptContext && isPromptRetryType(lastPromptContext.messageType)) {
        lastPromptContext = refreshPromptContext(lastPromptContext, promptMessages, lifePoints);
        if ((lastPromptContext.promptRetries ?? 0) >= MAX_PROMPT_RETRIES_PER_PROMPT) {
          const passiveForced = passivePromptRetryAction(lastPromptContext);
          if (passiveForced) {
            emitLog({ event: "retry_prompt_cap_passive", attempt: lastPromptContext.promptRetries });
            lib.duelSetResponse(handle, passiveForced.response);
            continue;
          }
          emitPromptRetryStuckEnd(lastPromptContext, { retryOnlySteps, decisions, duelTurn, messages });
          const lpOutcome = outcomeFromLifePoints(lifePoints);
          emitResult({
            winner: lpOutcome?.winner ?? null,
            loser: lpOutcome?.loser ?? null,
            turns: Math.max(1, duelTurn),
            decisions,
            end_reason: "retry_stuck",
            life_points: [...lifePoints],
            tags: ["edopro", "ocgcore-wasm", "retry_stuck"],
            script_stats: scriptLoadStats,
          });
          return;
        }
        let remainingPrompt = remainingPromptRetryActions(lastPromptContext);
        if (remainingPrompt.length === 0) {
          remainingPrompt = remainingAnnounceCardRetryVariants(lastPromptContext);
        }
        if (remainingPrompt.length === 0) {
          remainingPrompt = remainingBattlecmdRetryVariants(lastPromptContext);
        }
        if (remainingPrompt.length > 0) {
          lastPromptContext.promptRetries = (lastPromptContext.promptRetries ?? 0) + 1;
          const retryAction = remainingPrompt[0];
          lastPromptContext.triedKeys.add(responseKey(retryAction));
          emitLog({
            event: "retry_prompt_action",
            prompt_type: lastPromptContext.messageType,
            remaining: remainingPrompt.length - 1,
            attempt: lastPromptContext.promptRetries,
            action: safe({ ...retryAction, response: undefined }),
          });
          lib.duelSetResponse(handle, retryAction.response);
          continue;
        }
        const passive = passivePromptRetryAction(lastPromptContext);
        if (passive) {
          emitLog({
            event: "retry_prompt_passive",
            prompt_type: lastPromptContext.messageType,
            action: safe({ ...passive, response: undefined }),
          });
          lib.duelSetResponse(handle, passive.response);
          continue;
        }
        emitPromptRetryStuckEnd(lastPromptContext, { retryOnlySteps, decisions, duelTurn, messages });
        const lpOutcome = outcomeFromLifePoints(lifePoints);
        emitResult({
          winner: lpOutcome?.winner ?? null,
          loser: lpOutcome?.loser ?? null,
          turns: Math.max(1, duelTurn),
          decisions,
          end_reason: "retry_stuck",
          life_points: [...lifePoints],
          tags: ["edopro", "ocgcore-wasm", "retry_stuck"],
          script_stats: scriptLoadStats,
        });
        return;
      }

      retryOnlySteps += 1;
      const unhandled = findUnhandledPrompt(messages, lifePoints);
      if (unhandled) {
        throw new Error(
          `Unhandled ocgcore prompt ${ocgMessageTypeStrings.get(unhandled.type) ?? unhandled.type}: ${JSON.stringify(safe(unhandled))}`,
        );
      }
      if (retryOnlySteps >= RETRY_STALL_THRESHOLD) {
        throw new Error(
          `ocgcore RETRY stall at LP ${lifePoints[0]}/${lifePoints[1]} (retry_only_steps=${retryOnlySteps}, messages=${JSON.stringify(safe(messages))}).`,
        );
      }
      if (retryQueue.length > 0) {
        const retryAction = retryQueue.shift();
        emitLog({ event: "retry_response", action: safe(retryAction) });
        lib.duelSetResponse(handle, retryAction.response);
        continue;
      }
      const contextRetry = nextRetryFromContext(lastPromptContext);
      if (contextRetry) {
        emitLog({ event: "retry_context_fallback", action: safe({ ...contextRetry, response: undefined }) });
        lib.duelSetResponse(handle, contextRetry.response);
        continue;
      }
      const fallback = pickSafeRetryResponse(messages, {
        preferYes: false,
        context: lastPromptContext,
      });
      if (fallback) {
        lastPromptContext ??= { triedKeys: new Set(), legalActions: [] };
        const key = responseKey(fallback);
        if (!lastPromptContext.triedKeys.has(key)) {
          lastPromptContext.triedKeys.add(key);
          emitLog({ event: "retry_safe_fallback", response: safe({ ...fallback, response: undefined }) });
          lib.duelSetResponse(handle, fallback.response);
          continue;
        }
      }
      emitPromptRetryStuckEnd(lastPromptContext, { retryOnlySteps, decisions, duelTurn, messages });
      const lpOutcome = outcomeFromLifePoints(lifePoints);
      emitResult({
        winner: lpOutcome?.winner ?? null,
        loser: lpOutcome?.loser ?? null,
        turns: Math.max(1, duelTurn),
        decisions,
        end_reason: "retry_stuck",
        life_points: [...lifePoints],
        tags: ["edopro", "ocgcore-wasm", "retry_stuck"],
        script_stats: scriptLoadStats,
      });
      return;
    }

    // While waiting for a RETRY after an agent response, only answer mandatory
    // hand-order prompts; other auto prompts would clobber retry context.
    const blockAutoRespond = (hasRetry && lastPromptContext?.messageType === OcgMessageType.SELECT_CARD)
      || (retryPending && lastPromptContext?.messageType === OcgMessageType.SELECT_CARD);
    const autoResponse = blockAutoRespond ? null : tryAutoRespond(messages, lifePoints);
    if (autoResponse) {
      if (autoResponse.action?.response && !retryPending) {
        lastPromptContext = {
          messageType: autoResponse.message.type,
          selectable: autoResponse.message,
          legalActions: autoResponse.legalActions.map((candidate) => ({ ...candidate })),
          triedKeys: new Set([responseKey(autoResponse.action)]),
        };
      }
      emitLog({
        event: "auto_response",
        reason: autoResponse.reason,
        action: safe({ ...autoResponse.action, response: undefined }),
      });
      lib.duelSetResponse(handle, autoResponse.action.response);
      continue;
    }

    if (maxDuelTurns > 0 && duelTurn > maxDuelTurns) {
      const forced = outcomeFromTurnLimit(lifePoints);
      emitLog({
        event: "max_duel_turns_reached",
        max_duel_turns: maxDuelTurns,
        duel_turn: duelTurn,
        life_points: [...lifePoints],
      });
      emitResult({
        winner: forced.winner,
        loser: forced.loser,
        turns: duelTurn,
        decisions,
        end_reason: "turn_limit",
        life_points: [...lifePoints],
        tags: ["edopro", "ocgcore-wasm", "turn_limit"],
        script_stats: scriptLoadStats,
      });
      return;
    }

    const selectable = [...messages].reverse().find((message) => {
      if (retryPending && message.type === OcgMessageType.SORT_CARD) {
        return false;
      }
      return legalActionsFor(message, lifePoints).length > 0;
    });
    if (!selectable) {
      if (retryPending && !hasRetry) {
        retryOnlySteps += 1;
        if (retryOnlySteps >= RETRY_PENDING_SPIN_LIMIT) {
          finishEngineStall({
            lifePoints,
            decisions,
            duelTurn,
            stallSteps,
            continueOnlyStreak,
            silenceMs: Date.now() - lastDuelOutboundAt,
            reason: "retry_pending_spin",
          });
          return;
        }
        continue;
      }
      throw new Error(`ocgcore is waiting, but no supported selectable message was emitted: ${JSON.stringify(safe(messages))}`);
    }
    retryOnlySteps = 0;

    const legalActions = legalActionsFor(selectable, lifePoints);
    const idleStallBreak = stallSteps >= STALL_IDLE_AGENT_THRESHOLD
      && (selectable.type === OcgMessageType.SELECT_IDLECMD
        || selectable.type === OcgMessageType.SELECT_BATTLECMD);
    if (
      forceProactive
      && !idleStallBreak
      && selectable.type !== OcgMessageType.SELECT_IDLECMD
      && selectable.type !== OcgMessageType.SELECT_BATTLECMD
    ) {
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
    const autoPass = chooseAutoPassAction(legalActions, { stallSteps });
    if (autoPass && !forceProactive && !idleStallBreak) {
      emitLog({
        event: "auto_pass_turn",
        selectable_type: ocgMessageTypeStrings.get(selectable.type) ?? selectable.type,
        selected_action: safe({ ...autoPass, response: undefined }),
      });
      lib.duelSetResponse(handle, autoPass.response);
      continue;
    }

    if (maxDecisions > 0 && decisions >= maxDecisions) {
      const forced = outcomeFromTurnLimit(lifePoints);
      emitLog({
        event: "max_decisions_reached",
        max_decisions: maxDecisions,
        duel_turn: duelTurn,
        life_points: [...lifePoints],
      });
      emitResult({
        winner: forced.winner,
        loser: forced.loser,
        turns: Math.max(1, duelTurn),
        decisions,
        end_reason: "max_decisions",
        life_points: [...lifePoints],
        tags: ["edopro", "ocgcore-wasm", "max_decisions"],
        script_stats: scriptLoadStats,
      });
      return;
    }
    if (maxDecisions === 0 && decisions >= SOFT_MAX_DECISIONS_UNLIMITED) {
      const forced = outcomeFromTurnLimit(lifePoints);
      emitLog({
        event: "soft_decision_cap",
        decisions,
        duel_turn: duelTurn,
        life_points: [...lifePoints],
      });
      emitResult({
        winner: forced.winner,
        loser: forced.loser,
        turns: Math.max(1, duelTurn),
        decisions,
        end_reason: "engine_stall",
        life_points: [...lifePoints],
        tags: ["edopro", "ocgcore-wasm", "decision_cap"],
        script_stats: scriptLoadStats,
      });
      return;
    }

    decisions += 1;
    lastDuelOutboundAt = Date.now();
    retryQueue = [];
    const stateId = `ocgcore-${decisions}`;
    let action;
    let requestedAction;
    let actionMessage;
    if (selectable.type === OcgMessageType.SELECT_CARD) {
      const enriched = enrichSelectCardMessage(selectable);
      action = chooseBestSelectCardAction(legalActions, enriched);
      requestedAction = action;
      actionMessage = { action_id: action.action_id };
      emitLog({
        event: "auto_select_card",
        state_id: stateId,
        selected_action: safe({ ...action, response: undefined }),
      });
    } else {
      const prompted = await promptAgentForActions({
        stateId,
        selectable,
        legalActions,
        lifePoints,
        duelTurn,
        decisions,
        reason: "decision",
      });
      action = prompted.action;
      requestedAction = prompted.requestedAction;
      actionMessage = prompted.actionMessage;
    }
    retryQueue = retryAlternatesFor(legalActions, action);
    lastPromptContext = {
      messageType: selectable.type,
      selectable,
      selects: selectable.type === OcgMessageType.SELECT_CARD
        ? (enrichSelectCardMessage(selectable).selects ?? [])
        : [],
      legalActions: legalActions.map((candidate) => ({ ...candidate })),
      triedKeys: new Set([responseKey(action)]),
      promptRevision: promptRevision(selectable),
      codeTriedKeys: new Set(),
      selectRetries: 0,
      forceCancelTried: false,
    };
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
  const summon = legalActions.find((action) => action.action_id.startsWith("normal-summon-"))
    ?? legalActions.find((action) => action.action_id.startsWith("special-summon-"));
  if (summon && requestedAction.action_id.startsWith("set-")) {
    return summon;
  }
  const activate = legalActions.find((action) => action.action_id.startsWith("activate-"));
  if (activate && requestedAction.action_id.startsWith("set-")) {
    return activate;
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

function finishEngineStall({
  lifePoints,
  decisions,
  duelTurn,
  stallSteps,
  continueOnlyStreak,
  silenceMs,
  reason = "engine_silence",
}) {
  const forced = outcomeFromTurnLimit(lifePoints);
  emitLog({
    event: "engine_stall",
    reason,
    stall_steps: stallSteps,
    continue_only_streak: continueOnlyStreak,
    silence_ms: silenceMs,
    life_points: [...lifePoints],
  });
  emitResult({
    winner: forced.winner,
    loser: forced.loser,
    turns: Math.max(1, duelTurn),
    decisions,
    end_reason: "engine_stall",
    life_points: [...lifePoints],
    tags: ["edopro", "ocgcore-wasm", "engine_stall"],
    script_stats: scriptLoadStats,
  });
}

function outcomeFromTurnLimit(lifePoints) {
  const lpOutcome = outcomeFromLifePoints(lifePoints);
  if (lpOutcome) {
    return lpOutcome;
  }
  if (lifePoints[0] === lifePoints[1]) {
    return { winner: null, loser: null };
  }
  if (lifePoints[0] > lifePoints[1]) {
    return { winner: playerName(0), loser: playerName(1) };
  }
  return { winner: playerName(1), loser: playerName(0) };
}

function findUnhandledPrompt(messages, lifePoints) {
  for (const message of messages) {
    if (message.type === OcgMessageType.RETRY) {
      continue;
    }
    if (!PROMPT_MESSAGE_TYPES.has(message.type)) {
      continue;
    }
    if (legalActionsFor(message, lifePoints).length > 0) {
      continue;
    }
    return message;
  }
  return null;
}

function tryAutoRespond(messages, lifePoints, { sortOnly = false, retryPass = false } = {}) {
  const autoTypes = sortOnly
    ? [OcgMessageType.SORT_CARD]
    : [
      OcgMessageType.SORT_CARD,
      OcgMessageType.SORT_CHAIN,
      OcgMessageType.SELECT_COUNTER,
      OcgMessageType.SELECT_SUM,
      OcgMessageType.SELECT_OPTION,
      OcgMessageType.ANNOUNCE_NUMBER,
      OcgMessageType.ANNOUNCE_RACE,
      OcgMessageType.ANNOUNCE_ATTRIB,
      OcgMessageType.ANNOUNCE_CARD,
    ];
  if (retryPass) {
    autoTypes.push(
      OcgMessageType.SELECT_PLACE,
      OcgMessageType.SELECT_DISFIELD,
      OcgMessageType.SELECT_POSITION,
      OcgMessageType.SELECT_YESNO,
      OcgMessageType.SELECT_EFFECTYN,
      OcgMessageType.SELECT_TRIBUTE,
    );
  }
  for (const type of autoTypes) {
    const message = [...messages].reverse().find((entry) => entry.type === type);
    if (!message) {
      continue;
    }
    const actions = legalActionsFor(message, lifePoints);
    if (actions.length === 0) {
      continue;
    }
    const action = pickRetryAutoAction(type, actions, message);
    if (!action) {
      continue;
    }
    return {
      reason: ocgMessageTypeStrings.get(type) ?? type,
      message,
      legalActions: actions,
      action,
    };
  }
  if (retryPass) {
    const chainMessage = [...messages].reverse().find((entry) => entry.type === OcgMessageType.SELECT_CHAIN);
    if (chainMessage) {
      const actions = legalActionsFor(chainMessage, lifePoints);
      const decline = actions.find((candidate) => candidate.action_id === "decline-chain");
      const action = decline ?? actions[0];
      if (action) {
        return {
          reason: ocgMessageTypeStrings.get(OcgMessageType.SELECT_CHAIN) ?? OcgMessageType.SELECT_CHAIN,
          message: chainMessage,
          legalActions: actions,
          action,
        };
      }
    }
    const unselectMessage = [...messages].reverse().find(
      (entry) => entry.type === OcgMessageType.SELECT_UNSELECT_CARD,
    );
    if (unselectMessage) {
      const actions = legalActionsFor(unselectMessage, lifePoints);
      const finish = actions.find((candidate) => candidate.action_id === "finish-selection");
      const cancel = actions.find((candidate) => candidate.action_id === "cancel-selection");
      const action = finish ?? cancel ?? actions[0];
      if (action) {
        return {
          reason: ocgMessageTypeStrings.get(OcgMessageType.SELECT_UNSELECT_CARD)
            ?? OcgMessageType.SELECT_UNSELECT_CARD,
          message: unselectMessage,
          legalActions: actions,
          action,
        };
      }
    }
  }
  return null;
}

function pickRetryAutoAction(type, actions, message) {
  if (type === OcgMessageType.SELECT_PLACE || type === OcgMessageType.SELECT_DISFIELD) {
    return actions[0];
  }
  if (type === OcgMessageType.SELECT_POSITION) {
    return actions[0];
  }
  if (type === OcgMessageType.SELECT_YESNO) {
    return actions.find((candidate) => candidate.action_id === "no") ?? actions[0];
  }
  if (type === OcgMessageType.SELECT_EFFECTYN) {
    return actions.find((candidate) => candidate.action_id === "decline-effect") ?? actions[0];
  }
  if (type === OcgMessageType.SELECT_TRIBUTE) {
    return actions[0];
  }
  if (type === OcgMessageType.ANNOUNCE_CARD) {
    const real = actions.find((action) => !action.action_id.includes("fallback"));
    return real ?? actions[0];
  }
  return actions[0];
}

function enrichSelectCardMessage(message) {
  if (!lib || !handle || message.type !== OcgMessageType.SELECT_CARD) {
    return message;
  }
  const selects = (message.selects ?? []).map((card) => {
    try {
      const info = lib.duelQuery(handle, {
        flags: OcgQueryFlags.CODE,
        controller: card.controller,
        location: card.location,
        sequence: card.sequence,
      });
      if (info?.code) {
        return { ...card, code: info.code };
      }
    } catch (_error) {
      // keep parsed card when query fails
    }
    return card;
  });
  return { ...message, selects };
}

function responseKey(responseOrAction) {
  const action = responseOrAction?.response != null ? responseOrAction : null;
  const response = action?.response ?? responseOrAction;
  const actionId = action?.action_id ?? null;
  return JSON.stringify(
    { action_id: actionId, response },
    (_key, value) => (typeof value === "bigint" ? value.toString() : value),
  );
}

function retryAlternatesFor(legalActions, chosenAction) {
  const chosenKey = responseKey(chosenAction);
  return legalActions.filter((candidate) => responseKey(candidate) !== chosenKey);
}

function resolvePromptMessages(messages, lastObservable) {
  const live = messages.filter((message) => message.type !== OcgMessageType.RETRY);
  if (live.length > 0) {
    return messages;
  }
  return lastObservable.length > 0 ? lastObservable : messages;
}

function activePromptFromMessages(messages) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.type === OcgMessageType.RETRY) {
      continue;
    }
    if (PROMPT_MESSAGE_TYPES.has(message.type)) {
      return message;
    }
  }
  return null;
}

function opponentFieldHasMonsters(activePlayer) {
  if (!lib || !handle) {
    return false;
  }
  const opponent = activePlayer === 0 ? 1 : 0;
  for (let sequence = 0; sequence < 7; sequence += 1) {
    try {
      const info = lib.duelQuery(handle, {
        flags: OcgQueryFlags.CODE,
        controller: opponent,
        location: OcgLocation.MZONE,
        sequence,
      });
      if (info?.code) {
        return true;
      }
    } catch (_error) {
      // ignore missing zones
    }
  }
  return false;
}

function isPromptRetryType(messageType) {
  return PROMPT_MESSAGE_TYPES.has(messageType)
    && messageType !== OcgMessageType.SELECT_CARD;
}

function promptRevision(message) {
  if (!message) {
    return "";
  }
  switch (message.type) {
    case OcgMessageType.SELECT_IDLECMD:
      return [
        message.player,
        message.summons?.length ?? 0,
        message.special_summons?.length ?? 0,
        message.pos_changes?.length ?? 0,
        message.activates?.length ?? 0,
        message.monster_sets?.length ?? 0,
        message.spell_sets?.length ?? 0,
        message.shuffle ? 1 : 0,
        message.to_bp ? 1 : 0,
        message.to_ep ? 1 : 0,
      ].join(":");
    case OcgMessageType.SELECT_BATTLECMD:
      return [
        message.player,
        message.attacks?.length ?? 0,
        message.chains?.length ?? 0,
        message.to_ep ? 1 : 0,
        message.to_m2 ? 1 : 0,
      ].join(":");
    case OcgMessageType.SELECT_CHAIN:
      return [
        message.forced ? 1 : 0,
        message.selects?.length ?? 0,
        (message.selects ?? []).map((card) => Number(card.code ?? 0)).join(","),
      ].join(":");
    case OcgMessageType.SELECT_PLACE:
    case OcgMessageType.SELECT_DISFIELD:
      return [message.player, message.field_mask >>> 0, message.count ?? 0].join(":");
    case OcgMessageType.SELECT_UNSELECT_CARD:
      return [
        message.select_cards?.length ?? 0,
        message.unselect_cards?.length ?? 0,
        message.can_cancel ? 1 : 0,
        message.can_finish ? 1 : 0,
      ].join(":");
    case OcgMessageType.ANNOUNCE_CARD:
      return [
        message.player,
        message.opcodes?.length ?? 0,
        (message.opcodes ?? []).map((opcode) => String(opcode)).join(","),
      ].join(":");
    default:
      return `${message.type}:${message.player ?? ""}`;
  }
}

function refreshPromptContext(context, messages, lifePoints) {
  if (!context) {
    return context;
  }
  const prompt = activePromptFromMessages(messages);
  if (!prompt) {
    return context;
  }
  const legalActions = legalActionsFor(prompt, lifePoints);
  if (!legalActions.length) {
    return context;
  }
  const revision = promptRevision(prompt);
  const promptChanged = prompt.type !== context.messageType || revision !== context.promptRevision;
  return {
    ...context,
    messageType: prompt.type,
    selectable: prompt,
    promptRevision: revision,
    legalActions: legalActions.map((candidate) => ({ ...candidate })),
    triedKeys: promptChanged ? new Set() : (context.triedKeys ?? new Set()),
    promptRetries: promptChanged ? 0 : (context.promptRetries ?? 0),
    agentRetryReprompt: promptChanged ? false : context.agentRetryReprompt,
  };
}

function promptRetryPriority(action) {
  const id = action.action_id;
  if (id === "no" || id === "decline-effect" || id === "decline-chain") {
    return 0;
  }
  if (id === "cancel-selection" || id === "finish-selection") {
    return 5;
  }
  if (id === "to-end-phase" || id === "to-main-phase-2") {
    return 10;
  }
  if (id === "to-battle-phase") {
    return 15;
  }
  if ((action.tags ?? []).includes("decline")) {
    return 20;
  }
  if (id.startsWith("unselect-")) {
    return 200;
  }
  if (id.startsWith("select-unselect-")) {
    return 250;
  }
  if (id.startsWith("set-")) {
    return 400;
  }
  if (id.startsWith("pos-change-")) {
    return 420;
  }
  if (id === "shuffle-hand") {
    return 425;
  }
  if (id.startsWith("option-")) {
    return 450;
  }
  if (id.startsWith("place-")) {
    return 480;
  }
  if (id.startsWith("position-")) {
    return 490;
  }
  if (id.startsWith("chain-") || id.startsWith("battle-chain-")) {
    return 600;
  }
  if (id.startsWith("special-summon-") || id.startsWith("normal-summon-")) {
    return 750;
  }
  if (id.startsWith("attack-")) {
    return 800;
  }
  if (id.startsWith("activate-") || id === "activate-effect") {
    return 900;
  }
  return 500;
}

function remainingPromptRetryActions(context) {
  const remaining = remainingRetryActions(context);
  return remaining.sort((left, right) => promptRetryPriority(left) - promptRetryPriority(right));
}

function remainingAnnounceCardRetryVariants(context) {
  if (context?.messageType !== OcgMessageType.ANNOUNCE_CARD || !context.selectable) {
    return [];
  }
  context.triedKeys ??= new Set();
  const refreshed = legalActionsFor(context.selectable, [8000, 8000]);
  const remaining = refreshed.filter((action) => !context.triedKeys.has(responseKey(action)));
  return remaining.sort((left, right) => cardName(left.response?.card ?? 0).localeCompare(cardName(right.response?.card ?? 0)));
}

function remainingBattlecmdRetryVariants(context) {
  if (context?.messageType !== OcgMessageType.SELECT_BATTLECMD || !context.legalActions?.length) {
    return [];
  }
  context.triedKeys ??= new Set();
  const variants = [];
  for (const action of context.legalActions) {
    if (action.response?.type !== OcgResponseType.SELECT_BATTLECMD) {
      continue;
    }
    const alternates = battlecmdResponseAlternates(action.response);
    for (const [alternateIndex, response] of alternates.entries()) {
      if (alternateIndex === 0) {
        continue;
      }
      const candidate = {
        ...action,
        action_id: `${action.action_id}~alt${alternateIndex}`,
        response,
      };
      if (!context.triedKeys.has(responseKey(candidate))) {
        variants.push(candidate);
      }
    }
  }
  return variants.sort((left, right) => promptRetryPriority(left) - promptRetryPriority(right));
}

function battlecmdResponseAlternates(response) {
  if (response?.type !== OcgResponseType.SELECT_BATTLECMD) {
    return [response];
  }
  const alternates = [response];
  const indexVariants = response.action === SelectBattleCMDAction.SELECT_BATTLE
    ? [response.index, 0]
    : [null, 0, -1];
  const baseKey = responseKey({ action_id: "battlecmd", response });
  for (const index of indexVariants) {
    const candidate = { ...response, index };
    if (responseKey({ action_id: "battlecmd", response: candidate }) !== baseKey) {
      alternates.push(candidate);
    }
  }
  return alternates;
}

async function tryAgentRetryRechoose(_args) {
  // Disabled during training: re-prompting the Python agent on RETRY adds IPC churn and
  // can look like a hung job when the engine is only cycling retries.
  return null;
}

function passivePromptRetryAction(context) {
  if (!context?.legalActions?.length) {
    return null;
  }
  context.triedKeys ??= new Set();
  const preferredIds = [
    "no",
    "decline-effect",
    "decline-chain",
    "cancel-selection",
    "finish-selection",
    "to-end-phase",
    "to-main-phase-2",
    "to-battle-phase",
  ];
  for (const actionId of preferredIds) {
    const action = context.legalActions.find((candidate) => candidate.action_id === actionId);
    if (!action) {
      continue;
    }
    const key = responseKey(action);
    if (context.triedKeys.has(key)) {
      continue;
    }
    context.triedKeys.add(key);
    return action;
  }
  for (const action of context.legalActions) {
    if (!(action.tags ?? []).includes("decline")) {
      continue;
    }
    const key = responseKey(action);
    if (context.triedKeys.has(key)) {
      continue;
    }
    context.triedKeys.add(key);
    return action;
  }
  return null;
}

function emitPromptRetryStuckEnd(context, { retryOnlySteps, decisions, duelTurn, messages = [] }) {
  const messageTypes = messages.map((message) => ocgMessageTypeStrings.get(message.type) ?? message.type);
  emitLog({
    event: "retry_stuck_end",
    retry_only_steps: retryOnlySteps,
    decisions,
    duel_turn: duelTurn,
    last_prompt_type: context?.messageType ?? null,
    last_prompt_name: context?.messageType != null
      ? (ocgMessageTypeStrings.get(context.messageType) ?? context.messageType)
      : null,
    prompt_revision: context?.promptRevision ?? null,
    legal_action_count: context?.legalActions?.length ?? 0,
    tried_responses: context?.triedKeys?.size ?? 0,
    message_types: messageTypes,
    pending_action_ids: (context?.legalActions ?? [])
      .filter((action) => !context?.triedKeys?.has(responseKey(action)))
      .map((action) => action.action_id),
    tried_action_ids: (context?.legalActions ?? [])
      .filter((action) => context?.triedKeys?.has(responseKey(action)))
      .map((action) => action.action_id),
  });
}

function nextRetryFromContext(context) {
  if (!context?.legalActions?.length) {
    return null;
  }
  context.triedKeys ??= new Set();
  for (const action of context.legalActions) {
    const key = responseKey(action);
    if (context.triedKeys.has(key)) {
      continue;
    }
    context.triedKeys.add(key);
    return action;
  }
  return null;
}

function pickSafeRetryResponse(messages, { preferYes = false, context = null } = {}) {
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
    if (actions.length > 0) {
      return actions[0];
    }
  }
  const contextFallback = pickRetryFallbackFromContext(context);
  if (contextFallback) {
    return contextFallback;
  }
  return null;
}

function selectCardActionSortKey(action, context = null) {
  if (action.response?.type === OcgResponseType.SELECT_CARD_CODES) {
    const codes = action.response?.codes ?? [];
    const selects = context?.selects ?? [];
    const indices = codes
      .map((code) => selects.findIndex((card) => Number(card.code ?? 0) === Number(code)))
      .filter((index) => index >= 0);
    const tagScore = indices.length ? scoreSelectCombo(selects, indices) : 0;
    return -20_000 - tagScore;
  }
  if (action.action_id.startsWith("select-card-combo-")) {
    const indices = action.response?.indicies;
    const selects = context?.selects;
    if (Array.isArray(indices)) {
      const indexSum = indices.reduce((sum, index) => sum + index, 0);
      const tagScore = Array.isArray(selects) ? scoreSelectCombo(selects, indices) : 0;
      return indexSum * 10 - tagScore;
    }
    return 500 + action.action_id.length;
  }
  const match = action.action_id.match(/^select-card-(\d+)$/);
  if (match) {
    return Number(match[1]);
  }
  if (action.action_id === "cancel-select-card") {
    return 10_000;
  }
  return 9_000;
}

function nextSelectCardCodeRetry(context) {
  if (context?.messageType !== OcgMessageType.SELECT_CARD) {
    return null;
  }
  context.codeTriedKeys ??= new Set();
  for (const action of context.legalActions ?? []) {
    if (action.response?.type !== OcgResponseType.SELECT_CARD_CODES) {
      continue;
    }
    const key = responseKey(action);
    if (context.codeTriedKeys.has(key)) {
      continue;
    }
    context.codeTriedKeys.add(key);
    return action;
  }
  return null;
}

function remainingRetryActions(context) {
  if (!context?.legalActions?.length) {
    return [];
  }
  context.triedKeys ??= new Set();
  const remaining = context.legalActions.filter(
    (action) => !context.triedKeys.has(responseKey(action)),
  );
  if (context.messageType === OcgMessageType.SELECT_CARD) {
    return remaining.sort(
      (left, right) => selectCardActionSortKey(left, context) - selectCardActionSortKey(right, context),
    );
  }
  return remaining;
}

function pickRetryFallbackFromContext(context) {
  if (!context?.legalActions?.length) {
    return null;
  }
  context.triedKeys ??= new Set();
  const cancel = context.legalActions.find(
    (action) => action.action_id.startsWith("cancel-") || (action.tags ?? []).includes("decline"),
  );
  if (cancel) {
    const key = responseKey(cancel);
    if (!context.triedKeys.has(key)) {
      context.triedKeys.add(key);
      return cancel;
    }
  }
  return nextRetryFromContext(context);
}

async function promptAgentForActions({
  stateId,
  selectable,
  legalActions,
  lifePoints,
  duelTurn,
  decisions,
  reason,
}) {
  emitLog({
    event: "decision_state",
    state_id: stateId,
    reason,
    selectable_type: ocgMessageTypeStrings.get(selectable.type) ?? selectable.type,
    player: selectable.player,
    legal_action_ids: legalActions.map((action) => action.action_id),
    ...(selectable.type === OcgMessageType.SELECT_CARD
      ? {
        select_card: {
          min: selectable.min,
          max: selectable.max,
          can_cancel: Boolean(selectable.can_cancel),
          count: selectable.selects?.length ?? 0,
          codes: (selectable.selects ?? []).map((card) => card.code),
          locations: (selectable.selects ?? []).map((card) => ({
            controller: card.controller,
            location: card.location,
            sequence: card.sequence,
          })),
        },
      }
      : {}),
  });
  lastDuelOutboundAt = Date.now();
  emit({
    type: "state",
    state: {
      state_id: stateId,
      turn: Math.max(1, duelTurn),
      duel_turn: Math.max(1, duelTurn),
      decision_index: decisions,
      active_player: playerName(selectable.player ?? 0),
      summary: summarizeMessage(selectable, lifePoints),
      legal_actions: legalActions.map(({ response: _response, ...action }) => action),
      public_zones: visibleZonesFor(selectable.player ?? 0, lifePoints),
      ...(selectable.type === OcgMessageType.SELECT_CARD
        ? {
          select_card: {
            min: Number(selectable.min ?? 1),
            max: Number(selectable.max ?? selectable.min ?? 1),
            pick_count: selectCardPickCount(selectable),
            can_cancel: Boolean(selectable.can_cancel),
            cards: (selectable.selects ?? []).map((card, index) => ({
              index,
              code: Number(card.code ?? 0),
              name: cardName(Number(card.code ?? 0)),
            })),
          },
        }
        : {}),
    },
  });

  const actionMessage = await readJsonLine();
  const requestedAction = legalActions.find((candidate) => candidate.action_id === actionMessage.action_id);
  if (!requestedAction) {
    throw new Error(`Unknown action_id ${actionMessage.action_id} for state ${stateId}.`);
  }
  const action = forceLpPressureAction(legalActions, requestedAction) ?? requestedAction;
  return { action, requestedAction, actionMessage };
}

function ensureOcgcoreSelectPatch() {
  const gatewayRoot = path.dirname(fileURLToPath(import.meta.url));
  const target = path.join(gatewayRoot, "node_modules/ocgcore-wasm/dist/index.js");
  if (!existsSync(target)) {
    emitLog({
      event: "ocgcore_patch_warning",
      message: "ocgcore-wasm is not installed; run npm install in gateways/edopro-ocgcore.",
    });
    return;
  }
  const source = readFileSync(target, "utf8");
  if (source.includes(OCGCORE_SELECT_PATCH_MARKER)) {
    emitLog({ event: "ocgcore_patch_ok", message: "SELECT_CARD list encoding is active (patch-ocgcore-select.mjs)." });
    return;
  }
  throw new Error(
    "ocgcore-wasm SELECT_CARD patch is missing. From gateways/edopro-ocgcore run: npm install && node patch-ocgcore-select.mjs",
  );
}

function isPressureAction(action) {
  const tags = action.tags ?? [];
  const id = action.action_id;
  if (id.startsWith("attack-") || id.startsWith("normal-summon-") || id.startsWith("special-summon-")) {
    return true;
  }
  if (id.startsWith("activate-") || id === "activate-effect" || id === "to-battle-phase") {
    return true;
  }
  if (tags.includes("lethal") || tags.includes("direct-attack")) {
    return true;
  }
  return id.startsWith("set-monster-");
}

function chooseAutoPassAction(legalActions, { stallSteps = 0 } = {}) {
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
  const requirePressure = stallSteps >= STALL_IDLE_AGENT_THRESHOLD;
  const hasProactiveAction = legalActions.some((action) => {
    if (requirePressure && isPressureAction(action)) {
      return true;
    }
    const tags = action.tags ?? [];
    const hasActiveTag = tags.some((tag) => !passiveTags.has(tag));
    const hasActivePrefix = !passivePrefixes.some((prefix) => action.action_id.startsWith(prefix));
    return !requirePressure && hasActiveTag && hasActivePrefix;
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
  if (message.type === OcgMessageType.SELECT_CARD) {
    message = enrichSelectCardMessage(message);
  }
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
    case OcgMessageType.SORT_CARD:
    case OcgMessageType.SORT_CHAIN:
      return sortCardActions(message);
    case OcgMessageType.SELECT_COUNTER:
      return selectCounterActions(message);
    case OcgMessageType.SELECT_SUM:
      return selectSumActions(message);
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
    const display = typeof option === "bigint" ? Number(option) : Number(option);
    return {
      action_id: `announce-number-${index}`,
      label: `Announce ${Number.isFinite(display) ? display : String(option)}`,
      tags: ["announce-number", "option"],
      // ocgcore expects the selected option index, not the announced value.
      response: { type: OcgResponseType.ANNOUNCE_NUMBER, value: index },
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

function normalizeAnnounceOpcodes(opcodes) {
  return (opcodes ?? []).map((opcode) => {
    if (typeof opcode === "bigint") {
      return opcode;
    }
    const numeric = Number(opcode);
    return Number.isFinite(numeric) ? BigInt(numeric) : 0n;
  });
}

function appendAnnounceCandidate(codes, seen, code) {
  const normalized = Number(code);
  if (!normalized || seen.has(normalized)) {
    return;
  }
  seen.add(normalized);
  codes.push(normalized);
}

function collectZoneCodes(team, location, codes, seen) {
  if (!lib || !handle) {
    return;
  }
  const count = lib.duelQueryCount(handle, team, location);
  for (let sequence = 0; sequence < count; sequence += 1) {
    try {
      const info = lib.duelQuery(handle, {
        flags: OcgQueryFlags.CODE,
        controller: team,
        location,
        sequence,
      });
      if (info?.code) {
        appendAnnounceCandidate(codes, seen, info.code);
      }
    } catch (_error) {
      // ignore missing slots
    }
  }
}

function collectAnnounceCandidateCodes(player) {
  const codes = [];
  const seen = new Set();
  if (!lib || !handle) {
    for (const code of duelDeckCards) {
      appendAnnounceCandidate(codes, seen, code);
    }
    return codes;
  }
  collectZoneCodes(player, OcgLocation.HAND, codes, seen);
  const locations = [
    OcgLocation.GRAVE,
    OcgLocation.MZONE,
    OcgLocation.SZONE,
    OcgLocation.DECK,
    OcgLocation.EXTRA,
    OcgLocation.REMOVED,
  ];
  for (let team = 0; team < 2; team += 1) {
    for (const location of locations) {
      collectZoneCodes(team, location, codes, seen);
    }
  }
  for (const code of duelDeckCards) {
    appendAnnounceCandidate(codes, seen, code);
  }
  return codes;
}

function cardMatchesAnnounceOpcodes(card, opcodes) {
  const normalized = normalizeAnnounceOpcodes(opcodes);
  if (!normalized.length) {
    return true;
  }
  try {
    return Boolean(cardMatchesOpcode(card, normalized));
  } catch (_error) {
    return false;
  }
}

function pickLooseAnnounceCard(player, opcodes) {
  const normalized = normalizeAnnounceOpcodes(opcodes);
  for (const code of collectAnnounceCandidateCodes(player)) {
    const card = database?.readCard(code);
    if (!card) {
      continue;
    }
    if (normalized.length && !cardMatchesOpcode(card, normalized)) {
      continue;
    }
    return code;
  }
  for (const code of collectAnnounceCandidateCodes(player)) {
    if (database?.readCard(code)) {
      return code;
    }
  }
  return null;
}

function announceCardActions(message) {
  const opcodes = message.opcodes ?? [];
  const matches = [];
  const seen = new Set();
  for (const code of collectAnnounceCandidateCodes(message.player)) {
    if (seen.has(code)) {
      continue;
    }
    const card = database?.readCard(code);
    if (!card) {
      continue;
    }
    if (!cardMatchesAnnounceOpcodes(card, opcodes)) {
      continue;
    }
    seen.add(code);
    matches.push(code);
  }
  matches.sort((left, right) => cardName(left).localeCompare(cardName(right)));
  if (matches.length === 0) {
    const looseCode = pickLooseAnnounceCard(message.player, opcodes);
    if (looseCode) {
      emitLog({
        event: "announce_card_loose_fallback",
        player: message.player,
        opcode_count: opcodes.length,
        pick: cardName(looseCode),
      });
      return [{
        action_id: "announce-card-loose-0",
        label: `Announce ${cardName(looseCode)}`,
        tags: ["announce-card", "fallback"],
        response: { type: OcgResponseType.ANNOUNCE_CARD, card: looseCode },
      }];
    }
    emitLog({
      event: "announce_card_no_matches",
      player: message.player,
      opcode_count: opcodes.length,
      candidates: collectAnnounceCandidateCodes(message.player).length,
    });
    return [];
  }
  emitLog({
    event: "announce_card_candidates",
    player: message.player,
    opcode_count: opcodes.length,
    match_count: matches.length,
    sample: matches.slice(0, 5).map((code) => cardName(code)),
  });
  return matches.map((code, index) => ({
    action_id: `announce-card-${index}`,
    label: `Announce ${cardName(code)}`,
    tags: ["announce-card", ...effectTagsForCard(code)],
    response: { type: OcgResponseType.ANNOUNCE_CARD, card: code },
  }));
}

function selectCardPickCounts(message) {
  const selects = message.selects ?? [];
  if (selects.length === 0) {
    return [];
  }
  const min = Math.max(0, Number(message.min ?? 1));
  const max = Math.min(Number(message.max ?? min), selects.length);
  const counts = [];
  for (let pickCount = min; pickCount <= max; pickCount += 1) {
    counts.push(pickCount);
  }
  return counts.length ? counts : [Math.min(selects.length, Math.max(1, min))];
}

function selectCardPickCount(message) {
  const counts = selectCardPickCounts(message);
  if (!counts.length) {
    return 0;
  }
  const min = Math.max(0, Number(message.min ?? 1));
  const max = Math.min(Number(message.max ?? min), (message.selects ?? []).length);
  return min === max ? counts[0] : max;
}

function selectCardCombinationCount(total, pickCount) {
  if (pickCount <= 0 || pickCount > total) {
    return 0;
  }
  let combos = 1;
  for (let index = 0; index < pickCount; index += 1) {
    combos = (combos * (total - index)) / (index + 1);
  }
  return Math.round(combos);
}

function selectCardCombinationLimit(total, pickCount) {
  const totalCombos = selectCardCombinationCount(total, pickCount);
  if (totalCombos <= 512) {
    return totalCombos;
  }
  return 512;
}

function selectCardCombinations(total, pickCount, maxCombos = selectCardCombinationLimit(total, pickCount)) {
  const combos = [];
  const picked = [];
  function walk(start) {
    if (combos.length >= maxCombos) {
      return;
    }
    if (picked.length === pickCount) {
      combos.push([...picked]);
      return;
    }
    for (let index = start; index < total; index += 1) {
      picked.push(index);
      walk(index + 1);
      picked.pop();
    }
  }
  walk(0);
  return combos;
}

function scoreSelectCombo(selects, combo) {
  let score = 0;
  for (const index of combo) {
    const code = Number(selects[index]?.code ?? 0);
    for (const tag of effectTagsForCard(code)) {
      score += 10;
      if (["removal", "negate", "search", "special-summon"].includes(tag)) {
        score += 5;
      }
    }
    if (duelDeckCards.includes(code)) {
      score += 3;
    }
  }
  return score;
}

function chooseBestSelectCardAction(legalActions, message) {
  const selects = message.selects ?? [];
  const candidates = legalActions.filter(
    (action) => action.action_id.startsWith("select-card")
      || action.action_id === "cancel-select-card",
  );
  if (!candidates.length) {
    return legalActions[0];
  }
  const context = { messageType: OcgMessageType.SELECT_CARD, selects };
  return candidates
    .sort((left, right) => selectCardActionSortKey(left, context) - selectCardActionSortKey(right, context))[0];
}

function pushSelectCardCodeActions(actions, selects, combo) {
  const codes = combo.map((index) => Number(selects[index]?.code ?? 0)).filter((code) => code > 0);
  if (!codes.length) {
    return;
  }
  const label = combo.map((index) => cardName(Number(selects[index]?.code ?? 0))).join(", ");
  actions.push({
    action_id: `select-card-codes-${combo.join("-")}`,
    label: `Select by code: ${label}`,
    tags: ["select-card", ...new Set(codes.flatMap((code) => effectTagsForCard(code)))],
    response: {
      type: OcgResponseType.SELECT_CARD_CODES,
      codes,
    },
  });
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
  const selects = message.selects ?? [];
  for (const pickCount of selectCardPickCounts(message)) {
    if (pickCount <= 0) {
      continue;
    }
    if (pickCount > 1) {
      const combos = selectCardCombinations(selects.length, pickCount)
        .sort((left, right) => scoreSelectCombo(selects, right) - scoreSelectCombo(selects, left));
      combos.forEach((combo) => {
        const codes = combo.map((index) => Number(selects[index]?.code ?? 0));
        const label = combo.map((index) => cardName(Number(selects[index]?.code ?? 0))).join(", ");
        actions.push({
          action_id: `select-card-combo-${combo.join("-")}`,
          label: `Select ${label}`,
          tags: ["select-card", ...new Set(codes.flatMap((code) => effectTagsForCard(code)))],
          response: {
            type: OcgResponseType.SELECT_CARD,
            indicies: combo,
          },
        });
        pushSelectCardCodeActions(actions, selects, combo);
      });
      continue;
    }
    selects.forEach((card, index) => {
      const code = Number(card.code ?? 0);
      actions.push({
        action_id: `select-card-${index}`,
        label: `Select ${cardName(code)}`,
        tags: ["select-card", ...effectTagsForCard(code)],
        response: {
          type: OcgResponseType.SELECT_CARD,
          indicies: [index],
        },
      });
      if (code > 0) {
        actions.push({
          action_id: `select-card-code-${index}`,
          label: `Select ${cardName(code)} (by code)`,
          tags: ["select-card", ...effectTagsForCard(code)],
          response: {
            type: OcgResponseType.SELECT_CARD_CODES,
            codes: [code],
          },
        });
      }
    });
  }
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
  const pickCount = selectCardPickCount(message);
  if (pickCount === 0) {
    return actions;
  }
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
  if (count <= 1) {
    return [preferredIndex];
  }
  return selectCardCombinations(total, count, 1)[0] ?? [preferredIndex];
}

function sortCardActions(message) {
  const cards = message.cards ?? [];
  if (cards.length === 0) {
    return [];
  }
  const identityOrder = cards.map((_card, index) => index);
  const label = cards.length === 1
    ? `Confirm order for ${cardName(cards[0].code)}`
    : `Keep ${cards.length} cards in current order`;
  return [{
    action_id: "sort-cards-identity",
    label,
    tags: ["sort-card", "shuffle"],
    response: { type: OcgResponseType.SORT_CARD, order: identityOrder },
  }];
}

function selectCounterActions(message) {
  const cards = message.cards ?? [];
  const target = Math.max(0, Number(message.count ?? 0));
  if (cards.length === 0) {
    return [{
      action_id: "select-counter-empty",
      label: "Confirm counter selection",
      tags: ["select-counter"],
      response: { type: OcgResponseType.SELECT_COUNTER, counters: [] },
    }];
  }
  const counters = new Array(cards.length).fill(0);
  let remaining = target;
  for (let index = 0; index < cards.length && remaining > 0; index += 1) {
    const available = Math.max(0, Number(cards[index].count ?? 0));
    const take = Math.min(available, remaining);
    counters[index] = take;
    remaining -= take;
  }
  return [{
    action_id: "select-counter-default",
    label: `Remove ${target} counter(s)`,
    tags: ["select-counter"],
    response: { type: OcgResponseType.SELECT_COUNTER, counters },
  }];
}

function selectSumActions(message) {
  const optional = message.selects ?? [];
  const must = message.selects_must ?? [];
  const targetAmount = Number(message.amount ?? 0);
  const minPicks = Math.max(0, Number(message.min ?? 0));
  const maxPicks = Math.max(minPicks, Number(message.max ?? optional.length));
  const mustSum = must.reduce((total, card) => total + Number(card.amount ?? 0), 0);
  let remaining = Math.max(0, targetAmount - mustSum);
  const picked = [];
  for (let index = 0; index < optional.length; index += 1) {
    if (picked.length >= maxPicks) {
      break;
    }
    if (remaining <= 0 && picked.length >= minPicks) {
      break;
    }
    picked.push(index);
    remaining -= Number(optional[index].amount ?? 0);
  }
  while (picked.length < minPicks && picked.length < optional.length) {
    const next = picked.length;
    if (!picked.includes(next)) {
      picked.push(next);
    } else {
      break;
    }
  }
  return [{
    action_id: "select-sum-default",
    label: `Select materials totalling ${targetAmount}`,
    tags: ["select-sum"],
    response: { type: OcgResponseType.SELECT_SUM, indicies: picked },
  }];
}

function idleActions(message, lifePoints) {
  const actions = [];
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
  (message.pos_changes ?? []).forEach((card, index) => {
    const tags = ["pos-change", ...effectTagsForCard(card.code)];
    actions.push({
      action_id: `pos-change-${index}`,
      label: `Change position of ${cardName(card.code)}`,
      expected_value: expectedValueForTags(tags),
      tags,
      response: { type: OcgResponseType.SELECT_IDLECMD, action: SelectIdleCMDAction.SELECT_POS_CHANGE, index },
    });
  });
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
  if (message.shuffle) {
    actions.push({
      action_id: "shuffle-hand",
      label: "Shuffle hand",
      tags: ["shuffle"],
      response: { type: OcgResponseType.SELECT_IDLECMD, action: SelectIdleCMDAction.SHUFFLE, index: null },
    });
  }
  if (message.to_bp) {
    actions.push({ action_id: "to-battle-phase", label: "Go to Battle Phase", tags: ["phase"], response: { type: OcgResponseType.SELECT_IDLECMD, action: SelectIdleCMDAction.TO_BP, index: null } });
  }
  if (message.to_ep) {
    actions.push({ action_id: "to-end-phase", label: "Go to End Phase", tags: ["phase"], response: { type: OcgResponseType.SELECT_IDLECMD, action: SelectIdleCMDAction.TO_EP, index: null } });
  }
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
    if (card.can_direct && opponentFieldHasMonsters(message.player)) {
      return;
    }
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
  const base = `EDOPro ${ocgMessageTypeStrings.get(message.type) ?? message.type} decision | LP ${playerName(0)}:${lifePoints[0]} ${playerName(1)}:${lifePoints[1]}`;
  if (message.type === OcgMessageType.SELECT_CARD) {
    const enriched = enrichSelectCardMessage(message);
    const pickCount = selectCardPickCount(enriched);
    const names = (enriched.selects ?? [])
      .map((card) => cardName(Number(card.code ?? 0)))
      .slice(0, 8)
      .join(", ");
    return `${base} | pick ${pickCount} from: ${names || "unknown cards"}`;
  }
  return base;
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

function generateRandomSeed() {
  const bytes = randomBytes(32);
  return Array.from({ length: 4 }, (_, index) => bytes.readBigUInt64BE(index * 8));
}

function parseArgs(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--edopro-home") {
      parsed.edoproHome = argv[++index];
    } else if (arg === "--max-decisions") {
      parsed.maxDecisions = argv[++index];
    } else if (arg === "--max-duel-turns") {
      parsed.maxDuelTurns = argv[++index];
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
  currentDuelSeed = parseSeed(startMessage.seed) ?? generateRandomSeed();
  if (startMessage.max_duel_turns !== undefined && startMessage.max_duel_turns !== null) {
    maxDuelTurns = Number(startMessage.max_duel_turns);
  }
  if (startMessage.max_decisions !== undefined && startMessage.max_decisions !== null) {
    maxDecisions = Number(startMessage.max_decisions);
  }

  ensureOcgcoreSelectPatch();

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
    printErr: (line) => {
      try {
        process.stderr.write(`${String(line).replace(/\r?\n$/, "")}\n`);
      } catch {
        // ignore stderr write failures
      }
    },
  });

  duelMode = resolveDuelMode(startMessage);
  emitLog({ event: "duel_mode", mode: String(duelMode) });

  handle = await lib.createDuel({
    flags: duelMode | OcgDuelMode.SIMPLE_AI,
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
