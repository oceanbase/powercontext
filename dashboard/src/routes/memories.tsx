import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Calendar,
  ChevronLeft,
  ChevronRight,
  Database,
  Filter,
  MoreHorizontal,
  RefreshCcw,
  Search,
  Trash2,
  User,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { api, type Memory } from "../lib/api";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

type SortField = "score" | "created_at" | "updated_at";
type SortOrder = "asc" | "desc";

export const Route = createFileRoute("/memories")({
  validateSearch: (search: Record<string, unknown>) => {
    const rawTimeRange = (search.time_range as string) || "all";
    const allowedRanges = new Set(["7d", "30d", "90d", "all"]);
    const time_range = allowedRanges.has(rawTimeRange) ? rawTimeRange : "all";

    const rawSortBy = (search.sort_by as string) || "";
    const allowedSorts = new Set<SortField>(["score", "created_at", "updated_at"]);
    const sort_by = allowedSorts.has(rawSortBy as SortField)
      ? (rawSortBy as SortField)
      : undefined;

    const rawOrder = (search.order as string) || "desc";
    const order: SortOrder = rawOrder === "asc" ? "asc" : "desc";

    return {
      user_id: search.user_id as string | undefined,
      agent_id: search.agent_id as string | undefined,
      page: (search.page as number) || 1,
      q: (search.q as string) || undefined,
      time_range,
      sort_by,
      order,
    };
  },
  component: MemoriesPage,
});

const LIMIT = 20;

type MemoryRow = Memory & { score?: number };

function MemoriesPage() {
  const { t } = useTranslation();
  const { user_id, agent_id, page, q, time_range, sort_by, order } = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const [searchInput, setSearchInput] = useState(q ?? "");
  const [userIdInput, setUserIdInput] = useState(user_id ?? "");
  const [agentIdInput, setAgentIdInput] = useState(agent_id ?? "");
  const [selectedMemory, setSelectedMemory] = useState<MemoryRow | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isFiltering, setIsFiltering] = useState(false);
  const queryClient = useQueryClient();

  // Keep inputs in sync if URL changes (e.g. back/forward).
  useEffect(() => { setSearchInput(q ?? ""); }, [q]);
  useEffect(() => { setUserIdInput(user_id ?? ""); }, [user_id]);
  useEffect(() => { setAgentIdInput(agent_id ?? ""); }, [agent_id]);

  const hasQuery = !!q && q.trim().length > 0;
  // Default sort: relevance when searching, created_at desc when listing.
  // "score" only makes sense in search mode — fall back to created_at otherwise.
  const effectiveSortBy: SortField =
    sort_by && (sort_by !== "score" || hasQuery)
      ? sort_by
      : hasQuery
      ? "score"
      : "created_at";

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["memories", user_id, agent_id, page, q, time_range, effectiveSortBy, order],
    queryFn: async () => {
      if (hasQuery) {
        const resp = await api.searchMemories({
          query: q!,
          user_id,
          agent_id,
          limit: LIMIT,
          time_range,
          sort_by: effectiveSortBy,
          order,
        });
        return {
          rows: resp.results as MemoryRow[],
          total: resp.total,
          mode: "search" as const,
        };
      }
      const resp = await api.getMemories({
        user_id,
        agent_id,
        limit: LIMIT,
        offset: (page - 1) * LIMIT,
        sort_by: effectiveSortBy === "score" ? undefined : effectiveSortBy,
        order,
        time_range: time_range === "all" ? undefined : time_range,
      });
      return {
        rows: resp.memories as MemoryRow[],
        total: resp.total,
        mode: "list" as const,
      };
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteMemory(id),
    onSuccess: () => {
      toast.success(t("memories.toast.deleted"));
      queryClient.invalidateQueries({ queryKey: ["memories"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
    onError: (err) => {
      toast.error(t("memories.toast.deleteFailed", { error: err.message }));
    },
  });

  const memories: MemoryRow[] = data?.rows ?? [];
  const total = data?.total ?? 0;
  const isSearchMode = data?.mode === "search";
  // Search returns top-K within `total`; pagination only makes sense for list mode.
  const totalPages = isSearchMode ? 1 : Math.ceil(total / LIMIT);

  const updateSearchParams = async (
    patch: Partial<{
      q: string | undefined;
      user_id: string | undefined;
      agent_id: string | undefined;
      time_range: string;
      sort_by: SortField | undefined;
      order: SortOrder;
      page: number;
    }>,
    resetPage = true,
  ) => {
    await navigate({
      search: (prev: any) => ({
        ...prev,
        ...patch,
        ...(resetPage ? { page: 1 } : {}),
      }),
    });
  };

  const handleFilter = async () => {
    setIsFiltering(true);
    try {
      await updateSearchParams({
        q: searchInput.trim() || undefined,
        user_id: userIdInput.trim() || undefined,
        agent_id: agentIdInput.trim() || undefined,
      });
      toast.success(t("memories.toast.filterApplied"));
    } catch (e) {
      toast.error(t("common.error"), { description: t("common.tryAgain") });
    } finally {
      setIsFiltering(false);
    }
  };

  const handleClearFilters = async () => {
    setSearchInput("");
    setUserIdInput("");
    setAgentIdInput("");
    await updateSearchParams({
      q: undefined,
      user_id: undefined,
      agent_id: undefined,
      time_range: "all",
      sort_by: undefined,
      order: "desc",
    });
    toast.success(t("memories.toast.filterCleared"));
  };

  const handleKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      handleFilter();
    }
  };

  const handleSortByCreatedAt = async () => {
    if (effectiveSortBy === "created_at") {
      await updateSearchParams(
        { sort_by: "created_at", order: order === "desc" ? "asc" : "desc" },
        false,
      );
    } else {
      await updateSearchParams({ sort_by: "created_at", order: "desc" }, false);
    }
  };

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await refetch();
      toast.success(t("memories.toast.refreshed"));
    } catch (error) {
      toast.error(t("common.error"), {
        description: t("common.tryAgain"),
      });
    } finally {
      setIsRefreshing(false);
    }
  };

  const fallbackCopyText = (text: string): boolean => {
    // First fallback: use `copy` event with explicit clipboardData payload.
    // This is often more reliable than copying from selected textarea alone.
    let eventCopied = false;
    const handleCopy = (event: ClipboardEvent) => {
      event.preventDefault();
      if (event.clipboardData) {
        event.clipboardData.setData("text/plain", text);
        eventCopied = true;
      }
    };

    document.addEventListener("copy", handleCopy);
    try {
      const copyTriggered = document.execCommand("copy");
      if (copyTriggered && eventCopied) {
        return true;
      }
    } finally {
      document.removeEventListener("copy", handleCopy);
    }

    // Second fallback: selected textarea + execCommand.
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.top = "0";
    textarea.style.left = "0";
    textarea.style.width = "1px";
    textarea.style.height = "1px";
    textarea.style.opacity = "0";
    textarea.setAttribute("aria-hidden", "true");

    const selection = document.getSelection();
    const previousRange =
      selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null;
    const activeElement = document.activeElement as HTMLElement | null;

    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);

    let copied = false;
    try {
      copied = document.execCommand("copy");
    } finally {
      document.body.removeChild(textarea);

      if (activeElement?.focus) {
        activeElement.focus();
      }

      if (selection && previousRange) {
        selection.removeAllRanges();
        selection.addRange(previousRange);
      }
    }

    return copied;
  };

  const copyText = async (text: string) => {
    let copied = false;
    let clipboardError: unknown;
    let permissionDenied = false;
    let usedClipboardApi = false;

    if (navigator.permissions?.query) {
      try {
        const permission = await navigator.permissions.query({
          name: "clipboard-write" as PermissionName,
        });
        if (permission.state === "denied") {
          permissionDenied = true;
          clipboardError = new Error("clipboard_permission_denied");
        }
      } catch (err) {
        // Ignore unsupported browser behavior from Permissions API.
      }
    }

    if (!permissionDenied && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        copied = true;
        usedClipboardApi = true;
      } catch (err) {
        clipboardError = err;
      }
    }

    if (!copied) {
      copied = fallbackCopyText(text);
    }

    if (!copied) {
      throw (clipboardError instanceof Error
        ? clipboardError
        : new Error("copy_failed"));
    }

    if (usedClipboardApi && navigator.clipboard?.readText) {
      try {
        const copiedText = await navigator.clipboard.readText();
        if (copiedText !== text) {
          throw new Error("copy_verification_failed");
        }
      } catch (err) {
        // Read-back may be blocked by browser policy even when write succeeds.
        if (
          !(err instanceof DOMException) ||
          (err.name !== "NotAllowedError" && err.name !== "SecurityError")
        ) {
          throw err;
        }
      }
    }
  };

  const getDisplayRunId = (memory: MemoryRow): string | undefined => {
    if (memory.run_id) {
      return memory.run_id;
    }

    const metadata = memory.metadata;
    if (!metadata || typeof metadata !== "object") {
      return undefined;
    }

    const metadataRunId =
      (metadata as Record<string, unknown>).run_id ??
      (typeof (metadata as Record<string, unknown>).filters === "object" &&
      (metadata as Record<string, unknown>).filters !== null
        ? ((metadata as Record<string, unknown>).filters as Record<string, unknown>).run_id
        : undefined);

    return typeof metadataRunId === "string" && metadataRunId.trim()
      ? metadataRunId
      : undefined;
  };

  const renderIdText = (
    value: string | undefined,
    fallback: string,
    maxWidthClass: string,
  ) => {
    const displayValue = value || fallback;
    const textNode = (
      <span
        className={`block ${maxWidthClass} truncate`}
        title={value || undefined}
      >
        {displayValue}
      </span>
    );

    if (!value) {
      return textNode;
    }

    return (
      <Tooltip>
        <TooltipTrigger asChild>{textNode}</TooltipTrigger>
        <TooltipContent
          side="top"
          align="start"
          className="max-w-[420px] break-all font-mono text-xs"
        >
          {value}
        </TooltipContent>
      </Tooltip>
    );
  };

  const renderContentText = (
    value: string | undefined,
    fallback: string,
    maxWidthClass: string,
  ) => {
    const rawValue = value?.trim() ? value : undefined;
    const maxLength = 120;
    const displayValue = rawValue
      ? (rawValue.length > maxLength
        ? `${rawValue.slice(0, maxLength)}...`
        : rawValue)
      : fallback;

    const textNode = (
      <span
        className={`block ${maxWidthClass} truncate text-sm leading-snug`}
        title={rawValue || undefined}
      >
        {displayValue}
      </span>
    );

    if (!rawValue) {
      return textNode;
    }

    return (
      <Tooltip>
        <TooltipTrigger asChild>{textNode}</TooltipTrigger>
        <TooltipContent
          side="top"
          align="start"
          className="max-w-[520px] break-all text-xs"
        >
          {rawValue}
        </TooltipContent>
      </Tooltip>
    );
  };

  if (error) {
    return (
      <div className="p-6">
        <Card className="border-destructive/50 bg-destructive/5">
          <CardHeader>
            <CardTitle className="text-destructive flex items-center gap-2">
              {t("memories.error")}
            </CardTitle>
            <CardDescription className="text-destructive/80">
              {(error as Error).message}
            </CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-4 space-y-6 max-w-7xl mx-auto">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Database className="text-primary" />
            {t("memories.title")}
          </h1>
          <p className="text-muted-foreground text-sm">
            {t("memories.subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {user_id && (
            <Badge variant="outline" className="gap-1 px-2 py-1">
              <User size={12} /> {user_id}
            </Badge>
          )}
          <Button variant="outline" size="sm" onClick={handleRefresh} disabled={isRefreshing}>
            <RefreshCcw
              className={`size-4 mr-2 ${isRefreshing ? "animate-spin" : ""}`}
            />
            {isRefreshing ? t("dashboard.refreshing") : t("memories.refresh")}
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div className="relative">
                <User className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground size-4" />
                <Input
                  placeholder={t("memories.filterByUserId")}
                  className="pl-9 h-9"
                  value={userIdInput}
                  onChange={(e) => setUserIdInput(e.target.value)}
                  onKeyPress={handleKeyPress}
                />
              </div>
              <div className="relative">
                <Database className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground size-4" />
                <Input
                  placeholder={t("memories.filterByAgentId")}
                  className="pl-9 h-9"
                  value={agentIdInput}
                  onChange={(e) => setAgentIdInput(e.target.value)}
                  onKeyPress={handleKeyPress}
                />
              </div>
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground size-4" />
                <Input
                  placeholder={t("memories.filterByContent")}
                  className="pl-9 h-9"
                  value={searchInput}
                  onChange={(e) => setSearchInput(e.target.value)}
                  onKeyPress={handleKeyPress}
                />
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Select
                value={time_range}
                onValueChange={(value) => updateSearchParams({ time_range: value })}
              >
                <SelectTrigger className="w-40 h-9" aria-label={t("memories.timeRange.label")}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="7d">{t("memories.timeRange.last7days")}</SelectItem>
                  <SelectItem value="30d">{t("memories.timeRange.last30days")}</SelectItem>
                  <SelectItem value="90d">{t("memories.timeRange.last90days")}</SelectItem>
                  <SelectItem value="all">{t("memories.timeRange.allTime")}</SelectItem>
                </SelectContent>
              </Select>
              <Select
                value={effectiveSortBy}
                onValueChange={(value) =>
                  updateSearchParams({ sort_by: value as SortField }, false)
                }
              >
                <SelectTrigger className="w-40 h-9" aria-label={t("memories.sort.label")}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {hasQuery && (
                    <SelectItem value="score">{t("memories.sort.score")}</SelectItem>
                  )}
                  <SelectItem value="created_at">{t("memories.sort.createdAt")}</SelectItem>
                  <SelectItem value="updated_at">{t("memories.sort.updatedAt")}</SelectItem>
                </SelectContent>
              </Select>
              <Button
                variant="outline"
                size="sm"
                className="h-9 gap-2"
                onClick={() =>
                  updateSearchParams({ order: order === "desc" ? "asc" : "desc" }, false)
                }
                title={order === "desc" ? t("memories.sort.desc") : t("memories.sort.asc")}
              >
                {order === "desc" ? <ArrowDown className="size-4" /> : <ArrowUp className="size-4" />}
                {order === "desc" ? t("memories.sort.desc") : t("memories.sort.asc")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="h-9 gap-2"
                onClick={handleFilter}
                disabled={isFiltering}
              >
                <Filter className={`size-4 ${isFiltering ? "animate-pulse" : ""}`} />
                {isFiltering ? t("memories.filtering") : t("memories.applyFilters")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-9 gap-2"
                onClick={handleClearFilters}
              >
                {t("memories.clearAllFilters")}
              </Button>
              {hasQuery && (
                <Badge variant="secondary" className="gap-1 ml-auto">
                  <Search size={12} /> {t("memories.search.modeHint")}
                </Badge>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="rounded-md border overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/50">
                  <TableHead className="w-[120px]">{t("memories.columns.userId")}</TableHead>
                  <TableHead className="w-[120px]">{t("memories.columns.agentId")}</TableHead>
                  <TableHead>{t("memories.columns.content")}</TableHead>
                  {isSearchMode && (
                    <TableHead className="w-[80px] hidden md:table-cell">
                      {t("memories.columns.score")}
                    </TableHead>
                  )}
                  <TableHead className="hidden md:table-cell">
                    {t("memories.columns.metadata")}
                  </TableHead>
                  <TableHead
                    className="hidden lg:table-cell cursor-pointer select-none"
                    onClick={handleSortByCreatedAt}
                  >
                    <span className="inline-flex items-center gap-1">
                      {t("memories.columns.createdAt")}
                      {effectiveSortBy === "created_at" ? (
                        order === "desc" ? (
                          <ArrowDown className="size-3" />
                        ) : (
                          <ArrowUp className="size-3" />
                        )
                      ) : (
                        <ArrowUpDown className="size-3 opacity-50" />
                      )}
                    </span>
                  </TableHead>
                  <TableHead className="w-[50px]"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  Array.from({ length: 5 }).map((_, i) => (
                    <TableRow key={i}>
                      <TableCell colSpan={isSearchMode ? 7 : 6} className="h-16 text-center">
                        <div className="flex items-center justify-center gap-2 text-muted-foreground">
                          <RefreshCcw className="size-4 animate-spin" />
                          {t("memories.loading")}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                ) : memories.length > 0 ? (
                  memories.map((memory) => (
                    <TableRow
                      key={memory.id}
                      className="group cursor-pointer hover:bg-accent/30 transition-colors"
                      onClick={() => setSelectedMemory(memory)}
                    >
                      <TableCell className="text-xs font-mono text-muted-foreground">
                        {renderIdText(memory.user_id, "-", "max-w-[110px]")}
                      </TableCell>
                      <TableCell className="text-xs font-mono text-muted-foreground">
                        {renderIdText(memory.agent_id, "-", "max-w-[110px]")}
                      </TableCell>
                      <TableCell className="w-[300px] lg:w-[500px]">
                        {renderContentText(memory.content, "-", "max-w-[280px] lg:max-w-[480px]")}
                      </TableCell>
                      {isSearchMode && (
                        <TableCell className="hidden md:table-cell text-xs font-mono">
                          {typeof memory.score === "number" ? memory.score.toFixed(3) : "-"}
                        </TableCell>
                      )}
                      <TableCell className="hidden md:table-cell">
                        <div className="flex flex-wrap gap-1">
                          {Object.entries(memory.metadata || {})
                            .slice(0, 2)
                            .map(([k, v]) => (
                              <Badge
                                key={k}
                                variant="outline"
                                className="text-[9px] font-normal py-0"
                              >
                                {k}: {String(v)}
                              </Badge>
                            ))}
                          {Object.keys(memory.metadata || {}).length > 2 && (
                            <span className="text-[9px] text-muted-foreground">
                              ...
                            </span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="hidden lg:table-cell text-xs text-muted-foreground">
                        <div className="flex items-center gap-1">
                          <Calendar size={12} />
                          {memory.created_at
                            ? new Date(memory.created_at).toLocaleDateString()
                            : "-"}
                        </div>
                      </TableCell>
                      <TableCell onClick={(e) => e.stopPropagation()}>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="size-8"
                            >
                              <MoreHorizontal className="size-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuLabel>{t("memories.columns.actions")}</DropdownMenuLabel>
                            <DropdownMenuItem
                              onClick={() => setSelectedMemory(memory)}
                            >
                              <Database className="size-4 mr-2" />
                              {t("memories.actions.viewDetails")}
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              className="text-destructive focus:text-destructive"
                              onClick={(e) => {
                                e.stopPropagation();
                                deleteMutation.mutate(memory.id);
                              }}
                            >
                              <Trash2 className="size-4 mr-2" />
                              {t("memories.actions.delete")}
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              onSelect={(e) => {
                                e.preventDefault();
                                e.stopPropagation();
                                const json = JSON.stringify(memory, null, 2);
                                copyText(json)
                                  .then(() => {
                                    toast.success(t("memories.actions.jsonCopied"));
                                  })
                                  .catch((err) => {
                                    console.error("Copy JSON failed:", err);
                                    toast.error(t("memories.actions.jsonCopyFailed"));
                                    window.prompt(
                                      "Clipboard unavailable. Please copy manually (Ctrl/Cmd + C).",
                                      json,
                                    );
                                  });
                              }}
                            >
                              {t("memories.actions.copyJson")}
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell
                      colSpan={isSearchMode ? 7 : 6}
                      className="h-32 text-center text-muted-foreground italic"
                    >
                      {t("memories.noMemories")}
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>

          <div className="flex items-center justify-between mt-4">
            <p className="text-xs text-muted-foreground">
              {t("memories.showing", { count: memories.length, total })}
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() =>
                  navigate({
                    search: (prev: any) => ({ ...prev, page: page - 1 }),
                  })
                }
              >
                <ChevronLeft className="size-4 mr-1" />
                {t("memories.prev")}
              </Button>
              <span className="text-xs font-medium">
                {t("memories.page", { page, total: totalPages || 1 })}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages}
                onClick={() =>
                  navigate({
                    search: (prev: any) => ({ ...prev, page: page + 1 }),
                  })
                }
              >
                {t("memories.next")}
                <ChevronRight className="size-4 ml-1" />
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Sheet
        open={!!selectedMemory}
        onOpenChange={(open) => !open && setSelectedMemory(null)}
      >
        <SheetContent className="sm:max-w-xl overflow-y-auto p-6">
          <SheetHeader className="space-y-2">
            <SheetTitle>{t("memories.detail.title")}</SheetTitle>
            <SheetDescription>{t("memories.detail.id")}: {selectedMemory?.memory_id || selectedMemory?.id}</SheetDescription>
          </SheetHeader>
          {selectedMemory && (
            <div className="mt-6 space-y-6 px-1">
              <div className="space-y-2">
                <h3 className="text-sm font-medium text-muted-foreground">
                  {t("memories.detail.content")}
                </h3>
                <p className="text-sm bg-muted p-3 rounded-md whitespace-pre-wrap leading-relaxed">
                  {selectedMemory.content}
                </p>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">{t("memories.detail.category")}</p>
                  <Badge variant="secondary">
                    {selectedMemory.category || "unknown"}
                  </Badge>
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">{t("memories.detail.createdAt")}</p>
                  <p className="text-sm">
                    {selectedMemory.created_at
                      ? new Date(selectedMemory.created_at).toLocaleString()
                      : t("memories.detail.none")}
                  </p>
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">{t("memories.detail.userId")}</p>
                  <p className="text-sm font-mono">
                    {renderIdText(
                      selectedMemory.user_id,
                      t("memories.detail.none"),
                      "max-w-full",
                    )}
                  </p>
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">{t("memories.detail.agentId")}</p>
                  <p className="text-sm font-mono">
                    {renderIdText(selectedMemory.agent_id, "NULL", "max-w-full")}
                  </p>
                </div>
              </div>

              <div className="space-y-1">
                <p className="text-xs text-muted-foreground">{t("memories.detail.runId")}</p>
                <p className="text-sm font-mono">
                  {getDisplayRunId(selectedMemory) || t("memories.detail.none")}
                </p>
              </div>

              <div className="space-y-2">
                <h3 className="text-sm font-medium">{t("memories.detail.metadata")}</h3>
                <div className="bg-muted p-3 rounded-md overflow-x-auto">
                  <pre className="text-xs">
                    {JSON.stringify(selectedMemory.metadata, null, 2)}
                  </pre>
                </div>
              </div>
            </div>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
