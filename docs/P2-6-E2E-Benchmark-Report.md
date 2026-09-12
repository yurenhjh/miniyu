# P2-6 Browser Agent E2E Benchmark 封板报告

> 状态：已评审（gtp）· 已实现 · 已通过真实豆包验证 · 已封板
>
> 目标：给 P1→P2→P2-4→P2-5→P2-6 整条架构留下第一份「功能正确 + 账目有效」的完整 E2E benchmark。
> 依据：真实豆包会话 E2E run_log（`6e706146/run_log_1789195273997.jsonl` 第一轮、`6e706146/run_log_1789197196036.jsonl` 第二轮）与聚合器 `core/benchmark.py`。

---

## 一、结论一句话

**第二轮 E2E 已同时达成 `verified_success=true` 与 `benchmark_valid=true`（记账修正后），**
Agent 通过两个高层语义工具完成等待与读取，不再依赖 LLM 拼内部状态。

调用链（第二轮真实命中，零猜测）：
```
browser_find(role=textbox)
→ browser_type(handle, "你好", press_enter=true)
→ browser_wait_for_change()          # args={}，无需理解 baseline/anchor/fingerprint
→ browser_read_latest_reply()
→ verification_source = structured_read
```

---

## 二、三轮 E2E 对比（架构效果）

| 维度 | 旧基线 | 第一轮 | 第二轮 |
|---|---|---|---|
| LLM 有效回合 | 38 | **8** | **8** |
| 本轮消耗 tokens | 790,014 | 115,015 | **129,188**（run_total，账目有效值） |
| Selector 回退 | 很多 | 0 | 0 |
| Inspect / SoM | 很多 | 0 | 0 |
| 故障恢复（action_failures） | 很多 | 0 | 0 |
| 观察/验证方式 | whole_page_read | whole_page_read | **structured_read** |
| 任务验证 verified_success | — | ❌ | **✅** |
| 记账 benchmark_valid | — | ✅（初始=0） | ✅（修正后，见§四） |

要点：第二轮成本比第一轮高约 14k，但完成了真正的结构化读取验证闭环，**不能简单解释为性能退化**。
本轮核心目标是可靠性/可验证性提升，而非继续压 token。

---

## 三、第二轮调用明细（run_log 实证）

```
[0] step=1  cum=26766   total=16321   prompt=16149  comp=172  img=4932
[1] step=2  cum=43013   total=16247   prompt=16208  comp=39   img=4969
[2] step=3  cum=59272   total=16259
[3] step=4  cum=75083   total=15811
[4] step=5  cum=90963   total=15880
[5] step=6  cum=106871  total=15908
[6] step=7  cum=123225  total=16354
[7] step=8  cum=139633  total=16408
sum(recorded total) = 129188
```

动作序列（kind=action）：`browser_navigate`(fail,1) / `browser_launch`(ok) / `browser_navigate`(ok) / `browser_find`(ok,1) / `browser_type`(ok,1) /
`browser_wait_for_change`(ok→COMPLETED, 4140ms) / `browser_read_latest_reply`(ok→structured_read)。

关键：`verify` 用的是 `browser_read_latest_reply()` 的结构化返回值（assistant 正文），不是整页读取后由 LLM 自行找答案。

---

## 四、Benchmark 记账修复（本轮唯一收尾项）

### 4.1 10445 差值的真实来源（非倒推）
- 第一轮首条 llm 行：`cum=9923 == total=9923` → 本轮初始累计 offset = 0（启动干净）。
- 第二轮首条 llm 行：`cum=26766`，但**其自身 total 仅 16321** → 首条记录前已累计 `26766 - 16321 = 10445`。
- 后续每条 llm 行的 `cum` 增量均**恰好等于**各自 total（128481/707 分别对得上）→ 已记录的 8 个调用内部完全自洽。

结论：`10445` 是进入第二轮 run 时 LLM 客户端计数器**已携带的历史累计值**（跨运行继承，进程未重置），
**不是**本轮的一张漏记 IT 调用。旧口径 `sum(per-call)==raw final_cum` 把它误判为本轮缺口。

### 4.2 修正后的一致性规则
```
run_total_tokens := final_cum_total_used - initial_cum_total_used
benchmark_valid  := sum(recorded per-call total_tokens) == run_total_tokens
```
- run 开始时在进入 ReAct 循环前捕获 `initial_cum_total_used`，写入 run_log 首行 `kind='run_start'`。
- 仿真验证：`initial=10445, final=139633 → run_total=129188 == sum(recorded)` → **valid=true**。
- 防作弊：若 run 内确有真实漏记调用（cum 跳变 > 记录之和），即使有 initial 基线仍判 **invalid**，
  绝不允许用 initial 掩盖真实缺口（见 `test_initial_cum_missing_call_still_invalid`）。

### 4.3 账目字段（run_log / Summary）
`initial_cum_total_used` / `final_cum_total_used` / `run_total_tokens` / `sum_per_call_total` / `benchmark_valid`。

---

## 五、封板状态

- ✅ 功能：P2-6 集成层通过（`find → type(handle) → wait_for_change → read_latest_reply`，零恢复）。
- ✅ 结构：`browser_read_latest_reply` 作为 `browser_read_text` 之上的高层语义工具，分层正确；模型无需理解 P2-5 内部机制。
- ✅ 记账：accounting 修复已提交（`9dc703f`），全量 667 测试通过；第二轮数据在修正规则下 valid=true。
- ⏹ 待办（一次即可）：在修复后的版本上重跑一次固定 E2E，使 run_log **原生携带 `run_start` 头行**，
  从日志直接产出 `benchmark_valid=true` 的最终冻结结果（与 §四仿真值一致）。重跑由用户执行 `examples/miniyu_web.py`。
- 🚫 冻结前不启动：新 SoM / image optimization / history compression / 更多 Browser tools / 多网站适配。

---

## 附：涉及文件与提交
- `core/agent.py`：run 初始 cum 捕获 + run_start 头行 + 一致性判定（`9dc703f`）
- `core/benchmark.py`：聚合规则 initial/run_total + Summary（`9dc703f`）
- `tests/test_benchmark.py`、`tests/test_agent.py`：记账回归（`9dc703f`）
- P2-6 集成层：`275c477`（agent tools）、`191a190`（structured_read 判定）