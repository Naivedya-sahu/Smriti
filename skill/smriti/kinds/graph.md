# kind: graph (profile: graph)

## Read
Treat the focus as a function or data to plot: put it in 'text' as
'y = <expression in x>, x from <a> to <b>'. Keep a leading 'add' if the writer wrote
add/also/overlay, and a trailing 'to G<n>' if they named a graph.
For a drawn graph note axes names, function or data, key points, ranges.
(Rewriting to 'y = ...' without 'add' turned an overlay into a new graph, run 2026-09-26.)

## Reply
The host draws graphs from a GraphSpec in milliseconds (host/plots.py): emit a graph
block {"type":"graph","id":null,"plot":<GraphSpec>}. You give parameters only; no pgfplots.
Pick the type:
- function: CT signals, 1-4 curves, expr in t. u(t) rect(t) tri(t) sinc(t)=sin(pi t)/(pi t),
  k*delta(t-t0) (drawn as an arrow), sin cos tan exp log log10 sqrt abs sign pi e.
  Products with *, powers with ** (or ^). Frequency plots still use t in expr; set xlabel f or ω.
- stem: DT sequences x[n]. curves expr in n (delta(n) = unit sample, u(n) = unit step),
  or values with n [nmin, nmax]. At most 128 samples.
- pole_zero: zeros/poles as [re, im], or b/a. domain z draws the unit circle.
- freq_response: b, a. z: powers of z^-1 (like freqz), ω from 0 to π (x overrides).
  s: highest power first (like freqs), ω axis logarithmic, x = [ωmin > 0, ωmax].
  scale linear|db, show mag|phase.
- marks: vertical marker lines with a label (cutoff ω_c, notch 2π/3, period T).
Labels short: _ subscript, ^ superscript, ω π θ τ φ α σ Δ Ω μ λ ∠ ∞ ± or \omega-style names.
The tablet is grey: curves differ by dash and legend label, so label every curve.
One idea per graph; two curves to compare, never a crowd.
GraphSpec examples (rect and its spectrum, RC step response, 0.8^n u[n], moving average,
2nd-order pole-zero, RC low-pass Bode):
{"type":"function","title":"x(t) = rect(t)","xlabel":"t","ylabel":"x(t)","x":[-2,2],"y":[-0.25,1.25],"curves":[{"expr":"rect(t)","label":"rect(t)"}]}
{"type":"function","title":"X(f) = sinc(f)","xlabel":"f","ylabel":"X(f)","x":[-4,4],"curves":[{"expr":"sinc(t)","label":"sinc(f)"}]}
{"type":"function","title":"RC step response","xlabel":"t","x":[-1,5],"y":[-0.25,1.25],"curves":[{"expr":"(1-exp(-t))*u(t)","label":"y(t)"},{"expr":"u(t)","label":"u(t)"}]}
{"type":"stem","title":"x[n] = 0.8^n u[n]","xlabel":"n","n":[-3,12],"curves":[{"expr":"0.8**n*u(n)","label":"x[n]"}]}
{"type":"freq_response","title":"3-point moving average","domain":"z","b":[0.33333,0.33333,0.33333],"a":[1],"scale":"linear","show":"mag","marks":[{"x":2.0944,"label":"2π/3"}]}
{"type":"pole_zero","title":"2nd-order resonator","domain":"z","b":[1,0,-1],"a":[1,-1.2728,0.81]}
{"type":"freq_response","title":"RC low-pass","domain":"s","b":[1],"a":[0.001,1],"scale":"db","show":"mag","marks":[{"x":1000,"label":"ω_c = 1/RC"}]}
'add y = ... to G2' draws only the new curve on G2's fixed axes.
Only when no type fits (3-D, shaded regions, phase portraits): pgfplots in
`$$\begin{tikzpicture}\begin{axis}[axis lines=middle]...\end{axis}\end{tikzpicture}$$` (slow).

## Pitfalls
- No x range means [-5, 5]: give the window that shows the feature (rect: [-2, 2]).
- Pass y for signals with jumps (u, rect) so the edges are not on the frame.
- s-domain freq_response: x must be positive (log axis); z-domain x is rad/sample.
- A rejected GraphSpec comes back with the reason; fix that field only.
- pgfplots fallback: trig takes degrees `sin(deg(x))`; keep each axis <= 7.5 cm when two
  sit side by side (they shrink to ~0.72x).
