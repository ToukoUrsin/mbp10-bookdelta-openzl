#!/usr/bin/env python3
"""Arrow parsed matched typed OpenZL MBP-10 comparator (O1 container)."""
from __future__ import annotations
import argparse, ctypes, hashlib, json, struct, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import zstandard as zstd
import arrow_bookdelta as ab

MAGIC=b'MBP10O1\0'; FOOT=b'END!'; U32=struct.Struct('<I'); U64=struct.Struct('<Q')
ROOT=Path(__file__).resolve().parent/'openzl-local'; ZL=ctypes.CDLL(str(ROOT/'bridge.so'))
ZL.bound.argtypes=[ctypes.c_size_t]; ZL.bound.restype=ctypes.c_size_t
ZL.compress_column.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_size_t,ctypes.c_int,ctypes.c_int,ctypes.c_int]; ZL.compress_column.restype=ctypes.c_size_t
AMAP={'A':0,'C':1,'M':2,'T':3}; SMAP={'A':0,'B':1,'N':2}
def zl(raw,width,count,kind):
    src=ctypes.create_string_buffer(raw); dst=ctypes.create_string_buffer(ZL.bound(len(raw)))
    n=ZL.compress_column(dst,len(dst),src,width,count,kind,19,1)
    if not 0<n<=len(dst): raise ValueError('OpenZL encode')
    return dst.raw[:n]
def cols(frame):
    n=len(frame[0]); out=[None]*len(ab.COLS)
    for i in ab.NUMERIC: out[i]=numpy_bytes(frame[i])
    out[ab.ACTION]=bytes((x.encode() if isinstance(x,str) else bytes(x))[0] for x in frame[ab.ACTION])
    out[ab.SIDE]=bytes((x.encode() if isinstance(x,str) else bytes(x))[0] for x in frame[ab.SIDE])
    b=bytearray()
    for s in frame[ab.SYMBOL]:
        x=s.encode() if isinstance(s,str) else bytes(s); b += U32.pack(len(x)); b += x
    out[ab.SYMBOL]=bytes(b); return out
def numpy_bytes(a):
    import numpy as np
    return np.asarray(a,dtype='<u8').tobytes()
def encode(source,out,limit=0):
    started=time.time(); rows=frames=0; row_n, digest = ab.sha_source(source, limit); pool=ThreadPoolExecutor(max_workers=4)
    with out.open('wb') as w:
        w.write(MAGIC+U32.pack(0)+U32.pack(len(ab.HEADER))+ab.HEADER)
        for fr in ab.frame_iter(source,limit):
            for i in range(len(fr[0])): pass
            rawcols=cols(fr); w.write(U32.pack(len(fr[0])))
            def comp(pair):
                i,raw=pair
                if i in ab.NUMERIC: return zl(raw,8,len(fr[0]),1)
                if i in (ab.ACTION,ab.SIDE): return zl(raw,1,len(fr[0]),2)
                return zstd.ZstdCompressor(level=19,write_checksum=True).compress(raw)
            for i,(raw,co) in enumerate(zip(rawcols,pool.map(comp,enumerate(rawcols)))):
                w.write(U64.pack(len(raw))+U64.pack(len(co))+co)
            rows += len(fr[0]); frames += 1
            if frames%25==0: print({'frames':frames,'rows':rows},flush=True)
        w.write(FOOT+U64.pack(rows)+U64.pack(row_n)+digest+digest)
    pool.shutdown(); n=out.stat().st_size; ah=hashlib.sha256(out.read_bytes()).hexdigest()
    rec={'format':'MBP10O1','candidate':False,'archive_bytes':n,'archive_sha256':ah,'rows':rows,'frames':frames,'source_row_bytes':row_n,'source_rows_sha256':digest.hex(),'seconds':time.time()-started}
    out.with_suffix('.encode.json').write_text(json.dumps(rec,indent=2)+'\n'); return rec
if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('source',type=Path); ap.add_argument('out',type=Path); ap.add_argument('--limit',type=int,default=0); a=ap.parse_args(); print(json.dumps(encode(a.source,a.out,a.limit),indent=2))
