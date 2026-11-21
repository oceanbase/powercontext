/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import { afterEach, expect, test } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryWorkspaceContent } from "../src/app/MemoryWorkspaceContent";
import { ServiceAgentWorkspace } from "../src/app/ServiceAgentWorkspace";
import {
  previewDataScopeId,
  previewDesktopState,
  previewEmptyScopeId,
  previewProfiles,
  previewRemoteConnectionId,
  previewLocalConnectionId,
  previewScopes,
  previewServiceAgents,
  previewWorkspaceMemoryCount,
} from "../src/app/preview-data";
import { buildServiceAgentWorkspaceModel } from "../src/app/service-agent-model";

afterEach(cleanup);

test("local-only state renders no invented remote service", () => {
  const state = previewDesktopState(
    null,
    "zh",
    previewLocalConnectionId,
    previewDataScopeId,
    [previewLocalConnectionId],
  );
  expect(state.profiles.map((profile) => profile.id)).toEqual([
    previewLocalConnectionId,
  ]);
  expect(state.reports.map((report) => report.connectionId)).toEqual([
    previewLocalConnectionId,
  ]);
  expect(state.active?.connectionId).toBe(previewLocalConnectionId);
  render(
    <ServiceAgentWorkspace
      state={state}
      language="zh"
      onState={() => {}}
      model={buildServiceAgentWorkspaceModel(state, {
        simulated: true,
        agentsByServiceId: previewServiceAgents("zh"),
      })}
    />,
  );
  expect(screen.queryByText("团队远程服务")).toBeNull();
  expect(screen.queryByText("暂无本机服务")).toBeNull();
  expect(screen.getByText("本机 PowerContext 服务")).toBeTruthy();
});

test("remote-only interface state contains no local connection or local check report", () => {
  const state = previewDesktopState(
    null,
    "zh",
    previewRemoteConnectionId,
    previewDataScopeId,
    [previewRemoteConnectionId],
  );
  expect(state.profiles.map((profile) => profile.id)).toEqual([
    previewRemoteConnectionId,
  ]);
  expect(state.reports.map((report) => report.connectionId)).toEqual([
    previewRemoteConnectionId,
  ]);
  expect(
    state.profiles.some((profile) => profile.id === previewLocalConnectionId),
  ).toBe(false);
  expect(state.active?.connectionId).toBe(previewRemoteConnectionId);
  expect(state.active?.report.connectionId).toBe(previewRemoteConnectionId);
  expect(state.active?.scope?.scope_id).toBe(previewDataScopeId);
});

test("the workspace model classifies services from endpoint data instead of preview ids", () => {
  const previewState = previewDesktopState(null, "zh");
  const localProfile = previewState.profiles.find(
    (profile) => profile.id === previewLocalConnectionId,
  )!;
  const remoteProfile = previewState.profiles.find(
    (profile) => profile.id === previewRemoteConnectionId,
  )!;
  const state = {
    ...previewState,
    profiles: [
      { ...localProfile, id: "saved-local-profile" },
      { ...remoteProfile, id: "saved-remote-profile" },
    ],
    reports: [],
    active: null,
  };

  const model = buildServiceAgentWorkspaceModel(state, {
    agentsByServiceId: {
      "saved-local-profile": [
        {
          id: "agent-1",
          name: "Codex",
          project: "PowerContext Desktop",
          space: "默认空间",
          lastWrite: "刚刚",
        },
      ],
    },
  });

  expect(model.services).toMatchObject([
    {
      profile: { id: "saved-local-profile" },
      kind: "local",
      agents: [{ id: "agent-1" }],
    },
    {
      profile: { id: "saved-remote-profile" },
      kind: "remote",
      agents: [],
    },
  ]);
});

test("preview services and spaces share one deterministic data source", () => {
  const profiles = previewProfiles("zh");
  const scopes = previewScopes("zh");
  const state = previewDesktopState(null, "zh");

  expect(profiles.map((profile) => profile.name)).toEqual([
    "本机服务",
    "团队远程服务",
  ]);
  expect(state.profiles).toEqual(profiles);
  expect(state.reports).toHaveLength(2);
  expect(
    state.profiles.find((profile) => profile.id === previewRemoteConnectionId),
  ).toMatchObject({
    endpoint: "https://powercontext.example.com",
    authentication: "bearer",
  });
  expect(scopes.map((scope) => scope.scope_id)).toEqual([
    previewEmptyScopeId,
    previewDataScopeId,
  ]);
  expect(scopes.map((scope) => scope.title)).toEqual([
    "默认空间",
    "OCP产品研发",
  ]);
  expect(state.active?.scope?.scope_id).toBe(previewEmptyScopeId);
});

test("the populated preview space reports 277 memories", () => {
  render(
    <MemoryWorkspaceContent
      language="zh"
      scopeTitle="OCP产品研发"
      serviceName="本机服务"
      serviceEndpoint="http://127.0.0.1:8000"
      initialCount={previewWorkspaceMemoryCount}
    />,
  );

  expect(screen.getByText("277 条记忆")).toBeTruthy();
  expect(
    screen.getByText("用户偏好简洁、直接的中文回复，并希望先看到结论。"),
  ).toBeTruthy();
  expect(screen.getAllByRole("listitem")).toHaveLength(
    previewWorkspaceMemoryCount,
  );
});

test("the default preview space remains empty", () => {
  render(
    <MemoryWorkspaceContent
      language="zh"
      scopeTitle="默认空间"
      serviceName="本机服务"
      serviceEndpoint="http://127.0.0.1:8000"
    />,
  );

  expect(screen.getByText("0 条记忆")).toBeTruthy();
  expect(screen.getByText("当前空间暂无记忆")).toBeTruthy();
});
