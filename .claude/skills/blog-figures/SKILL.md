---
name: blog-figures
description: Use when adding or changing a diagram in a PowerContext blog post (docs/en/blog/posts, docs/zh/blog/posts) — a flowchart, architecture, pipeline, state machine, store or comparison. Diagrams are static HTML figures built with the site's figure kit in Sunbeam tokens and written in both languages. Covers when a figure is HTML or an image, the format rules the build enforces, the kit's parts and what each color means, layout on the canvas grid, captions and labels, the layout helper, translation, preview and the checks a figure must pass.
---

# Blog figures

Diagrams in blog posts are HTML, not pictures. Their words are real text: they translate, read in dark mode, stay sharp at any size, and can be searched and copied. They follow the reference style of long-form technical articles: ink outlines for the actors, one accent for the path the text is about, warm grey panels for stores, dashed lines for what is optional or asynchronous, and nothing decorative.

Read these before the first figure:

- `website/src/components/figures.module.css` — the kit: every class and what it draws.
- `docs/en/blog/publishing.md` — files, front matter, covers, captions and the checks every post passes.
- One converted post end to end, such as `docs/en/blog/posts/magpie-gateway-powercontext-memory.md` (pipelines, a store, rows) or `power-system-agent-memory.md` (layers, a state machine, a long flowchart).

## 1. HTML or image

| Content | Use |
| --- | --- |
| Boxes, arrows and words: flows, architecture, lifecycles, comparisons, tables of states | An HTML figure (this skill) |
| A product screenshot, a photograph, a cover | An image file under `docs/assets/blog/<slug>/` with localized alt text |
| A chart of measured numbers | A Markdown table first; a figure only when the shape of the data is the point |

Never redraw a screenshot as HTML, and never ship a diagram as PNG or SVG. A figure must say one thing; if it needs two titles, it is two figures.

## 2. Format

Posts are Markdown with static raw HTML, the same embed standard as the Arion blog. The build parses it with `rehype-raw` and rejects anything that is not static (`website/src/lib/blog-html.ts`):

- `<script>`, `<style>`, `<iframe>`, `<object>`, `<embed>`, `<link>` and `on*` attributes fail the build. Style comes only from the kit's classes and the position variables below; never write colors, fonts or `style` properties other than `--x --y --w --h --fw --fh --cols`.
- A figure is one HTML block: **no blank lines inside it**. A blank line ends the block and Markdown starts parsing your HTML as text.
- Markdown is not parsed inside the block. Write `<code>`, `<a href>` and `<strong>` yourself.

```html
<figure class="fig" id="fig-event-inbox">
<div class="fig-canvas" style="--fw:960;--fh:300" role="img" aria-label="An event is written to the inbox, then a worker runs it">
<svg viewBox="0 0 960 300">
<path class="fig-wire accent" d="M210 37 L294 37" marker-end="url(#fig-arrow-accent)"/>
</svg>
<div class="fig-box" style="--x:0;--y:0;--w:210;--h:74"><b>Handler</b><span>HTTP endpoint</span></div>
<div class="fig-box accent" style="--x:300;--y:0;--w:210;--h:74"><b>Event inbox</b><span>Database table</span></div>
</div>
<figcaption>Figure 2: the handler only writes the event; the worker decides when to run it.</figcaption>
</figure>
```

| Part | Rule |
| --- | --- |
| `id` | `fig-<name>`, unique in the post and identical in both languages. |
| `.fig-canvas` | `--fw`/`--fh` are the canvas size in units. Use `--fw:960`; choose `--fh` to fit, with no empty band at the bottom. |
| `role="img"` + `aria-label` | The figure's alt text: what it shows, in the post's language, one sentence. |
| `<svg viewBox="0 0 --fw --fh">` | The wires, in the same units. It comes first; panels sit under it, everything else over it. |
| `<figcaption>` | The post's numbered caption (`Figure 3: …` / `图 3：…`, as the post numbers figures). It says what the reader should notice, not what the boxes already say. |

Never put a title inside the figure; the caption and the surrounding paragraph carry it.

## 3. The kit

Every part is placed by its top-left corner with `--x --y` and sized with `--w --h`, all in canvas units. Text inside scales with the canvas.

| Class | Draws | Content | Modifiers |
| --- | --- | --- | --- |
| `fig-box` | An actor or step: ink outline, centered text | `<b>` name, then `<span>` lines | `accent`, `quiet`, `dashed`, `fill`, `solid`, `tint-1`…`tint-5`, `round`, `start` (left-aligned) |
| `fig-panel` | A region holding other parts: a store, a service, a process boundary | `<header><b>Name</b><span>kind</span></header>` | `accent` (accent name), `outline`, `dashed`, `frame`, `center` |
| `fig-card` | A record inside a panel: card face, hairline | `<b>`, `<span>` lines | `accent`, `quiet` (dashed, dropped), `center` |
| `fig-rows` | Table-like rows; columns from `--cols` (in `em`) | `<div>` per row; `<span>` cells, `<em>` status (right-aligned), `<strong>` accent cell | `accent` (outlines the group), `quiet`, `head` |
| `fig-label` | Words on a wire or beside a part | text, `<code>` | `accent`, `strong`, `muted`, `small`, `center`/`end` (anchor), `wrap` (with `--w`) |
| `fig-chip` | A short tag, such as a loop's name | text | `accent` |
| `fig-dot` | State-machine start; end with `end` | — | `end` |
| `path.fig-wire` | A connector inside the `<svg>` | `d` in canvas units | `accent`, `muted`, `dashed`, `thin` |

`<span class="accent">` inside a box or card is that part's result line (larger, accent). Arrowheads: `marker-end="url(#fig-arrow)"`, `#fig-arrow-accent` or `#fig-arrow-muted` matching the wire's tone; add `marker-start` with the same value for a two-way wire. The article page defines them once.

### What color means

Color carries meaning, never decoration. A reader should be able to say why each part is the color it is.

| Look | Means | Limit |
| --- | --- | --- |
| Ink outline (`fig-box`) | An actor or step | — |
| Accent (`accent`) | The path or part the paragraph is about | One idea per figure; usually one path |
| Solid ink (`solid`) | The one result or end state | At most one per figure |
| Warm grey (`fill`, `fig-panel`) | A store, a passive part, a note | — |
| Dashed | Optional, asynchronous, not blocking, or dropped | Say which in a label or the caption |
| Muted / `quiet` | Context the reader can skip, or what does not happen | — |
| `tint-1`…`tint-5` | Categories that must be told apart (layers, loop stages) | Only when there are categories; never with accent on the same parts |

Accent is PowerContext blue (`--primary`). Do not add other colors, gradients, shadows, glows or icons. Code identifiers go in `<code>` (monospace, no chip).

## 4. Layout

- **Grid.** Canvas 960 wide. Snap edges to multiples of 2 and keep a rhythm: 16 units inside panels, 24–40 between parts in a row, 60–90 between rows that wires cross. Repeated parts share width and spacing (`x = i * step`).
- **Sizes.** Box heights: 46–56 for a name, 64–76 with one line under it, 90–130 with two or three lines. Panel headers take the top 48–52 units.
- **Text budget.** At `--fw:960` a bold name runs about 8.5 units per Latin character and 17 per Chinese character; a line under it about 7.5 and 14. Width for text = box width − 32. Check the Chinese edition: it is usually the one that wraps.
- **Wires.** Orthogonal segments only; corners are rounded by the helper (radius 10). End a wire 6 units before the part it points to so the arrowhead does not touch the outline. Route through gutters between parts; a wire never crosses text. Labels sit beside the segment they name, not on it.
- **Reading order.** Left to right, then top to bottom. Loops return along the outside. A long flow snakes across rows (row 2 runs right to left) rather than growing tall.
- **Phones.** The canvas never shrinks below 38rem; narrower screens scroll it sideways. Do not design a separate phone layout.

## 5. Writing the figure

Use the helper. It turns one layout into both languages, computes wire paths with rounded corners, and writes the figure into both posts:

```python
# python3 path/to/your_figures.py   (Python 3.9+, standard library only)
import sys
sys.path.insert(0, '.claude/skills/blog-figures/scripts')
from figure import Fig, place

f = Fig(960, 300, alt=('An event is written to the inbox, then a worker runs it',
                       '事件先写入收件箱，再由 Worker 执行'))
f.box('handler', 0, 0, 210, 74, 'Handler', ('HTTP endpoint', 'HTTP 端点'))
f.panel('inbox', 300, 0, 660, 200, ('Event inbox', '事件收件箱'), ('Database table', '数据库表'), 'accent')
f.rows('events', 316, 56, 628, 128, [
    ['09:00', ('A record changed', '一条记录被修改'), ('~done', '~完成')],
    ['14:02', ('A background job finished', '后台任务完成'), ('~pending', '~待处理')],
], '4em 1fr 6em', 'accent')
f.arrow('handler', 'r', 'inbox', 'l', 'accent', fb=0.2)
f.label(255, 8, ('1. Write the event', '1. 写入事件'), 'accent small center')
place('my-post-slug', 'event-inbox', f)
```

- Text is a string (same in both languages) or an `(en, zh)` tuple. A leading `!` makes it inline HTML (`'!<code>prepare_context</code>'`); in box lines `^` marks the result line; in rows `~` is a status cell and `^` an accent cell.
- `place(post, name, fig)` replaces the image line that references `assets/blog/**/<name>.png|svg` in both editions, takes its alt text as the label, and folds the caption paragraph after it into `<figcaption>`. For a new figure, write `<figure class="fig" id="fig-<name>">` and `</figure>` on two lines where it belongs and pass `alt=`. Re-running replaces the figure in place, so iterate on the layout and run again.
- Keep the layout script with your branch notes, not in the repository; the HTML in the posts is the source of truth. Small edits (a word, a position) can be made in the HTML directly, in both editions.

Delete the image files a figure replaces, and any `.en.svg`/`.zh.svg` pair, in the same change.

## 6. Two languages

- Same `id`, same geometry, same parts and colors in both editions; only words change. Never let one edition drop a part to make text fit; resize the part for both.
- Translate meaning, not word order. Keep API names, commands, file names, product names (PowerContext, Source, Memory, Scope, Handoff, Skill) and status values that are identifiers (`queued`, `CREATED`) unchanged in both.
- The English edition must contain no Chinese characters, including in `aria-label`; `tests/e2e/test_blog_site.py` checks it.

## 7. Preview

```bash
pnpm --dir website dev
```

The dev server reads generated copies of the posts. After editing a post, regenerate them, then reload:

```bash
pnpm --dir website exec tsx scripts/generate-blog.ts
```

Open the article (`/en/blog/<slug>/#fig-<name>`, then `/zh/…`) and check, in a real browser:

1. Light and dark theme: every part is readable, wires show above panels.
2. Both languages: no text overflows its part or wraps into three lines where two were planned.
3. A phone-width window: the figure scrolls sideways and the page does not.
4. Keyboard and screen reader: the figure is one image with its label; the caption follows it.

## 8. Checks

```bash
uv run pytest tests/e2e/test_blog_site.py -q
make docs-test
```

The build fails on forbidden elements or event handlers; the tests fail on a figure missing from one edition, an empty label, duplicate ids, or Chinese in the English edition.

## 9. Review checklist

1. One idea: the caption can say what the reader should notice in one sentence.
2. One accent path, at most one solid result; every dashed line and color explained by a label or the caption.
3. No title, legend box or decoration inside the figure; no repeated word that a label, box or caption already says.
4. Orthogonal wires that never cross text; arrowheads clear of outlines; parts aligned to a shared grid.
5. Both editions identical in structure; identifiers untranslated; English free of Chinese.
6. Checked in light, dark, both languages and at phone width; replaced image files deleted.
