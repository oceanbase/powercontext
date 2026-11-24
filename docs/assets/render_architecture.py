# /// script
# requires-python = ">=3.11"
# ///
"""Write the README architecture diagram as light and dark SVGs for each language.

Run: python3 docs/assets/render_architecture.py
Outputs readme-architecture-{en,zh,ja}-{light,dark}.svg. The layout follows
docs/{en,zh}/docs/workflows/architecture.md; keep the two in step.
"""

from pathlib import Path
from xml.sax.saxutils import escape

ASSETS = Path(__file__).resolve().parent
WIDTH, HEIGHT = 1280, 600
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans','Microsoft YaHei',Arial,sans-serif"

THEMES = {
    "light": {
        "bg": "#ffffff",
        "panel": "#f7f9fd",
        "box": "#ffffff",
        "line": "#dde4ef",
        "ink": "#0f1b33",
        "ink2": "#4a5875",
        "muted": "#7a879e",
        "accent": "#0866ee",
        "soft": "#e8f1ff",
        "server": "#f2f7ff",
    },
    "dark": {
        "bg": "#0d1117",
        "panel": "#121a28",
        "box": "#0f1726",
        "line": "#283449",
        "ink": "#eef3fc",
        "ink2": "#b2bed2",
        "muted": "#8391a8",
        "accent": "#5b9dff",
        "soft": "#16284a",
        "server": "#111d33",
    },
}

COPY = {
    "zh": {
        "title": "PowerContext 架构",
        "hosts": "Agent 与应用",
        "agents": ("编程 Agent", "Codex · Claude Code", "OpenCode …"),
        "apps": ("你的应用", "Python · HTTP · MCP", "LangGraph · LangChain"),
        "connect": "接入方式",
        "hooks": ("Hooks · MCP · SDK", "记录输入", "召回上下文"),
        "gateway": ("网关（可选）", "注入上下文", "工作指导 · 记录对话"),
        "model": ("Agent 的模型", "经网关调用"),
        "server": "PowerContext Server",
        "pills": ("Scope 隔离", "访问控制", "版本记录"),
        "source": ("Source", "来源证据", "对话 · 文档 · 工单"),
        "process": ("后台处理", "记忆提取", "Dream · 审核"),
        "artifact": "Artifact",
        "families": ("记忆", "主题记忆", "画像", "经验", "技能", "交接"),
        "prepared": ("召回与组装", "为下一轮", "准备上下文"),
        "plugins": (("Source 插件", "接入业务数据和文件"), ("Artifact 插件", "定义自己的制品类型")),
        "infra": "基础设施",
        "storage": ("存储", "SQLite · OceanBase · seekDB"),
        "models": ("模型服务", "生成 · 向量"),
        "capture": "记录",
        "context": "上下文",
    },
    "en": {
        "title": "PowerContext architecture",
        "hosts": "Agents & apps",
        "agents": ("Coding agents", "Codex · Claude Code", "OpenCode …"),
        "apps": ("Your application", "Python · HTTP · MCP", "LangGraph · LangChain"),
        "connect": "Integrations",
        "hooks": ("Hooks · MCP · SDK", "Capture input", "Recall context"),
        "gateway": ("Gateway", "Optional", "Inject context", "Guide work · capture"),
        "model": ("Agent's model", "Called via the gateway"),
        "server": "PowerContext Server",
        "pills": ("Scopes", "Access control", "Revisions"),
        "source": ("Sources", "Captured", "evidence"),
        "process": ("Processing", "Memory extraction", "Dream · review"),
        "artifact": "Artifacts",
        "families": ("Memory", "Topics", "Profiles", "Experience", "Skills", "Handoffs"),
        "prepared": ("Recall", "Context for", "the next turn"),
        "plugins": (
            ("Source plugins", "Bring in business data and files"),
            ("Artifact plugins", "Define your own artifact types"),
        ),
        "infra": "Infrastructure",
        "storage": ("Storage", "SQLite · OceanBase · seekDB"),
        "models": ("Model services", "Generation · Embedding"),
        "capture": "capture",
        "context": "context",
    },
    "ja": {
        "title": "PowerContext のアーキテクチャ",
        "hosts": "Agent とアプリ",
        "agents": ("開発用 Agent", "Codex · Claude Code", "OpenCode …"),
        "apps": ("独自のアプリ", "Python · HTTP · MCP", "LangGraph · LangChain"),
        "connect": "接続方法",
        "hooks": ("Hooks · MCP · SDK", "入力を記録", "コンテキストを想起"),
        "gateway": ("ゲートウェイ", "任意", "コンテキストを注入", "作業ガイド · 記録"),
        "model": ("Agent のモデル", "ゲートウェイ経由"),
        "server": "PowerContext Server",
        "pills": ("Scope による分離", "アクセス制御", "リビジョン"),
        "source": ("Source", "根拠となる記録", "会話・文書など"),
        "process": ("記憶の処理", "記憶の抽出", "Dream · レビュー"),
        "artifact": "Artifact",
        "families": ("記憶", "トピック", "プロファイル", "経験", "スキル", "引き継ぎ"),
        "prepared": ("想起", "次のターンの", "コンテキスト"),
        "plugins": (
            ("Source プラグイン", "業務データとファイルを取り込む"),
            ("Artifact プラグイン", "独自の Artifact 型を定義"),
        ),
        "infra": "基盤",
        "storage": ("ストレージ", "SQLite · OceanBase · seekDB"),
        "models": ("モデルサービス", "生成 · 埋め込み"),
        "capture": "記録",
        "context": "コンテキスト",
    },
}


def text(x, y, s, t, size=14, weight=400, color="ink", anchor="start"):
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{t[color]}" '
        f'text-anchor="{anchor}">{escape(s)}</text>'
    )


def rect(x, y, w, h, t, fill="box", stroke="line", r=10, width=1.2):
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{t[fill]}" stroke="{t[stroke]}" '
        f'stroke-width="{width}"/>'
    )


def box(x, y, w, h, title, *notes, t, accent=False):
    parts = [
        rect(x, y, w, h, t, fill="soft" if accent else "box", stroke="accent" if accent else "line"),
        text(x + 16, y + 28, title, t, 15.5, 700, "accent" if accent else "ink"),
    ]
    parts += [text(x + 16, y + 52 + 21 * i, note, t, 13.5, 400, "ink2") for i, note in enumerate(notes)]
    return "".join(parts)


def panel(x, y, w, h, title, t, fill="panel", stroke="line"):
    return rect(x, y, w, h, t, fill=fill, stroke=stroke, r=18) + text(x + 20, y + 34, title, t, 14, 700, "muted")


def path(d, t, color="muted", both=False):
    start = f' marker-start="url(#head-{color})"' if both else ""
    return (
        f'<path d="{d}" stroke="{t[color]}" stroke-width="2" fill="none" stroke-linejoin="round" '
        f'marker-end="url(#head-{color})"{start}/>'
    )


def pills(right, y, labels, t):
    widths = [24 + sum(13 if ord(ch) > 0x2E80 else 7.6 for ch in label) for label in labels]
    x = right - sum(widths) - 8 * (len(labels) - 1)
    parts = []
    for label, w in zip(labels, widths, strict=True):
        parts += [
            rect(x, y, w, 26, t, fill="soft", stroke="soft", r=13),
            text(x + 12, y + 18, label, t, 13, 600, "accent"),
        ]
        x += w + 8
    return "".join(parts)


def diagram(lang, theme):
    c, t = COPY[lang], THEMES[theme]
    markers = "".join(
        f'<marker id="head-{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="{t[name]}"/></marker>'
        for name in ("muted", "accent")
    )
    # The Server pipeline reads left to right: Sources, processing, Artifacts, recall.
    sx, sw, sy, sh = (556, 720, 896, 1110), (136, 148, 186, 122), 126, 144
    families = []
    for i, name in enumerate(c["families"]):
        fx, fy = 906 + (i % 2) * 84, 168 + (i // 2) * 30
        families += [
            rect(fx, fy, 80, 24, t, stroke="accent", r=7, width=1),
            text(fx + 40, fy + 16.5, name, t, 11.5, 600, "accent", "middle"),
        ]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'role="img" aria-labelledby="title"><title id="title">{escape(c["title"])}</title><defs>{markers}</defs>',
        f'<rect width="{WIDTH}" height="{HEIGHT}" rx="20" fill="{t["bg"]}"/>',
        f'<g font-family="{FONT}">',
        # Agents and applications
        panel(24, 24, 220, 552, c["hosts"], t),
        box(40, 74, 188, 96, *c["agents"], t=t),
        box(40, 190, 188, 96, *c["apps"], t=t),
        # Integrations
        panel(264, 24, 224, 552, c["connect"], t),
        path("M228 122H274", t),
        path("M228 238H274", t),
        box(280, 74, 192, 212, *c["hooks"], t=t),
        box(280, 318, 192, 116, *c["gateway"], t=t),
        box(280, 466, 192, 78, *c["model"], t=t),
        path("M376 438V460", t),
        # Server
        panel(532, 24, 724, 392, "", t, fill="server", stroke="accent"),
        text(556, 62, c["server"], t, 19, 800, "accent"),
        pills(1232, 42, c["pills"], t),
        box(sx[0], sy, sw[0], sh, *c["source"], t=t),
        box(sx[1], sy, sw[1], sh, *c["process"], t=t),
        rect(sx[2], sy, sw[2], sh, t, fill="soft", stroke="accent"),
        text(sx[2] + 16, sy + 28, c["artifact"], t, 15.5, 700, "accent"),
        *families,
        box(sx[3], sy, sw[3], sh, *c["prepared"], t=t),
        *(path(f"M{sx[i] + sw[i] + 2} {sy + 72}H{sx[i + 1] - 4}", t) for i in range(3)),
        # Plugins extend Sources and Artifacts.
        box(556, 314, 320, 74, *c["plugins"][0], t=t),
        box(896, 314, 336, 74, *c["plugins"][1], t=t),
        path(f"M624 310V{sy + sh + 4}", t),
        path(f"M989 310V{sy + sh + 4}", t),
        # Both integration paths capture input and receive prepared context.
        path("M476 214H550", t, "accent"),
        text(510, 205, c["capture"], t, 10.5, 600, "accent", "middle"),
        path(f"M1171 {sy - 2}V106H510V150H478", t, "accent"),
        text(840, 99, c["context"], t, 12.5, 600, "accent", "middle"),
        # Infrastructure: processing calls the model services; the Server persists to storage.
        panel(532, 436, 724, 140, c["infra"], t),
        box(556, 482, 320, 76, *c["models"], t=t),
        box(896, 482, 336, 76, *c["storage"], t=t),
        path("M794 420V476", t),
        path("M1064 420V476", t, both=True),
        "</g></svg>",
    ]
    return "".join(parts)


def main():
    for lang in COPY:
        for theme in THEMES:
            (ASSETS / f"readme-architecture-{lang}-{theme}.svg").write_text(diagram(lang, theme), encoding="utf-8")
    print(f"wrote {len(COPY) * len(THEMES)} diagrams")


if __name__ == "__main__":
    main()
