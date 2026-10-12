# kind: circuit (profile: circuit)

## Read
Treat the focus as an electrical circuit. In 'circuit' list only the components you SEE, each as type + its label/value exactly as
written (e.g. 'resistor <label>; capacitor <label>; NPN transistor'). Never invent values;
leave a value out if none is written.
(No concrete values in this example on purpose: a 4B model copied '4ohm'/'12V' into real
circuit photos, eval 2026-09-26.)

## Reply
ANSWER the question first: rule/formula and result in $$...$$ with units. Draw a circuit
ONLY if asked to draw. Then `$$\begin{circuitikz}...\end{circuitikz}$$`, labels in math
mode, elements R, C, L, V (uppercase), I, sV, npn, D.

## circuitikz rules
- Labels MUST be math mode: `to[R, l=$R_1$]`, `to[R, l=$4\Omega$]`, `v=$12\,V$`. Bare
  `\Omega` outside `$ $` fails to compile and the drawing is lost.
- Op-amps: place `node[op amp](A){}` FIRST, wire ONLY from anchors (A.-), (A.+), (A.out)
  with right-angle paths (|-, -|, ++(dx,dy)). Never guess absolute coordinates near the
  op-amp (runs 12 + 2026-10-02). Feedback closes from A.out back to A.-.
- Colours: black default; blue/red are available pens (one mask per colour).

## Verified example (active low-pass)
```latex
\draw (0,0) node[op amp](A){}
  (A.-) to[R, l=$R_1$, -o] ++(-2.5,0) node[left]{$v_{in}$}
  (A.+) -- ++(-0.6,0) node[ground]{}
  (A.-) |- ++(1,1.5) coordinate(f) to[R, l=$R_2$] (f -| A.out) -- (A.out)
  (A.-) |- ++(1,3) coordinate(g) to[C, l=$C$] (g -| A.out) -- (f -| A.out)
  (A.out) to[short, -o] ++(1,0) node[right]{$v_{out}$};
```
