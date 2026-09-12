# P2-8 mini design：Browser Agent 泛化稳定性 / Recovery 优化

> 状态：**Phase 1 = PASS（已完成）→ Phase 2 第一批 related-content 已实现、单测通过、真实浏览器 v2 链路验证通过（0 token）**
> 前置：P2-7 已 CLOSED（含 D oracle 基线纠正：A1/A2/C PASS，D 仅 interaction path PASS / full task NOT VERIFIED）。A1/A2/C + D 的 interaction path 作为回归基线。
> 纪律：P2-8 Phase 1 唯一目标 = **解释** D 为何比 A1/A2/C 更不稳定，**不是**让 D 变稳定。

---

## 一、研究问题（Phase 1 锁定 4 个）

| # | 问题 | 一句话 | 决定后续优化哪一层 |
|---|---|---|---|
| R1 | 为什么 D 会猜 selector？ | Agent 主动构造 `h2#examples_2 ~ p` / `+ p` | observation / read contract / prompt |
| R2 | 为什么会重复 read？ | 同一步反复 read_text 多次 | read contract / prompt |
| R3 | selector_fallback 是主动策略还是失败恢复？ | 主动性 vs 恢复性 | prompt / recovery policy |
| R4 | action_failures=3 为何放大成 recovery_depth=6？ | 1 个真问题 vs 3 个独立问题叠加 | recovery policy / observation |

## 二、Phase 1 根因分析方法（全程只读）

- 输入：D run_log（`run_log_1789215142310.jsonl`）+ `run_d.py` + `tool_registry.py`/`browser_controller.py` 的 find/read schema 与实现 + MDN 页面确定性 probe。
- 方法：把 run_log 重建为 LLM→tool→result 时间线，逐 step 归因（cause→immediate reaction→next action→eventual recovery）。
- 禁用：不修改 browser_find / browser_read_text / browser_controller / recovery / System Prompt / semantic_blocks / wait / SoM / benchmark 判定逻辑。
- 目标：0 token 增量（不跑真实 API、不改模型配置）。

## 三、成功标准（Phase 1）

- R1-R4 每个结论都必须有 run_log step / tool result / DOM probe 证据，禁止纯推测。
- 输出一张 attribution matrix：现象｜证据｜最可能根因｜排除原因｜置信度。
- 能回答：recovery_depth=6 到底来自 1 个真问题 + 恢复链条，还是 3 个独立问题叠加。

## 四、明确禁止修改范围（Phase 1 硬边界）

browser_find / browser_read_text / browser_controller / recovery / System Prompt /
semantic_blocks / wait / SoM / benchmark 判定逻辑 / run_d.py 的任务文本。
（run_d.py 已做过的 harness 解包修复除外——那是 attempt #0 已定案的 driver bug）

## 五、Phase 2 候选优化方向（只列候选，**不实施**）

> 依 Phase 1 结论定向。每批只动一层，每批后重跑 A1→A2→C→D 全矩阵。

**Phase 2 第一批：read-contract 层（已批准开始）**
- **核心问题**：`read_text(handle)` 只取当节点文本，对「标题锚块 + 正文在下游容器」的文档页（如 MDN）无法直接取得正文，迫使 Agent 猜 selector。
- **第一步（不写实现）**：先精确定义 read-contract v2 的「可读区块」语义，再实现。
- **三种候选（用户倾向 B/C，因比盲目向父节点爬更可控）**：
  - A. **enclosing semantic container**：read(handle) 向上爬到语义容器后 innerText。
  - B. **observation 返回可读文本范围 / related-content handle**：find/read 返回该 handle 可读到什么范围，或给出相关联的正文 handle。
  - C. **find 直接返回「目标 + 其可读内容区」的 handle**：定位时同时产出可读内容句柄。
- **第一批唯一目标**：让 D 的「标题 + 正文首句」任务可通过结构化 handle/read 完成，**不允许 selector 猜测**。

**后续候选（本批不动）**
- observation 层、prompt / tool-use 层、recovery policy 层：仅记录，不实现。

---

## 六、Phase 1 根因分析结果（2026-09-12）

> 本部分由 run_log 证据 + 确定性 DOM probe 得出，未跑任何真实 API。

### 6.1 关键复现事实（DOM probe，0 token）

MDN button 页的 Examples 相关的 DOM 结构（确定性验证）：

```
H2#examples_2 (text_len=8, 子: <a>Examples</a>)
  └─ SECTION.content-section (text_len=8)   ← 仅含标题锚
       └─ DIV.layout__body (text_len=17675)  ← 示例正文其实分布在下游容器
```

- `h2#examples_2` 的**兄弟节点 = 只有它自己**（probe B 返回单元素列表）。因此 `~ p` / `+ p` 这类"找 h2 后的正文段"选择器**必然落空**——这就是 step6/step8 双失败的 DOM 成因，**不是 read 实现缺陷**。
- `section:has(> h2#examples_2)` 命中 section 的 innerText = **"Examples"**（probe C），因为正文不在该 section 内。→ **step5 返回 "Examples" 是忠实正确**，不是 bug。

### 6.2 R1：为什么 D 会猜 selector？—— **归因：observation 信息不足 + prompt 刚性不足**

证据链（run_log step4→6→8）：

- step4 `browser_read_text(target=e4)` → `"Examples"`（handle 忠实返回锚文本）。此时 Agent 的"任务需要正文首句"与"只拿到标题"产生缺口。
- step5 转而 `read_text(selector="section:has(> h2#examples_2)")` → 仍 `"Examples"`。Agent 看到"读标签也没得到正文"。
- step6 `read_text(selector="h2#examples_2 ~ p")` → FAIL。Agent 开始**主动构造 CSS** 想定位"h2 后的正文段"。
- step8 `read_text(selector="h2#examples_2 + p")` → FAIL。

结论：`read(handle)` 的契约（read_text doc）允许 `selector=` 参数，且未在"已用 handle 拿到锚文本后"强制 Agent 停止猜测。当 handle 对目标任务（含正文）信息不足时，Agent 自然滑向 selector 猜测。**根因组合：read(handle) 对"标题锚块"返回可达范围远小于 Agent 对任务的理解 + prompt 未硬约束"只用 handle/read 区块"**。

是否 site-specific？**否**——是 MDN 对"Examples 文档读者"常见的"标题节=semantic 空块、正文在各代码容器"的 DOM 写法，但**任何同类"标题即锚点、正文在 sibling 容器"的页面都会触发**，属通用 observation/prompt 缺口而非单站点 hack。

### 6.3 R2：为什么重复 read？—— **归因：目标不可达导致的重试 + 读到的结果未满足任务**

证据链：step4（handle，成功但只标题）→ step5（selector，成功但只标题）→ step7（selector h2，成功但只标题）→ step9（find text="The"... fail）。

Agent 的**每次 read 都返回了真文本**（"Examples"），且**每次都"成功"**（ok=true），但每次都**不满足"标题+正文首句"**的任务。于是 Agent 反复尝试不同定位方式期望拿到正文——这不是"失败重试"，而是"结果未达语义目标→策略切换"的迭代。**重复 read 的直接来源：任务要求的"正文首句"在该 handle 可达域内不可达**，Agent 不得不反复换法，直到耗尽策略（step10 inspect→截图、step11 browser_extract 网页提取 skill 超时）。

### 6.4 R3：selector_fallback 是主动策略还是失败恢复？—— **两者兼有，先主动后恢复**

- **主动**：step5 在第一次 handle read 后，Agent 主动选择"用 selector 读 section 更精确"（thought: "应该读取 h2 所在的 section"），属于**主动策略**。
- **恢复**：step6/step8 两个 selector FAIL 后，step9-11 发生**恢复迁移**——从 read 转 find（带 selector）、又转 browser_inspect（SoM 截图）、再转 browser_extract（导航式网页提取 skill）。
- 区分点：**首次 selector 是主动，selector FAIL 后的工具升级链是恢复**。若只统计"fallback=1"会低估实际策略迁移（实际发生了 read→find→inspect→extract 的 4 段迁移，只是 schema 只把 selector 那次记为 selector_fallback）。

### 6.5 R4：action_failures=3 为何放大成 recovery_depth=6？—— **单点缺口开始恢复级联**

失败钉死 3 处：step6 selector、step8 selector、step11 browser_extract 超时。但 recovery 从 step6 起，每个失败触发 1 轮 LLM 重规划（step6→7、step8→9、step11→12），且 step9/step10 的 find+inspect 属于**主动迁移而非失败恢复**（ok=true），却仍计入恢复链，因为 recovery_depth 按"LLM 额外重规划轮次"统计。

**关键结论**：recovery_depth=6 不是 3 个独立问题，而是 **1 个真实问题缺口**（MDN Examples 标题块的正文在本节不可达 + read(handle) 不提供区块读取）**串联触发的一条恢复链**。若 root 缺口被 observation/read-contract 弥合（如 read(handle) 可读其所在区块），step4 就能拿到正文，step5-11 全部不会发生。

### 6.6 边界说明（为何不算 external / 纯 site-specific / own-capability 瀑布）

- 非 external：页面稳定加载、无重定向/验证码；probe 与 run_log 均成功导航。
- 非 site-specific 瀑布：问题通用（任何"标题锚空块 + 正文在别处"页面都会触发 observation 缺口），只是 MDN 恰好暴露了它。
- 定为 **own-capability 的 observation/read-contract 缺口**：Agent 的 find/click/requery 闭环正确，但 read(handle) 对"含正文的区域"无表达力，叠加 prompt 未硬约束，导致恢复级联。**这与 P2-7 A1/A2/CLOSED 不冲突**——A1/A2 目标都是"标题/单一文本节点"类目标，read(handle) 可达；D 的目标是"标题+正文"，read(handle) 不可达。

### 6.7 是否进入 Phase 2？—— **建议是，但以"read contract"为主攻点**

Phase 1 证据已足够：recovery_depth=6 的根是 **read(handle) 无法表达"读整块/区块"**。Phase 2 应先验证"给 read_text(handle) 增加区块读取语义（或 find 返回该目标可达文本提示）后，Agent 是否无需猜 selector 即可拿到正文"。每批只动 read-contract 这一层，改后重跑 A1→A2→C→D。

---

## 七、输出物清单

- [x] P2-8 mini design
- [x] D failure timeline（§6 内）
- [x] 四个问题证据结论（§6.2-6.5）
- [x] attribution matrix（见回话交付，或于此追加）
- [x] 下一阶段候选优化点（§五，不实施）
- [x] 是否有足够证据进入 Phase 2（§6.7）

---

## 八、D oracle 基线纠正（2026-09-12 复核）

P2-7 原把 D 记为 "PASS" 存在 **oracle false positive**：D 任务要求「Examples 标题 + 正文首句」，但 Agent 只返回标题并反问用户（run_log step12 终答），旧 oracle 仅检查 `"Examples" in answer` → 误判 task_success=true。修正后 D 定级：

- **interaction path：PASS**（find→click→重新 find→read_title 闭环正确）
- **full task completion：NOT VERIFIED**（正文首句从未取得）
- 原 `task_success`：oracle false positive
- **不影响 A1/A2/C 结果**；不会把 D 原结果再当作完整任务 PASS baseline
- 原 run_log 保留为极有价值的 **P2-8 failure sample**

> 该纠正符合 P2-7 初衷：benchmark 本身也要能暴露自己的测量错误。

---

## 九、Phase 2 · read-contract v2 语义定义（先定语义，不写实现）

> 决策：先回答下述 6 问，再实现。**禁止直接实现 `handle → parent.innerText`**。
> 候选 A/B/C，用户倾向 B/C（比盲目爬父节点更可控）。

### 9.1 六个必须先回答的问题

**① handle 对应元素是什么？**
`read_text(handle)` 当前按 `handle → ref` 定位到**单一元素**，返回其 `innerText || textContent`。无可读区块概念。MDN 例：handle 指向 `h2#examples_2` 内锚 `<a>Examples</a>`，innerText 仅 8 字符。

**② "可读区块"如何确定？**
不应默认"某个祖先节点"。候选边界：
- 最近**语义块容器**（如 `section.content-section`）→ 但 MDN 该 section innerText 仍只有 "Examples"（probe C），**不可行**。
- 目标所在**文档区域**（其可见正文分布到的容器，如 `div.layout__body`）→ 可能过大（17,675 字符）。
- 需由 observation/read 工具**显式暴露**，而非 Agent 臆测。

**③ 读取范围如何限制，避免读出整个页面/巨大容器？**
需要长度上限 + 语义边界（段落级）。不能无界向上爬。参考超 2000 字符截断（browser_extract 已有 `text[:2000]` 先例）。

**④ A1 当前 read(handle) 行为如何完全兼容？**
A1（static.html）read(handle) 读单元素文本、click 跳转后重 find，已冻结。read-contract v2 若改「默认读区块」，会**破坏 A1 的语义**（A1 读的是单目标文本）。因此 v2 必须以**显式的新参数/新入口**引入，不改变默认行为（向后兼容），默认仍是"读单节点"。

**⑤ stale handle 如何处理？**
沿用冻结契约：handle 随导航/重排失效 → 抛 stale/LookupError → 重 find。v2 对区块句柄同样适用，不能因其"大区块"而放行复用旧 handle。

**⑥ 目标文本存在于下游容器而非当前节点时，如何提供确定性可读范围？**
MDN 例正文在下游 `div` 容器，当前 handle 无法达。确定性方案：**observation（find/snapshot）对带正文语义关联的目标，同时给出"可读内容 handle"或"可达文本范围"，而非让 Agent 猜 selector**。

### 9.2 三种候选评估

| 候选 | 机制 | 优点 | 风险 | 对 D 的可行性 |
|---|---|---|---|---|
| **A** enclosing semantic container | handle → 向上剥到语义块 innerText | 实现简单 | 如 MDN，最近语义块仍无正文；爬得过高过大 | 对 MDN 低效（section 空块 / body 过大） |
| **B** observation 返回可读范围/related handle | read/find 返回"本 handle 可读到哪 + 相关正文 handle" | 显式、可控，不给 Agent 留猜 selector 空间 | 需扩展 observation 契约 | 直接解决（Agent 拿到正文 handle） |
| **C** find 返回目标+可读内容区 handle | find 定位时并行产出正文句柄 | 一次定位即可读 | find 语义扩展较大 | 直接解决，但改动 find（冻结项） |

### 9.3 倾向结论

**倾向 B 为主（首选），C 为备选**：B 在 read-contract 层扩展，不触碰冻结的 find；C 需要动 find（P2-7 冻结项），故排在 B 之后。A 作为明确否决（对 MDN 该结构无效）。

### 9.4 待用户确认后方可实现

- [x] 同意以 B 为主方向（observation/read 返回 related-content / 可达范围）？
- [x] 若 B 需最小动 find 返回字段（在现有 find 结果追加"可读内容区 handle"字段，不改变排序/过滤语义）是否接受？
- [x] v2 以**新参数/新入口**引入（默认行为不变，保证 A1 兼容），确认？

> 三项已全部确认 → 进入 Phase 2 第一批实现（见 §十）。

---

## 十、Phase 2 第一批实现记录（read-contract / related-content）

> 状态：**已实现 + 单测通过 + 真实浏览器 v2 全链路验证通过（0 token）**

### 10.1 实现依据（确定性 DOM 事实）

对 MDN button 页确定性探测确认：`h2#examples_2` 的父 `section.content-section` 是**空小节**（innerText=8，仅标题锚 a），正文在 **DOM 顺序随后的兄弟 section**（`Creating a basic button` 等）。因此 related-content 不能靠"祖先爬 / 紧邻 sibling"，必须编码为"标题类目标 → 其后块级正文兄弟容器"。

### 10.2 实现

- **`_RELATED_CONTENT_JS`（模块级常量）**：`_relHeading(el)` 上溯到标题祖先；`_relBody(h, limit)` 收集标题之后块级正文兄弟，遇 ≤ 同级下一标题停止，超 `limit` 截断。find 与 read 共用，单源不产生 find/read 分叉。
- **`browser_find` JS 注入**：仅对**标题类目标**（h1-h6 或其下钻 host 的标题祖先）计算 related_content；仅标题身下有可信正文时才返回，否则缺省（不无条件生成，控制 observation 成本）。
  ```json
  "related_content": {"relation":"content", "origin_ref":"…", "preview":"…", "char_count", "truncated"}
  ```
- **`_alloc_handles`**：为 related_content 分配独立 handle（registry 以 `rel:<origin_ref>` 登记，与元素同 session/同指纹）；related handle 与元素 handle 不同。
- **`read_text(target=<related_handle>, mode="content")`**：仅消费 `related_content.handle`，经 `_RELATED_CONTENT_JS` 确定性重推导正文；拒绝 selector / 裸 ref / 非 related handle。
- **`tool_registry.browser_read_text`**：透传 `mode` 参数（schema 自动生成可见）。

### 10.3 硬上限与截断

- 正文硬上限 **2000 字符**（沿用项目 `text[:2000]` 先例）。
- `truncated` 诚实标记超限（MDN 实际 char_count=2297 → truncated=True），绝不伪装完整。
- preview=正文前 80 字符。

### 10.4 单测与回归

新增 `tests/test_read_contract_v2.py`（7 用例）：
- find 标题类 + 有正文 → 产出 related handle（与元素 handle 不同）
- mode=content 读回正文
- mode=content 拒绝非 related handle / 拒绝 selector
- 旧 read 行为完全不变（mode 默认不开启 → A1 兼容）
- stale related handle（页面变化后）失效
- related handle 与普通 handle 隔离

**全量回归：`702 passed`**（含既有 A1/A2/C 相关测试；无回归）。与 P2-7 A1/A2 语义（旧 read 单节点路径）完全兼容。

### 10.5 真实浏览器验证（无 LLM，MDN）

```
find(role=link, text=Examples) → e1(目录) / e2(标题锚 ref=…section:9_h2:0_a:0)
e2.related_content → {handle:e3, relation:content, char_count:2297, truncated:True}
read_text(target=e3, mode=content) → "Creating a basic button\nThis example creates a clickable button…"
→ V2_PATH: PASS
```

标题锚 e2 正确关联正文 handle e3，mode=content 读回 Examples 正文首段。**证明了 find→current_handle→related_content_handle→v2 content read 的闭环，全程无 selector 猜测、无 inspect/SoM、无 whole-page**。

### 10.6 待办（需真实 API）

- [ ] 用 `Qwen3.7-Max-2026-06-08` 执行 D Agent Smoke，观察 Agent 是否**主动**走 `find→related_content.handle→read(mode=content)` 路径而非猜 selector（不强制，仅观察）。
- [ ] D 新 oracle 同时验证 Examples + 正文首句（不再单用 "Examples"）。
- [ ] A1→A2→C→D 四类回归（C 用 Qwen3.7-Max；A1/A2 本地可无 LLM 断言）。