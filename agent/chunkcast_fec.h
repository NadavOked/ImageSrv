/* ImageSrv's XOR codec. A short final packet is padded with zeros on the wire. */
#ifndef CHUNKCAST_FEC_H
#define CHUNKCAST_FEC_H
#include <stddef.h>
#include <string.h>

#define FEC_K 32u
#define CC_PAYLOAD 1440u

static void fec_encode(unsigned char *parity, const unsigned char *group, size_t bytes)
{
    memset(parity, 0, CC_PAYLOAD);
    for (size_t offset = 0; offset < bytes; offset += CC_PAYLOAD) {
        size_t n = bytes - offset < CC_PAYLOAD ? bytes - offset : CC_PAYLOAD;
        for (size_t i = 0; i < n; i++) parity[i] ^= group[offset + i];
    }
}

/* Missing is the sole absent packet within this group. Do not read its stale slot bytes. */
static void fec_decode(unsigned char *group, size_t bytes, unsigned missing, const unsigned char *parity)
{
    size_t at = (size_t)missing * CC_PAYLOAD;
    size_t n = bytes - at < CC_PAYLOAD ? bytes - at : CC_PAYLOAD;
    memcpy(group + at, parity, n);
    for (size_t offset = 0; offset < bytes; offset += CC_PAYLOAD) {
        if (offset == at) continue;
        size_t take = bytes - offset < n ? bytes - offset : n;
        for (size_t i = 0; i < take; i++) group[at + i] ^= group[offset + i];
    }
}
#endif
