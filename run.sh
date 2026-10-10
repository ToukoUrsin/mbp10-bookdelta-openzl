#!/usr/bin/env bash
# Clean-room reproduction of the full MBP-10 result from public sources only.
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
R=results; mkdir -p $R
t(){ date -u +%FT%TZ; }
echo "start $(t)" | tee $R/timeline.txt
apt-get update -qq && apt-get install -y -qq build-essential cmake git zstd xz-utils curl > $R/apt.log 2>&1 || { tail $R/apt.log; exit 2; }
pip install -q zstandard numpy pyarrow > $R/pip.log 2>&1 || { tail $R/pip.log; exit 2; }
git clone -q --depth 1 --branch v0.3.0 --recurse-submodules --shallow-submodules https://github.com/facebook/openzl /tmp/openzl > $R/git.log 2>&1 || { tail $R/git.log; exit 2; }
(cd /tmp/openzl && git rev-parse HEAD) | tee $R/openzl-commit.txt
cmake -S /tmp/openzl -B /tmp/ozb -DCMAKE_BUILD_TYPE=Release -DOPENZL_BUILD_SHARED_LIBS=ON -DOPENZL_BUILD_CPP=OFF -DOPENZL_BUILD_CUSTOM_PARSERS=OFF -DOPENZL_BUILD_TOOLS=OFF -DOPENZL_BUILD_CLI=OFF -DOPENZL_BUILD_EXAMPLES=OFF -DOPENZL_BUILD_TESTS=OFF -DOPENZL_BUILD_BENCHMARKS=OFF -DOPENZL_INSTALL=OFF -DOPENZL_ALLOW_INTROSPECTION=OFF > $R/cmake.log 2>&1 || { tail -30 $R/cmake.log; exit 3; }
cmake --build /tmp/ozb -j4 > $R/build.log 2>&1 || { tail -40 $R/build.log; exit 3; }
find /tmp/ozb \( -name '*.a' -o -name '*.so*' \) -type f | tee $R/built-libs.txt
LIBS=$(find /tmp/ozb -name 'libopenzl*.a' -type f | head -1); Z=$(find /tmp/ozb -name 'libzstd*.a' -type f | head -1); L4=$(find /tmp/ozb -name 'liblz4*.a' -type f | head -1)
echo "libs=$LIBS $Z $L4" | tee -a $R/timeline.txt
gcc -O2 -shared -fPIC bridge.c -I/tmp/openzl/include -I/tmp/ozb/include -o bridge.so -Wl,--start-group $LIBS $Z $L4 -Wl,--end-group -lm -lpthread 2> $R/bridge.log || { cat $R/bridge.log; exit 4; }
python3 -c "import ctypes;ctypes.CDLL('./bridge.so');print('bridge loads')" || exit 4
cp bridge.so openzl-local/bridge.so
echo "built $(t)" | tee -a $R/timeline.txt
curl -sSL -o mbp10.csv.zst https://github.com/alegitdude/Backtester/releases/download/demo-data/ES-glbx-20251105.mbp-10.csv.zst
sha256sum mbp10.csv.zst | tee $R/source.sha256; stat -c %s mbp10.csv.zst | tee -a $R/source.sha256
python3 openzl_candidate.py mbp10.csv.zst candidate.bin --candidate > $R/candidate.encode.log 2>&1; cp candidate.encode.json $R/
echo "candidate encoded $(t)" | tee -a $R/timeline.txt
python3 arrow_openzl_fast.py mbp10.csv.zst stock_openzl.bin > $R/stock.encode.log 2>&1; cp stock_openzl.encode.json $R/
echo "stock encoded $(t)" | tee -a $R/timeline.txt
python3 independent_openzl_candidate.py candidate.bin mbp10.csv.zst --out $R/candidate.independent.json > $R/candidate.check.log 2>&1; echo "candidate check rc=$?" | tee -a $R/timeline.txt
python3 fast_controls.py candidate.bin --out $R/candidate.controls.json > $R/candidate.controls.log 2>&1; echo "controls rc=$?" | tee -a $R/timeline.txt
python3 independent_openzl.py stock_openzl.bin mbp10.csv.zst --out $R/stock.independent.json > $R/stock.check.log 2>&1; echo "stock check rc=$?" | tee -a $R/timeline.txt
echo "checked $(t)" | tee -a $R/timeline.txt
zstd -d -c mbp10.csv.zst | zstd -19 --long=27 -T4 -q -c | wc -c | tee $R/zstd19-long27.bytes
echo "zstd19 $(t)" | tee -a $R/timeline.txt
zstd -d -c mbp10.csv.zst | xz -9 -T4 -c | wc -c | tee $R/xz9.bytes
echo "end $(t)" | tee -a $R/timeline.txt
cat $R/candidate.encode.json $R/stock_openzl.encode.json $R/candidate.independent.json $R/stock.independent.json $R/candidate.controls.json 2>/dev/null
rm -f candidate.bin stock_openzl.bin mbp10.csv.zst
