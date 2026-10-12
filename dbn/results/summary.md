rows: 12,104,179; DBN bytes 4,454,340,704 (368.0 B/row); checksums identical: True

| encoding | bytes | vs DBN+zstd | compress s (median) | decode s (median) | M rows/s | MB/s of DBN |
|---|---:|---:|---:|---:|---:|---:|
| DBN (uncompressed) | 4,454,340,704 | +1336.2% | — | 0.48 | 25.43 | 9,360 |
| DBN + zstd (dbn crate writer, level 0 = zstd default 3) | 310,152,253 | +0.0% | 6.5 | 1.85 | 6.54 | 2,408 |
| DBN + zstd -19 | 235,057,218 | -24.2% | 962.9 | 1.62 | 7.46 | 2,746 |
| bookdelta + OpenZL L19 (ours) | 129,798,855 | -58.1% | 255.4 | 5.64 | 2.15 | 790 |

decode runs (s): es.dbn: 0.48, 0.47, 0.48, 0.47, 0.48; es.dbn.zst: 1.85, 1.83, 1.86, 1.84, 2.01; es.dbn.z19: 1.62, 1.60, 1.65, 1.62, 2.18; es.dbnzl: 5.53, 5.64, 5.58, 5.71, 5.70
compress runs (s): DBN + zstd (dbn crate writer, level 0 = zstd default 3): 7.0, 6.3, 6.5; DBN + zstd -19: 962.9; bookdelta + OpenZL L19 (ours): 307.4, 255.4, 253.8
