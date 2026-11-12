# Pipeline 小样本准备结果

独立执行器已接入真实 `AtomicMemoryGenerationPipeline.extract()`，提供源码提交冻结校验、逐次发送、
私有审计、费用预留、失败停止与显式语义审阅。候选选择和 v10 指令、编码、评分均未改动。
此准备结果不包含线上请求，不授权生产；历史 v9 原子性失败和 v10/v11 比较结果保留。

完整本地回归 388 项通过，新增回归 17 项通过。模块与根项目类型检查通过；两套锁文件、仓库 hooks、
wheel/sdist 的九个源码与合同文件通过验证。Ruff 0.16 全评估仍有 13 项既有诊断，整个 evaluation 类型检查
仍有一项 LongMemEval 既有能力错误。未重跑其他 Python 版本。

实际 SDK 的模拟传输完成两次 Pipeline 调用，受控输出保留 4/4 和 2/2 原子事实并成功使用 copy。
消息文本估算分别为 3682、3831 token，均小于 4096；HTTP JSON 整包估算为 3918、4243，不能当作实际计费 token。
模拟响应及审阅不能证明真实模型的原子性、copy 或 Unicode 保真；旧整句 scorer 未决分数独立保留。

[机器结果](results/v12-readiness/sdk-preflight.json)保存代码身份、请求哈希、成本和受控诊断，
[验证摘要](results/v12-readiness/validation-summary.json)记录检查边界。真实执行必须另有实际 Git HEAD 冻结、
相同身份的预检和计划绑定授权。旧计划审批文件不能直接替换为新计划审批。
操作步骤与条件费用定义见 [执行合同](V12_PROTOCOL.zh.md)。最终结论保持 `insufficient_evidence`。
