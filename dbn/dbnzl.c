/* bookdelta + OpenZL codec for native DBN MBP-10 files (single thread).
 *   dbnzl enc IN.dbn OUT.dbnzl    encode; prints JSON
 *   dbnzl dec IN.dbnzl OUT.dbn    decode back to the byte-identical DBN file
 *   dbnzl dec IN.dbnzl -          decode to in-memory DBN records only (timing); prints JSON
 * Transform = the published MBP10O1 candidate: zigzag(ts_recv - ts_event); per-instrument
 * carried residuals of price and all 60 book-level px/sz/ct fields; action+side in one
 * radix-3 byte; every column through OpenZL L19 (brute-force over numeric / field-LZ /
 * delta graphs) with checksums. The DBN metadata header is kept verbatim (zstd 19). */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <zstd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include "openzl/zl_compress.h"
#include "openzl/zl_compressor.h"
#include "openzl/zl_decompress.h"
#include "openzl/codecs/zl_generic.h"
#include "openzl/codecs/zl_field_lz.h"
#include "openzl/codecs/zl_delta.h"
#include "openzl/codecs/zl_brute_force_selector.h"

#define FRAME 65536
#define RECLEN 368
#define NNUM 71
#define NRES 61
static const char MAGIC[8] = "MBP10DZ1";
static const char ACTS[] = "ACMTFR", SIDES[] = "ABN";

static void die(const char* m) { fprintf(stderr, "dbnzl: %s\n", m); exit(1); }
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }
static uint64_t zz(uint64_t d) { return (d << 1) ^ (uint64_t)((int64_t)d >> 63); }
static uint64_t unzz(uint64_t v) { return (v >> 1) ^ (uint64_t)(-(int64_t)(v & 1)); }
static uint64_t rd(const uint8_t* p, int w) { uint64_t v = 0; memcpy(&v, p, w); return v; }
static void wr(uint8_t* p, uint64_t v, int w) { memcpy(p, &v, w); }

/* Numeric column c -> (byte offset, width, signed) inside the 368-byte Mbp10Msg. */
static int OFF[NNUM], WID[NNUM], SGN[NNUM], RES[NNUM];
static void layout(void) {
    int o[11] = {32, 8, 1, 2, 4, 31, 16, 24, 30, 40, 44}, w[11] = {8, 8, 1, 2, 4, 1, 8, 4, 1, 4, 4};
    for (int c = 0; c < 11; c++) { OFF[c] = o[c]; WID[c] = w[c]; SGN[c] = (c == 6 || c == 9); RES[c] = (c == 6); }
    for (int l = 0; l < 10; l++) {
        int b = 48 + 32 * l, lo[6] = {0, 8, 16, 20, 24, 28}, lw[6] = {8, 8, 4, 4, 4, 4};
        for (int k = 0; k < 6; k++) { int c = 11 + 6 * l + k; OFF[c] = b + lo[k]; WID[c] = lw[k]; SGN[c] = k < 2; RES[c] = 1; }
    }
}
static uint64_t getv(const uint8_t* r, int c) {
    uint64_t v = rd(r + OFF[c], WID[c]);
    if (SGN[c] && WID[c] < 8) v = (uint64_t)(int64_t)(int32_t)v;
    return v;
}

/* instrument_id -> state row (open addressing). */
#define HCAP 65536
static uint32_t hkey[HCAP]; static int32_t hval[HCAP]; static int nslots;
static int64_t (*state)[NRES];
static int slot(uint32_t id) {
    uint32_t h = (id * 2654435761u) & (HCAP - 1);
    while (hval[h] >= 0) { if (hkey[h] == id) return hval[h]; h = (h + 1) & (HCAP - 1); }
    if (nslots >= HCAP / 2) die("too many instruments");
    hkey[h] = id; hval[h] = nslots;
    memset(state[nslots], 0, sizeof state[0]);
    return nslots++;
}
static void state_init(void) { memset(hval, 0xff, sizeof hval); nslots = 0; state = calloc(HCAP / 2, sizeof state[0]); }

static size_t zl_compress(void* dst, size_t cap, const void* src, size_t width, size_t count, int numeric) {
    ZL_Compressor* g = ZL_Compressor_create(); ZL_CCtx* c = ZL_CCtx_create(); ZL_TypedRef* in = NULL; size_t res = SIZE_MAX;
    ZL_GraphID graph = ZL_GRAPH_COMPRESS_GENERIC;
    if (numeric) {
        ZL_GraphID cand[] = {ZL_GRAPH_NUMERIC, ZL_GRAPH_FIELD_LZ,
            ZL_Compressor_registerStaticGraph_fromNode1o(g, ZL_NODE_DELTA_INT, ZL_GRAPH_NUMERIC),
            ZL_Compressor_registerStaticGraph_fromNode1o(g, ZL_NODE_DELTA_INT, ZL_GRAPH_FIELD_LZ)};
        ZL_RESULT_OF(ZL_GraphID) r = ZL_Compressor_buildBruteForceSelectorGraph(g, cand, 4);
        if (ZL_RES_isError(r)) goto done;
        graph = ZL_RES_value(r);
    }
    if (ZL_isError(ZL_Compressor_selectStartingGraphID(g, graph))) goto done;
    if (ZL_isError(ZL_CCtx_refCompressor(c, g))) goto done;
    if (ZL_isError(ZL_CCtx_setParameter(c, ZL_CParam_compressionLevel, 19))) goto done;
    if (ZL_isError(ZL_CCtx_setParameter(c, ZL_CParam_formatVersion, ZL_MAX_FORMAT_VERSION))) goto done;
    if (ZL_isError(ZL_CCtx_setParameter(c, ZL_CParam_compressedChecksum, 1))) goto done;
    if (ZL_isError(ZL_CCtx_setParameter(c, ZL_CParam_contentChecksum, 1))) goto done;
    in = numeric ? ZL_TypedRef_createNumeric(src, width, count) : ZL_TypedRef_createStruct(src, width, count);
    if (!in) goto done;
    ZL_Report r = ZL_CCtx_compressTypedRef(c, dst, cap, in);
    if (!ZL_isError(r)) res = ZL_validResult(r);
done:
    ZL_TypedRef_free(in); ZL_CCtx_free(c); ZL_Compressor_free(g);
    if (res == SIZE_MAX) die("OpenZL compress");
    return res;
}

static uint8_t* slurp(const char* p, size_t* n) {
    int fd = open(p, O_RDONLY); struct stat st; if (fd < 0 || fstat(fd, &st)) die("open input");
    *n = st.st_size; if (!*n) die("empty input");
    uint8_t* b = mmap(NULL, *n, PROT_READ, MAP_PRIVATE, fd, 0); if (b == MAP_FAILED) die("mmap");
    madvise(b, *n, MADV_SEQUENTIAL); close(fd); return b;
}
static void put(FILE* f, const void* p, size_t n) { if (fwrite(p, 1, n, f) != n) die("write"); }

static int enc(const char* in, const char* out) {
    size_t n; double t0 = now();
    uint8_t* d = slurp(in, &n);
    if (n < 8 || memcmp(d, "DBN", 3)) die("not a DBN file");
    size_t mlen = 8 + (size_t)rd(d + 4, 4);
    if ((n - mlen) % RECLEN) die("records are not all MBP-10");
    size_t rows = (n - mlen) / RECLEN;
    FILE* f = fopen(out, "wb"); if (!f) die("open output");
    put(f, MAGIC, 8);
    size_t mcap = ZSTD_compressBound(mlen); uint8_t* mz = malloc(mcap);
    size_t mz_n = ZSTD_compress(mz, mcap, d, mlen, 19); if (ZSTD_isError(mz_n)) die("zstd");
    uint64_t h[2] = {mlen, mz_n}; put(f, h, 16); put(f, mz, mz_n);
    uint64_t* col = malloc((size_t)NNUM * FRAME * 8); uint8_t* pair = malloc(FRAME);
    size_t cap = ZL_compressBound((size_t)FRAME * 8); uint8_t* dst = malloc(cap);
    state_init();
    for (size_t r0 = 0; r0 < rows; r0 += FRAME) {
        uint32_t m = rows - r0 < FRAME ? rows - r0 : FRAME;
        for (uint32_t j = 0; j < m; j++) {
            const uint8_t* r = d + mlen + (r0 + j) * RECLEN;
            if (r[0] != RECLEN / 4 || r[1] != 0x0A) die("record is not MBP-10");
            int s = slot((uint32_t)rd(r + 4, 4)), k = 0;
            const char* a = memchr(ACTS, r[28], 6); const char* sd = memchr(SIDES, r[29], 3);
            if (!r[28] || !r[29] || !a || !sd) die("unknown action/side");
            pair[j] = (uint8_t)((a - ACTS) * 3 + (sd - SIDES));
            for (int c = 0; c < NNUM; c++) {
                uint64_t v = getv(r, c);
                if (c == 0) v = zz(v - getv(r, 1));
                else if (RES[c]) { uint64_t p = (uint64_t)state[s][k]; state[s][k++] = (int64_t)v; v = zz(v - p); }
                col[(size_t)c * FRAME + j] = v;
            }
        }
        put(f, &m, 4);
        for (int c = 0; c <= NNUM; c++) {
            size_t z = c < NNUM ? zl_compress(dst, cap, col + (size_t)c * FRAME, 8, m, 1) : zl_compress(dst, cap, pair, 1, m, 0);
            uint64_t zn = z; put(f, &zn, 8); put(f, dst, z);
        }
    }
    put(f, "END!", 4); uint64_t rr = rows; put(f, &rr, 8);
    long sz = ftell(f); fclose(f);
    printf("{\"op\":\"dbnzl-encode\",\"rows\":%zu,\"seconds\":%.4f,\"bytes\":%ld}\n", rows, now() - t0, sz);
    return 0;
}

static int dec(const char* in, const char* out) {
    double t0 = now(); size_t n; uint8_t* a = slurp(in, &n); size_t p = 0;
    if (n < 24 || memcmp(a, MAGIC, 8)) die("magic");
    uint64_t mlen = rd(a + 8, 8), mz_n = rd(a + 16, 8); p = 24;
    uint8_t* meta = malloc(mlen);
    if (p + mz_n > n || ZSTD_decompress(meta, mlen, a + p, mz_n) != mlen) die("metadata"); p += mz_n;
    FILE* f = strcmp(out, "-") ? fopen(out, "wb") : NULL;
    if (f) put(f, meta, mlen);
    uint64_t* col = malloc((size_t)NNUM * FRAME * 8); uint8_t* pair = malloc(FRAME);
    uint8_t* recs = malloc((size_t)FRAME * RECLEN);
    ZL_DCtx* dc = ZL_DCtx_create(); state_init();
    uint64_t rows = 0, sum = 0;
    for (;;) {
        if (p + 4 > n) die("truncated");
        if (!memcmp(a + p, "END!", 4)) { if (p + 12 != n || rd(a + p + 4, 8) != rows) die("footer"); break; }
        uint32_t m = (uint32_t)rd(a + p, 4); p += 4;
        if (!m || m > FRAME) die("frame size");
        for (int c = 0; c <= NNUM; c++) {
            if (p + 8 > n) die("truncated"); uint64_t zn = rd(a + p, 8); p += 8;
            if (p + zn > n) die("truncated");
            void* dst = c < NNUM ? (void*)(col + (size_t)c * FRAME) : (void*)pair; size_t w = c < NNUM ? 8 : 1;
            ZL_OutputInfo info; ZL_Report r = ZL_DCtx_decompressTyped(dc, &info, dst, w * m, a + p, zn);
            if (ZL_isError(r) || info.decompressedByteSize != w * m || info.fixedWidth != w || info.numElts != m) die("OpenZL decode");
            p += zn;
        }
        for (uint32_t j = 0; j < m; j++) {
            uint8_t* r = recs + (size_t)j * RECLEN;
            r[0] = RECLEN / 4; r[1] = 0x0A;
            uint64_t ts_event = col[(size_t)1 * FRAME + j];
            uint32_t iid = (uint32_t)col[(size_t)4 * FRAME + j];
            int s = slot(iid), k = 0;
            for (int c = 1; c < NNUM; c++) {
                uint64_t v = col[(size_t)c * FRAME + j];
                if (RES[c]) { v = (uint64_t)state[s][k] + unzz(v); state[s][k++] = (int64_t)v; }
                wr(r + OFF[c], v, WID[c]);
            }
            wr(r + 32, ts_event + unzz(col[j]), 8);
            r[28] = ACTS[pair[j] / 3]; r[29] = SIDES[pair[j] % 3];
            const uint64_t* w = (const uint64_t*)r;
            for (int q = 0; q < RECLEN / 8; q++) sum += w[q];
        }
        if (f) put(f, recs, (size_t)m * RECLEN);
        rows += m;
    }
    if (f) fclose(f);
    fprintf(f ? stderr : stdout, "{\"op\":\"dbnzl-decode\",\"rows\":%lu,\"seconds\":%.4f,\"checksum\":\"%016lx\"}\n", (unsigned long)rows, now() - t0, (unsigned long)sum);
    return 0;
}

int main(int argc, char** argv) {
    layout();
    if (argc == 4 && !strcmp(argv[1], "enc")) return enc(argv[2], argv[3]);
    if (argc == 4 && !strcmp(argv[1], "dec")) return dec(argv[2], argv[3]);
    fprintf(stderr, "usage: dbnzl enc IN.dbn OUT.dbnzl | dbnzl dec IN.dbnzl OUT.dbn|-\n"); return 2;
}
