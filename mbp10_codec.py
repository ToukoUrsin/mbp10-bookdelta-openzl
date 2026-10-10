#!/usr/bin/env python3
"""Streaming exact MBP-10 codec and a carried-price/timing experiment.

The public Backtester release has a complete weekday MBP-10 CSV rather than
the MBO CSV used by the frozen mode-40 result.  This file keeps the CSV
lexical contract explicit, then compares a typed frame stream with a changed
candidate: ``ts_recv-ts_event`` and per-instrument/per-level price residuals.
All numeric tokens in this release are canonical decimal integers, so the
scalar inverse recreates the original line bytes exactly.
"""
from __future__ import annotations
import argparse, hashlib, io, json, shutil, struct, time
from pathlib import Path
import zstandard as zstd

FRAME = 65536
MAGIC = b"MBP10C1\0"
FOOT = b"END!"
U32 = struct.Struct("<I")
U64 = struct.Struct("<Q")
I64 = struct.Struct("<q")
COLS = tuple(
    "ts_recv,ts_event,rtype,publisher_id,instrument_id,action,side,depth,price,size,flags,ts_in_delta,sequence,"
    "bid_px_00,ask_px_00,bid_sz_00,ask_sz_00,bid_ct_00,ask_ct_00,bid_px_01,ask_px_01,bid_sz_01,ask_sz_01,bid_ct_01,ask_ct_01,"
    "bid_px_02,ask_px_02,bid_sz_02,ask_sz_02,bid_ct_02,ask_ct_02,bid_px_03,ask_px_03,bid_sz_03,ask_sz_03,bid_ct_03,ask_ct_03,"
    "bid_px_04,ask_px_04,bid_sz_04,ask_sz_04,bid_ct_04,ask_ct_04,bid_px_05,ask_px_05,bid_sz_05,ask_sz_05,bid_ct_05,ask_ct_05,"
    "bid_px_06,ask_px_06,bid_sz_06,ask_sz_06,bid_ct_06,ask_ct_06,bid_px_07,ask_px_07,bid_sz_07,ask_sz_07,bid_ct_07,ask_ct_07,"
    "bid_px_08,ask_px_08,bid_sz_08,ask_sz_08,bid_ct_08,ask_ct_08,bid_px_09,ask_px_09,bid_sz_09,ask_sz_09,bid_ct_09,ask_ct_09,symbol"
    .split(",")
)
HEADER = (",".join(COLS) + "\n").encode()
ACTION = COLS.index("action")
SIDE = COLS.index("side")
SYMBOL = COLS.index("symbol")
NUMERIC = tuple(i for i in range(len(COLS)) if i not in (ACTION, SIDE, SYMBOL))
PRICE = tuple(i for i, c in enumerate(COLS) if c == "price" or c.startswith("bid_px_") or c.startswith("ask_px_"))
UNDEF = (1 << 63) - 1

def sha_path(p: Path):
    h = hashlib.sha256(); n = 0
    with p.open("rb") as f:
        while b := f.read(1 << 20): h.update(b); n += len(b)
    return n, h.hexdigest()

def source_row_digest(path: Path):
    h = hashlib.sha256(); n = 0
    raw = path.open("rb"); stream = zstd.ZstdDecompressor().stream_reader(raw) if path.suffix == ".zst" else raw
    f = io.BufferedReader(stream)
    try:
        if f.readline() != HEADER: raise ValueError("source header")
        while b := f.read(1 << 20): h.update(b); n += len(b)
    finally:
        f.close()
        if stream is not raw: raw.close()
    return n, h.hexdigest()

def zz(v: int) -> int:
    return (v << 1) if v >= 0 else ((-v << 1) - 1)

def unzz(v: int) -> int:
    return v >> 1 if not (v & 1) else -((v >> 1) + 1)

def rows(path: Path, limit: int = 0):
    raw = path.open("rb")
    stream = zstd.ZstdDecompressor().stream_reader(raw) if path.suffix == ".zst" else raw
    f = io.BufferedReader(stream)
    try:
        hdr = f.readline()
        if hdr != HEADER: raise ValueError("MBP10 header mismatch")
        out = []
        count = 0
        while limit == 0 or count < limit:
            line = f.readline()
            if not line: break
            if not line.endswith(b"\n"): raise ValueError("non-newline row")
            vals = line[:-1].split(b",")
            if len(vals) != len(COLS): raise ValueError(f"column count {len(vals)}")
            # Release fields are canonical integer text; the check prevents a
            # silent lexical change from entering a supposedly exact archive.
            for i in NUMERIC:
                if str(int(vals[i])).encode() != vals[i]:
                    raise ValueError(f"noncanonical numeric field {COLS[i]}={vals[i]!r}")
            if len(vals[ACTION]) != 1 or len(vals[SIDE]) != 1:
                raise ValueError("action/side width")
            out.append((line, vals)); count += 1
            if len(out) == FRAME:
                yield out; out = []
        if out: yield out
    finally:
        f.close()
        if stream is not raw: raw.close()

def typed_values(frame, candidate: bool, state):
    """Return fixed/length-delimited bytes for each column plus source lines."""
    n = len(frame); cols = [bytearray() for _ in COLS]
    for _, v in frame:
        nums = [None] * len(COLS)
        for i in NUMERIC: nums[i] = int(v[i])
        # timing: store event then receive-event as a zigzag signed residual
        if candidate:
            nums[0] = zz(nums[0] - nums[1])
        for i in NUMERIC:
            x = nums[i]
            if candidate and i in PRICE:
                key = (nums[4], i)
                prior = state.get(key, 0)
                x = zz(int(v[i]) - prior)
                state[key] = int(v[i])
            cols[i].extend(U64.pack(x & ((1 << 64) - 1)))
        cols[ACTION].extend(v[ACTION]); cols[SIDE].extend(v[SIDE])
        cols[SYMBOL].extend(U32.pack(len(v[SYMBOL]))); cols[SYMBOL].extend(v[SYMBOL])
    return cols

def restore_cols(cols, n, candidate, state):
    vals = [[None] * n for _ in COLS]
    for i in NUMERIC:
        raw = cols[i]
        if len(raw) != n * 8: raise ValueError(f"numeric column extent {COLS[i]}")
        vals[i] = list(struct.unpack("<" + "Q" * n, raw))
    if len(cols[ACTION]) != n or len(cols[SIDE]) != n: raise ValueError("char extent")
    vals[ACTION] = [bytes([x]) for x in cols[ACTION]]
    vals[SIDE] = [bytes([x]) for x in cols[SIDE]]
    p = 0
    for j in range(n):
        if p + 4 > len(cols[SYMBOL]): raise ValueError("symbol length")
        ln = U32.unpack_from(cols[SYMBOL], p)[0]; p += 4
        if p + ln > len(cols[SYMBOL]): raise ValueError("symbol bytes")
        vals[SYMBOL][j] = cols[SYMBOL][p:p+ln]; p += ln
    if p != len(cols[SYMBOL]): raise ValueError("symbol trailing")
    for j in range(n):
        if candidate:
            vals[0][j] = (vals[1][j] + unzz(vals[0][j])) & ((1 << 64) - 1)
        if candidate:
            for i in PRICE:
                key = (vals[4][j], i); x = unzz(vals[i][j]) + state.get(key, 0)
                vals[i][j] = x & ((1 << 64) - 1); state[key] = vals[i][j]
    return vals

def encode(source: Path, out: Path, candidate: bool, limit: int = 0):
    start = time.time(); out.parent.mkdir(parents=True, exist_ok=True)
    src_h = hashlib.sha256(); row_h = hashlib.sha256(); rows_n = bytes_n = frames = 0; state = {}
    cctx = zstd.ZstdCompressor(level=19, write_checksum=True)
    with out.open("wb") as w:
        w.write(MAGIC); w.write(U32.pack(1 if candidate else 0)); w.write(U32.pack(len(HEADER))); w.write(HEADER)
        for fr in rows(source, limit):
            for line, _ in fr: src_h.update(line); row_h.update(line); bytes_n += len(line)
            cols = typed_values(fr, candidate, state)
            w.write(U32.pack(len(fr)))
            for raw in cols:
                comp = cctx.compress(bytes(raw)); w.write(U64.pack(len(raw))); w.write(U64.pack(len(comp))); w.write(comp)
            rows_n += len(fr); frames += 1
            if frames % 25 == 0: print({"frames": frames, "rows": rows_n}, flush=True)
        w.write(FOOT); w.write(U64.pack(rows_n)); w.write(U64.pack(bytes_n)); w.write(row_h.digest()); w.write(src_h.digest())
    n, h = sha_path(out)
    rec = {"format":"MBP10C1", "candidate":candidate, "source":str(source), "archive":str(out), "archive_bytes":n, "archive_sha256":h,
           "rows":rows_n, "frames":frames, "source_row_bytes":bytes_n, "source_rows_sha256":row_h.hexdigest(), "source_csv_sha256":src_h.hexdigest(), "seconds":time.time()-start}
    out.with_suffix(".encode.json").write_text(json.dumps(rec, indent=2) + "\n")
    return rec

def decode(archive: Path, expected: Path | None = None):
    start = time.time(); row_h = hashlib.sha256(); src_h = hashlib.sha256(); rows_n = bytes_n = frames = 0; state = {}
    with archive.open("rb") as r:
        if r.read(8) != MAGIC: raise ValueError("magic")
        candidate = U32.unpack(r.read(4))[0]; hl = U32.unpack(r.read(4))[0]; hdr = r.read(hl)
        if hdr != HEADER: raise ValueError("header")
        dctx = zstd.ZstdDecompressor()
        while True:
            mark = r.read(4)
            if mark == FOOT: break
            if len(mark) != 4: raise ValueError("frame truncation")
            n = U32.unpack(mark)[0]; cols = []
            for _ in COLS:
                raw_n_b = r.read(8); comp_n_b = r.read(8)
                if len(raw_n_b) != 8 or len(comp_n_b) != 8: raise ValueError("column header")
                raw_n = U64.unpack(raw_n_b)[0]; comp_n = U64.unpack(comp_n_b)[0]; comp = r.read(comp_n)
                if len(comp) != comp_n: raise ValueError("column truncation")
                raw = dctx.decompress(comp, max_output_size=raw_n)
                if len(raw) != raw_n: raise ValueError("column size")
                cols.append(raw)
            vals = restore_cols(cols, n, bool(candidate), state)
            for j in range(n):
                toks = [None] * len(COLS)
                for i in NUMERIC: toks[i] = str(vals[i][j]).encode()
                toks[ACTION] = vals[ACTION][j]; toks[SIDE] = vals[SIDE][j]; toks[SYMBOL] = vals[SYMBOL][j]
                line = b",".join(toks) + b"\n"
                row_h.update(line); src_h.update(line); rows_n += 1; bytes_n += len(line)
            frames += 1
        tail = r.read(8 + 8 + 32 + 32)
        if len(tail) != 80 or r.read(1): raise ValueError("footer/trailing")
        dr, db = U64.unpack(tail[:8])[0], U64.unpack(tail[8:16])[0]
        if (dr, db, tail[16:48], tail[48:80]) != (rows_n, bytes_n, row_h.digest(), src_h.digest()):
            raise ValueError(f"identity dr={dr}/{rows_n} db={db}/{bytes_n} row={tail[16:48].hex()}/{row_h.hexdigest()} src={tail[48:80].hex()}/{src_h.hexdigest()}")
    out = {"complete":True, "candidate":bool(candidate), "rows":rows_n, "frames":frames, "source_row_bytes":bytes_n, "source_rows_sha256":row_h.hexdigest(), "source_csv_sha256":src_h.hexdigest(), "archive_sha256":sha_path(archive)[1], "seconds":time.time()-start}
    if expected:
        en, eh = source_row_digest(expected); out.update(source_row_bytes=en, expected_source_rows_sha256=eh)
        if eh != row_h.hexdigest(): raise ValueError("expected source mismatch")
        out["source_match"] = True
    return out

def controls(archive: Path, expected: Path, outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True); b = archive.read_bytes(); n = len(b); mid = n // 2; result = {}
    for name, mut in {
        "truncation": lambda x: x[:-1], "append": lambda x: x + b"X",
        "header": lambda x: bytes([x[0] ^ 1]) + x[1:],
        "payload": lambda x: x[:mid] + bytes([x[mid] ^ 1]) + x[mid+1:],
    }.items():
        p = outdir / (archive.name + "." + name); p.write_bytes(mut(b))
        try: decode(p, expected); result[name] = False
        except Exception as e: result[name] = True
        p.unlink(missing_ok=True)
    (outdir / "controls.json").write_text(json.dumps(result, indent=2) + "\n"); return result

def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("encode"); e.add_argument("source", type=Path); e.add_argument("out", type=Path); e.add_argument("--candidate", action="store_true"); e.add_argument("--limit", type=int, default=0)
    d = sub.add_parser("decode"); d.add_argument("archive", type=Path); d.add_argument("--expected", type=Path)
    c = sub.add_parser("controls"); c.add_argument("archive", type=Path); c.add_argument("expected", type=Path); c.add_argument("outdir", type=Path)
    a = ap.parse_args()
    if a.cmd == "encode": print(json.dumps(encode(a.source, a.out, a.candidate, a.limit), indent=2))
    elif a.cmd == "decode": print(json.dumps(decode(a.archive, a.expected), indent=2))
    else: print(json.dumps(controls(a.archive, a.expected, a.outdir), indent=2))
if __name__ == "__main__": main()
