# replay.awk — stroke-font replayer (busybox awk compatible).
# Reads a .sf stroke-font file, emits lamp pen-injection commands for `text`.
# Grammar (Elxnk lamp, stdin): "pen down x y [pressure]", "pen move x y", "pen up".
#
#   awk -f replay.awk -v text="hello" [-v x0=100 -v y0=150 -v maxw=1300 -v step=3] font.sf
#
# step = emit every Nth waypoint; lamp interpolates smoothly between waypoints
# (500 events per move — sparse waypoints keep it fast, ink stays smooth).
#
# .sf format (built by host/ink.py compile):
#   F <em> <line_height> <pressure> <size>
#   G <ascii-code> <advance-em>
#   S x1 y1 x2 y2 ...

BEGIN {
    if (x0 == "") x0 = 100
    if (y0 == "") y0 = 150
    if (maxw == "") maxw = 1300
    if (step == "") step = 3
    for (i = 32; i < 127; i++) ORD[sprintf("%c", i)] = i
}

$1 == "F" { em = $2; lh = $3; press = $4; size = $5; next }
$1 == "G" { g = $2; adv[g] = $3; ns[g] = 0; next }
$1 == "S" { ns[g]++; st[g, ns[g]] = substr($0, 3); next }

function clampx(v) { return v < 0 ? 0 : (v > 1403 ? 1403 : v) }
function clampy(v) { return v < 0 ? 0 : (v > 1871 ? 1871 : v) }

END {
    scale = size / em
    lineh = int(size * lh)
    x = x0; y = y0
    nw = split(text, words, " ")
    for (w = 1; w <= nw; w++) {
        word = words[w]
        ww = 0
        for (i = 1; i <= length(word); i++) {
            c = ORD[substr(word, i, 1)]
            ww += adv[(c in adv) ? c : 63] * scale     # 63 = '?'
        }
        if (x > x0 && x + ww > maxw) { x = x0; y += lineh }
        for (i = 1; i <= length(word); i++) {
            c = ORD[substr(word, i, 1)]
            if (!(c in adv)) c = 63
            for (s = 1; s <= ns[c]; s++) {
                n = split(st[c, s], pt, " ")
                npts = n / 2
                for (p = 1; p <= npts; p++) {
                    if (p > 1 && p < npts && (p - 1) % step != 0) continue
                    px = clampx(int(x + pt[2 * p - 1] * scale))
                    py = clampy(int(y + pt[2 * p] * scale))
                    if (p == 1) printf "pen down %d %d %d\n", px, py, press
                    else        printf "pen move %d %d\n", px, py
                }
                print "pen up"
            }
            x += adv[c] * scale
        }
        x += adv[32] * scale
    }
}
