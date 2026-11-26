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

import { afterEach, expect, test, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ServiceAgentWorkspace } from "../src/app/ServiceAgentWorkspace";
import type { DesktopState } from "../src/generated/ipc";
import {
  previewDesktopState,
  previewLocalConnectionId,
  previewProfiles,
  previewServiceAgents,
} from "../src/app/preview-data";
import { buildServiceAgentWorkspaceModel } from "../src/app/service-agent-model";
import { desktopApi } from "../src/shared/ipc";

afterEach(cleanup);

const state: DesktopState = {
  generation: 1,
  profiles: [
    {
      id: "team",
      revision: 1,
      name: "团队服务",
      endpoint: "https://context.example.com",
      authentication: "bearer",
      caPem: null,
      compatibility: null,
      credentialState: "stored",
    },
  ],
  reports: [],
  active: null,
  compatibilityProfiles: [],
  pendingCredentialCleanup: 0,
  lastWrite: null,
};

function simulatedModel(value: DesktopState, localRunning = true) {
  const localProfile = previewProfiles("zh").find(
    (profile) => profile.id === previewLocalConnectionId,
  )!;
  const stateWithLocal = {
    ...value,
    profiles: [
      localProfile,
      ...value.profiles.filter((profile) => profile.id !== localProfile.id),
    ],
  };
  return buildServiceAgentWorkspaceModel(stateWithLocal, {
    simulated: true,
    agentsByServiceId: previewServiceAgents("zh"),
    runningByServiceId: { [localProfile.id]: localRunning },
  });
}

test("requires the new address credential and keeps it in the session for a saved local connection", async () => {
  const profile = {
    ...state.profiles[0],
    name: "Local fixture",
    endpoint: "http://127.0.0.1:8000/",
    credentialState: "session_only" as const,
  };
  const native = { ...state, profiles: [profile] };
  const save = vi.spyOn(desktopApi, "save").mockResolvedValueOnce(native);
  const user = userEvent.setup();
  render(
    <ServiceAgentWorkspace state={native} language="en" onState={vi.fn()} />,
  );
  await user.click(screen.getByRole("button", { name: "More actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Edit connection" }));
  const dialog = screen.getByRole("dialog", { name: "Edit connection" });
  const address = within(dialog).getByLabelText("Service address");
  await user.clear(address);
  await user.type(address, "http://127.0.0.1:9000");
  await user.click(within(dialog).getByRole("button", { name: "Save" }));
  expect(
    within(dialog).getByText(
      "Enter the credential for the new service address.",
    ),
  ).toBeTruthy();
  expect(save).not.toHaveBeenCalled();
  await user.type(
    within(dialog).getByLabelText("Credential"),
    "new-fixture-token",
  );
  await user.click(within(dialog).getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", { name: "Edit connection" }),
    ).toBeNull(),
  );
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      endpoint: "http://127.0.0.1:9000",
      keepCredential: false,
      credential: { secret: "new-fixture-token", storage: "session_only" },
    }),
  );
  save.mockRestore();
});

test.each([
  ["zh", "暂无可用的服务与 Agent", "安装服务", "连接服务"],
  [
    "en",
    "No available Services & Agents",
    "Install service",
    "Connect service",
  ],
] as const)(
  "shows new-user onboarding when no service exists in %s",
  (language, title, deploy, connect) => {
    render(
      <ServiceAgentWorkspace
        state={{ ...state, profiles: [] }}
        language={language}
        onState={vi.fn()}
        onDeployLocal={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: title })).toBeTruthy();
    expect(
      screen.getByText(
        language === "zh"
          ? /请先安装或连接 PowerContext 服务/
          : /Install or connect a PowerContext service first/,
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: deploy })).toBeTruthy();
    expect(screen.getByRole("button", { name: connect })).toBeTruthy();
    expect(
      screen.queryByText(
        language === "zh" ? "暂无本机服务" : "No local service",
      ),
    ).toBeNull();
  },
);

test("shows onboarding in the disconnected development preview", () => {
  render(
    <ServiceAgentWorkspace
      state={{ ...state, profiles: [] }}
      language="zh"
      onState={vi.fn()}
    />,
  );

  expect(
    screen.getByRole("heading", { name: "暂无可用的服务与 Agent" }),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "安装服务" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "连接服务" })).toBeTruthy();
  expect(screen.queryByText("团队远程服务")).toBeNull();
  expect(screen.queryByText("暂无本机服务")).toBeNull();
});

test("explains unavailable native local service installation from onboarding", async () => {
  const user = userEvent.setup();
  render(
    <ServiceAgentWorkspace
      state={{ ...state, profiles: [] }}
      language="zh"
      onState={vi.fn()}
    />,
  );

  expect(
    screen.getByRole("heading", { name: "暂无可用的服务与 Agent" }),
  ).toBeTruthy();
  expect(screen.queryByText("运行中")).toBeNull();

  await user.click(screen.getByRole("button", { name: "安装服务" }));

  const deployDialog = screen.getByRole("dialog", {
    name: "安装 PowerContext 服务",
  });
  expect(
    within(deployDialog).getByText(/暂不支持从 Desktop 安装本机服务/),
  ).toBeTruthy();
  expect(
    within(deployDialog)
      .getByRole("button", { name: "安装服务" })
      .hasAttribute("disabled"),
  ).toBe(true);
});

test("closing the existing-service dialog restores focus without scrolling the page", async () => {
  const user = userEvent.setup();
  const originalFocus = HTMLElement.prototype.focus;
  const focus = vi
    .spyOn(HTMLElement.prototype, "focus")
    .mockImplementation(function (this: HTMLElement, options?: FocusOptions) {
      if (options === undefined) originalFocus.call(this);
      else originalFocus.call(this, options);
    });
  render(
    <ServiceAgentWorkspace state={state} language="zh" onState={vi.fn()} />,
  );

  const trigger = screen.getByRole("button", { name: "连接已有服务" });
  trigger.focus();
  fireEvent.click(trigger);
  focus.mockClear();
  await user.click(
    within(
      screen.getByRole("dialog", { name: "连接已有 PowerContext 服务" }),
    ).getByRole("button", { name: "取消" }),
  );

  expect(focus).toHaveBeenLastCalledWith({ preventScroll: true });
  expect(focus.mock.instances[focus.mock.instances.length - 1]).toBe(trigger);
  focus.mockRestore();
});

test("keeps all service content visible without collapse controls", async () => {
  const user = userEvent.setup();
  render(
    <ServiceAgentWorkspace
      state={state}
      language="zh"
      onState={vi.fn()}
      model={simulatedModel(state)}
    />,
  );

  expect(screen.queryByLabelText(/展开或收起/)).toBeNull();
  const localHeader = screen
    .getAllByText("本机 PowerContext 服务")[0]
    .closest(".service-accordion-header")!;
  expect(
    within(localHeader as HTMLElement).queryByRole("button", {
      name: "连接本地 Agent",
    }),
  ).toBeNull();
  expect(
    within(localHeader as HTMLElement).getByRole("button", {
      name: "更多操作",
    }),
  ).toBeTruthy();
  expect(screen.queryByText("暂无本机服务")).toBeNull();
  expect(screen.queryByRole("button", { name: "安装服务" })).toBeNull();
  expect(screen.getByRole("switch", { name: "开机自动启动" })).toBeTruthy();
  expect(screen.getByText("生成模型")).toBeTruthy();
  expect(screen.getByText("嵌入模型")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "连接本地服务" })).toBeNull();
  expect(screen.getByRole("button", { name: "连接已有服务" })).toBeTruthy();

  await user.click(screen.getByRole("button", { name: "连接已有服务" }));
  expect(
    screen.getByRole("dialog", { name: "连接已有 PowerContext 服务" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "关闭弹窗" }));

  expect(screen.getAllByText("http://127.0.0.1:8000")).toHaveLength(1);
  expect(screen.queryByText("本机 Agent")).toBeNull();
  expect(screen.queryByText("绑定空间")).toBeNull();
  expect(screen.getByText("Codex")).toBeTruthy();
  expect(screen.getByText("Cursor")).toBeTruthy();
  expect(screen.queryByText("默认空间")).toBeNull();
  expect(screen.getAllByText("OCP产品研发")).toHaveLength(2);
  expect(screen.getByText("暂无关联此服务的本机 Agent")).toBeTruthy();
  expect(screen.queryByText("尚未添加远程服务")).toBeNull();
  const connectAgentButtons = screen.getAllByRole("button", {
    name: "连接本地 Agent",
  });
  expect(connectAgentButtons).toHaveLength(2);
  await user.click(connectAgentButtons[0]);
  expect(screen.getByRole("dialog", { name: "连接本地 Agent" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "关闭弹窗" }));
});

test("identifies the service and Agent when capture is disabled", async () => {
  const user = userEvent.setup();
  render(
    <ServiceAgentWorkspace
      state={state}
      language="zh"
      onState={vi.fn()}
      model={simulatedModel(state)}
    />,
  );

  await user.click(screen.getByRole("button", { name: "管理连接：Codex" }));
  await user.click(screen.getByRole("menuitem", { name: "消息采集设置" }));
  const disabledDialog = screen.getByRole("dialog", { name: "消息采集设置" });
  await user.click(
    within(disabledDialog).getByRole("switch", { name: "采集用户消息" }),
  );
  await user.click(
    within(disabledDialog).getByRole("button", { name: "保存" }),
  );
  expect(
    screen.getByText("本机 PowerContext 服务下的 Codex Agent 的消息采集已关闭"),
  ).toBeTruthy();
}, 20_000);

test("identifies the service and Agent when capture is enabled", async () => {
  const user = userEvent.setup();
  render(
    <ServiceAgentWorkspace
      state={state}
      language="zh"
      onState={vi.fn()}
      model={simulatedModel(state)}
    />,
  );

  await user.click(screen.getByRole("button", { name: "管理连接：Codex" }));
  await user.click(screen.getByRole("menuitem", { name: "消息采集设置" }));
  const enabledDialog = screen.getByRole("dialog", { name: "消息采集设置" });
  await user.click(within(enabledDialog).getByRole("button", { name: "保存" }));
  expect(
    screen.getByText("本机 PowerContext 服务下的 Codex Agent 的消息采集已打开"),
  ).toBeTruthy();
}, 20_000);

test("edits only a remote connection name and credential", async () => {
  const user = userEvent.setup();
  render(
    <ServiceAgentWorkspace
      state={previewDesktopState(null, "zh")}
      language="zh"
      onState={vi.fn()}
      model={simulatedModel(previewDesktopState(null, "zh"))}
    />,
  );

  const remoteHeader = screen
    .getByText("团队远程服务")
    .closest(".service-accordion-header")!;
  await user.click(
    within(remoteHeader as HTMLElement).getByRole("button", {
      name: "更多操作",
    }),
  );
  await user.click(screen.getByRole("menuitem", { name: "编辑连接" }));

  const dialog = screen.getByRole("dialog", { name: "编辑连接" });
  const name = within(dialog).getByLabelText("连接名称");
  const address = within(dialog).getByLabelText("服务地址");
  const credential = within(dialog).getByLabelText("凭据");
  expect(address.getAttribute("readonly")).not.toBeNull();
  expect(address.matches(":disabled")).toBe(false);
  expect(address.getAttribute("value")).toBe(
    "https://powercontext.example.com",
  );
  expect(credential.getAttribute("type")).toBe("password");
  expect(within(dialog).getByText("留空将继续使用当前凭据")).toBeTruthy();

  await user.type(credential, "updated-secret");
  await user.click(within(dialog).getByRole("button", { name: "显示凭据" }));
  expect(credential.getAttribute("type")).toBe("text");
  expect((credential as HTMLInputElement).value).toBe("updated-secret");

  await user.clear(name);
  await user.type(name, "新的远程服务名称");
  await user.click(within(dialog).getByRole("button", { name: "保存" }));
  expect(screen.queryByRole("dialog", { name: "编辑连接" })).toBeNull();
  expect(screen.getByText("新的远程服务名称")).toBeTruthy();
  expect(screen.queryByText("团队远程服务")).toBeNull();
});

test("adds edit and status checks before restart in the local service menu", async () => {
  const user = userEvent.setup();
  render(
    <ServiceAgentWorkspace
      state={state}
      language="zh"
      onState={vi.fn()}
      model={simulatedModel(state)}
    />,
  );

  const localHeader = screen
    .getByText("本机 PowerContext 服务")
    .closest(".service-accordion-header")!;
  await user.click(
    within(localHeader as HTMLElement).getByRole("button", {
      name: "更多操作",
    }),
  );
  expect(
    screen.getAllByRole("menuitem").map((item) => item.textContent),
  ).toEqual([
    "创建记忆空间",
    "编辑连接",
    "检查连接状态",
    "重启服务",
    "停止服务",
    "删除连接",
    "卸载服务",
  ]);

  await user.click(screen.getByRole("menuitem", { name: "编辑连接" }));
  const editDialog = screen.getByRole("dialog", { name: "编辑连接" });
  expect(within(editDialog).getByLabelText("服务地址")).toHaveProperty(
    "readOnly",
    false,
  );
  expect(within(editDialog).getByLabelText("凭据")).toHaveProperty(
    "type",
    "password",
  );
  await user.click(within(editDialog).getByRole("button", { name: "取消" }));

  await user.click(
    within(localHeader as HTMLElement).getByRole("button", {
      name: "更多操作",
    }),
  );
  await user.click(screen.getByRole("menuitem", { name: "检查连接状态" }));
  const checkDialog = screen.getByRole("dialog", { name: "检查连接状态" });
  expect(
    within(checkDialog).getByText("正在检查本机服务连接状态…"),
  ).toBeTruthy();
  expect(await within(checkDialog).findByText("连接状态正常")).toBeTruthy();
  expect(within(checkDialog).getByText(/尚未探测就绪状态/)).toBeTruthy();
  expect(within(checkDialog).queryByText(/远程服务/)).toBeNull();
});

test("requires confirmation before starting a stopped local service", async () => {
  const user = userEvent.setup();
  render(
    <ServiceAgentWorkspace
      state={state}
      language="zh"
      onState={vi.fn()}
      model={simulatedModel(state, false)}
    />,
  );

  const localHeader = screen
    .getByText("本机 PowerContext 服务")
    .closest(".service-accordion-header")!;
  await user.click(
    within(localHeader as HTMLElement).getByRole("button", {
      name: "更多操作",
    }),
  );
  await user.click(screen.getByRole("menuitem", { name: "启动服务" }));

  const dialog = screen.getByRole("alertdialog", {
    name: "确认启动本机服务",
  });
  expect(
    within(dialog).getByText(/Codex 可继续采集消息和调用记忆/),
  ).toBeTruthy();
  expect(screen.getByText("已停止")).toBeTruthy();
  await user.click(within(dialog).getByRole("button", { name: "启动服务" }));

  expect(
    await screen.findByText("服务“本机 PowerContext 服务”已启动"),
  ).toBeTruthy();
  expect(screen.queryByText("已停止")).toBeNull();
}, 30_000);

test("locks the local connection name while allowing address and credential updates", async () => {
  const user = userEvent.setup();
  render(
    <ServiceAgentWorkspace
      state={state}
      language="zh"
      onState={vi.fn()}
      model={simulatedModel(state)}
    />,
  );

  const localHeader = screen
    .getByText("本机 PowerContext 服务")
    .closest(".service-accordion-header")!;
  await user.click(
    within(localHeader as HTMLElement).getByRole("button", {
      name: "更多操作",
    }),
  );
  await user.click(screen.getByRole("menuitem", { name: "编辑连接" }));

  const dialog = screen.getByRole("dialog", { name: "编辑连接" });
  const name = within(dialog).getByLabelText("连接名称");
  const address = within(dialog).getByLabelText("服务地址");
  const credential = within(dialog).getByLabelText("凭据");
  expect(name).toHaveProperty("readOnly", true);
  expect(name.matches(":disabled")).toBe(false);
  expect(address).toHaveProperty("readOnly", false);
  expect(credential).toHaveProperty("type", "password");
  expect(credential).not.toHaveProperty("readOnly", true);

  await user.clear(address);
  await user.type(address, "http://127.0.0.1:9000");
  await user.click(within(dialog).getByRole("button", { name: "保存" }));

  expect(screen.queryByRole("dialog", { name: "编辑连接" })).toBeNull();
  expect(screen.getByText("http://127.0.0.1:9000")).toBeTruthy();
});

test("checks a remote connection in the same result dialog pattern as Agent verification", async () => {
  const user = userEvent.setup();
  render(
    <ServiceAgentWorkspace
      state={previewDesktopState(null, "zh")}
      language="zh"
      onState={vi.fn()}
      model={simulatedModel(previewDesktopState(null, "zh"))}
    />,
  );

  const remoteHeader = screen
    .getByText("团队远程服务")
    .closest(".service-accordion-header")!;
  await user.click(
    within(remoteHeader as HTMLElement).getByRole("button", {
      name: "更多操作",
    }),
  );
  await user.click(screen.getByRole("menuitem", { name: "检查连接状态" }));

  const dialog = screen.getByRole("dialog", { name: "检查连接状态" });
  expect(within(dialog).getByText("正在检查服务连接状态…")).toBeTruthy();
  expect(
    within(dialog)
      .getByRole("button", { name: "完成" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(await within(dialog).findByText("连接状态正常")).toBeTruthy();
  expect(within(dialog).getByText(/尚未探测就绪状态/)).toBeTruthy();
});

test("confirms remote connection deletion with the local connection dialog pattern", async () => {
  const user = userEvent.setup();
  render(
    <ServiceAgentWorkspace
      state={previewDesktopState(null, "zh")}
      language="zh"
      onState={vi.fn()}
      model={simulatedModel(previewDesktopState(null, "zh"))}
    />,
  );

  const remoteHeader = screen
    .getByText("团队远程服务")
    .closest(".service-accordion-header")!;
  await user.click(
    within(remoteHeader as HTMLElement).getByRole("button", {
      name: "更多操作",
    }),
  );
  await user.click(screen.getByRole("menuitem", { name: "删除连接" }));

  const dialog = screen.getByRole("alertdialog", { name: "确认删除连接" });
  expect(within(dialog).getByText(/只删除 Desktop 中的连接配置/)).toBeTruthy();
  expect(within(dialog).getByRole("button", { name: "取消" })).toBeTruthy();
  const deleteButton = within(dialog).getByRole("button", { name: "删除" });
  await user.click(deleteButton);

  expect(screen.queryByText("团队远程服务")).toBeNull();
  expect(
    await screen.findByText("服务“团队远程服务”的连接已删除"),
  ).toBeTruthy();
});

test(
  "configures previously unconfigured models through the Agent flow",
  { timeout: 15_000 },
  async () => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    const user = userEvent.setup();
    render(
      <ServiceAgentWorkspace
        state={state}
        language="zh"
        onState={vi.fn()}
        model={simulatedModel(state)}
      />,
    );

    expect(screen.getAllByText("未配置")).toHaveLength(2);
    expect(screen.queryByText("—")).toBeNull();
    expect(screen.getByText("生成模型")).toBeTruthy();
    expect(screen.getByText("嵌入模型")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "配置生成模型" }));

    const dialog = screen.getByRole("dialog", { name: "配置生成模型" });
    expect(
      within(dialog).getByRole("tab", { name: "Agent 协助" }),
    ).toBeTruthy();
    expect(within(dialog).queryByLabelText("嵌入模型")).toBeNull();
    expect(within(dialog).getByText(/不包含 API Key 或 Token/)).toBeTruthy();

    await user.click(
      within(dialog).getByRole("button", {
        name: "Agent 已完成，检查配置",
      }),
    );

    expect(await screen.findAllByText("已配置")).toHaveLength(1);
    expect(screen.queryByRole("dialog", { name: "配置生成模型" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "配置嵌入模型" }));
    const newEmbeddingDialog = screen.getByRole("dialog", {
      name: "配置嵌入模型",
    });
    await user.click(
      within(newEmbeddingDialog).getByRole("button", {
        name: "Agent 已完成，检查配置",
      }),
    );
    await waitFor(() => expect(screen.getAllByText("已配置")).toHaveLength(2));

    await user.click(screen.getByRole("button", { name: "修改嵌入模型" }));
    const embeddingDialog = screen.getByRole("dialog", {
      name: "修改嵌入模型",
    });
    const embeddingModelType =
      within(embeddingDialog).getByLabelText("模型类型（不可修改）");
    expect(embeddingModelType.hasAttribute("disabled")).toBe(true);
    expect(within(embeddingDialog).queryByLabelText("生成模型名称")).toBeNull();
    const embeddingEndpoint =
      within(embeddingDialog).getByLabelText("服务地址");
    const embeddingApiKey = within(embeddingDialog).getByLabelText(
      "API Key（仅保存在 Desktop）",
    );
    expect(
      embeddingEndpoint.compareDocumentPosition(embeddingApiKey) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      embeddingApiKey.compareDocumentPosition(embeddingModelType) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(embeddingEndpoint).toHaveProperty(
      "value",
      "https://api.example.com/v1",
    );
    await user.clear(embeddingEndpoint);
    await user.click(
      within(embeddingDialog).getByRole("button", { name: "保存并应用" }),
    );
    expect(within(embeddingDialog).getByText("请输入服务地址")).toBeTruthy();
    expect(within(embeddingDialog).getByText("请输入 API Key")).toBeTruthy();
    expect(within(embeddingDialog).queryByText("连通性校验失败")).toBeNull();
    await user.type(embeddingEndpoint, "https://embeddings.example.com/v1");
    await user.type(embeddingApiKey, "secret");
    await user.click(
      within(embeddingDialog).getByRole("button", { name: "保存并应用" }),
    );
    expect(
      await within(embeddingDialog).findByText("连通性校验失败"),
    ).toBeTruthy();
    const embeddingError = within(embeddingDialog).getByRole("alert");
    expect(within(embeddingError).getByText("连通性校验失败")).toBeTruthy();
    expect(within(embeddingError).getByText(/无法连接到模型服务/)).toBeTruthy();
    await user.click(
      within(embeddingDialog).getByRole("button", { name: "取消" }),
    );

    await user.click(screen.getByRole("button", { name: "修改生成模型" }));
    const generationDialog = screen.getByRole("dialog", {
      name: "修改生成模型",
    });
    const endpoint = within(generationDialog).getByLabelText("服务地址");
    await user.clear(endpoint);
    await user.click(
      within(generationDialog).getByRole("button", { name: "校验连通性" }),
    );
    expect(within(generationDialog).getByText("请输入服务地址")).toBeTruthy();
    expect(within(generationDialog).getByText("请输入 API Key")).toBeTruthy();
    expect(within(generationDialog).queryByText("连通性校验失败")).toBeNull();

    await user.type(endpoint, "https://api.example.com/v1");
    await user.type(
      within(generationDialog).getByLabelText("API Key（仅保存在 Desktop）"),
      "secret",
    );
    await user.click(
      within(generationDialog).getByRole("button", { name: "校验连通性" }),
    );
    expect(
      await within(generationDialog).findByText("已获取可用模型"),
    ).toBeTruthy();
    expect(within(generationDialog).getByLabelText("生成模型")).toBeTruthy();
    expect(
      within(generationDialog).getByRole("button", { name: "保存并应用" }),
    ).not.toHaveProperty("disabled", true);
    const editedModelName = within(generationDialog).getByLabelText("生成模型");
    await user.clear(editedModelName);
    await user.type(editedModelName, "invalid-model");
    await user.click(
      within(generationDialog).getByRole("button", { name: "保存并应用" }),
    );
    expect(
      await within(generationDialog).findByText(
        "该模型当前不可用，请检查名称或选择其他模型",
      ),
    ).toBeTruthy();
    vi.unstubAllGlobals();
  },
);

test("offers a manual model configuration form", async () => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const user = userEvent.setup();
  render(
    <ServiceAgentWorkspace
      state={state}
      language="zh"
      onState={vi.fn()}
      model={simulatedModel(state)}
    />,
  );

  await user.click(screen.getByRole("button", { name: "配置生成模型" }));
  const dialog = screen.getByRole("dialog", { name: "配置生成模型" });
  await user.click(within(dialog).getByRole("tab", { name: "手动配置" }));

  expect(
    within(dialog)
      .getByRole("combobox", { name: "生成模型" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(
    within(dialog).getByRole("button", { name: "保存并应用" }),
  ).not.toHaveProperty("disabled", true);

  await user.type(
    within(dialog).getByLabelText("服务地址"),
    "https://api.example.com/v1",
  );
  await user.type(
    within(dialog).getByLabelText("API Key（仅保存在 Desktop）"),
    "secret",
  );
  await user.click(within(dialog).getByRole("button", { name: "保存并应用" }));
  expect(within(dialog).getByText("请先校验 API Key 的连通性")).toBeTruthy();
  await user.click(within(dialog).getByRole("button", { name: "校验连通性" }));
  expect(await within(dialog).findByText("已获取可用模型")).toBeTruthy();
  expect(
    within(dialog).getByRole("combobox", { name: "生成模型" }),
  ).toHaveProperty("value", "qwen-plus");
  await user.click(
    within(dialog).getByRole("button", { name: "显示可用模型" }),
  );
  await user.click(screen.getByRole("menuitem", { name: "qwen-max" }));
  const modelName = within(dialog).getByRole("combobox", {
    name: "生成模型",
  });
  expect(modelName).toHaveProperty("value", "qwen-max");
  await user.clear(modelName);
  await user.type(modelName, "unavailable-model");
  await user.click(within(dialog).getByRole("button", { name: "保存并应用" }));
  expect(
    await within(dialog).findByText(
      "该模型当前不可用，请检查名称或选择其他模型",
    ),
  ).toBeTruthy();
  await user.clear(modelName);
  await user.type(modelName, "custom-generation-model");
  await user.click(within(dialog).getByRole("button", { name: "保存并应用" }));
  await waitFor(() =>
    expect(screen.queryByRole("dialog", { name: "配置生成模型" })).toBeNull(),
  );

  expect(screen.getByText("生成模型")).toBeTruthy();
  expect(screen.getByText("嵌入模型")).toBeTruthy();
  expect(screen.getByText("已配置")).toBeTruthy();
  expect(screen.getByText("未配置")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "配置生成模型" })).toBeNull();
  expect(screen.getByRole("button", { name: "修改生成模型" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "配置嵌入模型" })).toBeTruthy();
  vi.unstubAllGlobals();
});

test("shows disabled model selectors and validates credentials before connectivity", async () => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const user = userEvent.setup();
  render(
    <ServiceAgentWorkspace
      state={state}
      language="zh"
      onState={vi.fn()}
      model={simulatedModel(state)}
    />,
  );

  await user.click(screen.getByRole("button", { name: "配置生成模型" }));
  const dialog = screen.getByRole("dialog", { name: "配置生成模型" });
  await user.click(within(dialog).getByRole("tab", { name: "手动配置" }));
  expect(
    within(dialog)
      .getByRole("combobox", { name: "生成模型" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(
    within(dialog).queryByRole("combobox", { name: "嵌入模型" }),
  ).toBeNull();
  expect(within(dialog).queryByText("请先校验 API Key 的连通性")).toBeNull();
  await user.click(within(dialog).getByRole("button", { name: "保存并应用" }));
  expect(within(dialog).getByText("请输入模型服务地址")).toBeTruthy();
  expect(within(dialog).getByText("请输入 API Key")).toBeTruthy();
  expect(within(dialog).queryByRole("alert")).toBeNull();

  await user.type(within(dialog).getByLabelText("服务地址"), "invalid");
  await user.type(
    within(dialog).getByLabelText("API Key（仅保存在 Desktop）"),
    "secret",
  );
  await user.click(within(dialog).getByRole("button", { name: "保存并应用" }));
  expect(within(dialog).getAllByText("请先校验 API Key 的连通性")).toHaveLength(
    1,
  );
  await user.click(within(dialog).getByRole("button", { name: "校验连通性" }));

  expect(within(dialog).getByRole("alert")).toBeTruthy();
  const generationError = within(dialog).getByRole("alert");
  expect(within(generationError).getByText("配置校验失败")).toBeTruthy();
  expect(within(generationError).getByText(/无法连接到模型服务/)).toBeTruthy();
  vi.unstubAllGlobals();
});
