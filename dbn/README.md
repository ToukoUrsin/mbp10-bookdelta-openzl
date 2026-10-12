# DBN vs bookdelta + OpenZL on the same ES MBP-10 day

Same 12,104,179 rows as the main result (GLBX.MDP3 ES, 2025-11-05, 12 instruments), as native DBN v3
`Mbp10Msg` records (368 B/row). Run on a Daytona sandbox (AMD EPYC 9254), 12.10.2026.

| encoding | bytes | vs DBN+zstd | compress s | decode s | M rows/s | MB/s of DBN out |
|---|---:|---:|---:|---:|---:|---:|
| DBN, uncompressed | 4,454,340,704 | — | — | 0.48 | 25.4 | 9,360 |
| DBN + zstd (dbn crate writer, zstd default level 3) | 310,152,253 | — | 6.5 | 1.85 | 6.54 | 2,408 |
| DBN + zstd -19 | 235,057,218 | −24.2% | 963 | 1.62 | 7.46 | 2,746 |
| **bookdelta + OpenZL L19** | **129,798,855** | **−58.1%** | 255 | 5.64 | 2.15 | 790 |

Decode is about 3× slower than DBN+zstd (5.6 s vs 1.85 s for the day).

## Method
- **DBN records:** Databento's free sample endpoint serves CSV only, so `dbnbench from-csv` (official
  `dbn` crate 0.71.0) rebuilds DBN from the raw-integer CSV export used in the main result, with
  raw_symbol→instrument_id mappings in the metadata. The `dbn` crate's own CSV encoder (mapped
  symbols) reproduces the source CSV byte-for-byte (`results/dbn-fidelity.txt`).
- **DBN side:** `dbnbench zst` re-encodes through the dbn crate's zstd writer; `dbnbench decode`
  uses `DynDecoder` + `decode_record::<Mbp10Msg>()`.
- **Ours:** `dbnzl.c`, single-threaded C over OpenZL v0.3.0 (32246b48). Same transform as the main result,
  applied to DBN fields: zigzag(ts_recv − ts_event), per-instrument carried residuals of price and all
  60 level px/sz/ct fields, action+side in one byte, each column OpenZL L19 (brute-force numeric /
  field-LZ / delta). DBN metadata is stored verbatim (zstd 19). `dbnzl dec` writes the identical `.dbn`
  file (SHA-256 `86427474…c4d5`, `results/exact.txt`), and Databento's native
  `test_data.mbp-10.v3.dbn` from the dbn repo also round-trips exactly.
- **Timing:** single thread, pinned with `taskset`, warm page cache. Decode: 5 interleaved runs per
  method on one core, every 368-byte record materialized and checksummed the same way on both sides
  (all checksums equal); median shown. Compress: median of 3 for ours and dbn+zstd, run concurrently
  one per core; zstd -19 once. All runs are in `results/`.

Reproduce: `bash run.sh` (Ubuntu, 4 vCPU / 8 GiB, about 20 min).
