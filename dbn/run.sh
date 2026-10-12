#!/usr/bin/env bash
# DBN vs bookdelta+OpenZL on the same 12,104,179 ES MBP-10 rows (GLBX.MDP3, 2025-11-05).
# Single-core timings pinned with taskset; decode runs are serial on an idle core, warm page cache.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
cd "$(dirname "$0")"; R=results; mkdir -p $R; W=${WORK:-/tmp/w}; mkdir -p $W
t(){ date -u +%FT%TZ; }
echo "start $(t)" | tee $R/timeline.txt
sudo apt-get update -qq && sudo apt-get install -y -qq build-essential cmake git zstd curl time bc python3 > $W/apt.log 2>&1
command -v cargo >/dev/null || { curl -sSf https://sh.rustup.rs | sh -s -- -y -q --profile minimal > $W/rustup.log 2>&1; }
. "$HOME/.cargo/env" 2>/dev/null || true
C=($(python3 -c 'import os;print(*sorted(os.sched_getaffinity(0)))')); echo "allowed cpus: ${C[*]}" | tee $R/cpu.txt
lscpu | grep -E 'Model name|^CPU\(s\)|MHz' | tee -a $R/cpu.txt; cat /sys/fs/cgroup/memory.max 2>/dev/null | tee -a $R/cpu.txt; free -g | tee -a $R/cpu.txt
[ -d /tmp/openzl ] || git clone -q --depth 1 --branch v0.3.0 --recurse-submodules --shallow-submodules https://github.com/facebook/openzl /tmp/openzl
(cd /tmp/openzl && git rev-parse HEAD) > $R/openzl-commit.txt
[ -f /tmp/ozb/libopenzl.a ] || { cmake -S /tmp/openzl -B /tmp/ozb -DCMAKE_BUILD_TYPE=Release -DOPENZL_BUILD_SHARED_LIBS=OFF -DOPENZL_BUILD_CPP=OFF -DOPENZL_BUILD_CUSTOM_PARSERS=OFF -DOPENZL_BUILD_TOOLS=OFF -DOPENZL_BUILD_CLI=OFF -DOPENZL_BUILD_EXAMPLES=OFF -DOPENZL_BUILD_TESTS=OFF -DOPENZL_BUILD_BENCHMARKS=OFF -DOPENZL_INSTALL=OFF -DOPENZL_ALLOW_INTROSPECTION=OFF > $W/cmake.log 2>&1 && cmake --build /tmp/ozb -j4 > $W/build.log 2>&1; }
OZ=$(find /tmp/ozb -name 'libopenzl*.a' | head -1); Z=$(find /tmp/ozb -name 'libzstd*.a' | head -1); L4=$(find /tmp/ozb -name 'liblz4*.a' | head -1)
ZH=$(dirname "$(find /tmp/openzl -name zstd.h -path '*lib/zstd.h' | head -1)")
gcc -O2 -march=x86-64-v2 dbnzl.c -I/tmp/openzl/include -I/tmp/ozb/include -I"$ZH" -o $W/dbnzl -Wl,--start-group $OZ $Z $L4 -Wl,--end-group -lm -lpthread
(cd dbnbench && CARGO_TARGET_DIR=$W/target cargo build --release -q)
cp $W/target/release/dbnbench $W/
cargo tree --manifest-path dbnbench/Cargo.toml -q --depth 1 | head -5 > $R/dbn-crate.txt
echo "built $(t)" | tee -a $R/timeline.txt

# 1. Native Databento DBN test file (dbn repo) round-trips byte-exact through our codec.
curl -sSL -o $W/td.dbn.zst https://raw.githubusercontent.com/databento/dbn/main/tests/data/test_data.mbp-10.v3.dbn.zst
zstd -dqf $W/td.dbn.zst -o $W/td.dbn
$W/dbnzl enc $W/td.dbn $W/td.dbnzl > /dev/null; $W/dbnzl dec $W/td.dbnzl $W/td.out.dbn > /dev/null
cmp $W/td.dbn $W/td.out.dbn && echo "databento test_data.mbp-10.v3.dbn round-trip: identical" | tee $R/native-roundtrip.txt

# 2. Same rows as the published result, as DBN records built with the official dbn crate.
SRC=$W/mbp10.csv.zst
[ -f $SRC ] || curl -sSL -o $SRC https://github.com/alegitdude/Backtester/releases/download/demo-data/ES-glbx-20251105.mbp-10.csv.zst
sha256sum $SRC | tee $R/source.sha256
if [ -n "${SMOKE:-}" ]; then zstd -dc $SRC | head -n $((SMOKE+1)) | zstd -q -f -o $W/smoke.csv.zst || true; SRC=$W/smoke.csv.zst; fi
/usr/bin/time -v $W/dbnbench from-csv $SRC $W/es.dbn 2> $W/fromcsv.log; grep -E '^\{|Elapsed' $W/fromcsv.log | tee $R/from-csv.txt
# Fidelity: dbn's own CSV encoder (with mapped symbols) must reproduce the source CSV.
A=$(zstd -dc $SRC | sha256sum | cut -c1-64); B=$($W/dbnbench to-csv $W/es.dbn | tr -d '\r' | sha256sum | cut -c1-64)
echo "source csv $A / dbn->csv $B" | tee $R/dbn-fidelity.txt; [ "$A" = "$B" ] && echo "identical" | tee -a $R/dbn-fidelity.txt
echo "dbn built $(t)" | tee -a $R/timeline.txt

# 3. Encodes: one per core, concurrently (3 runs of ours, then dbn+zstd x3, zstd -19 once).
for i in 1 2 3; do taskset -c ${C[$((i-1))]} $W/dbnzl enc $W/es.dbn $W/es.$i.dbnzl > $R/enc-dbnzl-$i.json & done
taskset -c ${C[3]} bash -c "for i in 1 2 3; do $W/dbnbench zst $W/es.dbn $W/es.\$i.dbn.zst > $R/enc-dbnzst-\$i.json; done; \
  s=\$(date +%s.%N); zstd -19 -T1 -q -c $W/es.dbn > $W/es.dbn.z19; e=\$(date +%s.%N); \
  echo \"{\\\"op\\\":\\\"zstd-19-encode\\\",\\\"seconds\\\":\$(echo \"\$e - \$s\" | bc),\\\"bytes\\\":\$(stat -c %s $W/es.dbn.z19)}\" > $R/enc-zstd19.json"
wait
cmp $W/es.1.dbnzl $W/es.2.dbnzl && cmp $W/es.1.dbnzl $W/es.3.dbnzl && cmp $W/es.1.dbn.zst $W/es.2.dbn.zst
mv $W/es.1.dbnzl $W/es.dbnzl; mv $W/es.1.dbn.zst $W/es.dbn.zst; rm -f $W/es.[23].*
echo "encoded $(t)" | tee -a $R/timeline.txt

# 4. Exact inverse: our archive decodes to the identical DBN file.
S1=$(sha256sum < $W/es.dbn | cut -c1-64); S2=$($W/dbnzl dec $W/es.dbnzl /dev/stdout | sha256sum | cut -c1-64)
S3=$(zstd -dc $W/es.dbn.zst | sha256sum | cut -c1-64)
echo "es.dbn $S1 / dbnzl->dbn $S2 / dbn.zst->dbn $S3" | tee $R/exact.txt; [ "$S1" = "$S2" ] && [ "$S1" = "$S3" ] && echo identical | tee -a $R/exact.txt
ls -l $W/es.dbn $W/es.dbn.zst $W/es.dbn.z19 $W/es.dbnzl | tee $R/sizes.txt
echo "verified $(t)" | tee -a $R/timeline.txt

# 5. Decode: serial on core 2, warm cache, 5 interleaved rounds. Every record is materialized
#    and checksummed (same 46-word sum on both sides).
cat $W/es.dbn $W/es.dbn.zst $W/es.dbn.z19 $W/es.dbnzl > /dev/null
: > $R/decode.jsonl
for i in 1 2 3 4 5; do
  taskset -c ${C[2]} $W/dbnbench decode $W/es.dbn >> $R/decode.jsonl
  taskset -c ${C[2]} $W/dbnbench decode $W/es.dbn.zst >> $R/decode.jsonl
  taskset -c ${C[2]} $W/dbnbench decode $W/es.dbn.z19 >> $R/decode.jsonl
  taskset -c ${C[2]} $W/dbnzl dec $W/es.dbnzl - | sed "s/}\$/,\"file\":\"es.dbnzl\"}/" >> $R/decode.jsonl
done
echo "end $(t)" | tee -a $R/timeline.txt
python3 summarize.py $R > $R/summary.md; cat $R/summary.md
