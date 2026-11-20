/* BLAKE3 for chunkcast (#3332): the official C implementation, version 1.8.1
 * (github.com/BLAKE3-team/BLAKE3, tag 1.8.1, ad639b126ef9; CC0-1.0 OR Apache-2.0,
 * the license files beside this one; THIRD_PARTY_NOTICES.md). The files beside
 * this one are that release's c/ directory, byte for byte; this one is ours.
 *
 * One translation unit for the three C files, so a build adds one C file and the
 * two assembly files -- SSE4.1 and SSE2, chosen at run time by blake3_dispatch.c --
 * and nothing else. AVX2 and AVX-512 are left out: the cloners (i5 650, Core 2)
 * have neither, and SSE4.1 is what they run. Without SSE2 it is the portable C. */
#define BLAKE3_NO_AVX2
#define BLAKE3_NO_AVX512
#include "blake3.c"
#include "blake3_dispatch.c"
#include "blake3_portable.c"
