#!/usr/bin/env node
/** Smoke test with real format-pack decks. Usage: node smoke-pack-duel.mjs [EDOPRO_HOME] [pack-json] [deck-a] [deck-b] */
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(root, "../..");
const edoproHome = process.argv[2] ?? process.env.EDOPRO_HOME ?? path.join(repoRoot, ".ygotrain/edopro-home");
const packPath = process.argv[3] ?? path.join(repoRoot, "configs/format-packs/banlists/banlist-2023-11.json");
const deckAName = process.argv[4] ?? "Kashtira";
const deckBName = process.argv[5] ?? "Primite Blue-Eyes";
const maxDecisions = Number(process.argv[6] ?? 600);

const aliases = JSON.parse(
  readFileSync(path.join(repoRoot, "configs/edopro-card-id-aliases.json"), "utf8"),
);
const canonicalize = (id) => {
  const mapped = aliases[String(id)];
  return mapped != null ? Number(mapped) : Number(id);
};
const canonicalizeList = (cards) => (cards ?? []).map((id) => canonicalize(id));

const pack = JSON.parse(readFileSync(packPath, "utf8"));
const deckA = pack.decks.find((entry) => entry.name === deckAName || entry.archetype === deckAName);
const deckB = pack.decks.find((entry) => entry.name === deckBName || entry.archetype === deckBName);
if (!deckA || !deckB) {
  throw new Error(`Could not find decks ${deckAName} and ${deckBName} in ${packPath}`);
}

const gateway = spawn(
  process.execPath,
  [path.join(root, "gateway.mjs"), "--edopro-home", edoproHome, "--duel-mode", "mr3", "--max-decisions", String(maxDecisions)],
  { stdio: ["pipe", "pipe", "inherit"] },
);

const start = {
  type: "start_duel",
  players: ["bot-a", "bot-b"],
  deck_a: {
    main: canonicalizeList(deckA.main),
    extra: canonicalizeList(deckA.extra),
    side: canonicalizeList(deckA.side),
  },
  deck_b: {
    main: canonicalizeList(deckB.main),
    extra: canonicalizeList(deckB.extra),
    side: canonicalizeList(deckB.side),
  },
  duel_mode: "mr3",
  seed: ["1", "2", "3", "4"],
};

console.error(`smoke-pack-duel: ${deckAName} vs ${deckBName} from ${path.basename(packPath)}`);
gateway.stdin.write(`${JSON.stringify(start)}\n`);

let decisions = 0;
const proactive = (actions) => {
  const order = [
    (a) => a.action_id?.startsWith("attack-"),
    (a) => a.action_id?.startsWith("normal-summon-"),
    (a) => a.action_id?.startsWith("special-summon-"),
    (a) => a.action_id?.startsWith("set-spell-"),
    (a) => a.action_id?.startsWith("set-monster-"),
    (a) => a.action_id?.startsWith("activate-"),
    (a) => a.action_id === "activate-effect",
    (a) => a.action_id?.startsWith("select-card-"),
    (a) => a.action_id?.startsWith("select-unselect-"),
    (a) => a.action_id === "finish-selection",
    (a) => a.action_id === "to-battle-phase",
    (a) => a.action_id === "to-end-phase",
    (a) => a.action_id?.startsWith("decline-"),
    (a) => a.action_id === "no",
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
    } else if (message.type === "log" && typeof message.message === "object") {
      const event = message.message.event;
      if (event === "auto_response" || event === "retry_safe_fallback" || event === "retry_response") {
        console.error("gateway:", JSON.stringify(message.message));
      }
    } else if (message.type === "result") {
      const stats = message.script_stats ?? {};
      const ok = message.winner && (stats.runtime_errors ?? 0) === 0;
      console.log(JSON.stringify({ ok, deckAName, deckBName, decisions, ...message }, null, 2));
      gateway.kill();
      process.exit(ok ? 0 : 1);
    }
  }
});

setTimeout(() => {
  console.error(`smoke-pack-duel timed out after 180s (${decisions} decisions)`);
  gateway.kill();
  process.exit(2);
}, 180_000);
