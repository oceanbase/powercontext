# /// script
# requires-python = ">=3.11"
# ///
"""Write the README use-case cards as light and dark SVGs for each language.

Run: python3 docs/assets/render_scenarios.py
Outputs scenarios/card-{handoff,shared,assistant,office,knowledge,users,domain,plugins}-{en,zh,ja}-{light,dark}.svg.
Also writes readme-prompt-rules-{en,zh,ja}-{light,dark}.svg, the full-width per-Scope prompt figure.
Each card stays within a documented capability; the linked workflow pages in the READMEs are the source.
"""

from pathlib import Path
from xml.sax.saxutils import escape

ASSETS = Path(__file__).resolve().parent / "scenarios"
WIDTH, HEIGHT = 640, 360
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans','Microsoft YaHei',Arial,sans-serif"

THEMES = {
    "light": {
        "bg": "#ffffff",
        "card": "#f7f9fd",
        "line": "#dde4ef",
        "box": "#ffffff",
        "ink": "#0f1b33",
        "ink2": "#4a5875",
        "muted": "#7a879e",
        "accent": "#0866ee",
        "soft": "#e8f1ff",
        "ok": "#0f8a5f",
    },
    "dark": {
        "bg": "#0d1117",
        "card": "#121a28",
        "line": "#283449",
        "box": "#0f1726",
        "ink": "#eef3fc",
        "ink2": "#b2bed2",
        "muted": "#8391a8",
        "accent": "#5b9dff",
        "soft": "#16284a",
        "ok": "#3fc58f",
    },
}

COPY = {
    "zh": {
        "handoff": {
            "tag": "任务交接",
            "title": "换个会话，接着做",
            "lead": "上次做到哪、下一步做什么，新会话直接接上。",
            "s1": "会话 1 · 周一",
            "s1a": "✓ 字段映射已验证",
            "s1b": "✓ 分批回填已准备",
            "s1c": "→ 下一步：跑第一批",
            "s2": "会话 2 · 周二",
            "ask": "接着上次的迁移做",
            "reply": "先跑第一批，再核对记录数。",
        },
        "shared": {
            "tag": "共享 Scope",
            "title": "多个 Agent，共用一份记忆",
            "lead": "在 Codex 里定下的约定，换到 Claude Code、OpenCode 也知道。",
            "agents": ("Codex", "Claude Code", "OpenCode"),
            "scope": "同一个 Scope",
            "rows": ("决定", "约束", "进度"),
        },
        "knowledge": {
            "tag": "知识库插件",
            "title": "公司文档，Agent 查得到",
            "lead": "导入 Markdown 和图片，按版本维护，检索结果带出处。",
            "docs": ("部署手册.md", "接口规范.md", "架构图.png"),
            "kb": "知识库",
            "q": "回滚步骤是什么？",
            "a": "→ 部署手册.md · 第 3 节",
        },
        "users": {
            "tag": "用户画像 · SDK",
            "title": "你的应用，记住每一位用户",
            "lead": "按业务用户 ID 积累画像，在 LangChain、LangGraph 等应用中召回。",
            "people": ("用户 U1", "用户 U2", "用户 U3"),
            "profile": "U1 的画像",
            "p1": "偏好简洁的中文回答",
            "p2": "常用移动端",
            "others": ("U2 的画像", "U3 的画像"),
        },
        "domain": {
            "tag": "行业 Agent",
            "title": "行业 Agent，越诊断越有经验",
            "lead": "历史诊断沉淀为可复用的经验，任务中断后也能接着做。",
            "steps": ("告警与遥测", "诊断 Agent", "结论与处置"),
            "memory_tag": "经验",
            "memory": "相似故障的处理经验",
            "resume_tag": "任务状态",
            "resume": "中断后从上一步继续",
        },
        "plugins": {
            "tag": "Source 与 Artifact 插件",
            "title": "工单和事故，变成能检索的知识",
            "lead": "用插件接入业务数据，按你定义的格式生成制品。",
            "src": "工单 OPS-7",
            "f1": "支付回调超时",
            "f2": "状态：已解决",
            "art": "工单摘要（自定义制品）",
            "a1": "原因：证书过期",
            "a2": "处理：轮换证书",
            "a3": "来源：OPS-7",
        },
        "assistant": {
            "tag": "个人助理",
            "title": "个人助理，记得你交代的事",
            "lead": "OpenClaw 在长对话压缩前先存下要点，换个聊天也记得。",
            "chat": "聊了一整天",
            "compact": "上下文压缩",
            "save": "先保存",
            "memory": "记忆",
            "m1": "周三的会改到上午 10 点",
            "m2": "订机票优先靠窗座位",
            "next": "第二天开个新聊天，助理照样记得",
        },
        "office": {
            "tag": "办公 Agent",
            "title": "办公流程，做过一次就会",
            "lead": "跑通的流程沉淀为经验和技能，审核后下次直接用。",
            "case": "第一次：月度报表",
            "c1": "1  从三个系统导出数据",
            "c2": "2  合并并核对差异",
            "c3": "3  发给财务",
            "skill": "技能：生成月度报表",
            "k1": "✓ 已审核",
            "k2": "→ 下个月直接调用",
        },
    },
    "en": {
        "handoff": {
            "tag": "Handoff",
            "title": "Pick up where you left off",
            "lead": "What was done and what comes next, ready in the new session.",
            "s1": "Session 1 · Monday",
            "s1a": "✓ Field mapping verified",
            "s1b": "✓ Batched backfill ready",
            "s1c": "→ Next: run the first batch",
            "s2": "Session 2 · Tuesday",
            "ask": "Continue the migration",
            "reply": "Run batch one, check counts.",
        },
        "shared": {
            "tag": "Shared Scope",
            "title": "One memory for every agent",
            "lead": "A rule agreed in Codex carries over to Claude Code and OpenCode.",
            "agents": ("Codex", "Claude Code", "OpenCode"),
            "scope": "One Scope",
            "rows": ("Decisions", "Constraints", "Progress"),
        },
        "knowledge": {
            "tag": "Knowledge-base plugin",
            "title": "Company docs your agent can search",
            "lead": "Import Markdown and images, keep revisions, and get cited results.",
            "docs": ("deploy-guide.md", "api-spec.md", "architecture.png"),
            "kb": "Knowledge base",
            "q": "How do I roll back?",
            "a": "→ deploy-guide.md · §3",
        },
        "users": {
            "tag": "Profiles · SDK",
            "title": "Your app remembers every user",
            "lead": "Profiles build up per business user ID, recalled in LangChain or LangGraph.",
            "people": ("User U1", "User U2", "User U3"),
            "profile": "U1's profile",
            "p1": "Prefers concise answers",
            "p2": "Mostly on mobile",
            "others": ("U2's profile", "U3's profile"),
        },
        "domain": {
            "tag": "Domain agents",
            "title": "Domain agents learn from every case",
            "lead": "Past diagnoses become reusable experience; interrupted tasks resume.",
            "steps": ("Alarms & data", "Diagnosis agent", "Finding & fix"),
            "memory_tag": "Experience",
            "memory": "How similar faults were fixed",
            "resume_tag": "Task state",
            "resume": "Resume from the last step",
        },
        "plugins": {
            "tag": "Source & Artifact plugins",
            "title": "Tickets become searchable knowledge",
            "lead": "Bring in business data with plugins and generate artifacts in your format.",
            "src": "Ticket OPS-7",
            "f1": "Payment callback timeout",
            "f2": "Status: resolved",
            "art": "Ticket summary (custom)",
            "a1": "Cause: expired certificate",
            "a2": "Fix: rotate certificate",
            "a3": "Source: OPS-7",
        },
        "assistant": {
            "tag": "Personal assistant",
            "title": "An assistant that remembers",
            "lead": "OpenClaw saves key points before the context is compacted.",
            "chat": "A day of chatting",
            "compact": "Context compaction",
            "save": "save",
            "memory": "Memory",
            "m1": "Wednesday meeting at 10 am",
            "m2": "Window seats on flights",
            "next": "A new chat the next day still knows",
        },
        "office": {
            "tag": "Office agents",
            "title": "Office work, learned once",
            "lead": "A process that worked becomes a skill, reused after review.",
            "case": "First time: monthly report",
            "c1": "1  Export from three systems",
            "c2": "2  Merge and reconcile",
            "c3": "3  Send to finance",
            "skill": "Skill: monthly report",
            "k1": "✓ Approved",
            "k2": "→ Reused next month",
        },
    },
    "ja": {
        "handoff": {
            "tag": "タスクの引き継ぎ",
            "title": "別のセッションで、続きから",
            "lead": "どこまで進み、次に何をするかを新しいセッションへ。",
            "s1": "セッション 1 · 月曜",
            "s1a": "✓ 項目の対応を検証済み",
            "s1b": "✓ 分割バックフィル準備済み",
            "s1c": "→ 次：最初のバッチを実行",
            "s2": "セッション 2 · 火曜",
            "ask": "前回の移行の続きを",
            "reply": "最初のバッチ後、件数を確認。",
        },
        "shared": {
            "tag": "共有 Scope",
            "title": "複数の Agent で、ひとつの記憶",
            "lead": "Codex で決めた約束を、Claude Code や OpenCode でも共有。",
            "agents": ("Codex", "Claude Code", "OpenCode"),
            "scope": "同じ Scope",
            "rows": ("判断", "制約", "進捗"),
        },
        "knowledge": {
            "tag": "ナレッジベースプラグイン",
            "title": "社内ドキュメントを、Agent が検索",
            "lead": "Markdown と画像を取り込み、版を管理し、出典つきで検索。",
            "docs": ("デプロイ手順.md", "API 仕様.md", "構成図.png"),
            "kb": "ナレッジベース",
            "q": "ロールバック手順は？",
            "a": "→ デプロイ手順.md · 第 3 節",
        },
        "users": {
            "tag": "プロファイル · SDK",
            "title": "アプリが、ユーザーを一人ずつ覚える",
            "lead": "業務ユーザー ID ごとにプロファイルを蓄積し、LangChain などで想起。",
            "people": ("ユーザー U1", "ユーザー U2", "ユーザー U3"),
            "profile": "U1 のプロファイル",
            "p1": "簡潔な回答を好む",
            "p2": "主にモバイルを利用",
            "others": ("U2 のプロファイル", "U3 のプロファイル"),
        },
        "domain": {
            "tag": "業界向け Agent",
            "title": "業界 Agent が、診断ごとに賢く",
            "lead": "過去の診断を再利用できる経験に。中断したタスクも再開。",
            "steps": ("アラームと計測", "診断 Agent", "結論と対処"),
            "memory_tag": "経験",
            "memory": "類似障害の対処経験",
            "resume_tag": "タスクの状態",
            "resume": "中断後は直前の段階から再開",
        },
        "plugins": {
            "tag": "Source と Artifact のプラグイン",
            "title": "チケットを、検索できる知識に",
            "lead": "プラグインで業務データを取り込み、独自の形式で Artifact を生成。",
            "src": "チケット OPS-7",
            "f1": "決済コールバックの遅延",
            "f2": "状態：解決済み",
            "art": "チケット要約（独自形式）",
            "a1": "原因：証明書の期限切れ",
            "a2": "対処：証明書を更新",
            "a3": "出典：OPS-7",
        },
        "assistant": {
            "tag": "パーソナルアシスタント",
            "title": "頼んだことを覚えている助手",
            "lead": "OpenClaw は会話の圧縮前に要点を保存し、次のチャットでも覚えている。",
            "chat": "一日中の会話",
            "compact": "コンテキスト圧縮",
            "save": "保存",
            "memory": "記憶",
            "m1": "水曜の会議は午前 10 時に",
            "m2": "飛行機は窓側の席を優先",
            "next": "翌日の新しいチャットでも覚えている",
        },
        "office": {
            "tag": "オフィス Agent",
            "title": "業務の手順を、一度で覚える",
            "lead": "うまくいった手順を経験とスキルに。レビュー後に再利用。",
            "case": "初回：月次レポート",
            "c1": "1  3 つのシステムから出力",
            "c2": "2  統合して差異を確認",
            "c3": "3  経理に送付",
            "skill": "スキル：月次レポート",
            "k1": "✓ 承認済み",
            "k2": "→ 翌月はそのまま実行",
        },
    },
}


def text(x, y, s, t, size=15, weight=400, color="ink", anchor="start"):
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{t[color]}" '
        f'text-anchor="{anchor}">{escape(s)}</text>'
    )


def rect(x, y, w, h, t, fill="box", stroke="line", r=10, dash=False):
    extra = ' stroke-dasharray="5 4"' if dash else ""
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{t[fill]}" stroke="{t[stroke]}"{extra}/>'


def arrow(x1, y1, x2, y2, t, color="muted"):
    return (
        f'<path d="M{x1} {y1}L{x2} {y2}" stroke="{t[color]}" stroke-width="2" fill="none" '
        f'marker-end="url(#head-{color})"/>'
    )


def chips(x, y, items, t, w=238, h=34, gap=6, size=14.5):
    return "".join(
        rect(x, y + i * (h + gap), w, h, t, fill="card", stroke="card", r=8)
        + text(x + 14, y + i * (h + gap) + h / 2 + 5, item, t, size, 400, "ink2")
        for i, item in enumerate(items)
    )


def fan_in(names, t, target_x=300, target_y=238):
    parts = []
    for i, name in enumerate(names):
        y = 152 + i * 60
        parts += [
            rect(32, y, 170, 44, t),
            text(117, y + 28, name, t, 15, 600, "ink", "middle"),
            arrow(206, y + 22, target_x, target_y, t),
        ]
    return parts


def handoff(c, t):
    return "".join([
        rect(32, 150, 262, 176, t),
        text(50, 180, c["s1"], t, 14, 700, "muted"),
        text(50, 218, c["s1a"], t, 15, 400, "ink2"),
        text(50, 250, c["s1b"], t, 15, 400, "ink2"),
        text(50, 290, c["s1c"], t, 15, 700, "accent"),
        arrow(300, 238, 336, 238, t, "accent"),
        rect(346, 150, 262, 176, t, stroke="accent"),
        text(364, 180, c["s2"], t, 14, 700, "muted"),
        rect(364, 198, 226, 40, t, fill="soft", stroke="soft", r=8),
        text(378, 224, c["ask"], t, 15, 600, "ink"),
        text(364, 272, c["reply"], t, 15, 400, "ink2"),
    ])


def shared(c, t):
    parts = fan_in(c["agents"], t)
    parts += [
        rect(310, 152, 298, 164, t, fill="soft", stroke="accent"),
        text(330, 186, c["scope"], t, 17, 700, "accent"),
    ]
    for i, row in enumerate(c["rows"]):
        parts += [rect(330, 204 + i * 36, 258, 28, t, r=6), text(344, 223 + i * 36, row, t, 14, 400, "ink2")]
    return "".join(parts)


def knowledge(c, t):
    parts = fan_in(c["docs"], t)
    parts += [
        rect(310, 152, 298, 164, t, stroke="accent"),
        text(330, 186, c["kb"], t, 17, 700, "accent"),
        rect(330, 204, 258, 40, t, fill="soft", stroke="soft", r=8),
        text(344, 230, c["q"], t, 15, 600, "ink"),
        text(330, 280, c["a"], t, 15, 600, "ink2"),
    ]
    return "".join(parts)


def users(c, t):
    parts = []
    for i, name in enumerate(c["people"]):
        y = 152 + i * 60
        parts += [rect(32, y, 170, 44, t), text(117, y + 28, name, t, 15, 600, "ink", "middle")]
    parts += [
        arrow(206, 174, 302, 190, t, "accent"),
        arrow(206, 234, 302, 275, t),
        arrow(206, 294, 302, 307, t),
        rect(310, 152, 298, 100, t, stroke="accent"),
        text(330, 184, c["profile"], t, 16, 700, "accent"),
        text(330, 214, "· " + c["p1"], t, 14.5, 400, "ink2"),
        text(330, 238, "· " + c["p2"], t, 14.5, 400, "ink2"),
        rect(310, 262, 298, 26, t, dash=True, r=8),
        text(326, 280, c["others"][0], t, 13, 400, "muted"),
        rect(310, 294, 298, 26, t, dash=True, r=8),
        text(326, 312, c["others"][1], t, 13, 400, "muted"),
    ]
    return "".join(parts)


def domain(c, t):
    w, gap, x0 = 172, 23, 32
    parts = []
    for i, step in enumerate(c["steps"]):
        x = x0 + i * (w + gap)
        mid = i == 1
        parts += [
            rect(x, 152, w, 56, t, fill="soft" if mid else "box", stroke="accent" if mid else "line"),
            text(x + w / 2, 186, step, t, 15, 700 if mid else 600, "accent" if mid else "ink", "middle"),
        ]
        if i < len(c["steps"]) - 1:
            parts.append(arrow(x + w + 2, 180, x + w + gap - 2, 180, t))
    parts += [
        f'<path d="M290 212L190 252" stroke="{t["accent"]}" stroke-width="2" fill="none" '
        f'marker-end="url(#head-accent)" marker-start="url(#head-accent)"/>',
        f'<path d="M336 212L436 252" stroke="{t["muted"]}" stroke-width="2" fill="none" '
        f'marker-end="url(#head-muted)" marker-start="url(#head-muted)"/>',
    ]
    for x, label, body, accent in (
        (32, c["memory_tag"], c["memory"], True),
        (330, c["resume_tag"], c["resume"], False),
    ):
        parts += [
            rect(x, 258, 278, 66, t, fill="soft" if accent else "box", stroke="accent" if accent else "line"),
            text(x + 18, 283, label, t, 13, 700, "muted"),
            text(x + 18, 309, body, t, 15, 600, "ink"),
        ]
    return "".join(parts)


def plugins(c, t):
    return "".join([
        rect(32, 150, 250, 176, t),
        text(50, 182, c["src"], t, 15, 700, "muted"),
        text(50, 222, c["f1"], t, 15, 600, "ink"),
        text(50, 254, c["f2"], t, 15, 400, "ink2"),
        arrow(288, 238, 324, 238, t, "accent"),
        rect(334, 150, 274, 176, t, stroke="accent"),
        text(352, 182, c["art"], t, 15.5, 700, "accent"),
        chips(352, 198, (c["a1"], c["a2"], c["a3"]), t),
    ])


def assistant(c, t):
    parts = [rect(32, 150, 250, 176, t), text(50, 180, c["chat"], t, 14, 700, "muted")]
    for i, w in enumerate((170, 120, 190, 140)):
        y = 194 + i * 26 + (18 if i >= 2 else 0)
        parts.append(rect(50, y, w, 16, t, fill="line", stroke="line", r=8))
    parts += [
        f'<path d="M44 251H270" stroke="{t["accent"]}" stroke-width="1.5" stroke-dasharray="5 4"/>',
        text(270, 247, c["compact"], t, 11.5, 600, "accent", "end"),
        arrow(288, 220, 330, 220, t, "accent"),
        text(309, 211, c["save"], t, 11.5, 600, "accent", "middle"),
        rect(334, 150, 274, 132, t, stroke="accent"),
        text(352, 180, c["memory"], t, 16, 700, "accent"),
        chips(352, 196, (c["m1"], c["m2"]), t, w=238),
        text(334, 312, c["next"], t, 14, 400, "ink2"),
    ]
    return "".join(parts)


def office(c, t):
    return "".join([
        rect(32, 150, 262, 176, t),
        text(50, 180, c["case"], t, 14, 700, "muted"),
        text(50, 218, c["c1"], t, 15, 400, "ink2"),
        text(50, 250, c["c2"], t, 15, 400, "ink2"),
        text(50, 282, c["c3"], t, 15, 400, "ink2"),
        arrow(300, 238, 336, 238, t, "accent"),
        rect(346, 150, 262, 176, t, stroke="accent"),
        text(364, 184, c["skill"], t, 16, 700, "accent"),
        rect(364, 204, 226, 40, t, fill="soft", stroke="soft", r=8),
        text(378, 230, c["k1"], t, 15, 600, "ok"),
        rect(364, 256, 226, 40, t, fill="card", stroke="card", r=8),
        text(378, 282, c["k2"], t, 15, 600, "ink"),
    ])


SCENES = {
    "handoff": handoff,
    "shared": shared,
    "assistant": assistant,
    "office": office,
    "knowledge": knowledge,
    "users": users,
    "domain": domain,
    "plugins": plugins,
}


def card(scene, lang, theme):
    c, t = COPY[lang][scene], THEMES[theme]
    markers = "".join(
        f'<marker id="head-{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="{t[name]}"/></marker>'
        for name in ("muted", "accent")
    )
    tag_width = 26 + sum(13.5 if ord(ch) > 0x2E80 else 9.2 for ch in c["tag"])
    return "".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'role="img" aria-labelledby="title"><title id="title">{escape(c["title"])}</title><defs>{markers}</defs>',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{t["bg"]}"/>',
        f'<rect x="1" y="1" width="{WIDTH - 2}" height="{HEIGHT - 2}" rx="18" fill="{t["card"]}" stroke="{t["line"]}"/>',
        f'<g font-family="{FONT}">',
        f'<rect x="32" y="28" width="{tag_width:.0f}" height="26" rx="13" fill="{t["soft"]}"/>',
        text(45, 46, c["tag"], t, 13.5, 700, "accent"),
        text(32, 94, c["title"], t, 26, 800),
        text(32, 126, c["lead"], t, 15.5, 400, "ink2"),
        SCENES[scene](c, t),
        "</g></svg>",
    ])


RULES = {
    "zh": {
        "tag": "按 Scope 定制提示词",
        "title": "同一个服务，每个 Scope 一套记忆规则",
        "lead": "记忆提取、合并、画像和主题记忆的提示词都能按 Scope 定制，并保留版本。",
        "server": "PowerContext Server",
        "deploy": "一套部署",
        "default": "其余 Scope：内置指令",
        "rows": (
            ("法务团队", "只记合同条款和截止日期", "记忆提取 · 第 3 版"),
            ("销售 · 个人 Scope", "记住客户的偏好和沟通习惯", "画像生成 · 第 1 版"),
            ("研发项目", "新决定替换旧决定，保留原因", "记忆合并 · 第 2 版"),
        ),
        "fixed": "服务端固定：输出结构 · 权限 · 证据边界 · 敏感信息保护",
    },
    "en": {
        "tag": "Prompts per Scope",
        "title": "One service, memory rules for every Scope",
        "lead": "Extraction, reconciliation, profile, and topic prompts are customized per Scope, with revisions.",
        "server": "PowerContext Server",
        "deploy": "One deployment",
        "default": "Other Scopes: built-in",
        "rows": (
            ("Legal team", "Keep contract terms and deadlines only", "Extraction · rev 3"),
            ("Sales · personal Scope", "Remember client preferences and tone", "Profile · rev 1"),
            ("R&D project", "New decisions replace old, keep the why", "Reconcile · rev 2"),
        ),
        "fixed": "Fixed by the server: output schema · permissions · evidence boundaries · sensitive-data safeguards",
    },
    "ja": {
        "tag": "Scope ごとのプロンプト",
        "title": "ひとつのサービスで、Scope ごとの記憶ルール",
        "lead": "抽出、統合、プロファイル、トピックのプロンプトを Scope ごとに設定でき、版も残ります。",
        "server": "PowerContext Server",
        "deploy": "ひとつのデプロイ",
        "default": "他の Scope：組み込み",
        "rows": (
            ("法務チーム", "契約条件と期限だけを記憶", "抽出 · 第 3 版"),
            ("営業 · 個人の Scope", "顧客の好みと話し方を覚える", "プロファイル · 第 1 版"),
            ("研究開発", "新しい判断で更新し、理由も残す", "統合 · 第 2 版"),
        ),
        "fixed": "サーバーが固定：出力形式 · 権限 · 根拠の範囲 · 機微情報の保護",
    },
}
BANNER_WIDTH, BANNER_HEIGHT = 1280, 420


def rules_banner(lang, theme):
    """Full-width figure for the README's per-Scope memory rules."""
    c, t = RULES[lang], THEMES[theme]
    markers = "".join(
        f'<marker id="head-{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="{t[name]}"/></marker>'
        for name in ("muted", "accent")
    )
    tag_width = 26 + sum(13.5 if ord(ch) > 0x2E80 else 9.2 for ch in c["tag"])
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{BANNER_WIDTH}" height="{BANNER_HEIGHT}" '
        f'viewBox="0 0 {BANNER_WIDTH} {BANNER_HEIGHT}" role="img" aria-labelledby="title">'
        f'<title id="title">{escape(c["title"])}</title><defs>{markers}</defs>',
        f'<rect width="{BANNER_WIDTH}" height="{BANNER_HEIGHT}" fill="{t["bg"]}"/>',
        f'<rect x="1" y="1" width="{BANNER_WIDTH - 2}" height="{BANNER_HEIGHT - 2}" rx="18" fill="{t["card"]}" '
        f'stroke="{t["line"]}"/>',
        f'<g font-family="{FONT}">',
        f'<rect x="32" y="28" width="{tag_width:.0f}" height="26" rx="13" fill="{t["soft"]}"/>',
        text(45, 46, c["tag"], t, 13.5, 700, "accent"),
        text(32, 94, c["title"], t, 26, 800),
        text(32, 126, c["lead"], t, 15.5, 400, "ink2"),
        rect(32, 156, 236, 192, t, fill="soft", stroke="accent"),
        text(50, 188, c["server"], t, 16, 700, "accent"),
        text(50, 214, c["deploy"], t, 14, 400, "ink2"),
        rect(50, 290, 200, 40, t, fill="box", stroke="line", r=8),
        text(64, 315, c["default"], t, 13.5, 400, "ink2"),
    ]
    for i, (scope, rule, op) in enumerate(c["rows"]):
        y = 156 + i * 68
        parts += [
            arrow(272, 252, 334, y + 28, t, "accent"),
            rect(340, y, 908, 56, t, stroke="line"),
            text(358, y + 34, scope, t, 14, 700, "muted"),
            rect(580, y + 9, 420, 38, t, fill="soft", stroke="soft", r=8),
            text(596, y + 34, "“" + rule + "”" if lang != "ja" else "「" + rule + "」", t, 15, 600, "ink"),
            rect(1016, y + 9, 216, 38, t, fill="card", stroke="card", r=8),
            text(1030, y + 34, op, t, 13.5, 600, "ink2"),
        ]
    parts += [
        f'<rect x="340" y="364" width="908" height="34" rx="8" fill="none" stroke="{t["line"]}" '
        f'stroke-dasharray="5 4"/>',
        text(358, 386, c["fixed"], t, 13.5, 600, "muted"),
        "</g></svg>",
    ]
    return "".join(parts)


def main():
    ASSETS.mkdir(exist_ok=True)
    for old in ASSETS.glob("card-*.svg"):
        old.unlink()
    for scene in SCENES:
        for lang in COPY:
            for theme in THEMES:
                (ASSETS / f"card-{scene}-{lang}-{theme}.svg").write_text(card(scene, lang, theme), encoding="utf-8")
    for lang in RULES:
        for theme in THEMES:
            (ASSETS.parent / f"readme-prompt-rules-{lang}-{theme}.svg").write_text(
                rules_banner(lang, theme), encoding="utf-8"
            )
    print(f"wrote {len(SCENES) * len(COPY) * len(THEMES)} cards to {ASSETS}")


if __name__ == "__main__":
    main()
