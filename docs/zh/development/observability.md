---
title: Observability 开发规范
description: 使用稳定字段、正确的 correlation 和 failure isolation 添加 operational events。
---

# Observability 开发规范

新增 log、metric 或 span 前，先确定它回答什么问题，以及哪个 boundary 拥有这项 observation。
字段名称、单位、outcome、error category 和 correlation 语义遵循
[operational field contract](../docs/operate/observability.md)。
[RFC 0046](../rfcs/0046_observability_foundations.md) 定义 privacy 与 signal ownership。

## 新增 operational event

使用 module logger 和 `powercontext._logging.log_safely`。Message 使用固定文本，批准的诊断值放入 structured fields。
优先记录有用的状态变化和失败；详细的 stage completion 通常使用 `DEBUG`。
已有 transport/application instrumentation 会统计次数和耗时，不要重复创建这些工作单元。

```python
import logging
from time import perf_counter

from powercontext._logging import log_safely

logger = logging.getLogger(__name__)


def record_search_completion(started_at: float) -> None:
    log_safely(
        logger,
        logging.DEBUG,
        "Memory search completed",
        extra={
            "event": "memory.search.completed",
            "operation": "memory.search",
            "unit": "stage",
            "outcome": "success",
            "duration_ms": max(perf_counter() - started_at, 0) * 1000,
        },
    )
```

在所属 search stage 成功完成后调用。这里的 operation 与 Runtime stage name 一致；application events 使用
HTTP contract 声明的 operation ID，例如 `search_memory`，HTTP 和 MCP 使用相同名称。
示例中的字段已在 allowlist 中，不需要修改 formatter。它不是 public instrumentation API。

在观察到 failure 或 cancellation 的 boundary 记录结果。No-op 和 policy block 与 error 保持区别。
使用已登记的 diagnostic code，以及可用时实际映射的 status，由 adapter 派生有限取值的 `error_category`。
不要把任意 exception message 当作 code。Cancellation 必须继续向上传播。

## 复用 execution identity

Server filter 从已绑定的 request context 补充缺失的 `request_id`，并关联当前 trace/span IDs。
Event 显式提供的 request ID 会保留。不要在 Runtime 方法中生成 request ID。
关闭 recording/export 后仍可能存在 trace ID，因此 correlation ID 本身不能证明 backend 保存了 trace。

Spawn runner 为 child events 绑定 assignment 拥有的 worker fields。Supervisor 使用同一个 `worker_id`
关联该 attempt；每个进程有自己的 `service_instance_id`。Scheduled work 清除偶然继承的 request correlation。
在已配置的 worker 内只需输出自己的 event，不要重新生成这些 identities。

`artifact_processing.worker.returned` 表示 child 返回结果；
`artifact_processing.worker.acknowledged` 才表示 supervisor 验证了 durable acknowledgement。
新增 lifecycle event 时保留这一区别，不能仅凭 process exit 判断业务完成。

## 扩展 structured fields

JSON formatter 会忽略未登记的 extras。确需新增字段时：

1. 定义含义、类型、单位、允许值和 privacy boundary。Count 必须说明统计单元；
   RSS high-water mark 不能描述为单次 request allocation。
2. 更新 `src/powercontext/server/logging.py` 中适用的 allowlist：通用字段为 `_OPERATIONAL_FIELDS`，
   processing 为 `_PROCESSING_FIELDS`，`code.*` events 为 `_CODE_FIELDS`。
3. 验证 JSON 和 console 输出。`_HumanContextFilter` 使用相同的 allowlists；request、trace 和 service
   identity 使用专用 console rendering，code 专用字段仅在 `code.*` events 中输出。
4. 更新中英文 operational field contract，并补充针对可观察输出的 regression test。
   重命名或删除已发布字段、event name 前，必须检查 compatibility。

Service identity 来自 `src/powercontext/server/telemetry.py`，logs 和 traces 共用同一 resource identity。
Error classification 来自 `powercontext._logging.error_category`；`blocked` 等 domain-owned category
由所属 boundary 明确设置。各 adapter 不应自行定义不同词汇。

## 保持 privacy 和 ownership

不要记录 Source/Memory content、query、prompt、response、vector、credential、authorization header、
完整 database URL 或敏感 filesystem path。Allowlist 只控制字段，不会自动对值脱敏。
这些值也不能拼入 message 或原样包含在 provider exception 中。
只有确认 error values 和关联上下文不含敏感数据后，才使用 `exc_info`。

Server 配置 handlers 和 format，通过 `worker_initializer` 向自动构建的 spawn workers 注入可序列化的
`WorkerLogging` bootstrap。Custom bindings 自己管理 launcher initialization。
通过 SDK 导入或运行 Runtime 时，不得配置 global logging。
Runtime observations 不依赖 Server settings、Prometheus 或 OTel implementation types。

Operational logs 保持 best-effort。Server handler 隔离 filter、formatter 和 write failures，
每个 handler 最多输出一次无内容的 failure notification。
不要把 logs 或 sampled traces 当作 durable audit、billing 或 commit ledger。
Request、trace、scope、source 和 worker identities 不作为 metric labels。

## 验证行为

运行 logging 与真实 worker 的 focused checks：

```bash
uv run pytest tests/test_server_logging.py tests/e2e/test_worker_logging.py -q
```

新增 instrumentation 时，验证可能回归的可观察结果：success、适用的 failure/cancellation、必要字段输出、
敏感值排除和 failure isolation。跨进程支持必须用真实 spawned worker 验证；
直接调用 hook 或替换 worker entrypoint 不能证明内置 worker 的完整覆盖。
Model provider 或 OTLP backend 的支持声明需要各自的真实执行证据。交付前运行 `make check`。

## Child execution 与 metric observations

Server 通过 Runtime 的可选 `worker_entrypoint_wrapper` 注入 `WorkerTracing.wrap`。Wrapper 在 launch 时构建，
只把当前 supervisor execution context 传过 spawn。Child composition 读取可选 `worker_observability` context，
构建 instrumented models 和 runtime stages；不要在 Runtime 中导入 Server settings 或自行配置 provider。
Custom launcher 自己负责 bootstrap。

Processing observation 在最终 reap 后产生，只有验证 acknowledgement 后才计为 `success`。Telemetry observer 必须是
同步、有界、无 I/O 的 callback，且不能改变 domain outcome。Queue-loss telemetry 由拥有 admission/write result 的
recorder 产生。Spawn worker 累计各 recorder 的丢失增量，在资源清理后通过成功或失败结果回传一份有界 summary。
Supervisor 每个 attempt 只报告一次；缺少 report 表示未知。后续 acknowledgement 验证失败也不能丢弃已收到的计数。
Queue gauge 仅表示父进程 recorder；child 丢失计数累加到本地 snapshot，不覆盖它。
Observation timestamp 与 scrape time 要明确区分，并记录
cache、reset 以及覆盖不完整的语义。

通过默认 `uv sync` 新建的开发环境不包含 `tracing-otlp`。在该环境中，`make test` 会跳过 OTLP worker tests
和 worker model-usage tests 中的 tracing 用例。`make check` 不执行这些测试。
`Main` workflow 的 `worker-observability` job 安装该 extra，
并要求所选测试全部通过，不允许 skip：

```bash
uv run --locked --extra tracing-otlp pytest tests/e2e/test_worker_otlp.py \
  tests/e2e/test_worker_model_usage.py tests/test_observability_health.py -q
```

这些检查覆盖真实 spawn、OTLP/HTTP、本地 model fixture、Pydantic AI propagation 和 metrics HTTP surface。
这些证据证明本地 reception，不能代替外部 backend 的存储与查询验收。

Background CLI listener 生命周期测试需要可丢弃的 OceanBase 实例及
`POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL`。`E2E harness` workflow 的 `atomic-memory-clean-oceanbase` job
通过 clean migration acceptance runner 执行 `tests/e2e/test_background_metrics.py`，不允许测试 skip。
该测试先初始化新建数据库，再启动真实 background CLI，检查 listener 启动、metrics 授权、business routes
不可用，以及 SIGTERM 后正常退出。Listener 启动预算不包含首次建表与索引创建。
CI 在 migration reports 中保留 `background-metrics/background.log`；启动失败时输出 child return code
和有界 log tail。此步骤通过后，才能认定 backend 验收通过。未设置 backend 变量时，默认测试会跳过此用例。
