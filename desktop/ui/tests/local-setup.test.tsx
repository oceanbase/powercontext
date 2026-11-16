/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { StrictMode } from "react";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useLocalServer } from "../src/app/useLocalServer";
import { LocalServerSettings } from "../src/app/LocalServerSettings";
import { opsApi, subscribeOpsProgress } from "../src/shared/ops";
import { localText } from "../src/app/local-server-messages";
import type { Language } from "../src/app/messages";
import type {
  OpsComponents,
  OpsOperationReply,
  OpsPlanApproval,
  OpsReferences,
  OpsProgress,
} from "../src/generated/ipc";

vi.mock("../src/shared/ops", () => ({
  subscribeOpsProgress: vi.fn(),
  opsApi: {
    inspect: vi.fn(),
    references: vi.fn(),
    preflight: vi.fn(),
    apply: vi.fn(),
    resume: vi.fn(),
    operationStatus: vi.fn(),
    cancel: vi.fn(),
    connect: vi.fn(),
  },
}));
const missing: OpsComponents = {
  support: "supported",
  resources: "missing_executable",
  configuration: "missing_or_invalid",
  registration: "not_installed",
  managerOwnership: "not_loaded",
  manager: "inactive",
  liveness: "unreachable",
  readiness: "not_probed",
};
const installed: OpsComponents = {
  ...missing,
  resources: "present",
  configuration: "valid",
  registration: "installed",
  managerOwnership: "owned",
  manager: "active",
  liveness: "live",
  endpoint: "http://127.0.0.1:8123",
  installationId: "installation-a",
};
let facts: OpsComponents;
let references: OpsReferences;
let progress: (event: OpsProgress) => void;
let status: OpsOperationReply;
function result(
  outcome: OpsOperationReply["outcome"] = "succeeded",
  attempt = 1,
): OpsOperationReply {
  return {
    operationId: "operation-a",
    outcome,
    attempt,
    status: {
      operationId: "operation-a",
      attempt,
      phase: outcome === "pending" ? "executing" : "finished",
      executor: { observation: "exited" },
      executionState: outcome === "pending" ? "interrupted" : "finished",
      outcome,
      observation: { components: facts },
      history: [
        {
          attempt: 1,
          outcome:
            attempt > 1
              ? "cancelled"
              : outcome === "pending"
                ? undefined
                : outcome,
        },
        ...(attempt > 1 ? [{ attempt, outcome }] : []),
      ],
      recoveryActions: outcome === "succeeded" ? [] : ["inspect", "preflight"],
      ...(outcome === "pending"
        ? {}
        : { result: { outcome, components: facts, recoveryActions: [] } }),
    },
  };
}
function plan(
  operation: OpsPlanApproval["operation"] = "install",
): OpsPlanApproval {
  return {
    approvalId: "approval-a",
    planId: "plan-a",
    operation,
    options: {},
    target: {
      targetId: "personal",
      packageVersion: "1.4.2",
      pythonExecutable: "/program/runtime/python",
      envFile: "/config/server.env",
      dataDir: "/data/personal",
      endpoint: "http://127.0.0.1:8123",
      startOnLogin: true,
    },
    effects: {
      mayStart: operation !== "stop" && operation !== "remove_registration",
      preservesData: true,
    },
    ...(operation === "install"
      ? {
          installation: {
            classification: "missing",
            resources: "missing",
            database: "new",
            spaceRequired: 123456,
            verifyRunning: false,
            preserveStopped: false,
            registrationPresent: false,
          },
        }
      : {}),
  };
}
function Harness({
  language = "en",
  before = async () => true,
  connect = async () => {},
}: {
  language?: Language;
  before?: (plan: OpsPlanApproval) => Promise<boolean>;
  connect?: () => Promise<void>;
}) {
  const controller = useLocalServer();
  return (
    <LocalServerSettings
      controller={controller}
      language={language}
      onManageConnections={() => {}}
      onConnect={connect}
      onBeforeApply={before}
    />
  );
}
beforeEach(() => {
  vi.resetAllMocks();
  facts = missing;
  references = {
    bindingAvailable: true,
    loginStartChoice: true,
    operations: [],
  };
  status = result();
  vi.mocked(opsApi.inspect).mockImplementation(async () => ({
    outcome: "succeeded",
    components: facts,
  }));
  vi.mocked(opsApi.references).mockImplementation(async () => references);
  vi.mocked(subscribeOpsProgress).mockImplementation(async (callback) => {
    progress = callback;
    return () => {};
  });
  vi.mocked(opsApi.preflight).mockImplementation(async (input) => ({
    outcome: "succeeded",
    approval: { ...plan(input.operation), options: input.options },
  }));
  vi.mocked(opsApi.operationStatus).mockImplementation(async () => status);
  vi.mocked(opsApi.apply).mockImplementation(async () => {
    facts = installed;
    references = {
      ...references,
      operations: [
        {
          operationId: "operation-a",
          bindingStatus: "current",
          operation: "install",
          options: { httpPort: 8123, startOnLogin: true },
        },
      ],
    };
    status = result();
    return status;
  });
});
afterEach(cleanup);
async function open(language: Language = "en") {
  const user = userEvent.setup();
  render(<Harness language={language} />);
  await waitFor(() =>
    expect(screen.queryByText(localText(language, "loading"))).toBeNull(),
  );
  return user;
}
test.each(["en", "zh"] as const)(
  "setup previews actual locations and requires explicit approval (%s)",
  async (language) => {
    const user = await open(language);
    expect(opsApi.preflight).not.toHaveBeenCalled();
    expect(opsApi.apply).not.toHaveBeenCalled();
    await user.clear(screen.getByLabelText(localText(language, "port")));
    await user.type(screen.getByLabelText(localText(language, "port")), "8123");
    await user.click(
      screen.getByRole("button", { name: localText(language, "install") }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: localText(language, "plan"),
    });
    expect(within(dialog).getByText("/data/personal")).toBeTruthy();
    expect(within(dialog).getByText("/config/server.env")).toBeTruthy();
    expect(within(dialog).getByText("123456")).toBeTruthy();
    expect(
      within(dialog).getByText(localText(language, "classification_missing")),
    ).toBeTruthy();
    expect(within(dialog).getByText(localText(language, "new"))).toBeTruthy();
    expect(opsApi.apply).not.toHaveBeenCalled();
    await user.click(
      within(dialog).getByRole("button", {
        name: localText(language, "approve"),
      }),
    );
    await screen.findByText(localText(language, "succeeded"));
    expect(opsApi.preflight).toHaveBeenCalledWith({
      operation: "install",
      options: { httpPort: 8123, startOnLogin: true },
    });
    expect(opsApi.apply).toHaveBeenCalledWith({
      approvalId: "approval-a",
      planId: "plan-a",
    });
  },
);
test("dismissing a plan and switching to an existing Server never apply it", async () => {
  const user = await open();
  await user.click(
    screen.getByRole("button", { name: "Install local Server" }),
  );
  await screen.findByRole("dialog");
  await user.keyboard("{Escape}");
  await user.click(
    screen.getByRole("button", { name: "Connect an existing Server" }),
  );
  expect(opsApi.apply).not.toHaveBeenCalled();
});
test.each([
  "repair",
  "start",
  "stop",
  "restart",
  "register",
  "remove_registration",
] as const)(
  "management %s uses the approved producer operation",
  async (operation) => {
    facts = installed;
    const user = await open();
    await user.click(
      screen.getByRole("button", { name: localText("en", operation) }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(localText("en", "preservation")),
    ).toBeTruthy();
    await user.click(
      within(dialog).getByRole("button", { name: "Approve and execute" }),
    );
    await waitFor(() => expect(opsApi.apply).toHaveBeenCalledOnce());
    expect(opsApi.preflight).toHaveBeenCalledWith({
      operation,
      options: operation === "register" ? { startOnLogin: true } : {},
    });
  },
);
test("reopening reconciles an interrupted operation and recovery keeps its identity and options", async () => {
  references.operations = [
    {
      operationId: "operation-a",
      bindingStatus: "current",
      operation: "install",
      options: { httpPort: 8123, startOnLogin: false },
    },
  ];
  status = result("pending");
  vi.mocked(opsApi.resume).mockImplementation(async () => {
    status = result("succeeded", 2);
    return status;
  });
  const user = await open();
  const resume = screen.getByRole("button", { name: "Review recovery plan" });
  await waitFor(() =>
    expect((resume as HTMLButtonElement).disabled).toBe(false),
  );
  expect(opsApi.apply).not.toHaveBeenCalled();
  expect(opsApi.resume).not.toHaveBeenCalled();
  await user.click(resume);
  expect(opsApi.preflight).toHaveBeenCalledWith({
    operation: "install",
    options: { httpPort: 8123, startOnLogin: false },
    resume: { operationId: "operation-a", expectedAttempt: 1 },
  });
  await user.click(
    (await screen.findByRole("dialog")).querySelectorAll("button")[1],
  );
  await waitFor(() =>
    expect(opsApi.resume).toHaveBeenCalledWith({
      approvalId: "approval-a",
      planId: "plan-a",
    }),
  );
  expect(opsApi.apply).not.toHaveBeenCalled();
});
test("uncertain live executor and legacy records are query-only, never blindly resumed", async () => {
  references.operations = [
    { operationId: "operation-a", bindingStatus: "current" },
  ];
  status = result("pending");
  status.status!.executionState = "unknown";
  await open();
  await screen.findByText("No terminal result yet");
  expect(
    (
      screen.getByRole("button", {
        name: "Review recovery plan",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(opsApi.resume).not.toHaveBeenCalled();
});
test("progress is bound to the approved request and cancellation does not imply completion", async () => {
  status = result("pending");
  status.status!.executionState = "unknown";
  const user = await open();
  let finish!: (reply: OpsOperationReply) => void;
  vi.mocked(opsApi.apply).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  vi.mocked(opsApi.cancel).mockImplementation(async () => {
    status = result("pending");
    status.status!.cancelRequested = true;
    return status;
  });
  await user.click(
    screen.getByRole("button", { name: "Install local Server" }),
  );
  await screen.findByRole("dialog");
  await user.click(screen.getByRole("button", { name: "Approve and execute" }));
  await act(async () =>
    progress({
      approvalId: "old-approval",
      requestId: "old",
      operationId: "other",
      attempt: 1,
      accepted: true,
      sequence: null,
      phase: null,
    }),
  );
  expect(
    (
      screen.getByRole("button", {
        name: "Request cancellation",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  await act(async () =>
    progress({
      approvalId: "approval-a",
      requestId: "request-a",
      operationId: "operation-a",
      attempt: 1,
      accepted: true,
      sequence: 1,
      phase: "preparing",
    }),
  );
  await user.click(
    screen.getByRole("button", { name: "Request cancellation" }),
  );
  expect(opsApi.cancel).toHaveBeenCalledWith({
    operationId: "operation-a",
    attempt: 1,
  });
  await screen.findByText(localText("en", "cancelHint"));
  expect(screen.queryByText("Operation succeeded")).toBeNull();
  await act(async () => finish(result("cancelled")));
});
test.each([
  "port_conflict",
  "existing_independent",
  "ownership_conflict",
  "database_migration_required",
  "resource_corrupt",
  "stale_approval",
])("actionable failure %s is shown without applying", async (code) => {
  vi.mocked(opsApi.preflight).mockRejectedValue(code);
  const user = await open();
  await user.click(
    screen.getByRole("button", { name: "Install local Server" }),
  );
  await screen.findByRole("alert");
  expect(opsApi.apply).not.toHaveBeenCalled();
});
test("missing distribution resources keep the existing-Server path available", async () => {
  references.bindingAvailable = false;
  vi.mocked(opsApi.inspect).mockResolvedValue({
    outcome: "unknown",
    bridgeError: "unavailable",
  });
  render(<Harness />);
  await screen.findByText("Local installation resources unavailable");
  expect(
    (
      screen.getByRole("button", {
        name: "Install local Server",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(
    (
      screen.getByRole("button", {
        name: "Connect an existing Server",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(false);
});
test("local connection handoff is explicit and its readiness effect is disclosed", async () => {
  facts = installed;
  const connect = vi.fn().mockResolvedValue(undefined);
  const user = userEvent.setup();
  render(<Harness connect={connect} />);
  const button = screen.getByRole("button", {
    name: "Connect and open default Scope",
  });
  await waitFor(() =>
    expect((button as HTMLButtonElement).disabled).toBe(false),
  );
  expect(connect).not.toHaveBeenCalled();
  await user.click(button);
  const confirmation = await screen.findByRole("alertdialog");
  expect(
    within(confirmation).getByText(/may probe models and dependencies/),
  ).toBeTruthy();
  await user.click(
    within(confirmation).getByRole("button", { name: "Confirm" }),
  );
  expect(connect).toHaveBeenCalledOnce();
});

test("a lost reply without progress discovers the new durable reference instead of replaying", async () => {
  const old = { ...result(), operationId: "old-operation" };
  references.operations = [
    {
      operationId: "old-operation",
      bindingStatus: "current",
      operation: "stop",
      options: {},
    },
  ];
  vi.mocked(opsApi.operationStatus).mockImplementation(
    async ({ operationId }) => (operationId === "old-operation" ? old : status),
  );
  vi.mocked(opsApi.apply).mockImplementation(async () => {
    references = {
      ...references,
      operations: [
        ...references.operations,
        {
          operationId: "operation-a",
          bindingStatus: "current",
          operation: "install",
          options: { httpPort: 8123 },
        },
      ],
    };
    status = result("pending");
    throw "timeout";
  });
  const user = await open();
  await screen.findByText("Operation succeeded");
  await user.click(
    screen.getByRole("button", { name: "Install local Server" }),
  );
  await screen.findByRole("dialog");
  await user.click(screen.getByRole("button", { name: "Approve and execute" }));
  await screen.findByText("No terminal result yet");
  expect(
    (screen.getByLabelText("Select recorded operation") as HTMLSelectElement)
      .value,
  ).toBe("operation-a");
  expect(opsApi.apply).toHaveBeenCalledOnce();
  expect(opsApi.resume).not.toHaveBeenCalled();
  expect(
    (
      screen.getByRole("button", {
        name: "Install local Server",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
});
test("a cancelled application context guard cannot dispatch the approved service mutation", async () => {
  const user = userEvent.setup();
  render(<Harness before={async () => false} />);
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Stop service",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  await user.click(screen.getByRole("button", { name: "Stop service" }));
  await screen.findByRole("dialog");
  await user.click(screen.getByRole("button", { name: "Approve and execute" }));
  expect(opsApi.apply).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog")).toBeTruthy();
});

test("integrity inspection failure is distinct from missing distribution resources", async () => {
  references.bindingAvailable = false;
  vi.mocked(opsApi.inspect).mockResolvedValue({
    outcome: "unknown",
    bridgeError: "integrity_failure",
  });
  render(<Harness />);
  await screen.findByText(localText("en", "integrity_failure"));
  expect(
    screen.queryByText("Local installation resources unavailable"),
  ).toBeNull();
});
test("a definite cancelled attempt does not claim its outcome is unconfirmed", async () => {
  references.operations = [
    {
      operationId: "operation-a",
      bindingStatus: "current",
      operation: "stop",
      options: {},
    },
  ];
  status = result("cancelled");
  await open();
  await screen.findByText("Cancelled");
  expect(screen.queryByText(localText("en", "uncertainHint"))).toBeNull();
});
test.each([
  "configuration_invalid",
  "configuration_not_owned",
  "runtime_damaged",
  "registration_missing",
  "option_unsupported",
  "integrity_failure",
  "timeout",
])("definite rejection %s has a specific recovery hint", async (code) => {
  vi.mocked(opsApi.preflight).mockRejectedValue(code);
  const user = await open();
  await user.click(
    screen.getByRole("button", { name: "Install local Server" }),
  );
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).not.toContain("Unknown or unconfirmed");
  expect(opsApi.apply).not.toHaveBeenCalled();
});

test("lost progress still permits discovering and cancelling the active durable operation", async () => {
  references.operations = [
    {
      operationId: "old-operation",
      bindingStatus: "current",
      operation: "stop",
      options: {},
    },
  ];
  const old = { ...result(), operationId: "old-operation" };
  vi.mocked(opsApi.operationStatus).mockImplementation(
    async ({ operationId }) => (operationId === "old-operation" ? old : status),
  );
  vi.mocked(subscribeOpsProgress).mockRejectedValue("unavailable");
  let finish!: (reply: OpsOperationReply) => void;
  vi.mocked(opsApi.apply).mockImplementation(() => {
    status = result("pending");
    status.status!.executionState = "unknown";
    references = {
      ...references,
      operations: [
        ...references.operations,
        {
          operationId: "operation-a",
          bindingStatus: "current",
          operation: "install",
          options: {},
        },
      ],
    };
    return new Promise((resolve) => {
      finish = resolve;
    });
  });
  vi.mocked(opsApi.cancel).mockImplementation(async () => status);
  const user = await open();
  await screen.findByText("Operation succeeded");
  await user.click(
    screen.getByRole("button", { name: "Install local Server" }),
  );
  await screen.findByRole("dialog");
  await user.click(screen.getByRole("button", { name: "Approve and execute" }));
  await user.click(screen.getByRole("button", { name: "Inspect local state" }));
  const cancel = screen.getByRole("button", { name: "Request cancellation" });
  await waitFor(() =>
    expect((cancel as HTMLButtonElement).disabled).toBe(false),
  );
  await user.click(cancel);
  expect(opsApi.cancel).toHaveBeenCalledWith({
    operationId: "operation-a",
    attempt: 1,
  });
  await act(async () => {
    status = result("cancelled");
    finish(status);
  });
});

test("deferred progress subscriptions are cleaned up across StrictMode remount and window close", async () => {
  const active = new Set<(event: OpsProgress) => void>();
  const register: Array<() => void> = [];
  vi.mocked(subscribeOpsProgress).mockImplementation(
    (callback) =>
      new Promise((resolve) => {
        register.push(() => {
          active.add(callback);
          resolve(() => {
            active.delete(callback);
          });
        });
      }),
  );
  const view = render(
    <StrictMode>
      <Harness />
    </StrictMode>,
  );
  await waitFor(() => expect(register.length).toBeGreaterThan(0));
  await act(async () => {
    register.forEach((complete) => complete());
  });
  expect(active.size).toBe(1);
  view.unmount();
  expect(active.size).toBe(0);
});

test("dismissing a plan during an awaited context guard revokes its execution consent", async () => {
  let release!: (answer: boolean) => void;
  const user = userEvent.setup();
  render(
    <Harness
      before={() =>
        new Promise((resolve) => {
          release = resolve;
        })
      }
    />,
  );
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Install local Server",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  await user.click(
    screen.getByRole("button", { name: "Install local Server" }),
  );
  await screen.findByRole("dialog");
  await user.click(screen.getByRole("button", { name: "Approve and execute" }));
  await user.keyboard("{Escape}");
  await act(async () => release(true));
  expect(opsApi.apply).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).toBeNull();
});
