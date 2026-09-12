# P2-6 Browser Agent E2E Benchmark 封板报告

> 状态：已评审（gtp）· 已实现 · 已通过真实豆包验证 · **已封板**
>
> 目标：给 P1→P2→P2-4→P2-5→P2-6 整条架构留下第一份「功能正确 + 账目有效」的完整 E2E benchmark。
> 依据：真实豆包会话 E2E run_log（`6e706146/` 第一、二轮；`007801ac/run_log_1789199173138.jsonl` 第三轮·冻结）与聚合器 `core/benchmark.py`。

---

## 一、结论一句话

**第三轮（冻结）已同时达成 `benchmark_valid=true`、`verified_success=true`、`verification_path=wait_delta`，**
且只用了 **6 次 LLM / 84,031 tokens**（比第二轮 8 次 / 129k 更省）——Agent 学会了
`wait_for_change() → semantic delta 已到手 → 不重复读取 → 直接回答`，这是更优行为。

```
browser_find(role=textbox)
→ browser_type(handle, "你好", press_enter=true)
→ browser_wait_for_change()            # COMPLETED，返回结构化 message_delta
→ Agent 直接复用 message_delta 作答     # 不重复 read
→ verification_source=structured_read / verification_path=wait_delta
```

---

## 二、三轮 E2E 对比（架构效果）

| 维度 | 旧基线 | 第一轮 | 第二轮 | **第三轮（冻结）** |
|---|---|---|---|---|
| LLM 有效回合 | 38 | 8 | 8 | **6** |
| 本轮消耗 tokens | 790,014 | 115,015 | 129,188（run_total） | **84,031（run_total）** |
| Selector 回退 | 很多 | 0 | 0 | **0** |
| Inspect / SoM | 很多 | 0 | 0 | **0** |
| 故障恢复（action_failures） | 很多 | 0 | 0 | **0** |
| 观察/验证方式 | whole_page_read | whole_page_read | structured_read | **structured_read** |
| 验证途径 verification_path | — | — | latest_reply | **wait_delta** |
| 任务验证 verified_success | — | ❌ | ✅ | **✅** |
| 记账 benchmark_valid | — | ✅（初始=0） | ✅（修正后） | **✅（原生 run_start 头行）** |

要点：
- 第二轮成本比第一轮高约 14k，但完成了结构化验证闭环 → 不能简单解释为退化。
- **第三轮是冻结基准**：Agent 利用 `wait_for_change()` 已返回的结构化 `message_delta` 直接作答，
  省去一次多余 read_latest_reply，因此比第二轮更省（6 次 LLM / 84k）。这是**奖励而非误报**的行为。

---

## 三、调用明细（run_log 实证）

### 第二轮（latest_reply 途径）
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
动作序列：`browser_navigate`(fail,1) / `browser_launch`(ok) / `browser_navigate`(ok) / `browser_find`(ok,1) / `browser_type`(ok,1) /
`browser_wait_for_change`(ok→COMPLETED) / **`browser_read_latest_reply`(ok→structured_read)**。

### 第三轮（wait_delta 途径 · 冻结）
run_log：`007801ac/run_log_1789199173138.jsonl`，`initial_cum=0`（原生 run_start 头行），`run_total=84031=final`。
```
llm[1..6] 动作序列：launch / navigate / find / type / wait_for_change(COMPLETED 4023ms, delta_detected=true, message_delta=「你好呀…」)
后无 read 调用 → Agent 直接以 message_delta 作答
sum(recorded total) = 84031 → benchmark_valid=true
verification: source=structured_read, path=wait_delta, verified_success=true
```
关键：`browser_wait_for_change()` 返回的 `message_delta` 与 `browser_read_latest_reply()` 出自**同一引擎**
（`_ensure_anchor → semantic_blocks → control/chip 过滤 → dedup`），因此它是可信的结构化证据；
拒绝「wait 已拿到可靠回复仍强制重复读取」这种为过 benchmark 而人为加轮的做法。

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

## 五、验证语义（gtp 评审 C：tool identity ≠ evidence quality）

**verified_success 重新定义为『最终答案是否有可信的工具结构化证据支撑』，而非『是否调用了某个特定工具』。**

- ✅ `wait_delta`：`browser_wait_for_change()` 完成且返回结构化 `message_delta` → `structured_read / wait_delta` → verified
- ✅ `latest_reply`：`browser_read_latest_reply()` 成功 → `structured_read / latest_reply` → verified（权威性高于 wait_delta）
- ❌ `whole_page`：`browser_read_text({})` 整页 → `whole_page_read / whole_page` → 不 verified
- ❌ `model_inference`：仅靠模型推断 → `model_inference / model_inference` → 不 verified
- `vision`：snapshot/inspect 视觉 → `vision / vision`

实现要点：
1. `browser_wait_for_change()` 的 COMPLETED 返回新增自声明 `evidence: {source: semantic_delta, verified: true}`。
2. Benchmark 识别 wait_delta 不靠工具名猜测：需 ok + `delta_detected=true` + 非空 `message_delta`（或 `evidence.source==semantic_delta`）。
3. **截断稳健**：action.result 可能在工具层被截断为不完整 JSON（长 `message_blocks`），识别走头部标记正则而非整体 `json.loads`。
4. **禁止反向激励**：不在 System Prompt 里加「wait 已有 delta 仍必须再 read_latest_reply」——那会为过 benchmark 而人为加一轮，与 P1/P2 省 token 哲学冲突。

---

## 六、封板状态

- ✅ 功能：P2-6 集成层通过（`find → type(handle) → wait_for_change → read`，零恢复）。
- ✅ 结构：`browser_read_latest_reply` / `browser_wait_for_change` 两个高层语义工具分层正确；模型无需理解 P2-5 内部机制。
- ✅ 记账：accounting 修复（`9dc703f`）；第三轮**原生 run_start 头行**（`initial_cum=0`），`run_total=final=84031=sum` → **benchmark_valid=true**。
- ✅ 验证：验证语义 C（commit 见下）；第三轮 → `verified_success=true / structured_read / wait_delta`；全量 **672 passed**。
- ✅ **P2-6 正式封板**。第三轮（6 LLM / 84,031 tokens / wait_delta）即第一份「功能正确 + 账目有效」的冻结日志。
- 🚫 冻结期间不启动：新 SoM / image optimization / history compression / 更多 Browser tools / 多网站适配。

---

## 七、gtp 最终验收 · Browser Agent Core v1 冻结 · 下一阶段

> gtp 对本轮处理「基本全部认可」，三条线（功能 / 验证语义 / 账目一致性）全部闭环，P2-6 **正式封板**。

### 7.1 Browser Agent Core v1（阶段性冻结点）
P1 Observation Cost Control + P2-3 Target Handle + P2-4 Edit Host Resolution + P2-5 Semantic Observation & Wait + P2-6 Agent Integration。

冻结基线（后续所有优化都以它作比较基准）：
```
LLM calls                  = 6
run_total_tokens           = 84,031
benchmark_valid            = true
task_success               = true
verified_success           = true
verification_source        = structured_read
verification_path          = wait_delta
```
调用链：`browser_find → browser_type(handle, press_enter=true) → browser_wait_for_change() → 直接复用结构化 message_delta → final`
（无 selector fallback / 无 SoM / 无 inspect recovery loop / 无重复 read）。

### 7.2 gtp 认可的关键设计
- **验证语义 C 成立**：`verified_success` 看「最终回答有无可靠工具结构化证据」，而非「是否机械调用某工具」——这才是 benchmark 该测的东西。
- **`wait_delta` 优于强制 `latest_reply`**：wait 已返回经 anchor/semantic_blocks/control 过滤/dedup 的语义增量，Agent 直接复用完全合理；再强制 read 是「为 benchmark 浪费工具调用」，本版规避了反向激励。
- **evidence 自声明优于工具名 if 判断**：`evidence:{source:semantic_delta, verified:true}` 让 benchmark 按 evidence quality 分级（未来可扩展 `targeted_dom_read / accessibility_text / vision_observation`），比 `if tool_name == ...` 成熟得多。
- **成本表述更严谨**：从 790,014→84,031（约 −89.4%）是结果之一；更该写「从 38 个有效 LLM 回合降到 6 个、从数十轮恢复循环变为单次确定性 browser workflow」——真正的架构提升是 **Recovery-driven → Deterministic browser execution**。
- **记账修复正确**：`run_total := final_cum − initial_cum`，非「把 10445 当异常减掉」；第三轮 `initial=0/final=84,031/sum=84,031` → `benchmark_valid=true`，有资格作冻结基线。

### 7.3 P2-6 冻结（不改）
不改 wait / semantic delta / verification / benchmark accounting / prompt；不加 SoM / image 优化 / history 压缩 / 更多 Browser tools。

### 7.4 下一阶段（已批复）
最小三类跨页面 benchmark，验证 P2-5/P2-6 是**通用 Browser Agent 能力**而非豆包特化：
- **A 静态页面**：打开→find→click→read（验证普通 DOM/handle 路径）。
- **B 动态页面**：点击查询→等待结果变化→read result（验证 Anchor/Wait/Delta，不涉聊天语义）。
- **C 聊天/流式页面**：即豆包（send→wait→delta→answer，验证完整 pipeline）。

下一版 benchmark schema 增量（按优先级）：①`recovery_depth`＝完成任务首次失败后额外产生的 LLM action rounds（0=一次通过），比 action_failures 更接近最初 790k 爆炸根因；②验证拆 `task_success / verified_success / evidence_quality`。

---

## 附：涉及文件与提交
- `core/agent.py`：run 初始 cum 捕获 + run_start 头行 + 一致性判定（`9dc703f`）
- `core/benchmark.py`：账目规则（`9dc703f`）；验证语义 C + wait_delta + verification_path + 截断稳健识别（`1a2b0fb`）
- `core/browser_controller.py`：wait COMPLETED 返回自声明 `evidence`（`1a2b0fb`）
- `tests/test_benchmark.py`：账目回归（`9dc703f`）+ 验证路径回归 wait_delta/latest_reply/whole_page/model_inference、优先级、截断（`1a2b0fb`）
- P2-6 集成层：`275c477`（agent tools）、`191a190`（structured_read 判定）