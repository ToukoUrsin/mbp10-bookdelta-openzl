#!/usr/bin/env python3
"""Independent scalar checker for MBP10O1 (OpenZL fixed children)."""
from __future__ import annotations
import argparse, ctypes, hashlib, io, json, struct, time
from pathlib import Path
import zstandard as zstd
import mbp10_codec as base

MAGIC = b"MBP10O1\0"; FOOT = base.FOOT; U32, U64 = struct.Struct("<I"), struct.Struct("<Q")
ROOT = Path(__file__).resolve().parent / "openzl-local"
ZL = ctypes.CDLL(str(ROOT / "bridge.so"))
ZL.decompress_column.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_int]
ZL.decompress_column.restype = ctypes.c_size_t

def zl_decompress(blob: bytes, width: int, count: int, kind: int) -> bytes:
    dst = ctypes.create_string_buffer(width * count)
    n = ZL.decompress_column(dst, len(dst), blob, len(blob), width, count, kind)
    if n != width * count: raise ValueError("OpenZL checksum/type/count")
    return dst.raw

def source_digest(path: Path):
    h = hashlib.sha256(); n = 0; raw = path.open("rb"); stream = zstd.ZstdDecompressor().stream_reader(raw) if path.suffix == ".zst" else raw; f = io.BufferedReader(stream)
    try:
        if f.readline() != base.HEADER: raise ValueError("source header")
        while b := f.read(1 << 20): h.update(b); n += len(b)
    finally:
        f.close()
        if stream is not raw: raw.close()
    return n, h.hexdigest()

def inverse(archive: Path, expected: Path | None = None) -> dict:
    started = time.time(); rh = hashlib.sha256(); rows = frames = bytes_n = 0; dctx = zstd.ZstdDecompressor()
    with archive.open("rb") as f:
        if f.read(8) != MAGIC: raise ValueError("magic")
        if U32.unpack(f.read(4))[0] != 0: raise ValueError("candidate flag")
        hl = U32.unpack(f.read(4))[0]
        if f.read(hl) != base.HEADER: raise ValueError("header")
        while True:
            mark = f.read(4)
            if mark == FOOT: break
            if len(mark) != 4: raise ValueError("frame marker")
            n = U32.unpack(mark)[0]; cols = []
            for i in range(len(base.COLS)):
                rn_b, cn_b = f.read(8), f.read(8)
                if len(rn_b) != 8 or len(cn_b) != 8: raise ValueError("column header")
                rn, cn = U64.unpack(rn_b)[0], U64.unpack(cn_b)[0]; comp = f.read(cn)
                if len(comp) != cn: raise ValueError("column truncation")
                if i in base.NUMERIC: raw = zl_decompress(comp, 8, n, 1)
                elif i in (base.ACTION, base.SIDE): raw = zl_decompress(comp, 1, n, 2)
                else: raw = dctx.decompress(comp, max_output_size=rn)
                if len(raw) != rn: raise ValueError("column extent")
                cols.append(raw)
            vals = []
            for i in base.NUMERIC: 
                if len(cols[i]) != n * 8: raise ValueError("numeric extent")
            pos = 0; syms = []
            for _ in range(n):
                if pos + 4 > len(cols[base.SYMBOL]): raise ValueError("symbol length")
                ln = U32.unpack_from(cols[base.SYMBOL], pos)[0]; pos += 4
                if pos + ln > len(cols[base.SYMBOL]): raise ValueError("symbol bytes")
                syms.append(cols[base.SYMBOL][pos:pos+ln]); pos += ln
            if pos != len(cols[base.SYMBOL]): raise ValueError("symbol trailing")
            nums = {i: struct.unpack("<" + "Q" * n, cols[i]) for i in base.NUMERIC}
            for j in range(n):
                fields = [b""] * len(base.COLS)
                for i in base.NUMERIC: fields[i] = str(nums[i][j]).encode()
                fields[base.ACTION] = bytes((cols[base.ACTION][j],)); fields[base.SIDE] = bytes((cols[base.SIDE][j],)); fields[base.SYMBOL] = syms[j]
                line = b",".join(fields) + b"\n"; rh.update(line); rows += 1; bytes_n += len(line)
            frames += 1
        tail = f.read(80)
        if len(tail) != 80 or f.read(1): raise ValueError("footer/trailing")
        if (U64.unpack(tail[:8])[0], U64.unpack(tail[8:16])[0], tail[16:48], tail[48:80]) != (rows, bytes_n, rh.digest(), rh.digest()): raise ValueError("identity")
    rec = {"archive":str(archive), "rows":rows, "frames":frames, "source_row_bytes":bytes_n, "source_rows_sha256":rh.hexdigest(), "archive_sha256":hashlib.sha256(archive.read_bytes()).hexdigest(), "complete":True, "seconds":time.time()-started}
    if expected:
        en, eh = source_digest(expected); rec.update(expected_source_row_bytes=en, expected_source_rows_sha256=eh, source_match=(eh == rh.hexdigest()))
        if (en, eh) != (bytes_n, rh.hexdigest()): raise ValueError("source mismatch")
    return rec

def controls(archive: Path, expected: Path, outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True); b = archive.read_bytes(); mid = len(b)//2; out = {}
    muts = {"truncation":b[:-1], "append":b+b"X", "header":bytes([b[0]^1])+b[1:], "payload":b[:mid]+bytes([b[mid]^1])+b[mid+1:]}
    for name, data in muts.items():
        p = outdir/(archive.name+"."+name); p.write_bytes(data)
        try: inverse(p, expected); out[name] = False
        except Exception: out[name] = True
        p.unlink(missing_ok=True)
    (outdir/"independent-controls.json").write_text(json.dumps(out, indent=2)+"\n"); return out

if __name__ == "__main__":
    ap=argparse.ArgumentParser();ap.add_argument("archive",type=Path);ap.add_argument("expected",type=Path);ap.add_argument("--out",type=Path);ap.add_argument("--controls",type=Path);a=ap.parse_args(); r=inverse(a.archive,a.expected); print(json.dumps(r,indent=2)); print(json.dumps(controls(a.archive,a.expected,a.controls),indent=2)) if a.controls else None; a.out.write_text(json.dumps(r,indent=2)+"\n") if a.out else None
