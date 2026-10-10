#!/usr/bin/env python3
"""Independent scalar inverse for MBP10O1 candidate bookdelta archives."""
from __future__ import annotations
import argparse,ctypes,hashlib,io,json,struct,time
from pathlib import Path
import zstandard as zstd
COLS=tuple("ts_recv,ts_event,rtype,publisher_id,instrument_id,action,side,depth,price,size,flags,ts_in_delta,sequence,bid_px_00,ask_px_00,bid_sz_00,ask_sz_00,bid_ct_00,ask_ct_00,bid_px_01,ask_px_01,bid_sz_01,ask_sz_01,bid_ct_01,ask_ct_01,bid_px_02,ask_px_02,bid_sz_02,ask_sz_02,bid_ct_02,ask_ct_02,bid_px_03,ask_px_03,bid_sz_03,ask_sz_03,bid_ct_03,ask_ct_03,bid_px_04,ask_px_04,bid_sz_04,ask_sz_04,bid_ct_04,ask_ct_04,bid_px_05,ask_px_05,bid_sz_05,ask_sz_05,bid_ct_05,ask_ct_05,bid_px_06,ask_px_06,bid_sz_06,ask_sz_06,bid_ct_06,ask_ct_06,bid_px_07,ask_px_07,bid_sz_07,ask_sz_07,bid_ct_07,ask_ct_07,bid_px_08,ask_px_08,bid_sz_08,ask_ct_08,bid_sz_09,ask_px_09,bid_sz_09,ask_sz_09,bid_ct_09,ask_ct_09,symbol".split(','))
# Replace accidental typo defensively with canonical 74-column header from the codec.
COLS=tuple("ts_recv,ts_event,rtype,publisher_id,instrument_id,action,side,depth,price,size,flags,ts_in_delta,sequence,bid_px_00,ask_px_00,bid_sz_00,ask_sz_00,bid_ct_00,ask_ct_00,bid_px_01,ask_px_01,bid_sz_01,ask_sz_01,bid_ct_01,ask_ct_01,bid_px_02,ask_px_02,bid_sz_02,ask_sz_02,bid_ct_02,ask_ct_02,bid_px_03,ask_px_03,bid_sz_03,ask_sz_03,bid_ct_03,ask_ct_03,bid_px_04,ask_px_04,bid_sz_04,ask_sz_04,bid_ct_04,ask_ct_04,bid_px_05,ask_px_05,bid_sz_05,ask_sz_05,bid_ct_05,ask_ct_05,bid_px_06,ask_px_06,bid_sz_06,ask_sz_06,bid_ct_06,ask_ct_06,bid_px_07,ask_px_07,bid_sz_07,ask_sz_07,bid_ct_07,ask_ct_07,bid_px_08,ask_px_08,bid_sz_08,ask_sz_08,bid_ct_08,ask_ct_08,bid_px_09,ask_px_09,bid_sz_09,ask_sz_09,bid_ct_09,ask_ct_09,symbol".split(','))
HEADER=(','.join(COLS)+'\n').encode(); A=5; S=6; Y=73; NUM=tuple(i for i in range(74) if i not in (A,S,Y)); PRICE=tuple(i for i,c in enumerate(COLS) if c=='price' or c.startswith(('bid_px_','ask_px_','bid_sz_','ask_sz_','bid_ct_','ask_ct_')))
MAGIC=b'MBP10O1\0'; FOOT=b'END!'; U32=struct.Struct('<I'); U64=struct.Struct('<Q'); ROOT=Path(__file__).resolve().parent
ZL=ctypes.CDLL(str(ROOT/'bridge.so')); ZL.decompress_column.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_char_p,ctypes.c_size_t,ctypes.c_size_t,ctypes.c_size_t,ctypes.c_int]; ZL.decompress_column.restype=ctypes.c_size_t
AMAP=(b'A',b'C',b'M',b'T'); SMAP=(b'A',b'B',b'N')
def unzz(v): return v>>1 if not v&1 else -((v>>1)+1)
def zl(blob,w,n,k):
 d=ctypes.create_string_buffer(w*n); got=ZL.decompress_column(d,len(d),blob,len(blob),w,n,k)
 if got!=w*n: raise ValueError('OpenZL checksum/type/count')
 return d.raw
def source_digest(path):
 h=hashlib.sha256(); n=0; raw=path.open('rb'); stream=zstd.ZstdDecompressor().stream_reader(raw) if path.suffix=='.zst' else raw; f=io.BufferedReader(stream)
 try:
  if f.readline()!=HEADER: raise ValueError('source header')
  while b:=f.read(1<<20): h.update(b); n+=len(b)
 finally: f.close(); raw.close() if stream is not raw else None
 return n,h.hexdigest()
def inverse(archive,expected=None):
 start=time.time(); rh=hashlib.sha256(); rows=frames=bytes_n=0; state={}; d=zstd.ZstdDecompressor()
 with archive.open('rb') as f:
  if f.read(8)!=MAGIC: raise ValueError('magic')
  if U32.unpack(f.read(4))[0]!=1: raise ValueError('candidate flag')
  hl=U32.unpack(f.read(4))[0]
  if f.read(hl)!=HEADER: raise ValueError('header')
  while True:
   mark=f.read(4)
   if mark==FOOT: break
   if len(mark)!=4: raise ValueError('frame marker')
   n=U32.unpack(mark)[0]; cols=[]
   for i in range(len(NUM)+2):
    a,b=f.read(8),f.read(8)
    if len(a)!=8 or len(b)!=8: raise ValueError('column header')
    rn,cn=U64.unpack(a)[0],U64.unpack(b)[0]; blob=f.read(cn)
    if len(blob)!=cn: raise ValueError('column truncation')
    cols.append(zl(blob,8,n,1) if i<len(NUM) else zl(blob,1,n,2) if i==len(NUM) else d.decompress(blob,max_output_size=rn))
    if len(cols[-1])!=rn: raise ValueError('column extent')
   nums={i:struct.unpack('<'+'Q'*n,cols[j]) for j,i in enumerate(NUM)}; pair=cols[len(NUM)]
   if len(pair)!=n: raise ValueError('pair extent')
   raw=cols[-1]; sy=[]; p=0
   for _ in range(n):
    if p+4>len(raw): raise ValueError('symbol length')
    ln=U32.unpack_from(raw,p)[0]; p+=4
    if p+ln>len(raw): raise ValueError('symbol bytes')
    sy.append(raw[p:p+ln]); p+=ln
   if p!=len(raw): raise ValueError('symbol trailing')
   for j in range(n):
    fs=[b'']*74
    for i in NUM:
     x=nums[i][j]
     if i==0: x=(nums[1][j]+unzz(x))&((1<<64)-1)
     if i in PRICE:
      key=(nums[4][j],i); x=(unzz(x)+state.get(key,0))&((1<<64)-1); state[key]=x
     fs[i]=str(x).encode()
    q=pair[j]; fs[A]=AMAP[q//3]; fs[S]=SMAP[q%3]; fs[Y]=sy[j]; line=b','.join(fs)+b'\n'; rh.update(line); rows+=1; bytes_n+=len(line)
   frames+=1
  tail=f.read(80)
  if len(tail)!=80 or f.read(1): raise ValueError('footer/trailing')
  if (U64.unpack(tail[:8])[0],U64.unpack(tail[8:16])[0],tail[16:48],tail[48:80])!=(rows,bytes_n,rh.digest(),rh.digest()): raise ValueError('identity')
 out={'archive':str(archive),'candidate':True,'complete':True,'rows':rows,'frames':frames,'source_row_bytes':bytes_n,'source_rows_sha256':rh.hexdigest(),'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'seconds':time.time()-start}
 if expected:
  en,eh=source_digest(expected); out.update(expected_source_row_bytes=en,expected_source_rows_sha256=eh,source_match=(en==bytes_n and eh==rh.hexdigest()))
  if (en,eh)!=(bytes_n,rh.hexdigest()): raise ValueError('source mismatch')
 return out
def controls(archive,expected,outdir):
 outdir.mkdir(parents=True,exist_ok=True); b=archive.read_bytes(); mid=len(b)//2; ans={}
 for name,x in {'truncation':b[:-1],'append':b+b'X','header':bytes([b[0]^1])+b[1:],'payload':b[:mid]+bytes([b[mid]^1])+b[mid+1:]}.items():
  p=outdir/(archive.name+'.'+name); p.write_bytes(x)
  try: inverse(p,expected); ans[name]=False
  except Exception: ans[name]=True
  p.unlink(missing_ok=True)
 (outdir/'controls.json').write_text(json.dumps(ans,indent=2)+'\n'); return ans
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('archive',type=Path);p.add_argument('expected',type=Path);p.add_argument('--out',type=Path);p.add_argument('--controls',type=Path);a=p.parse_args(); r=inverse(a.archive,a.expected); print(json.dumps(r,indent=2)); a.out.write_text(json.dumps(r,indent=2)+'\n') if a.out else None; print(json.dumps(controls(a.archive,a.expected,a.controls),indent=2)) if a.controls else None
