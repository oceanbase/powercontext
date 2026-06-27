// @vitest-environment jsdom

import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

type SearchState = {
	user_id?: string;
	agent_id?: string;
	run_id?: string;
	event_type?: string;
	q?: string;
	cursor?: string;
	page: number;
	time_range: string;
	order: "asc" | "desc";
};

const routerMocks = vi.hoisted(() => ({
	search: {
		page: 1,
		time_range: "30d",
		order: "desc" as const,
	} as SearchState,
	navigate: vi.fn(async (_options?: unknown) => undefined),
}));

const queryMocks = vi.hoisted(() => ({
	stats: undefined as unknown,
	sessions: undefined as unknown,
	timeline: undefined as unknown,
}));

const messages: Record<string, string> = {
	"sessions.title": "Sessions",
	"sessions.subtitle": "Inspect coding-agent memory timelines.",
	"sessions.filters": "Filters",
	"sessions.filterByUserId": "Filter by User ID...",
	"sessions.filterByAgentId": "Filter by Agent ID...",
	"sessions.filterByRunId": "Filter by Run ID...",
	"sessions.filterByContent": "Search timeline...",
	"sessions.applyFilters": "Apply",
	"sessions.clearAllFilters": "Clear",
	"sessions.loading": "Loading...",
	"sessions.noSessions": "No sessions found.",
	"sessions.noEvents": "No timeline events found.",
	"sessions.none": "None",
	"sessions.error": "Error loading sessions",
	"sessions.prev": "Prev",
	"sessions.next": "Next",
	"sessions.page": "Page {{page}} of {{total}}",
	"sessions.showingSessions": "Showing {{count}} of {{total}} sessions",
	"sessions.precision.title": "Snapshot precision.",
	"sessions.precision.body":
		"Timeline rows are projected from current memory records until an event log store is available.",
	"sessions.timeRange.last7days": "Last 7 days",
	"sessions.timeRange.last30days": "Last 30 days",
	"sessions.timeRange.last90days": "Last 90 days",
	"sessions.timeRange.allTime": "All time",
	"sessions.stats.sessions": "Sessions",
	"sessions.stats.events": "Events",
	"sessions.stats.changed": "Changed Memories",
	"sessions.stats.noopRate": "No-op Rate",
	"sessions.sessionTable.title": "Session List",
	"sessions.columns.runId": "Run ID",
	"sessions.columns.userAgent": "User / Agent",
	"sessions.columns.lastSeen": "Last Seen",
	"sessions.columns.events": "Events",
	"sessions.timeline.title": "Timeline",
	"sessions.timeline.allRuns": "All runs",
	"sessions.timeline.allEvents": "All events",
	"sessions.timeline.firstPage": "First page",
	"sessions.detail.title": "Event Detail",
	"sessions.detail.preview": "Preview",
	"sessions.detail.memoryId": "Memory ID",
	"sessions.detail.agentId": "Agent ID",
	"sessions.detail.metadata": "Metadata",
	"sessions.toast.filterApplied": "Filter applied",
	"sessions.toast.filterCleared": "Filters cleared",
	"sessions.toast.refreshed": "Sessions refreshed",
};

function t(key: string, values?: Record<string, string | number>) {
	let value = messages[key] ?? key;
	for (const [name, replacement] of Object.entries(values ?? {})) {
		value = value.replace(`{{${name}}}`, String(replacement));
	}
	return value;
}

vi.mock("@tanstack/react-router", () => ({
	createFileRoute: (path: string) => (options: Record<string, unknown>) => ({
		...options,
		options,
		fullPath: path,
		useSearch: () => routerMocks.search,
	}),
	lazyRouteComponent: () => () => null,
	useNavigate: () => routerMocks.navigate,
}));

vi.mock("@tanstack/react-query", () => ({
	useQuery: vi.fn(({ queryKey }: { queryKey: string[] }) => {
		const key = queryKey[0];
		const data =
			key === "session-stats"
				? queryMocks.stats
				: key === "sessions"
					? queryMocks.sessions
					: queryMocks.timeline;
		return {
			data,
			error: null,
			isLoading: false,
			refetch: vi.fn(async () => ({ data })),
		};
	}),
}));

vi.mock("react-i18next", () => ({
	useTranslation: () => ({ t }),
}));

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
	},
}));

import { SessionsPage } from "./sessions";

const defaultStats = {
	total_sessions: 1,
	total_events: 2,
	changed_memories: 1,
	no_op_rate: 0.5,
	precision: "memory_snapshot",
	capabilities: { memory_snapshot: true, event_log: false },
};

const defaultSessions = {
	sessions: [
		{
			run_id: "run-1",
			user_id: "user-1",
			agent_id: "agent-1",
			first_seen: "2026-06-20T10:00:00Z",
			last_seen: "2026-06-20T10:05:00Z",
			event_count: 2,
			memory_count: 1,
			latest_preview: "latest session preview",
			precision: "memory_snapshot",
		},
	],
	total: 1,
	limit: 20,
	offset: 0,
	precision: "memory_snapshot",
	capabilities: { memory_snapshot: true, event_log: false },
};

const defaultTimeline = {
	events: [
		{
			event_id: "event-1",
			occurred_at: "2026-06-20T10:03:00Z",
			run_id: "run-1",
			user_id: "user-1",
			agent_id: "agent-1",
			memory_id: "mem-1",
			event_type: "command_result",
			pipeline_mode: "simple",
			content_preview: "pytest failed",
			metadata: { nested: "ok" },
			precision: "memory_snapshot",
		},
	],
	total: 1,
	limit: 20,
	next_cursor: "cursor-2",
	order: "desc",
	precision: "memory_snapshot",
	capabilities: { memory_snapshot: true, event_log: false },
};

function resetData() {
	queryMocks.stats = defaultStats;
	queryMocks.sessions = defaultSessions;
	queryMocks.timeline = defaultTimeline;
	routerMocks.search = {
		page: 1,
		time_range: "30d",
		order: "desc",
	};
}

function renderSessions(search: Partial<SearchState> = {}) {
	routerMocks.search = {
		page: 1,
		time_range: "30d",
		order: "desc",
		...search,
	};
	return render(<SessionsPage />);
}

function latestSearchPatch() {
	const call = routerMocks.navigate.mock.calls.at(-1)?.[0];
	if (!call || typeof call !== "object" || !("search" in call)) {
		throw new Error("Last navigate call did not include a search updater");
	}
	const search = (call as { search: (previous: SearchState) => SearchState })
		.search;
	return search({
		page: 3,
		time_range: "30d",
		order: "desc",
		cursor: "old-cursor",
	});
}

function buttonContaining(text: string) {
	const button = screen
		.getAllByRole("button")
		.find((item) => item.textContent?.includes(text));
	if (!button) {
		throw new Error(`Button containing ${text} was not found`);
	}
	return button;
}

afterEach(() => {
	cleanup();
	resetData();
	routerMocks.navigate.mockClear();
});

describe("Sessions dashboard route", () => {
	it("renders empty session and timeline states with snapshot precision", () => {
		queryMocks.stats = {
			...defaultStats,
			total_sessions: 0,
			total_events: 0,
			changed_memories: 0,
			no_op_rate: 0,
		};
		queryMocks.sessions = {
			...defaultSessions,
			sessions: [],
			total: 0,
		};
		queryMocks.timeline = {
			...defaultTimeline,
			events: [],
			total: 0,
			next_cursor: undefined,
		};

		renderSessions();

		expect(screen.getByText("Snapshot precision.")).toBeTruthy();
		expect(screen.getByText("No sessions found.")).toBeTruthy();
		expect(screen.getByText("No timeline events found.")).toBeTruthy();
		expect(screen.getByText("Showing 0 of 0 sessions")).toBeTruthy();
	});

	it("does not show the snapshot banner for event-log precision", () => {
		queryMocks.stats = {
			...defaultStats,
			precision: "event_log",
			capabilities: { memory_snapshot: false, event_log: true },
		};
		queryMocks.timeline = {
			...defaultTimeline,
			precision: "event_log",
			capabilities: { memory_snapshot: false, event_log: true },
		};

		renderSessions();

		expect(screen.queryByText("Snapshot precision.")).toBeNull();
		expect(screen.getByText("Session List")).toBeTruthy();
	});

	it("applies user, agent, run, and content filters through route search", async () => {
		renderSessions({ page: 3, cursor: "old-cursor" });

		fireEvent.change(screen.getByPlaceholderText("Filter by User ID..."), {
			target: { value: "user-2" },
		});
		fireEvent.change(screen.getByPlaceholderText("Filter by Agent ID..."), {
			target: { value: "agent-2" },
		});
		fireEvent.change(screen.getByPlaceholderText("Filter by Run ID..."), {
			target: { value: "run-2" },
		});
		fireEvent.change(screen.getByPlaceholderText("Search timeline..."), {
			target: { value: "failed" },
		});
		fireEvent.click(screen.getByRole("button", { name: /apply/i }));

		await waitFor(() => expect(routerMocks.navigate).toHaveBeenCalled());
		expect(latestSearchPatch()).toMatchObject({
			user_id: "user-2",
			agent_id: "agent-2",
			run_id: "run-2",
			q: "failed",
			cursor: undefined,
			page: 1,
		});
	});

	it("handles session selection, cursor pagination, and detail sheet rendering", async () => {
		queryMocks.sessions = {
			...defaultSessions,
			total: 45,
		};

		renderSessions();

		expect(screen.getByText("Page 1 of 3")).toBeTruthy();
		fireEvent.click(screen.getAllByText("run-1")[0]);

		await waitFor(() => expect(routerMocks.navigate).toHaveBeenCalled());
		expect(latestSearchPatch()).toMatchObject({
			run_id: "run-1",
			cursor: undefined,
			page: 1,
		});

		routerMocks.navigate.mockClear();
		const nextButtons = screen.getAllByRole("button", { name: "Next" });
		fireEvent.click(nextButtons[nextButtons.length - 1]);

		await waitFor(() => expect(routerMocks.navigate).toHaveBeenCalled());
		expect(latestSearchPatch()).toMatchObject({ cursor: "cursor-2" });

		fireEvent.click(buttonContaining("pytest failed"));

		expect(screen.getByText("Metadata")).toBeTruthy();
		expect(
			screen.getByText((content) => content.includes('"nested": "ok"')),
		).toBeTruthy();
	});
});
