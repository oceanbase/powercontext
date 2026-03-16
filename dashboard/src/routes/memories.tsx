import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
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
import { useState } from "react";
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

export const Route = createFileRoute("/memories")({
  validateSearch: (search: Record<string, unknown>) => {
    return {
      user_id: search.user_id as string | undefined,
      agent_id: search.agent_id as string | undefined,
      page: (search.page as number) || 1,
    };
  },
  component: MemoriesPage,
});

const LIMIT = 20;

function MemoriesPage() {
  const { t } = useTranslation();
  const { user_id, agent_id, page } = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const [searchInput, setSearchInput] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [userIdInput, setUserIdInput] = useState("");
  const [userIdFilterTerm, setUserIdFilterTerm] = useState("");
  const [agentIdInput, setAgentIdInput] = useState("");
  const [agentIdFilterTerm, setAgentIdFilterTerm] = useState("");
  const [selectedMemory, setSelectedMemory] = useState<Memory | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isFiltering, setIsFiltering] = useState(false);
  const queryClient = useQueryClient();

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["memories", user_id, agent_id, page, searchTerm],
    queryFn: () =>
      api.getMemories({
        user_id,
        agent_id,
        limit: LIMIT,
        offset: (page - 1) * LIMIT,
      }),
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

  const memories = data?.memories || [];
  const total = data?.total || 0;
  const totalPages = Math.ceil(total / LIMIT);

  const filteredMemories = memories.filter((m) => {
    const contentMatch = !searchTerm || 
      m.content.toLowerCase().includes(searchTerm.toLowerCase());
    const userIdMatch = !userIdFilterTerm || 
      m.user_id?.toLowerCase().includes(userIdFilterTerm.toLowerCase());
    const agentIdMatch = !agentIdFilterTerm || 
      m.agent_id?.toLowerCase().includes(agentIdFilterTerm.toLowerCase());
    return contentMatch && userIdMatch && agentIdMatch;
  });

  const handleFilter = async () => {
    setIsFiltering(true);
    try {
    const normalizedSearch = searchInput.trim();
    const normalizedUserId = userIdInput.trim();
    const normalizedAgentId = agentIdInput.trim();

    setSearchTerm(normalizedSearch);
    setUserIdFilterTerm(normalizedUserId);
    setAgentIdFilterTerm(normalizedAgentId);
      if (page !== 1) {
        await navigate({
          search: (prev: any) => ({ ...prev, page: 1 }),
        });
      }
      // Give a brief moment for the UI to update
      await new Promise(resolve => setTimeout(resolve, 300));
    toast.success(t("memories.toast.filterApplied"));
    } catch (error) {
      toast.error(t("common.error"), {
        description: t("common.tryAgain"),
      });
    } finally {
      setIsFiltering(false);
    }
  };

  const handleClearFilters = async () => {
    setSearchInput("");
    setUserIdInput("");
    setAgentIdInput("");
    setSearchTerm("");
    setUserIdFilterTerm("");
    setAgentIdFilterTerm("");
    if (page !== 1) {
      await navigate({
        search: (prev: any) => ({ ...prev, page: 1 }),
      });
    }
    toast.success(t("memories.toast.filterCleared"));
  };

  const handleKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      handleFilter();
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

  const getDisplayRunId = (memory: Memory): string | undefined => {
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
            <div className="flex items-center gap-2">
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
                  <TableHead className="hidden md:table-cell">
                    {t("memories.columns.metadata")}
                  </TableHead>
                  <TableHead className="hidden lg:table-cell">
                    {t("memories.columns.createdAt")}
                  </TableHead>
                  <TableHead className="w-[50px]"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  Array.from({ length: 5 }).map((_, i) => (
                    <TableRow key={i}>
                      <TableCell colSpan={6} className="h-16 text-center">
                        <div className="flex items-center justify-center gap-2 text-muted-foreground">
                          <RefreshCcw className="size-4 animate-spin" />
                          {t("memories.loading")}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                ) : filteredMemories.length > 0 ? (
                  filteredMemories.map((memory) => (
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
                          {new Date(memory.created_at).toLocaleDateString()}
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
                      colSpan={6}
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
              {searchTerm
                ? `${filteredMemories.length} filtered results from page ${page}`
                : t("memories.showing", { count: memories.length, total })}
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
                    {new Date(selectedMemory.created_at).toLocaleString()}
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
