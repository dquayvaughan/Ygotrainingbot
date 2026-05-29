#!/usr/bin/env node
/** Quick headless duel smoke test. Usage: node smoke-duel.mjs [EDOPRO_HOME] [vanilla|frog] */
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const edoproHome = process.argv[2] ?? process.env.EDOPRO_HOME ?? "C:/ProjectIgnis";
const deckKind = process.argv[3] ?? "vanilla";
const vanilla = Array(40).fill(1184620);
let deck = vanilla;
if (deckKind === "frog") {
  const packCandidates = [
    path.resolve(root, "../../data/yearly-bracket-2010-clean/2010/packs/bot-01.json"),
    path.resolve(root, "../../data/yearly-bracket-2010/2010/packs/bot-01.json"),
  ];
  const packPath = packCandidates.find((candidate) => {
    try {
      readFileSync(candidate);
      return true;
    } catch {
      return false;
    }
  });
  if (!packPath) {
    throw new Error(`frog deck pack not found; tried ${packCandidates.join(", ")}`);
  }
  deck = JSON.parse(readFileSync(packPath, "utf8")).decks[0].main;
  console.error(`smoke-duel: using frog deck from ${packPath}`);
}

const gateway = spawn(
  process.execPath,
  [path.join(root, "gateway.mjs"), "--edopro-home", edoproHome, "--duel-mode", "mr3"],
  { stdio: ["pipe", "pipe", "inherit"] },
);

const start = {
  type: "start_duel",
  players: ["bot-a", "bot-b"],
  deck_a: deck,
  deck_b: vanilla,
  duel_mode: "mr3",
};

gateway.stdin.write(`${JSON.stringify(start)}\n`);

let decisions = 0;
const proactive = (actions) => {
  const order = [
    (a) => a.action_id?.startsWith("attack-"),
    (a) => a.action_id?.startsWith("normal-summon-"),
    (a) => a.action_id?.startsWith("special-summon-"),
    (a) => a.action_id?.startsWith("activate-"),
    (a) => a.action_id === "to-battle-phase",
    (a) => a.action_id === "to-end-phase",
  ];
  for (const pick of order) {
    const hit = actions.find(pick);
    if (hit) return hit;
  }
  return actions[0];
};

let buffer = "";
gateway.stdout.on("data", (chunk) => {
  buffer += chunk.toString();
  let index;
  while ((index = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, index);
    buffer = buffer.slice(index + 1);
    if (!line.trim()) continue;
    const message = JSON.parse(line);
    if (message.type === "state") {
      decisions += 1;
      const action = proactive(message.state.legal_actions);
      gateway.stdin.write(
        `${JSON.stringify({
          type: "action",
          state_id: message.state.state_id,
          agent: message.state.active_player,
          action_id: action.action_id,
        })}\n`,
      );
    } else if (message.type === "log") {
      const payload = message.message;
      if (typeof payload === "string" && payload.includes("GetID")) {
        console.error("script error:", payload);
      }
    } else if (message.type === "result") {
      const stats = message.script_stats ?? {};
      const exitCode = message.winner && (stats.runtime_errors ?? 0) === 0 ? 0 : 1;
      console.log(JSON.stringify({ deckKind, decisions, ...message }, null, 2));
      gateway.kill();
      process.exit(exitCode);
    }
  }
});

setTimeout(() => {
  console.error(`smoke-duel (${deckKind}) timed out after 120s`);
  gateway.kill();
  process.exit(2);
}, 120_000);
