#!/bin/sh
# Compile every starter program with OpenPLC's own toolchain, and check the
# 32-bit helpers they contain against the C library bit for bit.
#
#   sh examples/openplc/check_starters.sh [path/to/OpenPLC_v3]
#
# Needs a built OpenPLC_v3 (default /opt/OpenPLC_v3, or $OPENPLC_DIR, or the
# argument) and g++. Everything happens in throwaway directories under
# mktemp: nothing inside OpenPLC_v3 is written and no runtime is started.
#
# 1. For each examples/openplc/<scene>/<scene>.st: iec2c (matiec) with the
#    flags compile_program.sh uses, g++ on the generated Config0.c and Res0.c,
#    OpenPLC's glue_generator, and g++ on the glueVars.cpp it writes. That is
#    compile_program.sh minus the final link against the runtime.
# 2. check_real_helpers.c is built against the C that matiec generated from
#    one starter and calls FF_WORDS_TO_REAL, FF_REAL_TO_DWORD and
#    FF_WORDS_TO_DINT directly: every normal float among two million
#    pseudo-random bit patterns must survive decode and encode unchanged.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
OPLC_ROOT="${1:-${OPENPLC_DIR:-/opt/OpenPLC_v3}}"
OPLC="$OPLC_ROOT/webserver"
[ -x "$OPLC/iec2c" ] || { echo "no iec2c under $OPLC -- build OpenPLC_v3 first"; exit 2; }

fail=0
for st in "$HERE"/*/*.st; do
  out="$(mktemp -d)"
  ( cd "$out" &&
    "$OPLC/iec2c" -f -l -p -r -R -a -I "$OPLC/lib" -T "$out" "$st" > iec2c.log 2>&1 &&
    g++ -std=gnu++11 -I "$OPLC/core/lib" -c Config0.c -w > gcc.log 2>&1 &&
    g++ -std=gnu++11 -I "$OPLC/core/lib" -c Res0.c -w >> gcc.log 2>&1 &&
    "$OPLC/core/glue_generator" > glue.log 2>&1 &&
    g++ -std=gnu++11 -I "$OPLC/core/lib" -I "$OPLC/core" -c glueVars.cpp -w >> gcc.log 2>&1 )
  if [ $? -eq 0 ]; then
    echo "compiled  $(basename "$st")  ($(grep -c __LOCATED_VAR "$out/LOCATED_VARIABLES.h") located variables)"
  else
    echo "FAILED    $(basename "$st")"
    cat "$out/iec2c.log" "$out/gcc.log" "$out/glue.log" 2>/dev/null | head -30
    fail=1
  fi
  rm -rf "$out"
done

# The batch-dosing starter declares all three helpers.
st="$HERE/batch-dosing/batch_dosing.st"
out="$(mktemp -d)"
( cd "$out" &&
  "$OPLC/iec2c" -f -l -p -r -R -a -I "$OPLC/lib" -T "$out" "$st" > /dev/null 2>&1 &&
  cp "$HERE/check_real_helpers.c" harness.c &&
  # The runtime supplies timers, sockets and the located variables; the
  # helpers need none of them, so they are left unresolved rather than faked.
  g++ -std=gnu++11 -I "$OPLC/core/lib" -I . harness.c -o harness -w -lm \
      -Wl,--unresolved-symbols=ignore-all > gcc.log 2>&1 &&
  ./harness ) || fail=1
rm -rf "$out"

[ $fail -eq 0 ] && echo "all starters compile; the 32-bit helpers are exact" || echo "FAILURES above"
exit $fail
