#!/usr/bin/env node
// Publish uwsp.py's output to Fangorn: each log's PDF to storage (the archive copy), then `log`, `incident` and
// `omission` records to <publisher>/uwsp-police on Base Sepolia. Records and files live in IPFS; each commit's
// root is anchored on chain, so a later change to any record is a visible new commit, never a silent edit.
//
//   node publish.js <outdir from uwsp.py>

import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { client } from "../../westmarch/src/client.js";

const dir = process.argv[2] ?? ".", REPO = process.env.REPO ?? "uwsp-police";
const { logs, incidents, omissions } = JSON.parse(readFileSync(`${dir}/fangorn.json`, "utf8"));
const fangorn = client({ network: "baseSepolia" }, JSON.parse(readFileSync(`${homedir()}/.fangorn/config.json`, "utf8")).privateKey);
const me = fangorn.getAddress();
await fangorn.initRepo(REPO);

async function push(records, what, deletes = []) {
    const { txHash, recordCids } = await fangorn.uploadBatch(REPO, records, deletes);
    const last = records.at(-1), back = await fangorn.engine.readRecord(me, REPO, last.id);
    if (back?.cid !== recordCids[last.id]) throw new Error(`${what}: commit ${txHash} didn't land`);
    console.log(`${what}: ${records.length} records  ${txHash}`);
    return recordCids;
}

// 1. The PDFs. CIDs are cached so a rerun doesn't upload again.
const cache = `${dir}/cids.json`, cids = existsSync(cache) ? JSON.parse(readFileSync(cache, "utf8")) : {};
// ponytail: the SDK's putFile check fetches a multi-block file's root with ?format=raw, which
// ipfs-test.fangorn.network ignores (it serves the whole file), so every multi-block upload fails it: the PDFs, and
// every commit's CAR once it passes one block (every 100th commit is a checkpoint holding the whole repo).
// So every upload here is checked the other way: the gateway must serve our exact bytes for the CID.
// Drop this once the gateway honours ?format=raw.
const sha = async (b) => Buffer.from(await crypto.subtle.digest("SHA-256", b)).toString("hex");
const storage = fangorn.getStorage();
storage.putFile = async (bytes, name) => {
    const cid = await storage.uploadBytes(bytes, name, "application/octet-stream");
    for (let i = 0; ; i++) { // a fresh upload can take a moment to reach the gateway
        const res = await fetch(`https://ipfs-test.fangorn.network/ipfs/${cid}`);
        if (res.ok && await sha(new Uint8Array(await res.arrayBuffer())) === await sha(bytes)) return cid;
        if (i === 5) throw new Error(`${name}: the gateway doesn't serve our bytes for ${cid}`);
        await new Promise((r) => setTimeout(r, 2000 * (i + 1)));
    }
};
for (const l of logs) {
    cids[l.file] ??= await storage.putFile(readFileSync(`${dir}/pdf/${l.file}`), l.file);
    writeFileSync(cache, JSON.stringify(cids, null, 1));
}
console.log(`${logs.length} PDFs in storage`);

// 2. The logs, each holding its PDF's CID; incidents pin the exact log version they came from.
const logCids = await push(logs.map(({ file, ...r }) => ({ ...r, payload: { ...r.payload, src: cids[file] } })), "logs");
const pin = (id) => ({ publisher: me.toLowerCase(), repo: REPO, id, cid: logCids[id] });
const rest = [...incidents.map((r) => ({ ...r, payload: { ...r.payload, logs: r.payload.logs.map(pin) } })),
              ...omissions.map((r) => ({ ...r, payload: { ...r.payload, logs: r.payload.logs.map(pin) } }))];

// Records published before that this run no longer produces (an omission a parser fix filled in): deleted in the
// first commit, so the repo says exactly what the logs say. The indexer's view is the current list.
const fresh = new Set([...logs, ...rest].map((r) => r.id));
const view = await (await fetch(`${process.env.INDEX ?? "https://uwsp-police-sepolia.fangorn.network"}/view.json`)).json();
let stale = view.records.filter((r) => r.repo === REPO && r.publisher === me.toLowerCase() && !fresh.has(r.id)).map((r) => r.id);
if (stale.length) console.log(`deleting ${stale.length} stale: ${stale.slice(0, 10).join(", ")}`);

// 3. Incidents and omissions. ponytail: 500 per commit, untuned; a rerun republishes the same ids, harmlessly.
// START=n resumes a run that stopped after n of these (the log records above are resent, harmlessly).
for (let i = Number(process.env.START ?? 0); i < rest.length; i += 500) {
    await push(rest.slice(i, i + 500), `records ${i + 1}-${Math.min(i + 500, rest.length)} of ${rest.length}`, stale);
    stale = [];
}
