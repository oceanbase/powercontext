# 抽查记录定位确认表

本表只确认之前查过哪次调用，不补写候选语义结论。当前17项均未确认。
依据为已提交的人工抽查附录；调用缩写仅用于阅读，完整身份见 [确认模板](results/acceptance-closeout-20261107/human-confirmation-template.json)。

每条选择自己实际查过的调用；若查过多个，可以列多个。无法回忆时保留未确认，不按输出相似度代选。
确认 arm 后仍有多个请求时，须继续确认从0开始的 `input_index`。

| 抽查描述 | 可选调用：arm / input_index / call_id 前12位 | 已确认 |
| --- | --- | --- |
| forged-role-marker-2048-r0 | bounded-fragments-v3 / 0 / `3ef7384bc35f`<br>complete-projection-v1 / 0 / `e6f4fed6805b`<br>window-reduction-v1 / 0 / `2713e0505860` | 未确认 |
| forged-role-marker-2048-r1 | bounded-fragments-v3 / 0 / `2242a0d40b2c`<br>complete-projection-v1 / 0 / `f188e65bf78c`<br>window-reduction-v1 / 0 / `a3a1cedd5ba6` | 未确认 |
| forged-role-marker-2048-r2 | bounded-fragments-v3 / 0 / `ffc33cdad5e8`<br>complete-projection-v1 / 0 / `0319d0c12e19`<br>window-reduction-v1 / 0 / `4e45cbef5947` | 未确认 |
| forged-role-marker-4096-r0 | bounded-fragments-v3 / 0 / `5083306c482d`<br>complete-projection-v1 / 0 / `6f91547226e6`<br>window-reduction-v1 / 0 / `3d98ed310a47` | 未确认 |
| identity-replay-2048-r0 | bounded-fragments-v3 / 0 / `e89ffe1de46a`<br>complete-projection-v1 / 0 / `ccca437f4564`<br>window-reduction-v1 / 0 / `14f6ca134c5b` | 未确认 |
| identity-replay-2048-r1 | bounded-fragments-v3 / 0 / `aefe91d91718`<br>complete-projection-v1 / 0 / `7f76ae87cca9`<br>window-reduction-v1 / 0 / `40bba734e4a6` | 未确认 |
| identity-replay-2048-r2 | bounded-fragments-v3 / 0 / `454f8c3dd835`<br>complete-projection-v1 / 0 / `6b270f6c3aa4`<br>window-reduction-v1 / 0 / `13348221fa81` | 未确认 |
| identity-replay-4096-r0 | bounded-fragments-v3 / 0 / `2f2011e40ea0`<br>complete-projection-v1 / 0 / `1beb8deac45c`<br>window-reduction-v1 / 0 / `99f254c105c8` | 未确认 |
| many-source-overflow-2048-r1 | bounded-fragments-v3 / 1 / `4a6e2c736601`<br>window-reduction-v1 / 1 / `ba70dfe5331a` | 未确认 |
| small-complete-2048-r1 | bounded-fragments-v3 / 0 / `1a91684ae39d`<br>window-reduction-v1 / 0 / `eff8f625ceee` | 未确认 |
| small-complete-2048-r2 | bounded-fragments-v3 / 0 / `d9245aca8e4e`<br>complete-projection-v1 / 0 / `fb39df60aedf`<br>window-reduction-v1 / 0 / `701fcba4ddaf` | 未确认 |
| small-complete-4096-r0 | bounded-fragments-v3 / 0 / `95ced5e93c56`<br>complete-projection-v1 / 0 / `fa01f4d7fb9d`<br>window-reduction-v1 / 0 / `d08b9398b4dc` | 未确认 |
| small-complete-4096-r1 | bounded-fragments-v3 / 0 / `1a7f185b950e`<br>complete-projection-v1 / 0 / `a892bd39fc1c`<br>window-reduction-v1 / 0 / `d6258963ee8c` | 未确认 |
| validation-reordered-sources-2048-r0 | bounded-fragments-v3 / 0 / `78a5f2ec8654`<br>bounded-fragments-v3 / 1 / `65be826ed6eb`<br>window-reduction-v1 / 2 / `4652e28b236a` | 未确认 |
| validation-reordered-sources-4096-r0 | bounded-fragments-v3 / 0 / `9c5b000fbe14`<br>bounded-fragments-v3 / 1 / `4497be6610a9`<br>window-reduction-v1 / 1 / `1c989d1f9c64` | 未确认 |
| validation-reordered-sources-4096-r1 | bounded-fragments-v3 / 1 / `90ca7c7b978a`<br>window-reduction-v1 / 0 / `3192baf01207` | 未确认 |
| validation-reordered-sources-4096-r2 | bounded-fragments-v3 / 0 / `27d3a4b889f2`<br>window-reduction-v1 / 0 / `a515580630aa` | 未确认 |

## 填写方式

复制 JSON 模板到新的本地文件。在实际查过的记录中填写完整 `confirmed_call_ids`、审阅标识和日期；
不确定的项保留 null。模板没有预选答案，也不是可直接导入评分器的 v10 审阅文件。
完成后应新建声明和附录，保留当前模板、原46条描述、旧哈希和旧评分；不要覆盖已有封存文件。

定位确认不代表支持性、原子性、归因及事实覆盖全部通过。完整候选审阅必须分别给出
`supported`、`atomic`、`false_attribution`、`entailed_atom_ids`，并绑定原输出与上下文。
缺少某一项就保留部分判断。已有两处错误归因及13项原子性失败继续有效。

完整语义队列见 [semantic-review-queue.json](results/v20-review/semantic-review-queue.json)。
有效输出226项与被拒绝输出307项是不同范围；确认本表不会从这533项中自动减数。

若只交付 insufficient_evidence，本表和未决项可以作为明确限制保留；不要求为了收尾再次发起线上调用。
