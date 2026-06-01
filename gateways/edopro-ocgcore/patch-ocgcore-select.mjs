#!/usr/bin/env node
/** Patch ocgcore-wasm SELECT_CARD responses for Project Ignis core format. */
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const target = path.join(root, "node_modules/ocgcore-wasm/dist/index.js");

const listEncoding =
  "case 5:case 12:case 14:if(e.indicies){t.i32(0),t.i32(e.indicies.length);for(let r of e.indicies)t.i32(r)}else t.i32(-1);break;";
const hybrid =
  "case 5:case 12:case 14:if(e.indicies){if(e.indicies.length===1){t.i32(0),t.i32(1),t.i32(e.indicies[0])}else{t.i32(3);let bm=0;for(let r of e.indicies)bm|=1<<r;t.i32(bm)}}else t.i32(-1);break;";
const bitmapOnly =
  "case 5:case 12:case 14:if(e.indicies){t.i32(3);let bm=0;for(let r of e.indicies)bm|=1<<r;t.i32(bm)}else t.i32(-1);break;";

let source = readFileSync(target, "utf8");
if (source.includes(listEncoding) && !source.includes(hybrid) && !source.includes(bitmapOnly)) {
  console.log("patch-ocgcore-select: list SELECT_CARD encoding already active");
  process.exit(0);
}
for (const oldPattern of [hybrid, bitmapOnly]) {
  if (source.includes(oldPattern)) {
    source = source.replace(oldPattern, listEncoding);
    writeFileSync(target, source);
    console.log("patch-ocgcore-select: switched SELECT_CARD encoding to list (all pick sizes)");
    process.exit(0);
  }
}
if (!source.includes(listEncoding)) {
  console.warn("patch-ocgcore-select: no known SELECT_CARD pattern found; skipping");
  process.exit(0);
}
console.warn("patch-ocgcore-select: no known SELECT_CARD pattern found; skipping");
