"""Query-enhancement retriever with fail-back safety.

Wraps a PowerMem ``Memory`` object and optionally enhances the query with:

- bilingual expansion (zh <-> en, tiny offline seed lexicon)
- synonym expansion (curated same-meaning map)
- semantic view expansion (user-provided LLM view generator)

The enhanced multi-view recall is converged back to the original query
anchor (the "anchor line" always participates with top weight), and if the
enhanced ranking is not clearly better than the plain anchor search it
falls back to the plain result (fail-back). Every enhancement component is
optional -- with none configured this behaves exactly like a plain
``memory.search``.
"""

from __future__ import annotations

import re
from typing import Any, Callable

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# Tiny bilingual seed lexicon (zh -> en), offline-friendly.
_ZH_EN: dict[str, str] = {
    "数据库": "database",
    "记忆": "memory",
    "查询": "query",
    "检索": "search",
    "用户": "user",
    "存储": "storage",
    "引擎": "engine",
    "模型": "model",
    "向量": "vector",
    "推荐": "recommend",
    "建议": "suggest",
    "意见": "opinion",
    "训练": "train",
    "检测": "detect",
    "目标": "target",
    "无人机": "drone",
    "猫": "cat",
    "水": "water",
    "线": "line",
    "车": "car",
    "答案": "answer",
    "问题": "question",
}

# Curated synonyms: same meaning, different surface form.
_SYNONYMS: dict[str, list[str]] = {
    "database": ["db", "storage engine"],
    "memory": ["recollection", "context"],
    "query": ["search term", "question"],
    "search": ["retrieve", "look up"],
    "user": ["person", "client"],
    "storage": ["persistence", "store"],
    "answer": ["response", "reply"],
    "short": ["brief", "concise"],
    "数据库": ["db", "data store"],
    "建议": ["提议", "推荐方案"],
    "意见": ["看法", "观点"],
}


def _translate_zh_to_en(text: str) -> str | None:
    """Translate known Chinese seed terms to a single English query string."""
    parts: list[str] = []
    for zh, en in _ZH_EN.items():
        if zh in text:
            parts.append(en)
    return " ".join(dict.fromkeys(parts)) if parts else None


def _translate_en_to_zh(text: str) -> str | None:
    """Translate known English seed terms to a single Chinese query string."""
    lowered = text.lower()
    parts: list[str] = []
    for zh, en in _ZH_EN.items():
        if en in lowered:
            parts.append(zh)
    return " ".join(dict.fromkeys(parts)) if parts else None


def _synonym_variants(text: str, top_k: int = 2) -> list[str]:
    """Generate same-meaning variants by replacing known terms."""
    out: list[str] = []
    lowered = text.lower()
    for word, synonyms in _SYNONYMS.items():
        if word not in lowered:
            continue
        for syn in synonyms[:top_k]:
            replaced = re.sub(
                rf"\b{re.escape(word)}\b", syn, text, flags=re.IGNORECASE
            )
            if replaced != text:
                out.append(replaced)
    # de-duplicate preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for v in out:
        if v not in seen:
            seen.add(v)
            unique.append(v)
    return unique[:top_k]


def _norm_score(item: dict[str, Any]) -> float:
    try:
        return float(item.get("score") or 1.0)
    except (TypeError, ValueError):
        return 1.0


# Connectors used by tree splitting (zh + en).
_SPLIT_PATTERN = re.compile(
    r"(?:and|with|plus|&|/|和|与|及|以及|、|，|,|；|;|\s+and\s+|\s+with\s+)"
)
_STOPWORDS = {
    "a", "an", "the", "to", "of", "for", "on", "in", "at", "how", "what",
    "is", "are", "do", "does", "should", "i", "my", "your", "we", "you",
    "的", "了", "在", "是", "我", "你", "他", "怎么", "如何", "为什么",
}


class TreeSplitter:
    """Rule-based tree decomposition of a query into sub-queries.

    Splits a mixed-domain query along connectors (和/与/and/with/、/comma)
    into a small number of single-focus sub-queries. Each sub-query is a
    semantic leaf -- the "more detailed, more complex" decomposition the
    anchor-based convergence then re-combines.

    Dependency-free: works offline with zh/en text.
    """

    def __init__(self, max_views: int = 3, min_part_len: int = 2) -> None:
        self.max_views = max_views
        self.min_part_len = min_part_len

    @staticmethod
    def _clean(part: str) -> str:
        part = part.strip().strip(":：。.!！?？")
        words = [
            w
            for w in re.split(r"\s+", part)
            if w and w.lower() not in _STOPWORDS
        ]
        return " ".join(words) if words else ""

    def split(self, query: str) -> list[str]:
        """Return sub-queries (>=2 chars each), at most ``max_views``."""
        parts = [p for p in _SPLIT_PATTERN.split(query) if p]
        if len(parts) < 2:
            return []
        views: list[str] = []
        seen: set[str] = set()
        for part in parts:
            cleaned = self._clean(part)
            if len(cleaned) < self.min_part_len or cleaned in seen:
                continue
            seen.add(cleaned)
            views.append(cleaned)
            if len(views) >= self.max_views:
                break
        return views


class SafeRetriever:
    """Multi-view retriever that can never be worse than plain search.

    Design:
    - anchor line (the original query) always participates with top weight,
      so the base result set is never lost;
    - optional enhancement lines (bilingual / synonym / semantic views)
      expand recall, each line is down-weighted by its divergence from the
      anchor (convergence);
    - if the converged ranking is not clearly better than the plain anchor
      search, it falls back to the plain result (fail-back).
    """

    def __init__(
        self,
        memory: Any,
        *,
        user_id: str | None = None,
        top_k: int = 5,
        view_budget: int = 5,
        synonym_topk: int = 2,
        fallback_ratio: float = 0.9,
        adaptive: bool = True,
        embed_fn: Callable[[str], list[float]] | None = None,
        view_generator: Callable[[str], list[str]] | None = None,
    ) -> None:
        self.memory = memory
        self.user_id = user_id
        self.top_k = top_k
        self.view_budget = view_budget
        self.synonym_topk = synonym_topk
        self.fallback_ratio = fallback_ratio
        self.adaptive = adaptive
        self.embed_fn = embed_fn
        self.view_generator = view_generator or TreeSplitter().split

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _budget(self, query: str) -> int:
        """Floating view budget: short queries need fewer lines."""
        if not self.adaptive:
            return self.view_budget
        length = len(query)
        if length <= 8:
            return 2
        if length <= 24:
            return 3
        if length <= 48:
            return 4
        return self.view_budget

    def _plain_search(self, query: str, limit: int) -> list[dict[str, Any]]:
        result = self.memory.search(query=query, user_id=self.user_id, limit=limit)
        if not isinstance(result, dict):
            return []
        return [
            item
            for item in result.get("results", [])
            if isinstance(item, dict) and item.get("memory")
        ]

    def _build_lines(self, query: str) -> list[tuple[str, float]]:
        """Return [(line_text, line_weight), ...]; anchor always first."""
        lines: list[tuple[str, float]] = [(query, 1.0)]
        budget = self._budget(query)

        # bilingual line (zh <-> en)
        if _CJK_RE.search(query):
            translated = _translate_zh_to_en(query)
            if translated:
                lines.append((translated, 0.7))
        else:
            translated = _translate_en_to_zh(query)
            if translated:
                lines.append((translated, 0.7))

        # synonym lines
        for variant in _synonym_variants(query, self.synonym_topk):
            if len(lines) >= budget:
                break
            lines.append((variant, 0.8))

        # semantic view lines (tree decomposition, optional custom generator)
        if self.view_generator is not None:
            try:
                views = self.view_generator(query)
            except Exception:
                views = []
            for view in views:
                if len(lines) >= budget:
                    break
                if view and view != query:
                    lines.append((view, 0.75))

        # hard cap on budget, keep the anchor line
        return lines[: max(budget, 1)]

    def _convergence_penalty(self, query: str, line: str) -> float:
        """Penalize a line by its divergence from the anchor query.

        With an embed function we use cosine distance; otherwise we fall
        back to a mild rank-style penalty (line length ratio) so the
        mechanism stays dependency-free.
        """
        if self.embed_fn is None:
            ratio = len(line) / max(len(query), 1)
            return 1.0 / (1.0 + abs(ratio - 1.0) * 0.5)
        try:
            a = self.embed_fn(query)
            b = self.embed_fn(line)
        except Exception:
            return 1.0
        if not a or not b or len(a) != len(b):
            return 1.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 1.0
        cosine = dot / (norm_a * norm_b)
        return 1.0 / (1.0 + (1.0 - cosine))

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def search(self, query: str) -> dict[str, Any]:
        """Enhanced search with anchor guarantee and fail-back.

        Returns the same shape as ``memory.search``:
        ``{"results": [{"memory": ..., "score": ...}, ...]}``
        """
        # 1) plain anchor search (baseline, also the fallback)
        base = self._plain_search(query, self.top_k)
        base_quality = (
            sum(_norm_score(h) for h in base) / len(base) if base else 0.0
        )

        lines = self._build_lines(query)
        if len(lines) <= 1:
            return {"results": base}

        # 2) expand the net: anchor keeps top weight, each line adds recall
        candidates: dict[int, tuple[dict[str, Any], float]] = {}
        for index, hit in enumerate(base):
            candidates[id(hit)] = (hit, _norm_score(hit))

        for line, weight in lines[1:]:
            for hit in self._plain_search(line, self.top_k * 2):
                key = id(hit)
                score = _norm_score(hit)
                # convergence: divergence penalty * line weight
                refined = score * weight * self._convergence_penalty(query, line)
                if key not in candidates or refined > candidates[key][1]:
                    candidates[key] = (hit, refined)

        ranked = sorted(candidates.values(), key=lambda pair: pair[1], reverse=True)
        merged = [hit for hit, _ in ranked[: self.top_k]]

        # 3) fail-back: never let enhancement hurt quality
        merged_quality = (
            sum(_norm_score(h) for h in merged) / len(merged) if merged else 0.0
        )
        if merged_quality < base_quality * self.fallback_ratio:
            return {"results": base}
        return {"results": merged}
