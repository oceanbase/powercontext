import { useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
	Activity,
	ArrowDown,
	ArrowUp,
	Clock,
	Filter,
	GitBranch,
	ListTree,
	RefreshCcw,
	Search,
	Sparkles,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
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
import { api, type SessionSummary, type TimelineEvent } from "../lib/api";

type SortOrder = "asc" | "desc";

export const Route = createFileRoute("/sessions")({
	validateSearch: (search: Record<string, unknown>) => {
		const rawTimeRange = (search.time_range as string) || "30d";
		const allowedRanges = new Set(["7d", "30d", "90d", "all"]);
		const time_range = allowedRanges.has(rawTimeRange) ? rawTimeRange : "30d";

		const rawOrder = (search.order as string) || "desc";
		const order: SortOrder = rawOrder === "asc" ? "asc" : "desc";

		const page = Number(search.page ?? 1);

		return {
			user_id: search.user_id as string | undefined,
			agent_id: search.agent_id as string | undefined,
			run_id: search.run_id as string | undefined,
			event_type: search.event_type as string | undefined,
			q: search.q as string | undefined,
			cursor: search.cursor as string | undefined,
			page: Number.isFinite(page) && page > 0 ? page : 1,
			time_range,
			order,
		};
	},
	component: SessionsPage,
});

const SESSION_LIMIT = 20;
const TIMELINE_LIMIT = 20;

function formatDate(value?: string) {
	if (!value) return "None";
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return value;
	return date.toLocaleString();
}

function formatRate(value: number) {
	return `${Math.round(value * 1000) / 10}%`;
}

function StatCard({
	title,
	value,
	icon: Icon,
}: {
	title: string;
	value: string | number;
	icon: typeof Activity;
}) {
	return (
		<Card>
			<CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
				<CardTitle className="text-sm font-medium">{title}</CardTitle>
				<Icon className="size-4 text-muted-foreground" />
			</CardHeader>
			<CardContent>
				<div className="text-2xl font-semibold tabular-nums">{value}</div>
			</CardContent>
		</Card>
	);
}

export function SessionsPage() {
	const { t } = useTranslation();
	const {
		user_id,
		agent_id,
		run_id,
		event_type,
		q,
		cursor,
		page,
		time_range,
		order,
	} = Route.useSearch();
	const navigate = useNavigate({ from: Route.fullPath });
	const [userIdInput, setUserIdInput] = useState(user_id ?? "");
	const [agentIdInput, setAgentIdInput] = useState(agent_id ?? "");
	const [runIdInput, setRunIdInput] = useState(run_id ?? "");
	const [queryInput, setQueryInput] = useState(q ?? "");
	const [selectedEvent, setSelectedEvent] = useState<TimelineEvent | null>(
		null,
	);
	const [isRefreshing, setIsRefreshing] = useState(false);

	useEffect(() => {
		setUserIdInput(user_id ?? "");
	}, [user_id]);
	useEffect(() => {
		setAgentIdInput(agent_id ?? "");
	}, [agent_id]);
	useEffect(() => {
		setRunIdInput(run_id ?? "");
	}, [run_id]);
	useEffect(() => {
		setQueryInput(q ?? "");
	}, [q]);

	const baseFilters = {
		user_id,
		agent_id,
		run_id,
		time_range: time_range === "all" ? undefined : time_range,
	};

	const statsQuery = useQuery({
		queryKey: ["session-stats", user_id, agent_id, run_id, time_range],
		queryFn: () => api.getSessionStats(baseFilters),
		retry: false,
	});

	const sessionsQuery = useQuery({
		queryKey: ["sessions", user_id, agent_id, run_id, page, time_range, order],
		queryFn: () =>
			api.getSessions({
				...baseFilters,
				limit: SESSION_LIMIT,
				offset: (page - 1) * SESSION_LIMIT,
				sort_by: "last_seen",
				order,
			}),
		retry: false,
	});

	const timelineQuery = useQuery({
		queryKey: [
			"timeline",
			user_id,
			agent_id,
			run_id,
			event_type,
			q,
			cursor,
			time_range,
			order,
		],
		queryFn: () =>
			api.getTimeline({
				...baseFilters,
				event_type,
				q,
				cursor,
				limit: TIMELINE_LIMIT,
				order,
				include_source: false,
			}),
		retry: false,
	});

	const stats = statsQuery.data;
	const sessions = sessionsQuery.data?.sessions ?? [];
	const sessionTotal = sessionsQuery.data?.total ?? 0;
	const sessionPages = Math.max(1, Math.ceil(sessionTotal / SESSION_LIMIT));
	const events = timelineQuery.data?.events ?? [];
	const precision =
		stats?.precision ?? timelineQuery.data?.precision ?? "memory_snapshot";

	const updateSearchParams = async (
		patch: Partial<{
			user_id: string | undefined;
			agent_id: string | undefined;
			run_id: string | undefined;
			event_type: string | undefined;
			q: string | undefined;
			cursor: string | undefined;
			page: number;
			time_range: string;
			order: SortOrder;
		}>,
	) => {
		await navigate({
			search: (prev) => ({
				...prev,
				...patch,
			}),
		});
	};

	const applyFilters = async () => {
		await updateSearchParams({
			user_id: userIdInput.trim() || undefined,
			agent_id: agentIdInput.trim() || undefined,
			run_id: runIdInput.trim() || undefined,
			q: queryInput.trim() || undefined,
			cursor: undefined,
			page: 1,
		});
		toast.success(t("sessions.toast.filterApplied"));
	};

	const clearFilters = async () => {
		setUserIdInput("");
		setAgentIdInput("");
		setRunIdInput("");
		setQueryInput("");
		await updateSearchParams({
			user_id: undefined,
			agent_id: undefined,
			run_id: undefined,
			event_type: undefined,
			q: undefined,
			cursor: undefined,
			page: 1,
			time_range: "30d",
			order: "desc",
		});
		toast.success(t("sessions.toast.filterCleared"));
	};

	const selectSession = async (session: SessionSummary) => {
		setRunIdInput(session.run_id);
		await updateSearchParams({
			run_id: session.run_id,
			cursor: undefined,
			page: 1,
		});
	};

	const refresh = async () => {
		setIsRefreshing(true);
		try {
			await Promise.all([
				statsQuery.refetch(),
				sessionsQuery.refetch(),
				timelineQuery.refetch(),
			]);
			toast.success(t("sessions.toast.refreshed"));
		} finally {
			setIsRefreshing(false);
		}
	};

	const error = statsQuery.error || sessionsQuery.error || timelineQuery.error;

	return (
		<div className="p-4 space-y-6 max-w-7xl mx-auto w-full">
			<div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
				<div>
					<h1 className="text-3xl font-bold tracking-tight">
						{t("sessions.title")}
					</h1>
					<p className="text-muted-foreground">{t("sessions.subtitle")}</p>
				</div>
				<div className="flex items-center gap-2">
					<Select
						value={time_range}
						onValueChange={(value) =>
							updateSearchParams({
								time_range: value,
								cursor: undefined,
								page: 1,
							})
						}
					>
						<SelectTrigger className="w-[150px]">
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="7d">
								{t("sessions.timeRange.last7days")}
							</SelectItem>
							<SelectItem value="30d">
								{t("sessions.timeRange.last30days")}
							</SelectItem>
							<SelectItem value="90d">
								{t("sessions.timeRange.last90days")}
							</SelectItem>
							<SelectItem value="all">
								{t("sessions.timeRange.allTime")}
							</SelectItem>
						</SelectContent>
					</Select>
					<Button
						variant="outline"
						size="icon"
						onClick={refresh}
						disabled={isRefreshing}
					>
						<RefreshCcw
							className={`size-4 ${isRefreshing ? "animate-spin" : ""}`}
						/>
					</Button>
				</div>
			</div>

			{precision === "memory_snapshot" && (
				<div className="rounded-md border bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
					<span className="font-medium text-foreground">
						{t("sessions.precision.title")}
					</span>{" "}
					{t("sessions.precision.body")}
				</div>
			)}

			{error && (
				<Card className="border-destructive/50 bg-destructive/5">
					<CardHeader>
						<CardTitle className="text-destructive">
							{t("sessions.error")}
						</CardTitle>
						<CardDescription>{(error as Error).message}</CardDescription>
					</CardHeader>
				</Card>
			)}

			<div className="grid grid-cols-1 gap-4 md:grid-cols-4">
				<StatCard
					title={t("sessions.stats.sessions")}
					value={stats?.total_sessions ?? 0}
					icon={GitBranch}
				/>
				<StatCard
					title={t("sessions.stats.events")}
					value={stats?.total_events ?? 0}
					icon={Activity}
				/>
				<StatCard
					title={t("sessions.stats.changed")}
					value={stats?.changed_memories ?? 0}
					icon={Sparkles}
				/>
				<StatCard
					title={t("sessions.stats.noopRate")}
					value={formatRate(stats?.no_op_rate ?? 0)}
					icon={ListTree}
				/>
			</div>

			<Card>
				<CardHeader>
					<CardTitle className="flex items-center gap-2">
						<Filter className="size-5" />
						{t("sessions.filters")}
					</CardTitle>
				</CardHeader>
				<CardContent>
					<div className="grid grid-cols-1 gap-3 md:grid-cols-5">
						<Input
							value={userIdInput}
							onChange={(event) => setUserIdInput(event.target.value)}
							placeholder={t("sessions.filterByUserId")}
						/>
						<Input
							value={agentIdInput}
							onChange={(event) => setAgentIdInput(event.target.value)}
							placeholder={t("sessions.filterByAgentId")}
						/>
						<Input
							value={runIdInput}
							onChange={(event) => setRunIdInput(event.target.value)}
							placeholder={t("sessions.filterByRunId")}
						/>
						<Input
							value={queryInput}
							onChange={(event) => setQueryInput(event.target.value)}
							placeholder={t("sessions.filterByContent")}
						/>
						<div className="flex gap-2">
							<Button className="flex-1" onClick={applyFilters}>
								<Search className="mr-2 size-4" />
								{t("sessions.applyFilters")}
							</Button>
							<Button variant="outline" onClick={clearFilters}>
								{t("sessions.clearAllFilters")}
							</Button>
						</div>
					</div>
				</CardContent>
			</Card>

			<div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
				<Card>
					<CardHeader>
						<CardTitle>{t("sessions.sessionTable.title")}</CardTitle>
						<CardDescription>
							{t("sessions.showingSessions", {
								count: sessions.length,
								total: sessionTotal,
							})}
						</CardDescription>
					</CardHeader>
					<CardContent>
						<Table>
							<TableHeader>
								<TableRow>
									<TableHead>{t("sessions.columns.runId")}</TableHead>
									<TableHead>{t("sessions.columns.userAgent")}</TableHead>
									<TableHead>{t("sessions.columns.lastSeen")}</TableHead>
									<TableHead className="text-right">
										{t("sessions.columns.events")}
									</TableHead>
								</TableRow>
							</TableHeader>
							<TableBody>
								{sessions.length === 0 && (
									<TableRow>
										<TableCell
											colSpan={4}
											className="h-24 text-center text-muted-foreground"
										>
											{sessionsQuery.isLoading
												? t("sessions.loading")
												: t("sessions.noSessions")}
										</TableCell>
									</TableRow>
								)}
								{sessions.map((session) => (
									<TableRow
										key={session.run_id}
										className="cursor-pointer"
										onClick={() => selectSession(session)}
									>
										<TableCell>
											<div className="max-w-[220px] truncate font-medium">
												{session.run_id}
											</div>
											<div className="mt-1 max-w-[260px] truncate text-xs text-muted-foreground">
												{session.latest_preview || t("sessions.none")}
											</div>
										</TableCell>
										<TableCell className="text-sm">
											<div>{session.user_id || t("sessions.none")}</div>
											<div className="text-muted-foreground">
												{session.agent_id || t("sessions.none")}
											</div>
										</TableCell>
										<TableCell className="text-sm">
											{formatDate(session.last_seen)}
										</TableCell>
										<TableCell className="text-right tabular-nums">
											{session.event_count}
										</TableCell>
									</TableRow>
								))}
							</TableBody>
						</Table>
						<div className="mt-4 flex items-center justify-between">
							<div className="text-sm text-muted-foreground">
								{t("sessions.page", { page, total: sessionPages })}
							</div>
							<div className="flex gap-2">
								<Button
									variant="outline"
									size="sm"
									disabled={page <= 1}
									onClick={() => updateSearchParams({ page: page - 1 })}
								>
									{t("sessions.prev")}
								</Button>
								<Button
									variant="outline"
									size="sm"
									disabled={page >= sessionPages}
									onClick={() => updateSearchParams({ page: page + 1 })}
								>
									{t("sessions.next")}
								</Button>
							</div>
						</div>
					</CardContent>
				</Card>

				<Card>
					<CardHeader>
						<div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
							<div>
								<CardTitle>{t("sessions.timeline.title")}</CardTitle>
								<CardDescription>
									{run_id ? run_id : t("sessions.timeline.allRuns")}
								</CardDescription>
							</div>
							<div className="flex gap-2">
								<Select
									value={event_type ?? "all"}
									onValueChange={(value) =>
										updateSearchParams({
											event_type: value === "all" ? undefined : value,
											cursor: undefined,
										})
									}
								>
									<SelectTrigger className="w-[170px]">
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										<SelectItem value="all">
											{t("sessions.timeline.allEvents")}
										</SelectItem>
										<SelectItem value="command_result">
											command_result
										</SelectItem>
										<SelectItem value="tool_result">tool_result</SelectItem>
										<SelectItem value="session_start">session_start</SelectItem>
										<SelectItem value="observation_raw">
											observation_raw
										</SelectItem>
										<SelectItem value="none">none</SelectItem>
									</SelectContent>
								</Select>
								<Button
									variant="outline"
									size="icon"
									onClick={() =>
										updateSearchParams({
											order: order === "desc" ? "asc" : "desc",
											cursor: undefined,
										})
									}
								>
									{order === "desc" ? (
										<ArrowDown className="size-4" />
									) : (
										<ArrowUp className="size-4" />
									)}
								</Button>
							</div>
						</div>
					</CardHeader>
					<CardContent>
						<div className="space-y-3">
							{events.length === 0 && (
								<div className="flex min-h-40 items-center justify-center rounded-md border border-dashed text-sm text-muted-foreground">
									{timelineQuery.isLoading
										? t("sessions.loading")
										: t("sessions.noEvents")}
								</div>
							)}
							{events.map((event) => (
								<button
									key={`${event.event_id}-${event.memory_id ?? ""}-${event.occurred_at ?? ""}`}
									type="button"
									className="flex w-full gap-3 rounded-md border bg-background p-3 text-left transition-colors hover:bg-accent/50"
									onClick={() => setSelectedEvent(event)}
								>
									<div className="mt-1 flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
										<Clock className="size-4" />
									</div>
									<div className="min-w-0 flex-1">
										<div className="flex flex-wrap items-center gap-2">
											<Badge variant="outline">{event.event_type}</Badge>
											{event.pipeline_mode && (
												<Badge variant="secondary">{event.pipeline_mode}</Badge>
											)}
											<span className="text-xs text-muted-foreground">
												{formatDate(event.occurred_at)}
											</span>
										</div>
										<p className="mt-2 line-clamp-2 text-sm">
											{event.content_preview}
										</p>
										<div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
											<span>{event.run_id || t("sessions.none")}</span>
											<span>{event.memory_id || t("sessions.none")}</span>
										</div>
									</div>
								</button>
							))}
						</div>
						<div className="mt-4 flex justify-end gap-2">
							{cursor && (
								<Button
									variant="outline"
									size="sm"
									onClick={() => updateSearchParams({ cursor: undefined })}
								>
									{t("sessions.timeline.firstPage")}
								</Button>
							)}
							<Button
								variant="outline"
								size="sm"
								disabled={!timelineQuery.data?.next_cursor}
								onClick={() =>
									updateSearchParams({
										cursor: timelineQuery.data?.next_cursor,
									})
								}
							>
								{t("sessions.next")}
							</Button>
						</div>
					</CardContent>
				</Card>
			</div>

			<Sheet
				open={!!selectedEvent}
				onOpenChange={(open) => !open && setSelectedEvent(null)}
			>
				<SheetContent className="w-full overflow-y-auto sm:max-w-xl">
					<SheetHeader>
						<SheetTitle>
							{selectedEvent?.event_type ?? t("sessions.detail.title")}
						</SheetTitle>
						<SheetDescription>
							{formatDate(selectedEvent?.occurred_at)}
						</SheetDescription>
					</SheetHeader>
					{selectedEvent && (
						<div className="mt-6 space-y-5">
							<div className="space-y-2">
								<h3 className="text-sm font-medium">
									{t("sessions.detail.preview")}
								</h3>
								<p className="rounded-md border bg-muted/40 p-3 text-sm">
									{selectedEvent.content_preview || t("sessions.none")}
								</p>
							</div>
							<div className="grid grid-cols-2 gap-3 text-sm">
								<div>
									<div className="text-muted-foreground">
										{t("sessions.columns.runId")}
									</div>
									<div className="break-all font-medium">
										{selectedEvent.run_id || t("sessions.none")}
									</div>
								</div>
								<div>
									<div className="text-muted-foreground">
										{t("sessions.detail.memoryId")}
									</div>
									<div className="break-all font-medium">
										{selectedEvent.memory_id || t("sessions.none")}
									</div>
								</div>
								<div>
									<div className="text-muted-foreground">
										{t("sessions.columns.userAgent")}
									</div>
									<div className="break-all font-medium">
										{selectedEvent.user_id || t("sessions.none")}
									</div>
								</div>
								<div>
									<div className="text-muted-foreground">
										{t("sessions.detail.agentId")}
									</div>
									<div className="break-all font-medium">
										{selectedEvent.agent_id || t("sessions.none")}
									</div>
								</div>
							</div>
							<div className="space-y-2">
								<h3 className="text-sm font-medium">
									{t("sessions.detail.metadata")}
								</h3>
								<pre className="max-h-[420px] overflow-auto rounded-md border bg-muted/40 p-3 text-xs">
									{JSON.stringify(selectedEvent.metadata ?? {}, null, 2)}
								</pre>
							</div>
						</div>
					)}
				</SheetContent>
			</Sheet>
		</div>
	);
}
