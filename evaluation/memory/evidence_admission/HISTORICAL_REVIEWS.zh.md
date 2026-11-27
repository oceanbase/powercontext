# 历史审阅回执

[English](HISTORICAL_REVIEWS.md)

这些回执记录对特定历史执行的判断，不是可复用实验设置。
它们与实现、夹具和冻结协议参数分开分发。[原结果包](RESULTS_ARTIFACT.md)的字节和校验和保持不变。

## 补充包可用性与核验

补充包已在[发布评论](https://github.com/oceanbase/powercontext/pull/2541#issuecomment-7166559603)中公开提供。
下载副本已确认与原始压缩包逐字节相同，两份审阅收据的校验和也已核验通过。

- 压缩包：`powercontext-2074-review-receipts-0c467f5b.tar.gz`
- 下载：[powercontext-2074-review-receipts-0c467f5b.tar.gz](https://github.com/user-attachments/files/35418256/powercontext-2074-review-receipts-0c467f5b.tar.gz)
- 压缩包大小：3,220 字节
- SHA-256：`6237f127b295f506ee8e7bc06e0f165ac71db38874428c7c7f047e50d4af7cee`
- 来源修订：`0c467f5b79fc1abec3236dd730d9e91227c217dd`
- 包内根目录：`historical-reviews/v1/`

| 相对该根目录的文件 | 字节 | SHA-256 |
| --- | ---: | --- |
| `human-sample-review-v1.json` | 13967 | `0febe93341dd28294cca698f009891badacdd2cff9fb50be8cccfa99d44e9dbf` |
| `v6/recorded-atom-reviews.json` | 3089 | `b9c48da969c4d7e95f8da0741ea67b03cd60319f2067c05a01758ebac0a75e2f` |

`index.json` 记录成员大小、哈希、原路径及原结果包校验和。
读取器采用代码或 v6 冻结合同固定的预期校验和，不信任索引自行声明的校验和。
原结果包中的声明与这里的来源声明不是同一字节版本，不能直接替换。

将 `REVIEW_ARCHIVE` 设为本地下载文件路径，`REVIEW_EXTRACT` 设为仓库外的解压目录。
下载、核验并解压补充包：

```bash
curl --fail --location --output "$REVIEW_ARCHIVE" \
  'https://github.com/user-attachments/files/35418256/powercontext-2074-review-receipts-0c467f5b.tar.gz'
printf '%s  %s\n' '6237f127b295f506ee8e7bc06e0f165ac71db38874428c7c7f047e50d4af7cee' "$REVIEW_ARCHIVE" | sha256sum --check -
tar -tzf "$REVIEW_ARCHIVE"
mkdir -p "$REVIEW_EXTRACT"
tar -xzf "$REVIEW_ARCHIVE" -C "$REVIEW_EXTRACT"
export POWERCONTEXT_EVIDENCE_ADMISSION_REVIEWS="$REVIEW_EXTRACT/historical-reviews/v1"
```

`POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS` 仍指向原结果包解压后的 `results` 目录。
两个变量对应不同证据包。默认离线测试不需要任何证据包、网络或凭据。
读取器不会下载、修补或替换缺失回执；文件缺失、字节或哈希变化时明确失败。
包内相对路径和符号链接不能越出配置的审阅根目录。显式指定的自定义声明路径由操作者选择，
按同时提供的明确校验和验证。

## 读取入口与身份

v6 历史回放使用 assistant 判断前，会按未改动的冻结合同校验原 `atom_reviews_sha256`，
保留原清单绑定，并在发布报告前再次核对回执。
普通 v6 协议读取和 v7 依赖身份无需外部审阅文件。当前 v6 身份声明外部依赖及其**预期**校验和，
不声称已经读取或验证文件。新回放成功后，报告另行记录已核验的回执校验和。

当前实现产生新的代码身份。现有报告、冻结代码身份和历史回执不会被重标为当前读取器的执行。
原历史复现仍需要[匹配的代码、锁定环境和私有审计材料](HISTORY_REPRODUCTION.md)。
缺失私有响应仍不可用，这些读取器不会发起新模型调用。

sample-review 的 `build` 命令省略 `--declaration` 时，从审阅根目录读取固定的历史声明。
显式选择声明时，须同时提供 `--declaration PATH` 和 `--declaration-sha256 SHA256`。
先验证字节，再执行已有的结果、队列、准备清单、状态、输出和候选绑定核验。
文件校验和一致不代表会话匹配、完整语义审阅或正式验收通过。
完整命令见[人工审阅步骤](HUMAN_REVIEW.zh.md)。

这些回执保留历史部分人工判断和 assistant 诊断，不重算归档结果，不清除语义未决，
不改变冻结的 `insufficient_evidence` 结论。
