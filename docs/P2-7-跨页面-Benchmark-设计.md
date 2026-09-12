# Browser Agent 跨页面 Benchmark 设计（P2-7·规划+单测阶段）

> 状态：**设计 + recovery_depth(v1) 实现 + 本地 demo 雏形 + 单测已完成；未执行真实 benchmark**
> 依据：gtp 评审（2026-09-13）——「下一阶段采用主集 + 泛化抽查的分层结构」，先搭实验框架，不碰已冻结的 P2-6 运行链路。

---

## 一、背景与目标

P2-6 已在真实豆包聊天页冻结出 **Browser Agent Core v1**（6 LLM / 84,031 tokens / verified_success=true / path=wait_delta）。
该基线证明 Core 在**聊天/流式场景**闭环。但其用例是「豆包特化」——尚不能证明 P2-5/P2-6 是**通用 Browser Agent 能力**。

本阶段目标：建一个**分层、隔离评价**的跨页面 benchmark，让后续任何改动都能回答：
> 是通用能力变好了，还是只针对某一个网页优化了？

同时新增指标 **recovery_depth**，量化「失败后 LLM 被迫额外重规划的轮次」——它比 `action_failures` 更接近
历史 790k token 爆炸的根因（失败→重规划→再失败→再规划…）。

---

## 二、四类 Benchmark（分层，隔离评价，不折算总分）

| 代号 | 名称 | 页面来源 | 验证点 | 是否正式通过门槛 |
|---|---|---|---|---|
| **A1** | Local Static | 自建 `examples/browser_benchmark/static.html` | 基础 DOM / Handle 路径：find→handle→click→read_targeted | ✅ 正式主集 |
| **A2** | Local Dynamic | 自建 `examples/browser_benchmark/dynamic.html` | **Anchor / Baseline / Wait-for-Change / Semantic Delta / Targeted Read**（非聊天） | ✅ 正式主集 |
| **C** | Doubao Chat / Streaming | 真实豆包（已冻结） | 完整 pipeline：send→wait→delta→answer | 📌 Reference（回归对照，不改） |
| **D** | Public Web Smoke | 1 个真实公共静态/动态页（未定） | 任意网页泛化抽查 | ⚠️ Smoke only，不进门槛 |

**关键原则（gtp）**
- A1/A2 是「科学实验」，D 是「泛化抽查」，C 是「真实复杂场景」。
- **D 只做 smoke**：无需登录、页面稳定、不依赖地区、少弹窗、DOM 相对普通；首次不选搜索引擎。
  D 失败标记 `external_smoke_failed`，**不**直接判 Core 回归失败。
- 四类**分别报告**，禁止平均成一个总分——否则「Local Dynamic=100% / Doubao=100% / Public=60%」的定位能力会被
  一个「82%」糊住。

---

## 三、A1 Local Static（正式主集）

**页面**：`examples/browser_benchmark/static.html`（纯离线、无外链、DOM 稳定）
**覆盖控件**：标题 / 普通文本 / button / a 链接 / input / select / checkbox / 长文本区域 / 可展开文本 / 表单 label。

**任务示例**：找到「查看更多」按钮 → click → **定向**读取展开后新增的第二段正文。
**预期验证路径**：`browser_find → browser_click(browser_find 的 handle) → browser_read_text(定向)`。
**成功条件（冻结基线形状）**：`find=1 / click=1 / read_targeted=1 / SoM=0 / selector_fallback=0 / coordinate_click=0 / recovery_depth=0`。

---

## 四、A2 Local Dynamic（正式主集，本项目最关键一环）

**页面**：`examples/browser_benchmark/dynamic.html`（离线、确定性 mock）
**结构**：城市输入框 + 「查询」按钮 + 加载态 + 结果区域；点击查询后约 **1~2 秒随机延迟**（`setTimeout`）更新结果
（`城市：北京 / 天气：晴 / 温度：26℃`）。**不模拟聊天、不依赖外网。**

**任务示例**：输入城市 → 点查询 → 等待结果变化 → 定向读取新结果。

**验证链路（证明 P2-5/P2-6 不依赖豆包语义）**
```
browser_type(城市, press_enter=False? 或直接 click 查询)
→ browser_click(查询)
→ browser_wait_for_change()   # 锚定位稳定容器；结果区域文本变化 → semantic delta
→ browser_read_text(定向) 或复用 wait 返回的 delta
```
**成功条件**：`wait=1 / delta=1 / read 定向=1 / anchor_lost=0 / loading_stuck=0 / recovery_depth=0`。

> 此任务一旦通过，即证明 **Anchor / Wait-for-Change / Semantic Delta 是通用观测原语**，
> 而非「针对豆包聊天特化」。

---

## 五、C 豆包 Chat / Streaming（Reference，冻结）

沿用 P2-6 冻结基线，**不改不开发**：
```
LLM calls = 6 · run_total = 84,031 · benchmark_valid=true
verified_success=true · source=structured_read · path=wait_delta
```
仅作为后续 **regression reference** 例行重跑；不以它为对象做进一步开发。

---

## 六、D Public Web Smoke（泛化抽查，非门槛）

- 选择 1 个稳定、免登录、少弹窗的真实公共页（白名单化；可先用文档型/类 Wikipedia/MDN 页面）。
- 任务简单：打开页面 → find 某标题 → click 页内链接 → read 目标标题/文本。
- 首次不选搜索引擎（结果动态性太强）。
- 结果独立报告；失败标 `external_smoke_failed`，**不**影响 A1/A2 的通过判定。

---

## 七、统一指标（每类都记录）

```
task_success · verified_success · benchmark_valid
llm_calls · run_total_tokens · prompt/completion/reasoning/image_tokens
find · click · type · send · wait · read · inspect · SoM(som_screenshot)
selector_fallback · coordinate_click
action_failures · recovery_depth · recovery_steps
wait_timeout · wait_anchor_lost · wait_loading_stuck
verification_source · verification_path
```
额外固化展示：A1/A2 = 正式主集；C = Reference；D = Smoke。保留 `Core v1 freeze baseline` 作比较基准。

---

## 八、recovery_depth（本轮唯一新指标）

### 8.1 定义（gtp）
```
recovery_depth = 完成任务中，首次 / 每次 action failure 之后额外产生的 LLM action rounds（轮次）
  0 = 一次通过
  1 = 失败后一次恢复
  3 = 连续三轮撞墙
  30+ ≈ 历史 790k 爆炸灾难
```
它比 `action_failures` 更接近根本问题：action failure 可能是工具内部已恢复成功，
而 **recovery_depth 真正反映 LLM 是否被迫重新思考**。

### 8.2 v1 —— 被动推断（**已实现，heuristic，不碰 runtime**）
- 位置：`core/benchmark.py` 的 `_recovery_metrics()`，仅读 run_log，不改 agent/controller/P2-6。
- 规则：每次失败 action（`kind=action, ok=False`）之后、直到下一个失败 action（或序列结束）之间
  出现的**去重后 LLM 行数** = 该次失败区间；`recovery_depth` = 各区间之和；`recovery_steps` = 逐失败分段明细。
- 判定信号：action 行的 `ok`（agent 写入的是工具自身 `result.success`），即失败=工具返回失败。
- **明确为 heuristic metric（`recovery_depth_is_heuristic_v1=true`）**：失败后的 LLM 行未必全是恢复行为
  （可能含任务正常收尾），report 中不得写成精确 recovery 事件计数。
- 单测（`tests/test_recovery_depth.py`，覆盖 gtp 6 个边界）——全部通过：
  1. failure 后无新 LLM → depth=0
  2. failure 后 1 个 LLM → depth=1
  3. failure 后多个 LLM → 正确累加
  4. 两次 failure 分段 → 逐失败独立 `llm_rounds`
  5. failure 后任务立即结束 → depth=0
  6. failure 后成功 action 但无新 LLM → depth=0
  - 附加：LLM 双写去重不虚增；`recovery_depth` 与 `benchmark_valid` / token 记账相互独立、互不污染。

### 8.3 v2 —— 显式 recovery 事件（**仅规划，不实现**）
更精确：运行时显式记录一次失败与恢复区间，替代 heuristic。schema（未来 v2）：
```json
{
  "kind": "recovery",
  "cause_step": 12,
  "cause_tool": "browser_type",
  "recovery_rounds": 2
}
```
规划要点：
- v1(v2) 双轨：本版本保留 v1 被动推断并标注 heuristic；v2 作为下一版 schema 增量，**不在此刻改 runtime**。
- v2 落地时才允许动 agent 运行链路（届时是独立本，不侵蚀 Core v1 冻结基线）。

---

## 九、本轮实施范围与冻结边界

**本轮已完成**
- ✅ `core/benchmark.py`：`recovery_depth`(v1) + `recovery_steps` + `recovery_depth_is_heuristic_v1`，同步 `format_summary`。
- ✅ `tests/test_recovery_depth.py`：9 例边界单测（全量 672 → 681，净增 9）。
- ✅ 本地 demo 雏形：`examples/browser_benchmark/static.html`、`dynamic.html`（仅页面 + 确定性 mock，未接 Agent 跑真实任务）。

**本轮明确禁止（P2-6 冻结保护）**
- ❌ 不改 `core/agent.py` / `browser_controller.py` / `browser_wait_for_change` / `semantic_blocks` / `browser_read_latest_reply` / System Prompt / SoM。
- ❌ 不为 v1 增加 agent 运行时 recovery 事件（留给 v2）。
- ❌ 不跑公共网站、不一次性跑三类 benchmark、不启动真实 Agent 任务。

**后续阶段（Phase 2，待本设计审核通过后）**
```
Phase 1  设计 + 本地 demo + 单测            （本轮，✅）
  ↓  审核
Phase 2  真实执行 A1 Local Static → A2 Local Dynamic → C 豆包 Regression → D Public Smoke（逐个，失败时能定位是哪类能力问题）
```
逐一推进、不一次性全做，保证「Dynamic 失败 = Core 在动态非聊天页面的能力问题」，而不是多种改动叠加后无法归因。

---

## 十、涉及文件

- `core/benchmark.py`：`_recovery_metrics` / summary 增 `recovery_depth`·`recovery_steps`·`recovery_depth_is_heuristic_v1` / `format_summary`
- `tests/test_recovery_depth.py`（新增）：recovery_depth 单测
- `examples/browser_benchmark/static.html`（新增）：A1 静态 demo
- `examples/browser_benchmark/dynamic.html`（新增）：A2 动态 demo
- `examples/browser_benchmark/run_a1.py`（新增）：A1 一次性 driver（起本地静态服务 → 跑真实 Agent → 聚合 run_log → 客观判定 task_success）
- `docs/P2-7-跨页面-Benchmark-设计.md`（本文档）

---

## 十一、A1 最小修复迭代（2026-09-13，已实现，范围锁定 3 件事）

A1 真实执行（第一轮）结论：**功能性通过 / Benchmark 门槛不通过 / Core 无回归 / 工具契约缺口**——
根因是 `browser_read_text` **只接受 `selector`、不接受 Target Handle**，
且提示词 handle-first 只覆盖 click/type。本轮只闭合「find → handle → read」契约，**不碰** wait/semantic_blocks/read_latest_reply/SoM。

### 11.1 代码修改（3 项）
1. **`browser_read_text` 增加 Target Handle 支持**（`core/browser_controller.py::read_text` + `core/tool_registry.py::browser_read_text`）：
   - 新增 `target=` 参数；解析优先级 `target(handle) → target(ref) → selector → whole page`。
   - `target` 为短 handle 或内部 ref 时，解析到 `[data-miniyu-ref="..."]` 元素并读取文本。
   - **拒绝把自猜的 CSS selector 当 target**（`#section1` 这类 → 明确报错引导）；确需按选择器可显式用 `selector=`。旧 selector/whole-page 行为不变。
2. **提示词补充 handle-first 覆盖 read + 禁止自猜 selector**（`core/agent.py` 守则 16）：
   - `click/type/read` 一律优先复用 find 返回的 handle；不要根据自然语言目标自猜 CSS selector；确需 selector 时用显式参数。
3. **benchmark 语义澄清（不改判定哲学）**：保留 `read_targeted` 字段兼容旧日志。

### 11.2 指标语义（写死口径）
- `read_targeted`  = `browser_read_text` 带定向目标的**调用次数**（含失败）。
- `read_ok_targeted` = 定向读取的**成功次数**。**A1 门槛看 `read_ok_targeted >= 1`**，而非 `read_targeted >= 1`。
- `som_screenshot` = `browser_snapshot` 的**调用次数**（观察性快照），**不等价于**发生了 SoM 视觉点击兜底
  ——真正的 SoM fallback 交互看 `coordinate_click`。两者在报告中分开解读。

### 11.3 A1 重跑理想路径
```
find("查看更多") → e1 → click(e1)
→ find("第二段") → e2 → read(target=e2) → structured_read
```
目标结果：`task_success=true / verified_success=true / benchmark_valid=true / find≥1 / click=1 /
read_ok_targeted=1 / selector_fallback=0 / coordinate_click=0 / action_failures=0 / recovery_depth=0`。

### 11.4 A1 重跑实测结论（2026-09-12，commit `9919664`）—— 两个阶段分开归因

**A1 Fix #1（read←handle 契约缺口）—— 已解决 ✔**
- `browser_read_text` 已支持 `target=`（handle/ref），提示词 16 覆盖 read 的 handle-first；
  新增 6 例 `tests/test_handle_read.py`，全量 478 passed / OK。
- 独立验证成立：`find("查看更多") → e1 → click(e1)` 全程 handle 走通，`method=handle`。
- 结论：read-handle 工具契约本身已修复并有独立测试覆盖，作为独立 commit `9919664` 保留。

**A1 新阻断点：find/snapshot observation scope 无法寻址普通文本段（核心设计缺口，未修）**
- 实测动作序列：
  ```
  find(查看更多,button) → e1 → click(e1) ✔
  find(role=text, 第二段) → []          # 断点
  find(selector=#p2…) → []
  browser_snapshot → 无「第二段」
  read_text(selector=#sec1) → 未找到（自猜 selector）
  read_text(整页) → 成功 → 终答（只靠 whole-page，task_success 兜住）
  ```
- 实测指标：`task_success=true / benchmark_valid=true / verified_success=false /
  find=3 / click=1 / read_targeted=1 / read_ok_targeted=0 / action_failures=2 /
  recovery_depth=10 / selector_fallback=1 / coordinate_click=0 / som_screenshot=1 /
  verification_source=whole_page_read / path=whole_page / llm_calls=11 / run_total_tokens=165124`。
- 根因（工具可寻址性，非模型违规、非 handle 系统故障）：`browser_find` 的 scope 只扫
  `a[href], button, input, textarea, select, [role], [onclick], [tabindex], [contenteditable]`
  （`browser_controller.py` ~L319-322）；`browser_snapshot` 同口径。目标 `<p id="second_para">`
  是无 role/tabindex/onclick 的纯文本节点，**不在 scope 内** → find 恒空、snapshot 恒无
  → 该元素**不存在 handle** → `find→handle→read` 无法启动。问题发生在 handle 诞生之前。
- 定性：这组 `task_success=true / verified_success=false / recovery_depth=10 / 165k tokens`
  是 P2-7 的**有价值的失败样本**——它证明「任务最终成功」≠「Agent 走了正确结构化路径」。

**结论与边界**：本轮**不进行第二轮 Core 修复**，尤其**不改 `browser_find` scope**——那属于后续
独立能力迭代（扩大 semantic observation），不能为了救 A1 临时改 Core，否则会把「测试原 Core」
与「边做 benchmark 边开发新 Core」搅在一起，违背 P2-7 逐层归因目标。下一轮只做 static.html
Demo 的最小任务目标调整（见下节方案，另输出），使第二次 targeted read 的目标属于当前 find scope，
从而真正测试 `find → handle → click → find → handle → targeted read`。

### 11.5 A1 第二次重跑（2026-09-12）—— 修正根因：残留浏览器锁档案，非 serving 缓存

**fixture 已按方案调整为可寻址**：static.html 给展开容器加 `role="region"` + `aria-label`
（不改任何页面逻辑）。**纯 find 隔离验证**通过——对磁盘当前 static.html：
`find(role=region) / find(role=region,text=第二段) / find(text=第二段) → eid:expanded_box`，
`find(text=查看更多) → btn_more + expanded_box`；`find(role=region,text=补充正文) → []`
（`textOf` 优先 `innerText`，可见文本时 `aria-label` 不参与匹配 → 任务应按「文本含第二段」寻址）。
⇒ **fixture 与 browser_find scope 兼容，role=region 可产生 handle。**

**第二次实测失败**（`read_targeted=0`）：`find(role=region,text=第二段)→[] / find(role=region)→[]`，
但整页读又能读到「第二段」。**修正后的根因**（用户实测提示 + driver 复现证实）：
run#1 结束时**没有关闭浏览器**，残留实例锁住**持久化 user_data_dir（%LOCALAPPDATA%/miniyu_edge）
与 9222 调试端口**；run#2 重新 `browser_launch` 时 attach 到 run#1 遗留浏览器上，页面仍是
**run#1 加载的旧 static.html（无 role）** → 结构化观察拿不到 role=region（页面上根本没有）→
`find→handle→read` 在起点即断 → inspect 超时 + whole-page 兜底。**不是 fixture、不是 find scope、
不是读契约、不是模型行为**——是 **driver/serving 生命周期**：浏览器未在 run 后关闭、档案/端口被占。

**driver 最小修复（commit 见 log，未改 Browser Core / find scope / P2-6 / benchmark 指标）**：
- 在 `agent.run` 之前，用 `agent.api.execute_tool` 驱动**同一个浏览器**完成
  `browser_launch → browser_navigate(带 ?bench=<now-ms> cache-bust 的唯一 URL，SimpleHTTPRequestHandler
  会剥 query 正常 200) → find(role=region) 就绪校验`；任一失败 **直接 abort，绝不带错/旧对象跑 Agent**。
- `_fixture_ready` 拆解 `execute_tool` 包装返回（`{success,tool,result}`），成功谓词用 `success` + 非空 result。
- **每次 run 结束必然 `browser_close`**（normal / abort / `--readiness-only` 三路都接），
  杜绝残留实例锁档案/端口导致的「下次 open 加载不出页面」。
- `--readiness-only` 只跑就绪链路不含 LLM/Agent（供 driver 自检）。
- A1 门槛收紧（gtp）：重点看**关键路径**而非固定 find 数——`task_success/
  verified_success/benchmark_valid=true`、`click(handle)=1`、`read_ok_targeted=1`、
  `action_failures=0/recovery_depth=0/selector_fallback=0/coordinate_click=0`、`verification_path=read_targeted`。
  成功路径：`fixture ready → find(查看更多)→e1 → click(e1) → find(role=region,text=第二段)→e2
  → read_text(target=e2) → structured_read`。

**下一步**：仅当此 driver 修复落地并确认首页加载为最新 fixture 后，才可正式重跑 A1 一次。

### 11.6 A1 正式重跑通过（2026-09-12，run_log_1789206273865.jsonl）—— ✅ 链路闭环

driver 修复后正式重跑，**成功关键路径完整出现**（run_log 实证，非截图指标）：
```
step1 find(text=查看更多, role=button) → e2 = eid:btn_more
step2 click(target=e2)                              # method=handle, clicked, page_changed=true
step3 find(role=region, text=第二段) → e3 = eid:expanded_box
step4 read_text(target=e3)                          # 返回区域正文，含「第二段（展开后可见）…」
```
指标（全部达标）：`task_success=true / verified_success=true / benchmark_valid=true /
find=2 / click=1 / read_targeted=1 / som_screenshot=0 / selector_fallback=0 /
coordinate_click=0 / action_failures=0 / recovery_depth=0 / wait_*=0 /
verification_source=structured_read / verification_path=read_targeted /
llm_calls=5 / run_total_tokens=52464`。浏览器 run 后自动 close，无残留实例。

→ **A1（Local Static）正式通过**：验证「基础结构化寻址 + Target Handle + Click + Targeted Read」链路闭环，
且证明先前的两次失败分别是 (1) read←handle 契约缺口（已修 9919664）与 (2) driver 未关浏览器→旧 DOM（已修 9c0f625），
均非 Browser Core / find scope / 模型行为缺陷。