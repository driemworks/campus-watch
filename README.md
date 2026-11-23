# Campus Watch

A map and timeline of every incident in the UWSP Police & Security weekly event and fire logs, since January 2024.

The page is one static file. It reads the records from a Fangorn indexer, `/view.json` for everything and `/search`
for search by meaning, and has no backend of its own. The default indexer is
`https://uwsp-police-sepolia.fangorn.network`; `?index=<url>` points the page at another one.

The records (`incident`, `log`, `omission`) are published to Fangorn by
`0x7b16387C5A8c514880E48bcacd52782FCF9Da936/uwsp-police`. Each log keeps the source PDF's URL, its sha256 and an
archived copy. A record can't be changed without a new signed commit, so edits stay visible.

Run it locally: `python3 -m http.server`, then open http://localhost:8000.
