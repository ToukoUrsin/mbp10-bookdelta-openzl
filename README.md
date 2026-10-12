# MBP-10 order-book residuals + OpenZL: 34.6% smaller than typed OpenZL on a full ES day

A reversible transform for Databento-style MBP-10 market-by-price data (normalized CSV), with
every transformed column compressed by unmodified OpenZL v0.3.0. Measured on one full public
CME ES day: 12,104,179 rows, 5,987,026,927 source bytes.

## Clean-room result (fresh sandbox, public inputs only)

`run.sh` builds OpenZL v0.3.0 from GitHub (commit `32246b48`), compiles the small C bridge
(`bridge.c`), downloads the source file, encodes, decodes with separate checkers, and runs the
general-purpose baselines. Results are in `results/`.

| encoding of the same 12,104,179 rows | bytes | candidate is |
|---|---:|---:|
| **bookdelta residuals + OpenZL L19 (candidate)** | **131,442,092** | — |
| same typed columns, OpenZL L19 (numeric / field-LZ / delta selection), no residuals | 201,014,822 | 34.61% smaller |
| `xz -9` of the CSV | 222,403,004 | 40.90% smaller |
| `zstd -19 --long=27` of the CSV | 222,600,553 | 40.95% smaller |
| source as published (`.csv.zst`) | 374,908,710 | 64.94% smaller |

Both MBP10O1 archives were decoded back to the exact source rows (row SHA-256
`89cda85f…f19e`, 5,987,026,927 bytes) by separate scalar decoders
(`independent_openzl_candidate.py`, `independent_openzl.py`), and the candidate decoder
rejects truncation, append, header and payload mutations (`results/candidate.controls.json`).
An earlier run with a different bridge build gave 131,122,312 vs 199,472,577 bytes (34.27%).

## Native DBN comparison

On the same rows as native DBN `Mbp10Msg` records, the archive is 129.8 MB, against 310.2 MB for DBN+zstd
and 235.1 MB for DBN+zstd -19. Decoding is about 3× slower than DBN+zstd (2.15 vs 6.54 M rows/s,
single core). Details and scripts: [`dbn/`](dbn/README.md).

## Transform

Per 65,536-row frame: zigzag residual of `ts_recv - ts_event`; per-instrument, per-column
carried residuals for `price` and every bid/ask price, size and count level; action and side
packed into one radix-3 byte; symbols as length-prefixed bytes (Zstandard L19). Every numeric
child and the action/side child go through OpenZL with checksums. Code:
`arrow_bookdelta.py` (transform), `openzl_candidate.py` (encoder), `arrow_openzl_fast.py`
(typed OpenZL comparator).

## Source

`ES-glbx-20251105.mbp-10.csv.zst`, a public Databento-format CSV export:
https://github.com/alegitdude/Backtester/releases/download/demo-data/ES-glbx-20251105.mbp-10.csv.zst
(SHA-256 `c7145dae21f45cba15e8038dc8ab2589553f88eb4cb2678adbe544982c33352b`).

## Scope

One instrument-day of normalized CSV, not native DBN binary. Compression ratio only; no
throughput claim. Held-out slices of the same day (rows 1,000,001–2,000,000 and
8,000,001–10,000,000) gave 39.3% and 29.6% vs the typed OpenZL comparator in earlier runs.
