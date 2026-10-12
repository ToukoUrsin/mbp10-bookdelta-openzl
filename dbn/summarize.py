#!/usr/bin/env python3
"""Summarize results/ into the comparison table (medians over runs)."""
import json, re, statistics as st, sys
from pathlib import Path
R = Path(sys.argv[1] if len(sys.argv) > 1 else 'results')
sizes = {}
for line in (R / 'sizes.txt').read_text().splitlines():
    f = line.split()
    sizes[Path(f[-1]).name] = int(f[4])
dbn_bytes = sizes['es.dbn']
dec = [json.loads(l) for l in (R / 'decode.jsonl').read_text().splitlines() if l.strip()]
rows = dec[0]['rows']
def med(xs): return st.median(xs)
def enc(pat): return [json.loads(p.read_text())['seconds'] for p in sorted(R.glob(pat))]
dsec = {}
for d in dec: dsec.setdefault(Path(d['file']).name, []).append(float(d['seconds']))
sums = {d['checksum'] for d in dec}
methods = [
    ('DBN (uncompressed)', 'es.dbn', None),
    ('DBN + zstd (dbn crate writer, level 0 = zstd default 3)', 'es.dbn.zst', enc('enc-dbnzst-*.json')),
    ('DBN + zstd -19', 'es.dbn.z19', enc('enc-zstd19.json')),
    ('bookdelta + OpenZL L19 (ours)', 'es.dbnzl', enc('enc-dbnzl-*.json')),
]
out = [f'rows: {rows:,}; DBN bytes {dbn_bytes:,} ({dbn_bytes/rows:.1f} B/row); checksums identical: {len(sums)==1}', '',
       '| encoding | bytes | vs DBN+zstd | compress s (median) | decode s (median) | M rows/s | MB/s of DBN |',
       '|---|---:|---:|---:|---:|---:|---:|']
zst = sizes['es.dbn.zst']
for name, f, e in methods:
    b = sizes[f]; ds = med(dsec[f])
    out.append(f'| {name} | {b:,} | {100*(b/zst-1):+.1f}% | {"—" if e is None else f"{med(e):.1f}"} | {ds:.2f} | '
               f'{rows/ds/1e6:.2f} | {dbn_bytes/ds/1e6:,.0f} |')
out += ['', 'decode runs (s): ' + '; '.join(f'{k}: ' + ', '.join(f'{x:.2f}' for x in v) for k, v in dsec.items()),
        'compress runs (s): ' + '; '.join(f'{n}: ' + ', '.join(f'{x:.1f}' for x in e) for n, f, e in methods if e)]
print('\n'.join(out))
