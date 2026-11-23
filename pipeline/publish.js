#!/usr/bin/env node
// Publish uwsp.py's output to Fangorn, only what's new or changed: compared record by record with what the indexer
// holds, so a week with no new log costs nothing. New logs' PDFs go to storage (the archive copy); records go to
// <publisher>/uwsp-police on Base Sepolia. Records and files live in IPFS; each commit's root is anchored on chain,
// so a later change to any record is a visible new commit, never a silent edit.
//
//   FANGORN_KEY=0x… node publish.js [workdir=work]     (FANGORN_KEY unset: ~/.fangorn/config.json's key)
//   DRY_RUN=1 …                                        say what would change, publish nothing

import { readFileSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { Fangorn, BaseSepoliaConfig } from "@fangorn-network/sdk";

const dir = process.argv[2] ?? "work", REPO = process.env.REPO ?? "uwsp-police";
const INDEX = process.env.INDEX ?? "https://uwsp-police-sepolia.fangorn.network";
const key = process.env.FANGORN_KEY || JSON.parse(readFileSync(`${homedir()}/.fangorn/config.json`, "utf8")).privateKey;
const fangorn = Fangorn.create({ privateKey: key, config: BaseSepoliaConfig, storage: { signedUrl: {} } });
const me = fangorn.getAddress().toLowerCase();
const { logs, incidents, omissions } = JSON.parse(readFileSync(`${dir}/fangorn.json`, "utf8"));

// What the indexer holds now: id -> { cid, payload }.
const view = await (await fetch(`${INDEX}/view.json`, { cache: "no-store" })).json();
const have = new Map(view.records.filter((r) => r.publisher === me && r.repo === REPO).map((r) => [r.id, r]));
// Payloads compared with sorted keys: the indexer's JSON needn't keep our key order.
const canon = (v) => JSON.stringify(v, (_, x) => x && typeof x === "object" && !Array.isArray(x) ? Object.fromEntries(Object.entries(x).sort()) : x);
const changed = (r) => canon(have.get(r.id)?.payload) !== canon(r.payload);

const storage = fangorn.getStorage();

async function push(records, what, deletes = []) {
    if (process.env.DRY_RUN) return console.log(`would publish ${what}: ${records.length} records, delete ${deletes.length}`), {};
    const { txHash, recordCids } = await fangorn.uploadBatch(REPO, records, deletes);
    const last = records.at(-1) ?? { id: deletes[0] }, back = await fangorn.engine.readRecord(fangorn.getAddress(), REPO, last.id);
    if (records.length ? back?.cid !== recordCids[last.id] : back) throw new Error(`${what}: commit ${txHash} didn't land`);
    console.log(`${what}: ${records.length} records, ${deletes.length} deleted  ${txHash}`);
    return recordCids;
}

// 1. Logs: a PDF already archived under the same sha256 keeps its CID; a new or changed one is uploaded.
const logRecords = [];
for (const { file, ...r } of logs) {
    const old = have.get(r.id)?.payload;
    const src = old?.sha256 === r.payload.sha256 && old.src ? old.src
        : process.env.DRY_RUN ? "(new upload)" : await storage.putFile(readFileSync(`${dir}/pdf/${file}`), file);
    logRecords.push({ ...r, payload: { ...r.payload, src } });
}
const newLogs = logRecords.filter(changed);
await initRepo();
const logCids = { ...Object.fromEntries(logRecords.map((r) => [r.id, have.get(r.id)?.cid])), ...(newLogs.length ? await push(newLogs, "logs") : {}) };

// 2. Incidents and omissions, each pinning the exact log version(s) it came from.
const pin = (id) => ({ publisher: me, repo: REPO, id, cid: logCids[id] });
const rest = [...incidents, ...omissions].map((r) => ({ ...r, payload: { ...r.payload, logs: r.payload.logs.map(pin) } }));
const fresh = new Set([...logRecords, ...rest].map((r) => r.id));
let stale = [...have.keys()].filter((id) => !fresh.has(id)); // published before, no longer produced (a parser fix)
const todo = rest.filter(changed);
console.log(`${newLogs.length} logs, ${todo.length} records new or changed, ${stale.length} to delete`);
for (let i = 0; i < todo.length || stale.length; i += 500) { // ponytail: 500 per commit, untuned
    await push(todo.slice(i, i + 500), `records ${i + 1}-${Math.min(i + 500, todo.length)} of ${todo.length}`, stale);
    stale = [];
}

async function initRepo() { if (!process.env.DRY_RUN && !have.size) await fangorn.initRepo(REPO); }
