# kind: sketch (profile: diagram)

## Read
Treat the focus as a block diagram: describe shapes and how they connect. List blocks and
arrows in 'text' as 'A -> B -> C'.

## Reply
ANSWER what was asked about the diagram in prose first. Draw ONE
`<svg viewBox="0 0 400 300">` block only if asked to draw.

## SVG rules
- Elements: line, rect, circle, ellipse, polyline, polygon, path (M L H V C Q Z), text.
- Label EVERY block: `<text x=".." y=".." font-size="11" text-anchor="middle">5V reg</text>`,
  centred, 1-3 words, plain text.
- Black strokes, no fill: it is redrawn as pen strokes.
- Model once drew `{}` as labels (run 4): labels are literal words, never placeholders.
