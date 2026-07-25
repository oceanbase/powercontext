- Proposal Name: `remote_access_architecture`
- Start Date: 2026-07-16
- RFC PR: [oceanbase/powercontext#11](https://github.com/oceanbase/powercontext/pull/11)
- Tracking Issue: 尚未分配

# Summary

本 RFC 提议为 PowerContext 建立远程访问边界。Server 通过 OpenAPI 定义的 HTTP contract 暴露 application
service。Generated Client SDK transport 和经过筛选的 MCP 功能面复用同一套 Server 语义。该提案定义职责归属和
contract flow，不定义 Runtime processing、persistence、scheduling 或最终 domain API。

本 RFC 使用 Source、Artifact Revision、ContextBundle、Memory Generation 和 Operation 等名称描述场景。相关
operation 和 schema 只用于解释远程边界。接受本 RFC 不代表接受这些名称作为 wire contract。

# Motivation

Python application 可以直接使用 Core Protocol type，但远程 process 和其他语言需要稳定的 transport boundary。
Agent integration 也需要发现部分 capability，同时不能为此创建第二套 application model。

该边界需要明确 HTTP contract 的唯一归属，并规定 domain behavior 到各个 transport 的转换路径。否则 Server
handler、Client SDK 和 MCP adapter 可能分别定义同一个 action，最终形成不一致的语义。

# Guide-level explanation

## 全局架构

Application 使用 Core Protocol model，并为 workflow 提供 Runtime application service。它可以在本地调用这些
service，也可以通过 Server 暴露。Remote client 使用根据 Server OpenAPI contract 生成的 SDK。Agent integration
使用同一 Server 提供的、更小的 MCP 投影。

```text
Core Protocol
      |
Runtime application services
      |-------------------------|
      v                         v
Local integration          Server adapters
                                |-------------------|
                                v                   v
                         OpenAPI HTTP contract     MCP projection
                                |
                     Generated Client transport
                                |
                      Handwritten Client facade

Component command groups -> CLI extension shell
```

| Layer | 职责 |
| --- | --- |
| Core Protocol | 可复用的 domain type 和 sans-I/O component contract |
| Runtime application service | Use-case behavior、transaction boundary、processing 和 retrieval semantics |
| Server | HTTP/MCP adapter、process assembly 和 deployment policy |
| Client transport | 根据 OpenAPI 生成的 serialization 和调用逻辑 |
| Client facade | 稳定 error 和符合语言习惯的 interaction pattern |
| CLI shell | 发现并挂载 component 持有的 command group |

Server-only assembly 不进入 Core Protocol 的公开功能面。当 HTTP model 与 Core Protocol model 语义相同时，
Server 可以映射两者；transport concern 不会因为 generated code 使用相似字段而成为 Core concept。

例如，Runtime 可以接收 evidence，并在处理后返回有用的 context。本 RFC 可能把这些示例 action 称为
"capture a Source" 和 "retrieve a ContextBundle"。这些名称用于解释 request flow，不会预留 Python name、URL
path、MCP tool、persistence record 或 lifecycle rule。

## 接入方式

HTTP 是完整的远程 contract。Client SDK 在该 contract 上提供符合各语言习惯的接口。MCP 只暴露为 Agent
interaction 选定的操作。增加 HTTP operation 不会自动增加 MCP tool 或 resource。

CLI 独立于这些 transport。它发现 component 提供的 command group，因此本地 Runtime command 和远程 Client
command 可以共存，不需要把任何一组具体 command 移入 shell。

## 组合方式

Python distribution 为 Client、Server 和 CLI 提供可选功能面。MCP 是由 Server 配置控制的 transport，不是独立
installation role。Process 可以只使用 Client 而不托管 Server。本地 Runtime 也可以使用 CLI 而不启用远程访问。
只有一个 environment 需要承担多个角色时才组合它们。

# Reference-level explanation

## 解释规则

上面的架构和职责边界是 normative 内容。下文的 operation category 和 name 只是示例。本 RFC 不接受具体的
domain operation、URL、schema、MCP name、storage model、job lifecycle 或 ranking rule。

## OpenAPI contract

受版本控制的 OpenAPI 文档定义 HTTP request、response、operation identifier 和 compatibility。Server 和 Client
generation 使用同一文档。Generated file 是 build artifact，不能通过直接编辑它来替代 contract 变更。

如果 Core Protocol model 已经表达所需语义，Server 应在 application boundary 映射该 model。如果 wire format
需要 transport-only metadata，则使用 transport model，不扩大 Core Protocol API。

## 示例 HTTP 功能面

远程边界预计需要以下几类 operation：

| 示例 operation | 纳入原因 |
| --- | --- |
| Health 和 readiness | Process orchestration |
| Capability discovery | 报告 assembled Runtime 提供的 behavior |
| Evidence submission | 说明 command 如何穿过远程边界 |
| Exact record retrieval | 说明 immutable query |
| Context retrieval | 说明有界 application query |
| Long-running work status | 说明 asynchronous completion |

这些类别不选择 resource name、path、consistency guarantee 或 completion semantics。相关决策依赖 Runtime
application-service boundary。

## Client SDK

Generated Client code 负责 wire serialization 和 endpoint call。HTTP contract 定义相关行为后，handwritten
facade 可以负责 stable exception、authentication policy、retry、pagination 或 waiting behavior。

## CLI extension

CLI 不复制 Server 或 Client behavior。它发现已安装 component 提供的 command group。这样可以保持本地 Runtime
command 与远程 Client command 相互独立，也允许一个 environment 同时组合两者。

本 RFC 规定可组合的角色，不规定 packaging internal。Packaging 可以调整，但用户必须能够在不安装 Server 的
情况下安装 remote Client，也必须能够为本地或远程 workflow 增加 CLI support。

## MCP 投影

显式 MCP 投影属于本 RFC 提议的架构。它建立在已经组装的 Server 上，而不是直接建立在 Core Protocol module
上，并使用与 HTTP 相同的 application service 和 policy decision。Adapter 选择面向任务的子集，不镜像所有
endpoint。

MCP tool、resource 和 prompt 具有不同的 interaction semantics。后续设计必须说明每个 primitive 的理由。
`capture_source`、`search_context` 和 `get_operation` 等名称只是示例。本 RFC 不接受这些名称，也不要求提供
stdio bridge。

Protocol version support 属于下游兼容性决策，不在本 RFC 范围内。

## 延后的部署细节

首个 Server 可以把每个 deployment 限制为一个 logical trust domain 和一个 catalog。这样无需在需求尚不明确时
选择 tenant identity 和 authorization model。该限制是否适用于第一版 Runtime-backed API，仍是未决的产品决策。

本 RFC 不选择 database、worker model、scheduler、lease algorithm、search backend、authentication scheme 或
process topology。这些选型依赖 Runtime behavior；当它们影响公开语义时，需要单独评审。

## Compatibility

OpenAPI 变更必须作为 public contract change 评审。CI 应使用固定版本工具重新生成 transport code 并拒绝 drift。
Behavioral test 应通过 public boundary 验证 request 和 response，不应断言偶然形成的 packaging internal 或
generated source structure。

# Drawbacks

OpenAPI-first development 增加了 generation step，评审者需要同时检查 contract 和 generated change。Handwritten
facade 也增加了一层 compatibility surface。MCP 比 HTTP 更窄，因此 Server 增加 capability 时需要显式决定是否
投影到 MCP。

Runtime 和 persistence 决策被延后后，一些有用 operation 仍未定义。这是有意的范围约束，但本 RFC 无法单独指导
evidence processing 或 retrieval 的实现。

# Rationale and alternatives

统一远程边界可以避免每个 Client 和 integration 分别定义 data model。OpenAPI 支持 generated Client，也便于
Server test 和 tooling 检查。完全手写 Client 在早期更简单，但多语言 transport parity 更难维护。

通过 HTTP 直接暴露 Core Protocol object 可以减少 mapping code，但会把 domain evolution 与 wire compatibility
绑定。当前提案在语义一致时复用 Core model，在 wire 需求不同时保留 transport-only model。

将 MCP 作为平行 service 可以让它独立演进，但 domain behavior 和 policy 可能偏离 HTTP。投影选定的 Server 语义
可以保留同一个 application boundary。

本 RFC 不包含由 API、worker 和 scheduler role 组成的 modular monolith。Runtime execution model 明确后，这种
设计仍可能适用；现在接受它会让 transport architecture 提前决定 Runtime policy。

# Prior art

PowerContext RFC 0002 将 Core Protocol 与 Runtime 持有的 workflow 分开。本 RFC 把同一边界应用到远程访问。

[Hindsight](https://github.com/vectorize-io/hindsight) 展示了 generated Client 和 asynchronous Operation tracking。
[Graphiti](https://github.com/getzep/graphiti) 和
[Supermemory](https://github.com/supermemoryai/supermemory) 提供 remote capture 和 queued processing 的实现案例。
它们的 execution model 是参考，不是本 RFC 的要求。

Contract 遵循 [OpenAPI Specification](https://spec.openapis.org/oas/)。MCP integration 遵循
[Model Context Protocol](https://modelcontextprotocol.io/specification/)，具体协议版本由实现决定。

# Unresolved questions

- 哪些 Runtime application-service command 和 query 已经足够稳定，可以远程暴露？
- 一个 logical trust domain 和一个 catalog 是否适合作为第一版 deployment boundary？
- 第一批 Runtime-backed HTTP operation 应提供哪些 compatibility guarantee？
- 哪些 HTTP operation 应同时成为 MCP tool、resource 或 prompt？
- 何时生成并发布其他语言的 SDK？

# Future possibilities

后续 RFC 可以定义 Runtime execution、durable work、persistence profile、retrieval consistency、authentication 和
multi-tenant isolation。这些设计可以增加远程 operation，而不需要改变本 RFC 确立的职责边界。

OpenAPI contract 和 conformance test 稳定后，Client SDK 可以覆盖更多语言。当对应的 Server 语义和 authorization
policy 明确后，MCP 可以增加 tool、resource 或 prompt。
