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
  const { user_id, agent_id, page } = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedMemory, setSelectedMemory] = useState<Memory | null>(null);
  const queryClient = useQueryClient();

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["memories", user_id, agent_id, page],
    queryFn: () =>
      api.getMemories({
        user_id,
        agent_id,
        limit: LIMIT,
        offset: (page - 1) * LIMIT,
      }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string | number) => api.deleteMemory(id),
    onSuccess: () => {
      toast.success("Memory deleted successfully");
      queryClient.invalidateQueries({ queryKey: ["memories"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
    onError: (err) => {
      toast.error(`Failed to delete memory: ${err.message}`);
    },
  });

  const memories = data?.memories || [];
  const total = data?.total || 0;
  const totalPages = Math.ceil(total / LIMIT);

  const filteredMemories = memories.filter(
    (m) =>
      m.content.toLowerCase().includes(searchTerm.toLowerCase()) ||
      m.category?.toLowerCase().includes(searchTerm.toLowerCase()),
  );

  if (error) {
    return (
      <div className="p-6">
        <Card className="border-destructive/50 bg-destructive/5">
          <CardHeader>
            <CardTitle className="text-destructive flex items-center gap-2">
              Error loading memories
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
            Memories
          </h1>
          <p className="text-muted-foreground text-sm">
            Manage and explore stored cognitive records.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {user_id && (
            <Badge variant="outline" className="gap-1 px-2 py-1">
              <User size={12} /> {user_id}
            </Badge>
          )}
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCcw
              className={`size-4 mr-2 ${isLoading ? "animate-spin" : ""}`}
            />
            Refresh
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-col md:flex-row md:items-center gap-4">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground size-4" />
              <Input
                placeholder="Filter by content or category..."
                className="pl-9"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" className="h-9 gap-2">
                <Filter className="size-4" />
                Filters
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="rounded-md border overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/50">
                  <TableHead className="w-[100px]">Category</TableHead>
                  <TableHead>Content</TableHead>
                  <TableHead className="hidden md:table-cell">
                    Metadata
                  </TableHead>
                  <TableHead className="hidden lg:table-cell">
                    Created At
                  </TableHead>
                  <TableHead className="w-[50px]"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  Array.from({ length: 5 }).map((_, i) => (
                    <TableRow key={i}>
                      <TableCell colSpan={5} className="h-16 text-center">
                        <div className="flex items-center justify-center gap-2 text-muted-foreground">
                          <RefreshCcw className="size-4 animate-spin" />
                          Loading memories...
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
                      <TableCell onClick={(e) => e.stopPropagation()}>
                        <Badge
                          variant="secondary"
                          className="font-mono text-[10px] capitalize"
                        >
                          {memory.category || "General"}
                        </Badge>
                      </TableCell>
                      <TableCell className="max-w-[300px] lg:max-w-[500px]">
                        <div className="flex flex-col gap-1">
                          <p className="text-sm line-clamp-2 leading-snug">
                            {memory.content}
                          </p>
                          {(memory.user_id || memory.agent_id) && (
                            <div className="flex gap-2 mt-1">
                              {memory.user_id && (
                                <span className="text-[10px] text-muted-foreground flex items-center gap-0.5">
                                  <User size={10} /> {memory.user_id}
                                </span>
                              )}
                            </div>
                          )}
                        </div>
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
                            <DropdownMenuLabel>Actions</DropdownMenuLabel>
                            <DropdownMenuItem
                              onClick={() => setSelectedMemory(memory)}
                            >
                              <Database className="size-4 mr-2" />
                              View Details
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              className="text-destructive focus:text-destructive"
                              onClick={(e) => {
                                e.stopPropagation();
                                deleteMutation.mutate(memory.id);
                              }}
                            >
                              <Trash2 className="size-4 mr-2" />
                              Delete Memory
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              onClick={(e) => {
                                e.stopPropagation();
                                const json = JSON.stringify(memory, null, 2);
                                navigator.clipboard.writeText(json);
                                toast.success("JSON copied to clipboard");
                              }}
                            >
                              Copy Raw JSON
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell
                      colSpan={5}
                      className="h-32 text-center text-muted-foreground italic"
                    >
                      No memories found.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>

          <div className="flex items-center justify-between mt-4">
            <p className="text-xs text-muted-foreground">
              Showing {filteredMemories.length} of {total} memories
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
                Prev
              </Button>
              <span className="text-xs font-medium">
                Page {page} of {totalPages || 1}
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
                Next
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
        <SheetContent className="sm:max-w-xl overflow-y-auto">
          <SheetHeader>
            <SheetTitle>Memory Details</SheetTitle>
            <SheetDescription>ID: {selectedMemory?.id}</SheetDescription>
          </SheetHeader>
          {selectedMemory && (
            <div className="mt-6 space-y-6">
              <div className="space-y-2">
                <h3 className="text-sm font-medium text-muted-foreground">
                  Content
                </h3>
                <p className="text-sm bg-muted p-3 rounded-md whitespace-pre-wrap leading-relaxed">
                  {selectedMemory.content}
                </p>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">Category</p>
                  <Badge variant="secondary">
                    {selectedMemory.category || "General"}
                  </Badge>
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">Created At</p>
                  <p className="text-sm">
                    {new Date(selectedMemory.created_at).toLocaleString()}
                  </p>
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">User ID</p>
                  <p className="text-sm font-mono">
                    {selectedMemory.user_id || "None"}
                  </p>
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">Agent ID</p>
                  <p className="text-sm font-mono">
                    {selectedMemory.agent_id || "None"}
                  </p>
                </div>
              </div>

              <div className="space-y-2">
                <h3 className="text-sm font-medium">Metadata</h3>
                <div className="bg-muted p-3 rounded-md overflow-x-auto">
                  <pre className="text-xs">
                    {JSON.stringify(selectedMemory.metadata, null, 2)}
                  </pre>
                </div>
              </div>

              {selectedMemory.run_id && (
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">Run ID</p>
                  <p className="text-sm font-mono">{selectedMemory.run_id}</p>
                </div>
              )}
            </div>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
