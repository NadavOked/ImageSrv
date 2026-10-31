#ifndef IMAGESRV_HMAC_SHA256_H
#define IMAGESRV_HMAC_SHA256_H

#include <stddef.h>
#include <stdint.h>

/* HMAC-SHA256 (RFC 2104 / 6234). out is 32 bytes. */
void hmac_sha256(const uint8_t *key, size_t key_len,
                 const uint8_t *msg, size_t msg_len,
                 uint8_t out[32]);

#endif
