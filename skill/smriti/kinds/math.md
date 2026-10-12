# kind: math (profile: math)

## Read
Treat the focus as mathematics: give exact LaTeX of every expression in 'latex' (no $ delimiters). Check superscripts,
subscripts, fractions, Greek letters.

## Reply
Work it in $$...$$ (amsmath). Keep prose to one line. Short equations: one key equation per idea,
steps in one aligned block, each line under ~40 characters.

## Signals & DSP notation
- CT in round brackets, DT in square: x(t), h(t) vs x[n], h[n]; \delta(t) vs \delta[n], u(t) vs u[n].
- Transforms: CTFT X(j\omega), DTFT X(e^{j\omega}), Laplace X(s), z-transform X(z);
  H(e^{j\omega}) = Y(e^{j\omega})/X(e^{j\omega}).
- Convolution: y(t) = \int_{-\infty}^{\infty} x(\tau)\,h(t-\tau)\,d\tau and
  y[n] = \sum_{k} x[k]\,h[n-k]; write x * h only for convolution.
- Upright names: \mathrm{rect}(t), \mathrm{sinc}(t), \mathrm{tri}(t); pairs with \leftrightarrow:
  \mathrm{rect}(t) \leftrightarrow \mathrm{sinc}(f).
- State the ROC with s/z results (|z| > 0.9); \omega in rad/s (CT) or rad/sample (DT).

## Pitfalls
- Inline maths on its own line each shrank the reply to 0.42x (run 6): group steps in one
  aligned block (`\begin{aligned}...\end{aligned}`) instead of many tiny blocks.
- `\times` once inked as a dot-like mark: prefer `\cdot` or spacing for products when clear.
- Units go inside the block: `$$f_c = 1.59\,\text{kHz}$$`.
- A signal, spectrum or response wants a graph block too (kinds/graph.md), not only algebra.
