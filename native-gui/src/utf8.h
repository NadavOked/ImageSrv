/* #1245: a Unicode code point into a text field's UTF-8 buffer. The fields
 * held one byte per typed character (ASCII); a Hebrew letter from the
 * SI-1452 layout is two. Header-only, so tests/test_native_gui_hebrew.py
 * builds it on its own. */
#ifndef IMAGESRV_UTF8_H
#define IMAGESRV_UTF8_H

#include <stddef.h>
#include <string.h>

/* <cp> as UTF-8 into out[0..2]; the byte count, or 0 for a code point a
 * text field does not take (a control character, DEL, a surrogate, or
 * past the BMP -- no key in either layout produces one). */
static inline size_t utf8_encode(int cp, char out[3]) {
    if (cp < 0x20 || cp == 0x7F || (cp >= 0xD800 && cp <= 0xDFFF) || cp > 0xFFFF) return 0;
    if (cp < 0x80) { out[0] = (char)cp; return 1; }
    if (cp < 0x800) {
        out[0] = (char)(0xC0 | (cp >> 6));
        out[1] = (char)(0x80 | (cp & 0x3F));
        return 2;
    }
    out[0] = (char)(0xE0 | (cp >> 12));
    out[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
    out[2] = (char)(0x80 | (cp & 0x3F));
    return 3;
}

/* Appends <cp> to the NUL-terminated <buf> of <n> bytes -- whole, or not at
 * all: half a UTF-8 sequence would draw as garbage and reach the manifest. */
static inline void utf8_append(char *buf, size_t n, int cp) {
    char u[3];
    size_t k = utf8_encode(cp, u), len = strlen(buf);
    if (k && len + k < n) { memcpy(buf + len, u, k); buf[len + k] = 0; }
}

#endif
