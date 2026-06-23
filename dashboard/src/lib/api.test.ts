import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

function installFetch(data: unknown) {
	const fetchMock = vi.fn(
		async (_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> =>
			({
				ok: true,
				json: async () => ({
					success: true,
					data,
				}),
			}) as Response,
	);
	vi.stubGlobal("fetch", fetchMock);
	vi.stubGlobal("localStorage", {
		getItem: vi.fn(() => "test-key"),
		setItem: vi.fn(),
	});
	vi.stubGlobal("window", {
		location: { origin: "http://localhost" },
	});
	return fetchMock;
}

afterEach(() => {
	vi.unstubAllGlobals();
});

describe("session timeline API client", () => {
	it("normalizes session list responses and sends run_id filters", async () => {
		const fetchMock = installFetch({
			sessions: [
				{
					run_id: "run-1",
					event_count: 2,
					memory_count: 2,
					latest_preview: "pytest failed",
					precision: "memory_snapshot",
				},
			],
			total: 1,
			limit: 20,
			offset: 0,
			precision: "memory_snapshot",
			capabilities: { memory_snapshot: true },
		});

		const result = await api.getSessions({ run_id: "run-1", limit: 20 });

		expect(result.sessions).toHaveLength(1);
		expect(result.total).toBe(1);
		expect(result.precision).toBe("memory_snapshot");
		const [input, init] = fetchMock.mock.calls[0];
		const url = new URL(String(input));
		expect(url.pathname).toBe("/api/v1/memories/sessions");
		expect(url.searchParams.get("run_id")).toBe("run-1");
		expect(init?.headers).toMatchObject({
			"X-API-Key": "test-key",
		});
	});

	it("normalizes timeline pages with items fallback and next cursor", async () => {
		installFetch({
			items: [
				{
					event_id: "obs-1",
					event_type: "command_result",
					content_preview: "pytest failed",
					metadata: {},
					precision: "memory_snapshot",
				},
			],
			total_count: 1,
			limit: 20,
			next_cursor: "cursor-1",
			order: "desc",
			capabilities: { memory_snapshot: true },
		});

		const result = await api.getTimeline({ event_type: "command_result" });

		expect(result.events).toHaveLength(1);
		expect(result.total).toBe(1);
		expect(result.next_cursor).toBe("cursor-1");
		expect(result.events[0].event_type).toBe("command_result");
	});
});
