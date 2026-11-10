#ifndef IMAGESRV_INSTALL_OSK_H
#define IMAGESRV_INSTALL_OSK_H

/* #2778 (Nadav 09/11): the installer's keyboard on the screen, for a server
 * installed with the mouse alone. Every field is ASCII, so the layout is the
 * US one -- the 47 keys of printable ASCII, and with Shift the other half.
 * Keys are numbered row by row, left to right, then Backspace, Shift, Space
 * and "סיום". Static inline, like install_raid.h, so install.c stays one
 * translation unit for the drivers that #include it. */

#include <string.h>

__attribute__((unused)) static const char *const OSK_LOWER[4] = { "`1234567890-=", "qwertyuiop[]\\", "asdfghjkl;'", "zxcvbnm,./" };
__attribute__((unused)) static const char *const OSK_UPPER[4] = { "~!@#$%^&*()_+", "QWERTYUIOP{}|", "ASDFGHJKL:\"", "ZXCVBNM<>?" };
enum { OSK_CHARS = 47, OSK_BKSP = OSK_CHARS, OSK_SHIFT, OSK_SPACE, OSK_DONE, OSK_KEYS };

/* the character key k types (Space included); 0 for Backspace, Shift and "סיום" */
static inline int osk_char(int k, int shift) {
    if (k == OSK_SPACE) return ' ';
    for (int r = 0; r < 4 && k >= 0; r++) {
        int n = (int)strlen(OSK_LOWER[r]);
        if (k < n) return (unsigned char)(shift ? OSK_UPPER[r] : OSK_LOWER[r])[k];
        k -= n;
    }
    return 0;
}

#endif
