import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ChevronRight,
  CircleCheck,
  Maximize2,
  Minimize2,
  ListFilter,
  Search,
  X,
} from "lucide-react";
import type { Language } from "./messages";
import {
  Dialog,
  DialogBody,
  DialogCloseButton,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import { PageActionButton } from "@/components/ui/page-action-button";
import { Button } from "@/components/ui/button";
import { Input, InputIcon } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ActionNotification } from "@/components/ui/action-notification";

type MemoryState = "active" | "stopped";
type MemorySource = "automatic" | "manual";
type SortMode = "created" | "used" | "recalls";

type MemoryItem = {
  id: string;
  text: string;
  source: MemorySource;
  state: MemoryState;
  createdAt: string;
  lastUsedAt: string;
  recalls: number;
  excerpts: string[];
};

const copy = {
  zh: {
    title: "记忆",
    demo: "演示数据",
    add: "录入记忆",
    search: "搜索记忆",
    status: "状态",
    active: "使用中",
    stopped: "已停止",
    source: "来源",
    allSources: "全部来源",
    automatic: "自动提取",
    manual: "手动记录",
    sort: "排序",
    created: "最近产生",
    used: "最近使用",
    recalls: "召回最多",
    result: (count: number) => `${count} 条记忆`,
    recallCount: (count: number) => `召回 ${count} 次`,
    lastUsed: "最近使用",
    details: "记忆详情",
    memoryContent: "记忆内容",
    basicInfo: "基础信息",
    original: "记忆来源",
    createdAt: "产生时间",
    previous: "上一条记忆",
    next: "下一条记忆",
    expand: "展开详情",
    collapse: "收起详情",
    closeDetails: "关闭详情",
    excerpt: (index: number) => `对话片段 ${index}`,
    expandExcerpt: "查看更多",
    collapseExcerpt: "收起",
    stop: "停止使用",
    restore: "开启使用",
    stoppedSuccessTitle: "已停止使用这条记忆",
    restoredSuccessTitle: "已开启使用这条记忆",
    closeNotification: "关闭通知",
    empty: "没有符合当前条件的记忆",
    noData: "当前空间暂无记忆",
    emptyHint: "尝试清除搜索词或调整筛选条件",
    newTitle: "录入记忆",
    content: "记忆内容",
    currentSpace: "当前空间",
    serviceName: "服务名称",
    serviceEndpoint: "服务地址",
    contentRequired: "请输入记忆内容",
    cancel: "取消",
    save: "保存到当前空间",
    backToTop: "回到顶部",
  },
  en: {
    title: "Memories",
    demo: "Demo data",
    add: "Record memory",
    search: "Search memories",
    status: "Status",
    active: "In use",
    stopped: "Stopped",
    source: "Source",
    allSources: "All sources",
    automatic: "Automatic",
    manual: "Manual",
    sort: "Sort",
    created: "Recently created",
    used: "Recently used",
    recalls: "Most recalled",
    result: (count: number) => `${count} memories`,
    recallCount: (count: number) => `${count} recalls`,
    lastUsed: "Last used",
    details: "Memory details",
    memoryContent: "Memory content",
    basicInfo: "Basic information",
    original: "Memory sources",
    createdAt: "Created",
    previous: "Previous memory",
    next: "Next memory",
    expand: "Expand details",
    collapse: "Collapse details",
    closeDetails: "Close details",
    excerpt: (index: number) => `Conversation excerpt ${index}`,
    expandExcerpt: "View more",
    collapseExcerpt: "Show less",
    stop: "Stop using",
    restore: "Enable",
    stoppedSuccessTitle: "This memory is no longer in use",
    restoredSuccessTitle: "This memory is now in use",
    closeNotification: "Close notification",
    empty: "No memories match these conditions",
    noData: "No memories in this space yet",
    emptyHint: "Clear the search or adjust the filters",
    newTitle: "Record a new memory",
    content: "Memory content",
    currentSpace: "Current space",
    serviceName: "Service name",
    serviceEndpoint: "Service address",
    contentRequired: "Enter memory content",
    cancel: "Cancel",
    save: "Save to current space",
    backToTop: "Back to top",
  },
} as const;

function formatDate(value: string, language: Language) {
  return new Intl.DateTimeFormat(language === "zh" ? "zh-CN" : "en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function Highlight({ text, query }: { text: string; query: string }) {
  const normalized = query.trim();
  if (!normalized) return <>{text}</>;
  const index = text
    .toLocaleLowerCase()
    .indexOf(normalized.toLocaleLowerCase());
  if (index < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, index)}
      <mark>{text.slice(index, index + normalized.length)}</mark>
      {text.slice(index + normalized.length)}
    </>
  );
}

function MemoryExcerpt({
  id,
  label,
  excerpt,
  expandLabel,
  collapseLabel,
}: {
  id: string;
  label: string;
  excerpt: string;
  expandLabel: string;
  collapseLabel: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [canExpand, setCanExpand] = useState(excerpt.length > 72);
  const excerptRef = useRef<HTMLQuoteElement>(null);

  useEffect(() => {
    if (expanded) return;
    const excerptElement = excerptRef.current;
    if (!excerptElement) return;

    const measure = () => {
      setCanExpand(
        excerpt.length > 72 ||
          excerptElement.scrollHeight > excerptElement.clientHeight + 1,
      );
    };
    measure();

    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(excerptElement);
    return () => observer.disconnect();
  }, [excerpt, expanded]);

  return (
    <article className="memory-excerpt">
      <span>{label}</span>
      <div className="memory-excerpt-content" data-expanded={expanded}>
        <blockquote id={id} ref={excerptRef}>
          {excerpt}
        </blockquote>
      </div>
      {canExpand && (
        <Button
          variant="text"
          size="sm"
          type="button"
          className="memory-excerpt-toggle"
          aria-controls={id}
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? collapseLabel : expandLabel}
        </Button>
      )}
    </article>
  );
}

function previewMemories(language: Language): MemoryItem[] {
  const zh = language === "zh";
  return [
    {
      id: "preview-memory-1",
      text: zh
        ? "用户偏好简洁、直接的中文回复，并希望先看到结论。"
        : "The user prefers concise, direct replies with the conclusion first.",
      source: "automatic",
      state: "active",
      createdAt: "2026-11-12T08:40:00Z",
      lastUsedAt: "2026-11-13T02:16:00Z",
      recalls: 18,
      excerpts: [
        zh
          ? "请先说结论，再补充必要的背景。"
          : "Lead with the conclusion, then add only the necessary context.",
        zh
          ? [
              "这次回复请先给出明确结论。",
              "随后说明最关键的判断依据。",
              "背景信息只保留与结论直接相关的部分。",
              "如果有多个要点，请按优先级排列。",
              "每个段落尽量简短，方便快速浏览。",
              "已经确认的信息不需要重复解释。",
              "遇到不确定内容时，请明确标注假设。",
              "需要用户选择时，再列出可选方案。",
              "界面文案也沿用同样的表达顺序。",
              "后续对话请持续保持这一回复习惯。",
            ].join("\n")
          : [
              "Start this reply with a clear conclusion.",
              "Then explain the most important reasoning.",
              "Keep only background that directly supports the conclusion.",
              "When there are several points, order them by priority.",
              "Keep paragraphs short so the reply is easy to scan.",
              "Do not repeat information that is already established.",
              "Label assumptions clearly when something is uncertain.",
              "Only present options when the user needs to choose.",
              "Use the same information order in interface copy.",
              "Keep following this reply style in future conversations.",
            ].join("\n"),
        zh
          ? "信息较多时，用简短段落帮助我快速浏览。"
          : "Use short paragraphs when there is more information to scan.",
        zh
          ? "不要重复解释已经确认过的内容。"
          : "Do not repeat details that have already been confirmed.",
        zh
          ? "如果结论依赖假设，请直接说明。"
          : "State any assumptions that the conclusion depends on.",
        zh
          ? "相关界面和后续对话都保持这一表达习惯。"
          : "Keep this style consistent across related screens and future conversations.",
      ],
    },
    {
      id: "preview-memory-2",
      text: zh
        ? "PowerContext Desktop 使用 React、TypeScript 与 Tauri 构建。"
        : "PowerContext Desktop is built with React, TypeScript, and Tauri.",
      source: "automatic",
      state: "active",
      createdAt: "2026-11-11T06:20:00Z",
      lastUsedAt: "2026-11-12T09:30:00Z",
      recalls: 12,
      excerpts: [
        zh
          ? "桌面端技术栈是 React、TypeScript 和 Tauri。"
          : "The desktop stack is React, TypeScript, and Tauri.",
      ],
    },
    {
      id: "preview-memory-3",
      text: zh
        ? "默认使用中文沟通，界面文案需要同时支持中英文。"
        : "Communicate in Chinese by default and keep UI copy bilingual.",
      source: "manual",
      state: "active",
      createdAt: "2026-11-10T03:15:00Z",
      lastUsedAt: "2026-11-12T05:42:00Z",
      recalls: 9,
      excerpts: [
        zh
          ? "请默认用中文，产品界面保留中英文能力。"
          : "Use Chinese by default while keeping the product bilingual.",
      ],
    },
    {
      id: "preview-memory-4",
      text: zh
        ? "界面应优先服务普通用户，避免直接暴露底层协议与诊断术语。"
        : "The UI should serve everyday users without exposing protocol or diagnostic jargon.",
      source: "automatic",
      state: "active",
      createdAt: "2026-11-09T11:05:00Z",
      lastUsedAt: "2026-11-11T07:18:00Z",
      recalls: 7,
      excerpts: [
        zh
          ? "不要让普通用户先理解 Server 内部概念。"
          : "Do not make everyday users understand Server internals first.",
      ],
    },
  ];
}

function buildPreviewMemories(language: Language, count: number): MemoryItem[] {
  const examples = previewMemories(language);
  const zh = language === "zh";
  const subjects = zh
    ? [
        "用户在查看记忆列表时，",
        "团队评审桌面端界面时，",
        "设计记忆管理流程时，",
        "连接新的 PowerContext 服务后，",
        "切换到其他记忆空间时，",
        "配置本地 Agent 时，",
        "搜索历史记忆时，",
        "处理空数据状态时，",
        "整理产品需求时，",
        "检查服务连接状态时，",
        "编写界面文案时，",
        "进行视觉验收时，",
      ]
    : [
        "When reviewing the memory list,",
        "During desktop UI reviews,",
        "When designing memory management flows,",
        "After connecting a new PowerContext service,",
        "When switching memory spaces,",
        "While configuring a local Agent,",
        "When searching previous memories,",
        "When handling an empty state,",
        "While refining product requirements,",
        "When checking service connectivity,",
        "When writing interface copy,",
        "During visual QA,",
      ];
  const details = zh
    ? [
        "希望先看到最重要的信息，再按需展开详情。",
        "偏好使用简短、明确的中文说明。",
        "需要保持页面结构稳定，避免内容加载后跳动。",
        "更关注最近使用时间，不需要暴露底层标识。",
        "希望操作结果立即反馈，并保留当前上下文。",
        "倾向于使用紧凑控件，减少不必要的页面高度。",
        "要求筛选条件清晰区分，避免与排序混淆。",
        "希望空状态只说明当前情况，不展示多余引导。",
        "会优先检查标题、主要操作和内容层级是否明确。",
        "要求同类按钮保持相同字号、尺寸与交互状态。",
        "希望服务、空间与记忆数据之间的归属关系清晰。",
        "倾向于把低频设置收进更多操作菜单。",
        "需要搜索结果即时更新，并高亮匹配内容。",
        "希望切换空间后只展示该空间所属的记忆。",
        "要求危险操作有明确说明，并且不会影响其他数据。",
        "偏好在列表中减少辅助标签，把详情留到侧栏查看。",
        "希望默认值能够直接使用，不重复要求用户填写。",
        "要求错误提示靠近问题字段，并保留已经输入的内容。",
        "希望状态信息紧跟对象名称，减少横向阅读距离。",
        "倾向于使用浅色背景表达悬停和选中状态。",
        "要求中文与英文界面保持一致的信息结构。",
        "希望页面宽度受控，避免信息在大屏上过度分散。",
        "需要主操作和次操作有清楚但不过度的视觉区分。",
        "希望模拟数据接近真实工作内容，便于判断最终效果。",
      ]
    : [
        "the most important information should appear before optional details.",
        "copy should stay concise, direct, and easy to scan.",
        "the page structure should remain stable while content loads.",
        "recent usage matters more than exposing internal identifiers.",
        "actions should provide immediate feedback without losing context.",
        "compact controls are preferred to unnecessary vertical space.",
        "filters must be visually distinct from sorting controls.",
        "empty states should explain the situation without extra guidance.",
        "the title, primary action, and content hierarchy are reviewed first.",
        "similar buttons should share the same type size and interaction states.",
        "ownership between services, spaces, and memories must stay clear.",
        "infrequent settings should live in an overflow menu.",
        "search results should update immediately and highlight matches.",
        "switching spaces should show only memories that belong to that space.",
        "destructive actions need clear scope and must not affect unrelated data.",
        "secondary labels should be removed from lists when details are available elsewhere.",
        "usable defaults should not require users to enter the same information again.",
        "field errors should appear locally without clearing entered content.",
        "status information should stay close to the object name.",
        "hover and selected states should use quiet background tokens.",
        "Chinese and English interfaces should preserve the same information structure.",
        "content width should stay controlled on large screens.",
        "primary and secondary actions need clear but restrained contrast.",
        "preview data should resemble real work so the final design is easier to judge.",
      ];
  return Array.from({ length: count }, (_, index) => {
    const sequence = index + 1;
    const example = examples[index % examples.length];
    const createdAt = new Date(example.createdAt);
    createdAt.setMinutes(createdAt.getMinutes() - index * 37);
    const lastUsedAt = new Date(example.lastUsedAt);
    lastUsedAt.setMinutes(lastUsedAt.getMinutes() - index * 11);
    const generatedIndex = index - examples.length;
    const subject = subjects[generatedIndex % subjects.length];
    const detail =
      details[Math.floor(generatedIndex / subjects.length) % details.length];
    const text =
      generatedIndex < 0 ? example.text : `${subject}${zh ? "" : " "}${detail}`;
    return {
      ...example,
      id: `preview-memory-${sequence}`,
      text,
      source: index % 5 === 0 ? "manual" : "automatic",
      createdAt: createdAt.toISOString(),
      lastUsedAt: lastUsedAt.toISOString(),
      recalls: 1 + ((index * 7) % 31),
      excerpts:
        generatedIndex < 0
          ? example.excerpts
          : [
              zh
                ? `此前讨论中确认：${text}`
                : `Earlier discussion confirmed: ${text}`,
              zh
                ? "团队在后续评审中确认，应继续遵循这条记忆。"
                : "The team confirmed in a later review that this memory should continue to guide the work.",
            ],
    };
  });
}

export function MemoryWorkspaceContent({
  language,
  scopeTitle,
  scopeId = scopeTitle,
  serviceName,
  serviceEndpoint,
  initialCount = 0,
}: {
  language: Language;
  scopeTitle: string;
  scopeId?: string;
  serviceName: string;
  serviceEndpoint: string;
  initialCount?: number;
}) {
  const t = copy[language];
  const [memoriesByScope, setMemoriesByScope] = useState<
    Record<string, MemoryItem[]>
  >(() => ({ [scopeId]: buildPreviewMemories(language, initialCount) }));
  const memories = memoriesByScope[scopeId] ?? [];
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<MemoryState>("active");
  const [source, setSource] = useState<MemorySource | "all">("all");
  const [sort, setSort] = useState<SortMode>("created");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailsExpanded, setDetailsExpanded] = useState(false);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const [addSubmitted, setAddSubmitted] = useState(false);
  const [showBackToTop, setShowBackToTop] = useState(false);
  const [stateNotification, setStateNotification] =
    useState<MemoryState | null>(null);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const selectedTriggerRef = useRef<HTMLButtonElement | null>(null);
  const detailScrollRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    setMemoriesByScope((current) =>
      Object.hasOwn(current, scopeId)
        ? current
        : {
            ...current,
            [scopeId]: buildPreviewMemories(language, initialCount),
          },
    );
    setQuery("");
    setStatus("active");
    setSource("all");
    setSort("created");
    setSelectedId(null);
    setDetailsExpanded(false);
    setAdding(false);
    setDraft("");
    setAddSubmitted(false);
    setStateNotification(null);
  }, [scopeId]);

  useEffect(() => {
    const scrollContainer = workspaceRef.current?.closest("main");
    if (!scrollContainer) return;
    const updateBackToTop = () =>
      setShowBackToTop(scrollContainer.scrollTop > 480);
    updateBackToTop();
    scrollContainer.addEventListener("scroll", updateBackToTop, {
      passive: true,
    });
    return () => scrollContainer.removeEventListener("scroll", updateBackToTop);
  }, []);

  function scrollToTop() {
    headingRef.current?.focus({ preventScroll: true });
    const reduceMotion = window.matchMedia?.(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    workspaceRef.current?.closest("main")?.scrollTo({
      top: 0,
      behavior: reduceMotion ? "auto" : "smooth",
    });
  }

  function updateMemories(update: (current: MemoryItem[]) => MemoryItem[]) {
    setMemoriesByScope((current) => ({
      ...current,
      [scopeId]: update(current[scopeId] ?? []),
    }));
  }

  const visible = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return memories
      .filter((item) => item.state === status || item.id === selectedId)
      .filter((item) => source === "all" || item.source === source)
      .filter(
        (item) =>
          !normalized || item.text.toLocaleLowerCase().includes(normalized),
      )
      .sort((left, right) => {
        if (sort === "recalls") return right.recalls - left.recalls;
        const field = sort === "used" ? "lastUsedAt" : "createdAt";
        return Date.parse(right[field]) - Date.parse(left[field]);
      });
  }, [memories, query, selectedId, sort, source, status]);
  const selected = memories.find((item) => item.id === selectedId) ?? null;
  const selectedIndex = visible.findIndex((item) => item.id === selectedId);

  useEffect(() => {
    if (selectedId && selectedIndex < 0) {
      setSelectedId(null);
      setDetailsExpanded(false);
    }
  }, [selectedId, selectedIndex]);

  useEffect(() => {
    if (!selectedId) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      if (detailsExpanded) {
        setDetailsExpanded(false);
      } else {
        setSelectedId(null);
        window.requestAnimationFrame(() => selectedTriggerRef.current?.focus());
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [detailsExpanded, selectedId]);

  function selectMemory(id: string, trigger?: HTMLButtonElement) {
    if (trigger) selectedTriggerRef.current = trigger;
    setSelectedId(id);
    window.requestAnimationFrame(() =>
      detailScrollRef.current?.scrollTo?.(0, 0),
    );
  }

  function selectRelative(offset: -1 | 1) {
    const next = visible[selectedIndex + offset];
    if (!next) return;
    selectMemory(next.id);
  }

  function closeDetails() {
    setSelectedId(null);
    setDetailsExpanded(false);
    window.requestAnimationFrame(() => selectedTriggerRef.current?.focus());
  }

  function toggleState(item: MemoryItem) {
    const next = item.state === "active" ? "stopped" : "active";
    updateMemories((current) =>
      current.map((memory) =>
        memory.id === item.id ? { ...memory, state: next } : memory,
      ),
    );
    setStateNotification(next);
  }

  function addMemory() {
    setAddSubmitted(true);
    if (!draft.trim()) return;
    const now = new Date().toISOString();
    const item: MemoryItem = {
      id: `memory-${Date.now()}`,
      text: draft.trim(),
      source: "manual",
      state: "active",
      createdAt: now,
      lastUsedAt: now,
      recalls: 0,
      excerpts: [draft.trim()],
    };
    updateMemories((current) => [item, ...current]);
    setDraft("");
    setAddSubmitted(false);
    setAdding(false);
    setStatus("active");
    setSelectedId(item.id);
  }

  return (
    <div ref={workspaceRef} className="memory-workspace">
      <div className="memory-page-heading">
        <h2 ref={headingRef} className="memory-page-title" tabIndex={-1}>
          {t.title}
        </h2>
        <span className="memory-count-badge">{t.result(visible.length)}</span>
      </div>

      <section className="memory-toolbar" aria-label={t.search}>
        <div className="memory-toolbar-controls">
          <label className="memory-search-field">
            <span className="sr-only">{t.search}</span>
            <InputIcon>
              <Search />
            </InputIcon>
            <Input
              adornment="leading"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t.search}
            />
          </label>
          <Select
            value={status}
            onValueChange={(value) => setStatus(value as MemoryState)}
          >
            <SelectTrigger aria-label={t.status}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="active">{t.active}</SelectItem>
              <SelectItem value="stopped">{t.stopped}</SelectItem>
            </SelectContent>
          </Select>
          <Select
            value={source}
            onValueChange={(value) => setSource(value as MemorySource | "all")}
          >
            <SelectTrigger aria-label={t.source}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t.allSources}</SelectItem>
              <SelectItem value="automatic">{t.automatic}</SelectItem>
              <SelectItem value="manual">{t.manual}</SelectItem>
            </SelectContent>
          </Select>
          <Select
            value={sort}
            onValueChange={(value) => setSort(value as SortMode)}
          >
            <SelectTrigger aria-label={t.sort}>
              <ListFilter className="memory-sort-icon" aria-hidden="true" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="created">{t.created}</SelectItem>
              <SelectItem value="used">{t.used}</SelectItem>
              <SelectItem value="recalls">{t.recalls}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <PageActionButton onClick={() => setAdding(true)}>
          {t.add}
        </PageActionButton>
      </section>

      <div
        className="memory-workspace-content"
        data-detail-open={!!selected}
        data-detail-expanded={detailsExpanded}
      >
        <section className="memory-list" aria-label={t.title}>
          {visible.length ? (
            <ul>
              {visible.map((item) => (
                <li key={item.id}>
                  <Button
                    variant="row"
                    className="memory-list-row"
                    data-selected={selected?.id === item.id}
                    onClick={(event) =>
                      selectMemory(item.id, event.currentTarget)
                    }
                  >
                    <span className="memory-row-copy">
                      <span>
                        <Highlight text={item.text} query={query} />
                      </span>
                    </span>
                    <span className="memory-row-usage">
                      <span>{t.recallCount(item.recalls)}</span>
                      <small>
                        {t.lastUsed} {formatDate(item.lastUsedAt, language)}
                      </small>
                    </span>
                    <ChevronRight aria-hidden="true" />
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="memory-list-empty">
              <Search aria-hidden="true" />
              <span>{memories.length === 0 ? t.noData : t.empty}</span>
            </div>
          )}
        </section>

        {selected && (
          <aside
            ref={detailScrollRef}
            className="memory-detail"
            aria-label={t.details}
          >
            <header className="memory-detail-header">
              <div className="memory-detail-heading">
                <h3>{t.details}</h3>
              </div>
              <div className="memory-detail-actions">
                {detailsExpanded && (
                  <>
                    <span className="memory-detail-position" aria-live="polite">
                      {selectedIndex + 1} / {visible.length}
                    </span>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="inline-flex">
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-sm"
                            className="memory-detail-icon-button"
                            aria-label={t.previous}
                            disabled={selectedIndex <= 0}
                            onClick={() => selectRelative(-1)}
                          >
                            <ArrowUp aria-hidden="true" />
                          </Button>
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>{t.previous}</TooltipContent>
                    </Tooltip>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="inline-flex">
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-sm"
                            className="memory-detail-icon-button"
                            aria-label={t.next}
                            disabled={selectedIndex >= visible.length - 1}
                            onClick={() => selectRelative(1)}
                          >
                            <ArrowDown aria-hidden="true" />
                          </Button>
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>{t.next}</TooltipContent>
                    </Tooltip>
                  </>
                )}
                {!detailsExpanded && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        className="memory-detail-icon-button"
                        aria-label={t.expand}
                        onClick={() => setDetailsExpanded(true)}
                      >
                        <Maximize2
                          className="memory-detail-expand-icon"
                          aria-hidden="true"
                        />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>{t.expand}</TooltipContent>
                  </Tooltip>
                )}
                {detailsExpanded && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        className="memory-detail-icon-button"
                        aria-label={t.collapse}
                        onClick={() => setDetailsExpanded(false)}
                      >
                        <Minimize2
                          className="memory-detail-collapse-icon"
                          aria-hidden="true"
                        />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>{t.collapse}</TooltipContent>
                  </Tooltip>
                )}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className="memory-detail-icon-button"
                      aria-label={t.closeDetails}
                      onClick={closeDetails}
                    >
                      <X aria-hidden="true" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>{t.closeDetails}</TooltipContent>
                </Tooltip>
              </div>
            </header>

            <div className="memory-detail-body">
              <div className="memory-detail-main memory-detail-scroll-region">
                <section className="memory-detail-section">
                  <h4>{t.memoryContent}</h4>
                  <p className="memory-detail-text">{selected.text}</p>
                </section>
                <section className="memory-excerpts">
                  <h4>{t.original}</h4>
                  {selected.excerpts.map((excerpt, index) => (
                    <MemoryExcerpt
                      key={`${selected.id}-${index}`}
                      id={`memory-excerpt-${selected.id}-${index}`}
                      label={t.excerpt(index + 1)}
                      excerpt={excerpt}
                      expandLabel={t.expandExcerpt}
                      collapseLabel={t.collapseExcerpt}
                    />
                  ))}
                </section>
              </div>

              <aside className="memory-detail-properties memory-detail-scroll-region">
                <h4>{t.basicInfo}</h4>
                <dl>
                  <div>
                    <dt>{t.status}</dt>
                    <dd>
                      {selected.state === "active" ? t.active : t.stopped}
                    </dd>
                  </div>
                  <div>
                    <dt>{t.source}</dt>
                    <dd>
                      {selected.source === "automatic" ? t.automatic : t.manual}
                    </dd>
                  </div>
                  <div>
                    <dt>{t.createdAt}</dt>
                    <dd>{formatDate(selected.createdAt, language)}</dd>
                  </div>
                  <div>
                    <dt>{t.lastUsed}</dt>
                    <dd>{formatDate(selected.lastUsedAt, language)}</dd>
                  </div>
                  <div>
                    <dt>{t.recalls}</dt>
                    <dd>{selected.recalls}</dd>
                  </div>
                </dl>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => toggleState(selected)}
                >
                  {selected.state === "active" ? t.stop : t.restore}
                </Button>
              </aside>
            </div>
          </aside>
        )}
      </div>

      {stateNotification && (
        <ActionNotification
          icon={<CircleCheck />}
          title={
            stateNotification === "stopped"
              ? t.stoppedSuccessTitle
              : t.restoredSuccessTitle
          }
          closeLabel={t.closeNotification}
          onClose={() => setStateNotification(null)}
          tone="success"
        />
      )}

      {adding && (
        <Dialog
          open
          onOpenChange={(open) => {
            if (open) return;
            setAdding(false);
            setDraft("");
            setAddSubmitted(false);
          }}
        >
          <DialogContent>
            <form
              className="memory-entry-form contents"
              onSubmit={(event) => {
                event.preventDefault();
                addMemory();
              }}
            >
              <DialogHeader>
                <DialogTitle>{t.newTitle}</DialogTitle>
                <DialogCloseButton label={t.cancel} />
              </DialogHeader>
              <DialogBody>
                <dl className="memory-entry-context">
                  <div>
                    <dt>{t.serviceName}</dt>
                    <dd>{serviceName}</dd>
                  </div>
                  <div>
                    <dt>{t.serviceEndpoint}</dt>
                    <dd title={serviceEndpoint}>{serviceEndpoint}</dd>
                  </div>
                  <div>
                    <dt>{t.currentSpace}</dt>
                    <dd>{scopeTitle}</dd>
                  </div>
                </dl>
                <Field>
                  <FieldLabel htmlFor="memory-content">{t.content}</FieldLabel>
                  <Textarea
                    id="memory-content"
                    autoFocus
                    aria-invalid={addSubmitted && !draft.trim()}
                    aria-describedby={
                      addSubmitted && !draft.trim()
                        ? "memory-content-error"
                        : undefined
                    }
                    rows={5}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                  />
                  {addSubmitted && !draft.trim() && (
                    <FieldError asChild>
                      <span id="memory-content-error">{t.contentRequired}</span>
                    </FieldError>
                  )}
                </Field>
              </DialogBody>
              <DialogFooter>
                <Button
                  variant="outline"
                  type="button"
                  onClick={() => {
                    setAdding(false);
                    setDraft("");
                    setAddSubmitted(false);
                  }}
                >
                  {t.cancel}
                </Button>
                <Button type="submit">{t.save}</Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      )}

      {showBackToTop && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="floating"
              size="icon"
              type="button"
              className="memory-back-to-top"
              aria-label={t.backToTop}
              onClick={scrollToTop}
            >
              <ArrowUp aria-hidden="true" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="left">{t.backToTop}</TooltipContent>
        </Tooltip>
      )}
    </div>
  );
}
