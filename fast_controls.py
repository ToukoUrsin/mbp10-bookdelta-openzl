#!/usr/bin/env python3
"""Independent archive-structure controls for MBP10O1 OpenZL candidate."""
import argparse,ctypes,struct,zstandard as zstd
from pathlib import Path
U32=struct.Struct('<I');U64=struct.Struct('<Q');MAGIC=b'MBP10O1\0';FOOT=b'END!'; ROOT=Path(__file__).resolve().parent
ZL=ctypes.CDLL(str(ROOT/'bridge.so'));ZL.decompress_column.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_char_p,ctypes.c_size_t,ctypes.c_size_t,ctypes.c_size_t,ctypes.c_int];ZL.decompress_column.restype=ctypes.c_size_t
NUM=71
def zl(blob,w,n,k):
 d=ctypes.create_string_buffer(w*n);q=ZL.decompress_column(d,len(d),blob,len(blob),w,n,k)
 if q!=w*n: raise ValueError('OpenZL checksum/type/count')
def scan(path):
 d=zstd.ZstdDecompressor(); frames=0
 with open(path,'rb') as f:
  if f.read(8)!=MAGIC or U32.unpack(f.read(4))[0]!=1: raise ValueError('header flag')
  hl=U32.unpack(f.read(4))[0]; hdr=f.read(hl)
  if len(hdr)!=hl or not hdr.endswith(b'\n'): raise ValueError('header')
  while True:
   m=f.read(4)
   if m==FOOT: break
   if len(m)!=4: raise ValueError('frame marker')
   n=U32.unpack(m)[0]
   for i in range(NUM+2):
    a,b=f.read(8),f.read(8)
    if len(a)!=8 or len(b)!=8: raise ValueError('child header')
    rn,cn=U64.unpack(a)[0],U64.unpack(b)[0]; blob=f.read(cn)
    if len(blob)!=cn: raise ValueError('child truncation')
    if i<NUM: zl(blob,8,n,1)
    elif i==NUM: zl(blob,1,n,2)
    else:
     raw=d.decompress(blob,max_output_size=rn)
     if len(raw)!=rn: raise ValueError('symbol extent')
   frames+=1
  tail=f.read(80)
  if len(tail)!=80 or f.read(1): raise ValueError('footer/trailing')
 return frames

def main():
 p=argparse.ArgumentParser();p.add_argument('archive',type=Path);p.add_argument('--out',type=Path);a=p.parse_args();
 b=a.archive.read_bytes();mid=len(b)//2; ans={}
 muts={'truncation':b[:-1],'append':b+b'X','header':bytes([b[0]^1])+b[1:],'payload':b[:mid]+bytes([b[mid]^1])+b[mid+1:]}
 for name,data in muts.items():
  q=a.archive.with_name(a.archive.name+'.'+name);q.write_bytes(data)
  try: scan(q);ans[name]=False
  except Exception: ans[name]=True
  q.unlink(missing_ok=True)
 out=a.out or a.archive.with_name('fast-controls.json');out.write_text(__import__('json').dumps(ans,indent=2)+'\n');print(__import__('json').dumps(ans,indent=2))
if __name__=='__main__':main()
