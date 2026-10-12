Series RLC at 1000 rad/s: the inductor wins, so the circuit is inductive.
$$\begin{circuitikz}
\draw (0,0) to[sV, l=$V_s$] (0,3) to[R, l=$R=10\,\Omega$, i>^=$I$] (3,3)
  to[L, l=$L=20\,\text{mH}$] (6,3) to[C, l=$C=100\,\mu\text{F}$] (6,0) -- (0,0);
\end{circuitikz}$$
Reactances and impedance:
$$\begin{aligned} X_L &= \omega L = 20\,\Omega, \quad X_C = \frac{1}{\omega C} = 10\,\Omega\\ Z &= 10 + j10 = 14.14\angle 45^\circ\,\Omega\\ I &= \frac{V_s}{Z} = 0.7071\angle -45^\circ\ \text{A} \end{aligned}$$
$$\begin{aligned} V_R &= 7.071\angle -45^\circ\ \text{V}, \quad V_L = 14.14\angle 45^\circ\ \text{V}, \quad V_C = 7.071\angle -135^\circ\ \text{V}\\ P &= 5\ \text{W}, \quad Q = 5\ \text{var}, \quad S = 7.071\ \text{VA} \end{aligned}$$
$$\begin{tikzpicture}
\begin{axis}[width=7cm, height=5cm, axis lines=middle, xlabel={$t$ (ms)}, domain=0:12.566, samples=120, legend pos=south west]
\addplot[blue, thick] {14.14*sin(deg(x))};
\addlegendentry{$v_s$}
\addplot[blue, thick] {10*sin(deg(x) - 45)};
\addlegendentry{$10\,i$}
\end{axis}
\end{tikzpicture}$$
$$\begin{tikzpicture}
\begin{axis}[width=7cm, height=5cm, xlabel={$\omega$ (rad/s)}, ylabel={$|I|$ (A)}, domain=100:2000, samples=150, ymin=0, ymax=1.1]
\addplot[black] {10/sqrt(100 + (0.02*x - 1/(0.0001*x))^2)};
\addplot[only marks, mark=*] coordinates {(707.1,1)};
\draw[dashed] (axis cs:707.1,0) -- (axis cs:707.1,1) node[above] {$\omega_0$};
\end{axis}
\end{tikzpicture}$$
Final answers:
$$\boxed{Z = 14.14\angle 45^\circ\,\Omega,\ I = 0.7071\angle -45^\circ\ \text{A},\ \text{pf} = 0.707\ \text{lagging},\ \omega_0 = 707.1\ \text{rad/s}}$$
