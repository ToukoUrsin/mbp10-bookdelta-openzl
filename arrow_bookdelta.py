#!/usr/bin/env python3
"""Arrow-backed MBP-10 bookdelta encoder.

This is a deliberately separate implementation of the MBP10D1 encoder.  It
uses pyarrow's C++ CSV parser for the expensive text-to-integer step, then
applies the same reversible transforms as the scalar reference codec.  The
archive format is byte-compatible with bookdelta_codec.py, so its independent
scalar checker can be used without importing this implementation.
"""
from __future__ import annotations
import argparse, hashlib, io, json, struct, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import pyarrow.csv as pacsv
import zstandard as zstd

FRAME = 65536
MAGIC = b"MBP10D1\0"; FOOT = b"END!"
U32 = struct.Struct("<I"); U64 = struct.Struct("<Q")
COLS = tuple("ts_recv,ts_event,rtype,publisher_id,instrument_id,action,side,depth,price,size,flags,ts_in_delta,sequence,bid_px_00,ask_px_00,bid_sz_00,ask_sz_00,bid_ct_00,ask_ct_00,bid_px_01,ask_px_01,bid_sz_01,ask_sz_01,bid_ct_01,ask_ct_01,bid_px_02,ask_px_02,bid_sz_02,ask_sz_02,bid_ct_02,ask_ct_02,bid_px_03,ask_px_03,bid_sz_03,ask_sz_03,bid_ct_03,ask_ct_03,bid_px_04,ask_px_04,bid_sz_04,ask_sz_04,bid_ct_04,ask_ct_04,bid_px_05,ask_px_05,bid_sz_05,ask_sz_05,bid_ct_05,ask_ct_05,bid_px_06,ask_px_06,bid_sz_06,ask_sz_06,bid_ct_06,ask_ct_06,bid_px_07,ask_px_07,bid_sz_07,ask_sz_07,bid_ct_07,ask_ct_07,bid_px_08,ask_px_08,bid_sz_08,ask_sz_08,bid_ct_08,ask_ct_08,bid_px_09,ask_px_09,bid_sz_09,ask_sz_09,bid_ct_09,ask_ct_09,symbol".split(','))
HEADER = (','.join(COLS) + '\n').encode()
ACTION = COLS.index('action'); SIDE = COLS.index('side'); SYMBOL = COLS.index('symbol')
NUMERIC = tuple(i for i in range(len(COLS)) if i not in (ACTION, SIDE, SYMBOL))
PRICE = tuple(i for i,c in enumerate(COLS) if c == 'price' or c.startswith(('bid_px_','ask_px_','bid_sz_','ask_sz_','bid_ct_','ask_ct_')))
AMAP = {'A': 0, 'C': 1, 'M': 2, 'T': 3}; SMAP = {'A': 0, 'B': 1, 'N': 2}

def zz(a):
    """Unsigned zigzag for an int64 NumPy array."""
    a = np.asarray(a, dtype=np.int64)
    # Source values and residuals are well within the signed 63-bit range;
    # this cast gives exactly the scalar codec's two's-complement packing.
    return np.where(a >= 0, a * 2, (-a * 2) - 1).astype('<u8', copy=False)

def sha_source(path: Path, limit=0):
    h = hashlib.sha256(); n = 0; rows = 0
    raw = path.open('rb'); f = io.BufferedReader(zstd.ZstdDecompressor().stream_reader(raw))
    try:
        if f.readline() != HEADER: raise ValueError('source header')
        if limit:
            while rows < limit:
                b = f.readline()
                if not b: break
                h.update(b); n += len(b); rows += 1
        else:
            while b := f.read(1 << 20): h.update(b); n += len(b)
    finally:
        f.close(); raw.close()
    return n, h.digest()

def batches(path: Path):
    raw = path.open('rb'); stream = zstd.ZstdDecompressor().stream_reader(raw)
    try:
        reader = pacsv.open_csv(stream, read_options=pacsv.ReadOptions(block_size=8 << 20, use_threads=True),
                                convert_options=pacsv.ConvertOptions(check_utf8=False))
        while True:
            try: yield reader.read_next_batch()
            except StopIteration: break
    finally:
        stream.close(); raw.close()

def frame_iter(path: Path, limit=0):
    """Yield dictionaries of NumPy columns in exactly 65536-row frames."""
    hold = [[] for _ in COLS]; held = 0; emitted = 0
    for rb in batches(path):
        n = rb.num_rows; off = 0
        # Arrow batches are much larger than a frame; slice without copying.
        while off < n:
            remaining = limit - emitted if limit else n - off
            if limit and remaining <= 0:
                break
            take = min(FRAME - held, n - off, remaining)
            for i, name in enumerate(COLS):
                arr = rb.column(i).slice(off, take)
                if i in (ACTION, SIDE, SYMBOL): hold[i].append(arr.to_pylist())
                else: hold[i].append(arr.to_numpy(zero_copy_only=False))
            held += take; off += take; emitted += take
            if held == FRAME:
                out = {}
                for i in range(len(COLS)):
                    if i in (ACTION, SIDE, SYMBOL): out[i] = sum(hold[i], [])
                    else: out[i] = np.concatenate(hold[i]).astype(np.int64, copy=False)
                yield out
                hold = [[] for _ in COLS]; held = 0
            if limit and emitted >= limit: break
        if limit and emitted >= limit: break
    if held:
        out = {}
        for i in range(len(COLS)):
            if i in (ACTION, SIDE, SYMBOL): out[i] = sum(hold[i], [])
            else: out[i] = np.concatenate(hold[i]).astype(np.int64, copy=False)
        yield out

def transformed(frame, candidate, state):
    n = len(frame[0]); out = []
    inst = frame[4]
    for i in NUMERIC:
        x = frame[i]
        if candidate and i == 0:
            x = zz(x - frame[1])
        elif candidate and i in PRICE:
            # Residuals are per instrument and per book/value column.  The
            # state carries across frame boundaries exactly as the scalar code.
            r = np.empty(n, dtype='<u8')
            for key in np.unique(inst):
                idx = np.flatnonzero(inst == key)
                vals = x[idx]
                prev = state.get((int(key), i), 0)
                d = np.empty(len(idx), dtype=np.int64); d[0] = vals[0] - prev
                if len(idx) > 1: d[1:] = vals[1:] - vals[:-1]
                r[idx] = zz(d); state[(int(key), i)] = int(vals[-1])
            x = r
        else:
            x = np.asarray(x, dtype='<u8')
        out.append(np.ascontiguousarray(x, dtype='<u8').tobytes())
    pair = bytearray(n)
    for j, (a, s) in enumerate(zip(frame[ACTION], frame[SIDE])):
        pair[j] = AMAP[a] * 3 + SMAP[s]
    out.append(bytes(pair))
    sym = bytearray()
    for s in frame[SYMBOL]:
        b = s.encode() if isinstance(s, str) else bytes(s)
        sym += U32.pack(len(b)); sym += b
    out.append(bytes(sym)); return out

def encode(source: Path, output: Path, candidate: bool, limit=0):
    t0 = time.time(); output.parent.mkdir(parents=True, exist_ok=True)
    row_n, digest = sha_source(source, limit); state = {}; rows_n = frames = 0
    # Parallel zstd keeps the OpenZL-compatible child payload contract while
    # making the expensive level-19 compression bounded by the assigned CPUs.
    # Each frame has many independent child streams.  zstd's internal worker
    # threads do not help on these small per-column calls, so overlap the
    # calls explicitly; the native zstd binding releases the GIL.
    pool = ThreadPoolExecutor(max_workers=4)
    def compress(raw):
        return zstd.ZstdCompressor(level=19, write_checksum=True).compress(raw)
    with output.open('wb') as w:
        w.write(MAGIC + U32.pack(1 if candidate else 0) + U32.pack(len(HEADER)) + HEADER)
        for frame in frame_iter(source, limit):
            n = len(frame[0]); w.write(U32.pack(n))
            raw_cols = transformed(frame, candidate, state)
            for raw, comp in zip(raw_cols, pool.map(compress, raw_cols)):
                w.write(U64.pack(len(raw)) + U64.pack(len(comp)) + comp)
            rows_n += n; frames += 1
            if frames % 25 == 0: print({'frames': frames, 'rows': rows_n}, flush=True)
        w.write(FOOT + U64.pack(rows_n) + U64.pack(row_n) + digest + digest)
    pool.shutdown()
    size = output.stat().st_size
    h = hashlib.sha256(output.read_bytes()).hexdigest()
    rec = {'format':'MBP10D1', 'candidate':candidate, 'archive_bytes':size, 'archive_sha256':h,
           'rows':rows_n, 'frames':frames, 'source_row_bytes':row_n, 'source_rows_sha256':digest.hex(),
           'source_csv_sha256':digest.hex(), 'seconds':time.time()-t0}
    output.with_suffix('.encode.json').write_text(json.dumps(rec, indent=2) + '\n'); return rec

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('source', type=Path); ap.add_argument('output', type=Path)
    ap.add_argument('--candidate', action='store_true'); ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    print(json.dumps(encode(a.source, a.output, a.candidate, a.limit), indent=2))
