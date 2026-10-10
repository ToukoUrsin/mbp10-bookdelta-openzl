#!/usr/bin/env python3
"""OpenZL L19 encoding of MBP-10 bookdelta-transformed columns."""
from __future__ import annotations
import argparse, ctypes, hashlib, json, struct, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import zstandard as zstd
import arrow_bookdelta as ab
MAGIC=b'MBP10O1\0'; FOOT=b'END!'; U32=struct.Struct('<I'); U64=struct.Struct('<Q')
ROOT=Path(__file__).resolve().parent
ZL=ctypes.CDLL(str(ROOT/'bridge.so'))
ZL.bound.argtypes=[ctypes.c_size_t]; ZL.bound.restype=ctypes.c_size_t
ZL.compress_column.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_size_t,ctypes.c_int,ctypes.c_int,ctypes.c_int]; ZL.compress_column.restype=ctypes.c_size_t

def zl(raw,width,count,kind):
    src=ctypes.create_string_buffer(raw); dst=ctypes.create_string_buffer(ZL.bound(len(raw)))
    n=ZL.compress_column(dst,len(dst),src,width,count,kind,19,1)
    if not 0<n<=len(dst): raise ValueError('OpenZL encode')
    return dst.raw[:n]

def rawcols(frame, candidate, state):
    # candidate transforms are the independent Arrow implementation's exact
    # timing and per-instrument/per-level residuals, then OpenZL compresses.
    if candidate: vals=ab.transformed(frame, True, state)
    else:
        vals=[]
        for i in ab.NUMERIC: vals.append(__import__('numpy').asarray(frame[i],dtype='<u8').tobytes())
        n=len(frame[0]); pair=bytearray(n)
        for j,(a,s) in enumerate(zip(frame[ab.ACTION],frame[ab.SIDE])): pair[j]=ab.AMAP[a]*3+ab.SMAP[s]
        vals.append(bytes(pair)); sy=bytearray()
        for s in frame[ab.SYMBOL]:
            b=s.encode() if isinstance(s,str) else bytes(s); sy += U32.pack(len(b))+b
        vals.append(bytes(sy)); return vals
    return vals

def encode(source,out,candidate=False,limit=0):
    started=time.time(); rows=frames=bytes_n=0; row_h=hashlib.sha256(); state={}; pool=ThreadPoolExecutor(max_workers=4)
    row_n,digest=ab.sha_source(source,limit)
    with out.open('wb') as w:
        w.write(MAGIC+U32.pack(1 if candidate else 0)+U32.pack(len(ab.HEADER))+ab.HEADER)
        for fr in ab.frame_iter(source,limit):
            n=len(fr[0]); raw=rawcols(fr,candidate,state)
            # Exact lexical source digest from parsed canonical values.
            for j in range(n):
                fs=[str(int(fr[i][j])).encode() for i in ab.NUMERIC]
                fs.insert(ab.ACTION,fr[ab.ACTION][j].encode()); fs.insert(ab.SIDE,fr[ab.SIDE][j].encode())
                s=fr[ab.SYMBOL][j].encode() if isinstance(fr[ab.SYMBOL][j],str) else bytes(fr[ab.SYMBOL][j]); fs.append(s)
                row_h.update(b','.join(fs)+b'\n'); bytes_n += sum(map(len,fs))+len(fs)
            def comp(pair):
                i,b=pair
                if i < len(ab.NUMERIC): return zl(b,8,n,1)
                if i == len(ab.NUMERIC): return zl(b,1,n,2)
                return zstd.ZstdCompressor(level=19,write_checksum=True).compress(b)
            comps=list(pool.map(comp,enumerate(raw)))
            w.write(U32.pack(n))
            for b,c in zip(raw,comps): w.write(U64.pack(len(b))+U64.pack(len(c))+c)
            rows+=n; frames+=1
            if frames%10==0: print({'frames':frames,'rows':rows},flush=True)
        w.write(FOOT+U64.pack(rows)+U64.pack(bytes_n)+row_h.digest()+digest)
    pool.shutdown(); rec={'format':'MBP10O1','candidate':candidate,'archive_bytes':out.stat().st_size,'archive_sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'rows':rows,'frames':frames,'source_row_bytes':bytes_n,'source_rows_sha256':row_h.hexdigest(),'expected_source_rows_sha256':digest.hex(),'seconds':time.time()-started}
    out.with_suffix('.encode.json').write_text(json.dumps(rec,indent=2)+'\n'); print(json.dumps(rec,indent=2)); return rec
if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('source',type=Path); p.add_argument('out',type=Path); p.add_argument('--candidate',action='store_true'); p.add_argument('--limit',type=int,default=0); a=p.parse_args(); encode(a.source,a.out,a.candidate,a.limit)
