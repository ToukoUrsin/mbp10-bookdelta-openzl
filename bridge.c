/* Exact typed-column interface to unmodified OpenZL v0.3.0. */
#include <stdint.h>
#include <stdio.h>
#include "openzl/zl_compress.h"
#include "openzl/zl_compressor.h"
#include "openzl/zl_decompress.h"
#include "openzl/codecs/zl_generic.h"
#include "openzl/codecs/zl_field_lz.h"
#include "openzl/codecs/zl_delta.h"
#include "openzl/codecs/zl_brute_force_selector.h"

size_t bound(size_t n) { return ZL_compressBound(n); }

size_t compress_column(void* dst, size_t cap, const void* src, size_t width,
                       size_t count, int kind, int level, int brute) {
    ZL_Compressor* g = ZL_Compressor_create();
    ZL_CCtx* c = ZL_CCtx_create();
    ZL_TypedRef* input = NULL;
    size_t result = SIZE_MAX;
    if (!g || !c) goto done;
    ZL_GraphID graph = ZL_GRAPH_COMPRESS_GENERIC;
    if (brute && kind == 1) {
        ZL_GraphID candidates[] = {
            ZL_GRAPH_NUMERIC, ZL_GRAPH_FIELD_LZ,
            ZL_Compressor_registerStaticGraph_fromNode1o(g, ZL_NODE_DELTA_INT, ZL_GRAPH_NUMERIC),
            ZL_Compressor_registerStaticGraph_fromNode1o(g, ZL_NODE_DELTA_INT, ZL_GRAPH_FIELD_LZ)
        };
        ZL_RESULT_OF(ZL_GraphID) r = ZL_Compressor_buildBruteForceSelectorGraph(g,candidates,4);
        if (ZL_RES_isError(r)) goto done;
        graph = ZL_RES_value(r);
    }
    if (ZL_isError(ZL_Compressor_selectStartingGraphID(g, graph))) goto done;
    if (ZL_isError(ZL_CCtx_refCompressor(c, g))) goto done;
    if (ZL_isError(ZL_CCtx_setParameter(c,ZL_CParam_compressionLevel,level))) goto done;
    if (ZL_isError(ZL_CCtx_setParameter(c,ZL_CParam_formatVersion,ZL_MAX_FORMAT_VERSION))) goto done;
    if (ZL_isError(ZL_CCtx_setParameter(c,ZL_CParam_compressedChecksum,1))) goto done;
    if (ZL_isError(ZL_CCtx_setParameter(c,ZL_CParam_contentChecksum,1))) goto done;
    input = kind == 1 ? ZL_TypedRef_createNumeric(src,width,count)
          : kind == 2 ? ZL_TypedRef_createStruct(src,width,count)
                      : ZL_TypedRef_createSerial(src,width*count);
    if (!input) goto done;
    ZL_Report r = ZL_CCtx_compressTypedRef(c,dst,cap,input);
    if (ZL_isError(r)) fprintf(stderr,"OpenZL: %s\n",ZL_CCtx_getErrorContextString(c,r));
    else result = ZL_validResult(r);
done:
    ZL_TypedRef_free(input); ZL_CCtx_free(c); ZL_Compressor_free(g);
    return result;
}

size_t decompress_column(void* dst, size_t cap, const void* src, size_t size,
                         size_t width, size_t count, int kind) {
    ZL_DCtx* c = ZL_DCtx_create();
    if (!c) return SIZE_MAX;
    ZL_OutputInfo info;
    ZL_Report r = ZL_DCtx_decompressTyped(c,&info,dst,cap,src,size);
    ZL_DCtx_free(c);
    ZL_Type type = kind == 1 ? ZL_Type_numeric : kind == 2 ? ZL_Type_struct : ZL_Type_serial;
    if (ZL_isError(r) || info.type != type || info.decompressedByteSize != width*count)
        return SIZE_MAX;
    if (kind && (info.fixedWidth != width || info.numElts != count)) return SIZE_MAX;
    return info.decompressedByteSize;
}
