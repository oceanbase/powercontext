#
# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Lay out a blog figure once and write it into both language editions.

The output is the static HTML of the figure kit (website/src/components/
figures.module.css). Nothing here runs at build time: the HTML it writes into
docs/{en,zh}/blog/posts/*.md is the source, reviewed like any other text.

Text is either a plain string (same in both languages) or an (en, zh) tuple.
A string starting with "!" is inline HTML, for <code>. In box and card lines,
"^" makes a line the accent result. In row cells, "~" marks a status and "^"
an accent cell.

    from figure import Fig, place

    f = Fig(960, 300)
    f.box('a', 0, 0, 200, 70, 'Handler', ('HTTP endpoint', 'HTTP 端点'))
    f.box('b', 300, 0, 200, 70, 'Worker', None, 'accent')
    f.arrow('a', 'r', 'b', 'l', 'accent')
    place('my-post-slug', 'diagram-name', f)

Run the script with python3 (3.9 or later, standard library only).
"""

from __future__ import annotations

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
POSTS = ROOT / "docs"
CAPTION = re.compile(r"^\*?(Figure \d+|图 ?\d+)")


def _text(value, lang):
    if isinstance(value, tuple):
        return value[0] if lang == "en" else value[1]
    return value


def _html(value, lang):
    value = _text(value, lang)
    if value is None:
        return ""
    if value.startswith("!"):
        return value[1:]
    return html.escape(value, quote=False)


def _num(value):
    value = float(value)
    return str(int(value)) if value.is_integer() else f"{value:.1f}".rstrip("0").rstrip(".")


class Fig:
    """A canvas of `w` × `h` units. Every position and size is in those units."""

    def __init__(self, w, h, alt=None, caption=None):
        self.w, self.h = w, h
        self.alt, self.caption = alt, caption  # usually taken from the post
        self.id = None
        self.items, self.wires, self.nodes = [], [], {}

    # Parts ---------------------------------------------------------------

    def box(self, key, x, y, w, h, title, lines=None, cls=""):
        """An actor or step. cls: accent, quiet, dashed, fill, solid, tint-1..5, round, start."""
        return self._add("box", key, x, y, w, h, title, lines, cls)

    def card(self, key, x, y, w, h, title, lines=None, cls=""):
        """A record inside a panel. cls: accent, quiet, center."""
        return self._add("card", key, x, y, w, h, title, lines, cls)

    def panel(self, key, x, y, w, h, title=None, subtitle=None, cls=""):
        """A region that holds other parts. cls: accent, outline, dashed, frame, center."""
        return self._add("panel", key, x, y, w, h, title, subtitle, cls)

    def rows(self, key, x, y, w, h, rows, cols="1fr", cls=""):
        """Table-like rows; cols is a CSS grid track list in em. cls: accent, quiet, head."""
        return self._add("rows", key, x, y, w, h, rows, cols, cls)

    def label(self, x, y, text, cls="", w=None):
        """Words placed by their top-left corner. cls: accent, strong, muted, small, center, end, wrap."""
        self.items.append(("label", x, y, w, None, text, None, cls))
        return self

    def chip(self, key, x, y, w, h, text, cls=""):
        return self._add("chip", key, x, y, w, h, text, None, cls)

    def dot(self, key, x, y, d, cls=""):
        """A state-machine start point, or an end point with cls='end'."""
        return self._add("dot", key, x, y, d, d, None, None, cls)

    def _add(self, kind, key, x, y, w, h, a, b, cls):
        self.nodes[key] = (x, y, w, h)
        self.items.append((kind, x, y, w, h, a, b, cls))
        return self

    # Wires ---------------------------------------------------------------

    def point(self, key, side, f=0.5, gap=0):
        """A point on a part's side (t, b, l, r), `f` of the way along it."""
        x, y, w, h = self.nodes[key]
        return {
            "t": (x + w * f, y - gap),
            "b": (x + w * f, y + h + gap),
            "l": (x - gap, y + h * f),
            "r": (x + w + gap, y + h * f),
        }[side]

    def wire(self, points, cls="", head=True, r=10):
        """A polyline with rounded corners. cls: accent, muted, dashed, thin. head: True, False or 'both'."""
        self.wires.append((points, cls, head, r))
        return self

    def arrow(self, a, side_a, b, side_b, cls="", fa=0.5, fb=0.5, gap=6, via=None):
        """A wire from one part's side to another's, leaving `gap` units for the head."""
        return self.wire([self.point(a, side_a, fa), *(via or []), self.point(b, side_b, fb, gap)], cls)

    @staticmethod
    def _path(points, r):
        pts = []
        for point in points:
            point = tuple(map(float, point))
            if not pts or point != pts[-1]:
                pts.append(point)
        pts = [
            p
            for i, p in enumerate(pts)
            if i in (0, len(pts) - 1)
            or (pts[i - 1][0] - p[0]) * (pts[i + 1][1] - p[1]) != (pts[i - 1][1] - p[1]) * (pts[i + 1][0] - p[0])
        ]
        d = f"M{_num(pts[0][0])} {_num(pts[0][1])}"
        for i in range(1, len(pts) - 1):
            (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
            l1 = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
            l2 = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            rr = min(r, l1 / 2, l2 / 2)
            ax, ay = x1 - (x1 - x0) / l1 * rr, y1 - (y1 - y0) / l1 * rr
            bx, by = x1 + (x2 - x1) / l2 * rr, y1 + (y2 - y1) / l2 * rr
            d += f" L{_num(ax)} {_num(ay)} Q{_num(x1)} {_num(y1)} {_num(bx)} {_num(by)}"
        return d + f" L{_num(pts[-1][0])} {_num(pts[-1][1])}"

    # Output --------------------------------------------------------------

    def _style(self, x, y, w, h):
        parts = [f"--x:{_num(x)}", f"--y:{_num(y)}"]
        parts += [f"--w:{_num(w)}"] if w is not None else []
        parts += [f"--h:{_num(h)}"] if h is not None else []
        return ";".join(parts)

    def _lines(self, lines, lang):
        out = ""
        for line in lines if isinstance(lines, list) else [lines] if lines is not None else []:
            value = _text(line, lang)
            if value.startswith("^"):
                out += f'<span class="accent">{_html(value[1:], lang)}</span>'
            else:
                out += f"<span>{_html(value, lang)}</span>"
        return out

    def render(self, lang):
        """The figure's HTML for one language; no blank lines, so Markdown keeps it as one block."""
        opening = f'<figure class="fig" id="{self.id}">' if self.id else '<figure class="fig">'
        out = [
            opening,
            f'<div class="fig-canvas" style="--fw:{self.w};--fh:{self.h}" role="img" '
            f'aria-label="{html.escape(_text(self.alt, lang) or "", quote=True)}">',
        ]
        if self.wires:
            out.append(f'<svg viewBox="0 0 {self.w} {self.h}">')
            for points, cls, head, r in self.wires:
                tones = cls.split()
                marker = ""
                if head:
                    tone = "accent" if "accent" in tones else "muted" if "muted" in tones else ""
                    ref = f"url(#fig-arrow{'-' + tone if tone else ''})"
                    marker = f' marker-end="{ref}"' + (f' marker-start="{ref}"' if head == "both" else "")
                out.append(f'<path class="{" ".join(["fig-wire", *tones])}" d="{self._path(points, r)}"{marker}/>')
            out.append("</svg>")
        for kind, x, y, w, h, a, b, cls in self.items:
            classes = " ".join([f"fig-{kind}", *cls.split()])
            style = self._style(x, y, w, h)
            if kind in ("box", "card"):
                inner = (f"<b>{_html(a, lang)}</b>" if a is not None else "") + self._lines(b, lang)
                out.append(f'<div class="{classes}" style="{style}">{inner}</div>')
            elif kind == "panel":
                inner = ""
                if a is not None:
                    inner = (
                        f"<header><b>{_html(a, lang)}</b>"
                        + (f"<span>{_html(b, lang)}</span>" if b else "")
                        + "</header>"
                    )
                out.append(f'<div class="{classes}" style="{style}">{inner}</div>')
            elif kind == "rows":

                def cell(value):
                    value = _text(value, lang)
                    if value.startswith("~"):
                        return f"<em>{_html(value[1:], lang)}</em>"
                    if value.startswith("^"):
                        return f"<strong>{_html(value[1:], lang)}</strong>"
                    return f"<span>{_html(value, lang)}</span>"

                body = "".join("<div>" + "".join(cell(c) for c in row) + "</div>" for row in a)
                out.append(f'<div class="{classes}" style="{style};--cols:{b}">{body}</div>')
            elif kind in ("label", "chip"):
                out.append(f'<span class="{classes}" style="{style}">{_html(a, lang)}</span>')
            elif kind == "dot":
                out.append(f'<span class="{classes}" style="{style}"></span>')
        out.append("</div>")
        if self.caption:
            out.append(f"<figcaption>{_html(self.caption, lang)}</figcaption>")
        out.append("</figure>")
        return "\n".join(out)


def _markdown_inline(text):
    """A caption paragraph's Markdown (links, code, bold, italics) as HTML."""
    text = text.strip()
    if text.startswith("*") and text.endswith("*") and not text.startswith("**"):
        text = text[1:-1]
    out = []
    for part in re.split(r"(`[^`]+`)", text):
        if part.startswith("`") and part.endswith("`"):
            out.append(f"<code>{html.escape(part[1:-1], quote=False)}</code>")
            continue
        part = html.escape(part, quote=False)
        part = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', part)
        out.append(re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", part))
    return "".join(out)


def place(post, name, fig):
    """Write `fig` into docs/{en,zh}/blog/posts/<post>.md as <figure id="fig-<name>">.

    First run: replaces the image line that references assets/blog/**/<name>.(png|svg),
    uses its alt text as the accessible label, and folds the caption paragraph that
    follows ("Figure 3: ...", "*图 1｜...*") into <figcaption>. Later runs replace
    the figure by its id and keep the label and caption already there. Without an
    image line or figure, it appends nothing and stops with an error: put a line
    `<figure class="fig" id="fig-<name>">` + `</figure>` where the figure belongs.
    """
    fig.id = f"fig-{name}"
    for lang in ("en", "zh"):
        path = POSTS / lang / "blog/posts" / f"{post}.md"
        lines = path.read_text(encoding="utf-8").split("\n")
        alt, caption = fig.alt, fig.caption
        starts = [i for i, line in enumerate(lines) if line == f'<figure class="fig" id="{fig.id}">']
        if starts:
            i = starts[0]
            end = next(j for j in range(i, len(lines)) if lines[j] == "</figure>")
            block = "\n".join(lines[i : end + 1])
            if fig.alt is None:
                found = re.search(r'aria-label="([^"]*)"', block)
                fig.alt = html.unescape(found.group(1)) if found else None
            if fig.caption is None:
                found = re.search(r"<figcaption>(.*)</figcaption>", block)
                fig.caption = "!" + found.group(1) if found else None
            lines[i : end + 1] = fig.render(lang).split("\n")
        else:
            pattern = re.compile(r"assets/blog/(?:[^/\s)]+/)*" + re.escape(name) + r"(\.(en|zh))?\.(png|svg)")
            hits = [
                k for k, line in enumerate(lines) if pattern.search(line) and line.lstrip().startswith(("[!", "!["))
            ]
            if len(hits) != 1:
                raise SystemExit(f"{lang}/{post}: expected one image line for {name}, found {len(hits)}")
            i = hits[0]
            if fig.alt is None:
                found = re.search(r"!\[([^\]]*)\]", lines[i])
                fig.alt = found.group(1) if found else None
            k = i + 1
            while k < len(lines) and not lines[k].strip():
                k += 1
            if fig.caption is None and k < len(lines) and CAPTION.match(lines[k]):
                fig.caption = "!" + _markdown_inline(lines[k])
                del lines[i + 1 : k + 1]
            lines[i] = fig.render(lang)
        fig.alt, fig.caption = alt, caption
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"{lang}: {path.relative_to(ROOT)} #{fig.id}")
