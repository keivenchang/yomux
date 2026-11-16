# TeX math in YOmux

Open this file in ViewEdit or Split. Edit the surrounding prose in ViewEditor; change equations in TextEditor.

Inline math: $E = mc^2$, $a*b*c$, and $\{1,2,3\}$.

Backslash delimiters: \(a_1 + b_1 = c_1\).

## Display equation

$$
\int_0^1 x^2\,dx = \frac{1}{3}
$$

## Matrix

$$
\begin{pmatrix}
1 & 2 \\
3 & 4
\end{pmatrix}
$$

\[\sum_{n=1}^{N} n = \frac{N(N+1)}{2}\]

## Literal text

Prices are $5 and $10. Escaped dollars stay literal: \$x\$.

Inline code stays literal: `$x^2$` and `\(x\)`.

```tex
\documentclass{article}
\begin{document}
This fenced source is not typeset: $x^2$.
\end{document}
```

The companion [paper.tex](paper.tex) opens as editable LaTeX source. The [HTML example](math.html) renders math with scripts disabled. Open paper.tex and choose Preview or Split to compile the LaTeX document to PDF. If the server has no TeX compiler, Preview offers to install one with your approval.
