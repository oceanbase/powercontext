# 实验 v4 离线结果

实验 v4 完成 1620 个离线样本，v3 候选的必需事实保留为 216/216、171/171、108/108，合计 495/495。共同可容纳性参考与原始 v3 实验一致；本实验内候选相对于 v1、v2 对照组没有已观察到的准备回归。

但跨协议比较暴露一项对照组回归：`probe-shifted-middle` 的 `bounded-fragments-v2` 在 4096 预算下，三次重放都由 3/3 降为 2/3。去掉内部 ID 让封装更短，贪心选择增加早期噪声范围并挤掉了原本选中的中部事实。因此不能声称所有组都没有退化，不执行本协议的真实 smoke。

结果保存于 `.artifacts/evidence-admission/v4-preparation-primary`、`v4-preparation-validation`、`v4-preparation-additional`；联合结果与跨协议比较分别为 `v4-comparison.json`、`v4-cross-protocol-comparison.json`，请求计划为 `v4-request-plan.json`。完整评测实现另存为 `v4-evaluator.tar.gz`，其内容身份可从运行清单核对；没有覆盖原始 v3 离线数据或首轮真实失败 smoke。

为满足全组无退化要求，使用独立冻结的实验 v5，将片段选择与模型封装节省分离，见 [协议](V5_PROTOCOL.md)。v4 的失败不通过修改标注、扩大预算或挑选重放消除。
