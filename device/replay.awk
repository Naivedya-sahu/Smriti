# replay.awk — stroke-font replayer (busybox awk compatible).
# Reads a .sf stroke-font file, emits lamp pen commands for `text`.
#
#   awk -f replay.awk -v text="hello" [-v x0=100 -v y0=150 -v maxw=1300] font.sf
#
# .sf format (built by host/ink.py compile):
#   F <em> <line_height> <pressure> <size>
#   G <ascii-code> <advance-em>
#   S x1 y1 x2 y2 ...

BEGIN {
    if (x0 == "") x0 = 100
    if (y0 == "") y0 = 150
    if (maxw == "") maxw = 1300
    for (i = 32; i < 127; i++) ORD[sprintf("%c", i)] = i
}

$1 == "F" { em = $2; lh = $3; press = $4; size = $5; next }
$1 == "G" { g = $2; adv[g] = $3; ns[g] = 0; next }
$1 == "S" { ns[g]++; st[g, ns[g]] = substr($0, 3); next }

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
                for (p = 1; p < n; p += 2) {
                    px = int(x + pt[p] * scale)
                    py = int(y + pt[p + 1] * scale)
                    if (px < 0) px = 0; if (px > 1403) px = 1403
                    if (py < 0) py = 0; if (py > 1871) py = 1871
                    printf "pen %d %d %d\n", px, py, press
                }
                print "pen_up"
            }
            x += adv[c] * scale
        }
        x += adv[32] * scale
    }
}
