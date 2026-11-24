# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pillow", "playwright"]
# ///
"""Render the README hero from benchmark evidence.

Run: uv run docs/assets/render_hero.py
Set CHROME=/path/to/chrome to use a specific browser; otherwise Playwright Chromium is used.
Fonts (Inter, Noto Sans SC) load from Google Fonts, so rendering needs network access.

Outputs readme-hero-{en,zh}-{light,dark}.gif and a still readme-hero-{en,zh}.png.
The GIF plays one build-up (baseline, solving, memory generation, savings) and then
holds the final state; every frame keeps the headline readable.
"""

import io
import json
import os
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

ASSETS = Path(__file__).resolve().parent
EVIDENCE = ASSETS.parent / "benchmarks" / "token-savings"
WIDTH, HEIGHT = 1280, 640
FRAME_MS = 40
BUILD_MS = 3600
LOOP_MS = 9000


def metrics():
    data = json.loads((EVIDENCE / "results.json").read_text())
    cost = json.loads((EVIDENCE / "cost-estimate.json").read_text())
    rows = data["mcs_common24"]
    off = sum(row["arms"]["gateway_off"]["tokens"]["total_tokens"] for row in rows)
    solving = sum(row["arms"]["MCS"]["tokens"]["total_tokens"] for row in rows)
    memory = cost["memory_input"] + cost["memory_output"]
    cache_rate = cost["observed_memory_cached"] / cost["observed_memory_input"]
    rates = cost["points_per_million"]
    cached = cost["observed_memory_cached"] + cost["missing_cache_input"] * cache_rate
    memory_points = (
        (cost["memory_input"] - cached) * rates["uncached_input"]
        + cached * rates["cached_input"]
        + cost["memory_output"] * rates["output"]
    ) / 1e6
    peak = max(cost["paired_case_bounds"], key=lambda row: 1 - row["pc_solving_plus_memory"] / row["off_solving"])
    assert peak["powercontext_pass"] and peak["off_pass"]
    result = {
        "tasks": len(rows),
        "solved_off": sum(row["arms"]["gateway_off"]["resolved"] for row in rows),
        "solved_pc": sum(row["arms"]["MCS"]["resolved"] for row in rows),
        "off": off,
        "solving": solving,
        "memory": memory,
        "total_cut": (1 - (solving + memory) / off) * 100,
        "solving_cut": (1 - solving / off) * 100,
        "cost_cut": (1 - (cost["pc_solving_points"] + memory_points) / cost["off_solving_points"]) * 100,
        "peak_case": peak["case"],
        "peak_cut": (1 - peak["pc_solving_plus_memory"] / peak["off_solving"]) * 100,
    }
    # The README copy quotes these figures; fail loudly if the evidence moves.
    assert result["solved_off"] == result["solved_pc"] == 16 and result["tasks"] == 24
    solved = lambda arm: {row["case"] for row in rows if row["arms"][arm]["resolved"]}
    assert solved("gateway_off") == solved("MCS"), "the headline claims the same tasks were solved"
    assert [f"{result[k]:.2f}" for k in ("total_cut", "solving_cut", "cost_cut", "peak_cut")] == [
        "32.57",
        "40.30",
        "15.42",
        "63.49",
    ]
    return result


def copy(lang, m):
    millions = lambda value: f"{value / 1e6:.2f}M"
    saved = millions(m["off"] - m["solving"] - m["memory"])
    if lang == "zh":
        return {
            "eyebrow": f"SWE-bench Pro · 同 {m['tasks']} 题 · Codex CLI",
            "line1": "同样解出题目，",
            "line2": f"Token 少用 {m['total_cut']:.2f}%",
            "sub": f"两组解出的是 {m['tasks']} 题中相同的 {m['solved_pc']} 题。PowerContext 一侧已计入记忆生成的开销。",
            "off": "基线",
            "off_note": "网关对照组（Gateway OFF）",
            "pc": "PowerContext",
            "pc_note": "代码检索 + 工作指导 + 项目记忆",
            "solving": f"解题 {millions(m['solving'])}",
            "memory": f"记忆生成 {millions(m['memory'])}",
            "saved": f"节省 {saved}",
            "stats": [
                (f"{m['solving_cut']:.2f}%", "仅解题，Token 减少"),
                (f"{m['cost_cut']:.2f}%", "估算模型成本降低（含记忆生成）"),
                (f"{m['peak_cut']:.2f}%", f"单题最高 Token 降幅（第 {m['peak_case']} 题）"),
            ],
        }
    return {
        "eyebrow": f"SWE-bench Pro · {m['tasks']} shared tasks · Codex CLI",
        "line1": "Same tasks solved.",
        "line2": f"{m['total_cut']:.2f}% fewer tokens.",
        "sub": f"Both arms solved the same {m['solved_pc']} of {m['tasks']} tasks. "
        "PowerContext's total includes memory generation.",
        "off": "Baseline",
        "off_note": "Gateway OFF",
        "pc": "PowerContext",
        "pc_note": "CodeGraph + Steering + Memory",
        "solving": f"Solving {millions(m['solving'])}",
        "memory": f"Memory generation {millions(m['memory'])}",
        "saved": f"{saved} saved",
        "stats": [
            (f"{m['solving_cut']:.2f}%", "fewer tokens, solving only"),
            (f"{m['cost_cut']:.2f}%", "lower est. cost, memory included"),
            (f"{m['peak_cut']:.2f}%", f"best single task (#{m['peak_case']})"),
        ],
    }


THEMES = {
    "light": {
        "page": "#ffffff",
        "card": "#ffffff",
        "border": "#e2e8f2",
        "ink": "#0f1b33",
        "ink2": "#46546f",
        "muted": "#7a879e",
        "accent": "#0866ee",
        "track": "#eef2f8",
        "base": "#b9c4d6",
        "solving": "#0866ee",
        "memory": "#7fb0ff",
        "stripe": "#0866ee",
        "saved": "#0866ee",
        "logo": "powercontext-color.png",
    },
    "dark": {
        "page": "#0d1117",
        "card": "#111827",
        "border": "#273248",
        "ink": "#eef3fc",
        "ink2": "#b2bed2",
        "muted": "#8391a8",
        "accent": "#5b9dff",
        "track": "#1a2436",
        "base": "#4a566c",
        "solving": "#3d88ff",
        "memory": "#9cc4ff",
        "stripe": "#3d88ff",
        "saved": "#7fb0ff",
        "logo": "powercontext-reverse.png",
    },
}

TEMPLATE = r"""<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Noto+Sans+SC:wght@400;500;700;900&display=block">
<style>
:root { __VARS__ }
* { box-sizing: border-box; margin: 0; }
html, body { width: __W__px; height: __H__px; background: var(--page); overflow: hidden; }
body { font-family: Inter, "Noto Sans SC", sans-serif; color: var(--ink); font-variant-numeric: tabular-nums; }
.card { position: absolute; inset: 0; border-radius: 24px; background: var(--card); border: 2px solid var(--border); }
.top { position: absolute; left: 64px; right: 64px; top: 48px; display: flex; justify-content: space-between; align-items: center; }
.top img { height: 38px; }
.eyebrow { font-size: 17px; font-weight: 600; letter-spacing: .02em; color: var(--muted); }
.head { position: absolute; left: 64px; top: 124px; font-size: 60px; line-height: 70px; font-weight: 800; letter-spacing: -.025em; }
.head .two { color: var(--accent); }
.sub { position: absolute; left: 64px; top: 278px; font-size: 21px; color: var(--ink2); }
.row { position: absolute; left: 64px; right: 64px; height: 46px; }
.label { position: absolute; left: 0; top: 0; width: 250px; }
.label b { display: block; font-size: 20px; font-weight: 700; }
.label span { display: block; font-size: 15px; color: var(--muted); margin-top: 3px; }
.track { position: absolute; left: 262px; width: __TRACK__px; top: 4px; height: 38px; border-radius: 8px; background: var(--track); }
.seg { position: absolute; top: 0; height: 38px; width: 0; }
.base { background: var(--base); border-radius: 8px; }
.solving { background: var(--solving); border-radius: 8px 0 0 8px; }
.memory { background: repeating-linear-gradient(135deg, var(--memory) 0 6px, var(--stripe) 6px 8.5px);
          border-radius: 0 8px 8px 0; }
.saved { border: 2px dashed var(--saved); border-radius: 8px; display: flex; align-items: center; justify-content: center;
         font-size: 18px; font-weight: 700; color: var(--saved); background: transparent; white-space: nowrap; }
.value { position: absolute; right: 0; top: 6px; font-size: 28px; font-weight: 700; }
.legend { position: absolute; left: 326px; top: 470px; display: flex; gap: 28px; font-size: 16px; color: var(--ink2); }
.legend i { display: inline-block; width: 14px; height: 14px; border-radius: 3px; margin-right: 8px; vertical-align: -1px; }
.stats { position: absolute; left: 64px; right: 64px; top: 530px; display: flex; justify-content: space-between;
         border-top: 1px solid var(--border); padding-top: 22px; }
.stat + .stat { border-left: 1px solid var(--border); padding-left: 36px; }
.stat { display: flex; align-items: baseline; gap: 12px; }
.stat b { font-size: 30px; font-weight: 800; letter-spacing: -.02em; color: var(--accent); }
.stat span { font-size: 16px; color: var(--ink2); }
</style>
<div class="card"></div>
<div class="top"><img src="__LOGO__" alt=""><div class="eyebrow">__EYEBROW__</div></div>
<div class="head"><div>__LINE1__</div><div class="two">__LINE2__</div></div>
<div class="sub">__SUB__</div>
<div class="row" style="top:338px"><div class="label"><b>__OFF__</b><span>__OFF_NOTE__</span></div>
  <div class="track"><div class="seg base" id="base"></div></div><div class="value" id="offv"></div></div>
<div class="row" style="top:406px"><div class="label"><b>__PC__</b><span>__PC_NOTE__</span></div>
  <div class="track" style="background:transparent">
    <div class="seg saved" id="saved"><span id="savedt">__SAVED__</span></div>
    <div class="seg solving" id="solving"></div><div class="seg memory" id="memory"></div></div>
  <div class="value" id="pcv" style="color:var(--accent)"></div></div>
<div class="legend" id="legend"><div><i style="background:var(--solving)"></i>__SOLVING__</div>
  <div><i class="memory" style="border-radius:3px"></i>__MEMORY__</div></div>
<div class="stats">__STATS__</div>
<script>
const M = __METRICS__, TRACK = __TRACK__;
const $ = id => document.getElementById(id);
const ease = t => 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 3);
const span = (t, a, b) => ease((t - a) / (b - a));
const px = v => TRACK * v / M.off;
const fmt = v => (v / 1e6).toFixed(2) + 'M';
const stats = [...document.querySelectorAll('.stat')];
window.seek = t => {
  const base = M.off * span(t, 200, 1200);
  const solving = M.solving * span(t, 1100, 2200);
  const memory = M.memory * span(t, 2200, 2700);
  $('base').style.width = px(base) + 'px';
  $('offv').textContent = fmt(base);
  $('solving').style.width = px(solving) + 'px';
  $('solving').style.borderRadius = memory > 0 ? '8px 0 0 8px' : '8px';
  $('memory').style.left = px(solving) + 'px';
  $('memory').style.width = px(memory) + 'px';
  $('pcv').textContent = fmt(solving + memory);
  const reveal = span(t, 2700, 3300);
  const savedLeft = px(M.solving + M.memory) + 4;
  $('saved').style.left = savedLeft + 'px';
  $('saved').style.width = (TRACK - savedLeft) + 'px';
  $('saved').style.opacity = reveal;
  $('legend').style.opacity = span(t, 2400, 3000);
  stats.forEach((s, i) => {
    const k = span(t, 2900 + i * 150, 3500 + i * 150);
    s.style.opacity = k;
    s.style.transform = `translateY(${(1 - k) * 10}px)`;
  });
};
</script>
"""


def page(lang, theme, m):
    c, t = copy(lang, m), THEMES[theme]
    css_vars = " ".join(f"--{key}: {value};" for key, value in t.items() if key != "logo")
    stats = "".join(f'<div class="stat"><b>{v}</b><span>{label}</span></div>' for v, label in c["stats"])
    replacements = {
        "__VARS__": css_vars,
        "__W__": str(WIDTH),
        "__H__": str(HEIGHT),
        "__TRACK__": "760",
        "__LOGO__": (ASSETS / t["logo"]).as_uri(),
        "__STATS__": stats,
        "__METRICS__": json.dumps(m),
    }
    replacements |= {f"__{key.upper()}__": value for key, value in c.items() if isinstance(value, str)}
    html = TEMPLATE
    for key, value in replacements.items():
        html = html.replace(key, value)
    assert "__" not in html.replace("__proto__", ""), "unfilled placeholder"
    return html


def load(tab, html):
    path = Path(tempfile.gettempdir()) / "powercontext-hero.html"
    path.write_text(html)
    tab.goto(path.as_uri())
    tab.evaluate("document.fonts.ready")
    tab.wait_for_function("[...document.fonts].some(f => f.family === 'Inter' && f.status === 'loaded')")
    # Labels must not collide with the bars or run off the card.
    assert tab.evaluate("""() => [...document.querySelectorAll('.label, .value, .stat, .sub, .head, .top')]
        .every(e => { const r = e.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth - 40; })""")
    # Bottom stats stay on one line with clear separation between them.
    assert tab.evaluate("""() => { const s = [...document.querySelectorAll('.stat')];
        const box = (e, q) => e.querySelector(q).getBoundingClientRect();
        return s.every(e => box(e, 'span').height < 30)
            && s.slice(1).every((e, i) => box(e, 'b').left - box(s[i], 'span').right >= 64); }""")


def gif_palette(frame, theme):
    """Median-cut palette with the flat theme colors pinned exactly, so large fills keep their true color."""
    entries = np.array(frame.quantize(colors=255, method=Image.Quantize.MEDIANCUT).getpalette()[: 255 * 3])
    palette = entries.reshape(-1, 3)
    for value in dict.fromkeys(v for v in theme.values() if v.startswith("#")):
        exact = np.array([int(value[i : i + 2], 16) for i in (1, 3, 5)])
        palette[((palette - exact) ** 2).sum(axis=1).argmin()] = exact
    return palette


def to_indexed(frame, palette):
    """Exact nearest-color mapping; Pillow's palette lookup is too coarse for near-white fills."""
    pixels = np.asarray(frame).astype(np.int32)
    packed = (pixels[..., 0] << 16 | pixels[..., 1] << 8 | pixels[..., 2]).ravel()
    colors, inverse = np.unique(packed, return_inverse=True)
    rgb = np.stack([colors >> 16, colors >> 8 & 255, colors & 255], axis=1)
    nearest = ((rgb[:, None, :] - palette[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
    image = Image.fromarray(nearest[inverse].astype(np.uint8).reshape(frame.height, frame.width), "P")
    image.putpalette(palette.astype(np.uint8).ravel().tolist())
    return image


def main():
    m = metrics()
    frames_total = LOOP_MS // FRAME_MS
    with sync_playwright() as p:
        chrome = os.environ.get("CHROME")
        browser = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
        still = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=2)
        motion = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1)
        for lang in ["en", "zh"]:
            for theme in ["light", "dark"]:
                html = page(lang, theme, m)
                if theme == "light":
                    load(still, html)
                    still.evaluate(f"seek({BUILD_MS})")
                    (ASSETS / f"readme-hero-{lang}.png").write_bytes(still.screenshot())
                load(motion, html)
                frames = []
                for tick in range(frames_total):
                    motion.evaluate(f"seek({min(tick * FRAME_MS, BUILD_MS)})")
                    frames.append(Image.open(io.BytesIO(motion.screenshot())).convert("RGB"))
                # Quantize every frame against the final state so the static canvas never flickers.
                palette = gif_palette(frames[-1], THEMES[theme])
                encoded = [to_indexed(frame, palette) for frame in frames]
                output = ASSETS / f"readme-hero-{lang}-{theme}.gif"
                encoded[0].save(
                    output,
                    save_all=True,
                    append_images=encoded[1:],
                    duration=FRAME_MS,
                    loop=0,
                    optimize=True,
                    disposal=1,
                )
                with Image.open(output) as gif:
                    assert gif.size == (WIDTH, HEIGHT)
                print(f"{output.name}: {output.stat().st_size:,} bytes", flush=True)
        browser.close()


if __name__ == "__main__":
    main()
