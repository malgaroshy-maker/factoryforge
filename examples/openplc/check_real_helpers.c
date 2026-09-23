/* Calls the matiec-compiled FF_* helpers from a starter program directly and
   compares them with the C library's own view of the same 32 bits.
   Built and run by check_starters.sh; see there. */
#include "iec_std_lib.h"
#include "accessor.h"
#include "POUS.h"
#include "Config0.h"
#include "POUS.c"
#include <stdio.h>
#include <string.h>
#include <stdint.h>

static uint32_t bits_of(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
static float float_of(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }

int main(void) {
    BOOL eno;
    long checked = 0, bad = 0;

    /* Values a scene actually sends, then two million pseudo-random patterns.
       Only normal floats: zero and subnormals are checked separately below,
       and the tag bus refuses NaN and infinity before they reach a wire. */
    static const uint32_t fixed[] = {
        0x3F800000u, 0xBF800000u, 0x3DCCCCCDu, 0x42C80000u, 0xC3889333u,
        0x00800000u, 0x80800000u, 0x7F7FFFFFu, 0xFF7FFFFFu, 0x3F7FFFFFu,
        0x4B7FFFFFu, 0x40490FDBu, 0x43340000u, 0x3E19999Au, 0x3F000000u};
    const long nfixed = (long)(sizeof fixed / sizeof fixed[0]);
    uint32_t x = 2463534242u;
    for (long i = 0; i < nfixed + 2000000; i++) {
        uint32_t u;
        if (i < nfixed) u = fixed[i];
        else { x ^= x << 13; x ^= x >> 17; x ^= x << 5; u = x; }
        uint32_t e = (u >> 23) & 0xFF;
        if (e == 0 || e == 255) continue;
        REAL dec = FF_WORDS_TO_REAL(1, &eno, (WORD)(u >> 16), (WORD)(u & 0xFFFF));
        DWORD enc = FF_REAL_TO_DWORD(1, &eno, float_of(u));
        checked++;
        if (bits_of(dec) != u || enc != u) {
            if (bad < 10) printf("REAL mismatch %08x: decoded %08x, encoded %08x\n",
                                 u, bits_of(dec), (unsigned)enc);
            bad++;
        }
    }

    /* Zero, negative zero and subnormals come back as zero, as documented. */
    static const uint32_t tiny[] = {0x00000000u, 0x80000000u, 0x00000001u, 0x007FFFFFu};
    for (unsigned i = 0; i < sizeof tiny / sizeof tiny[0]; i++) {
        REAL dec = FF_WORDS_TO_REAL(1, &eno, (WORD)(tiny[i] >> 16), (WORD)(tiny[i] & 0xFFFF));
        DWORD enc = FF_REAL_TO_DWORD(1, &eno, float_of(tiny[i]));
        checked++;
        if (dec != 0.0f || (enc & 0x7FFFFFFFu) != 0) {
            printf("tiny %08x: decoded %g, encoded %08x\n", tiny[i], (double)dec, (unsigned)enc);
            bad++;
        }
    }

    /* The DINT reassembly across the signed range's corners. */
    static const int32_t ints[] = {0, 1, -1, 9, 70000, 1000000, 2147483647,
                                   (int32_t)0x80000000u, 27648, -27648, 32767, -32768};
    for (unsigned i = 0; i < sizeof ints / sizeof ints[0]; i++) {
        uint32_t u = (uint32_t)ints[i];
        DINT got = FF_WORDS_TO_DINT(1, &eno, (WORD)(u >> 16), (WORD)(u & 0xFFFF));
        checked++;
        if (got != ints[i]) { printf("DINT mismatch %d -> %d\n", ints[i], got); bad++; }
    }

    printf("32-bit helpers: %ld values checked, %ld mismatches\n", checked, bad);
    return bad ? 1 : 0;
}
