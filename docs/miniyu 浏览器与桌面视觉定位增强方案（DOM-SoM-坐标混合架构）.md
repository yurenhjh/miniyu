# miniyu 浏览器与桌面视觉定位增强方案
## ——基于 DOM/Accessibility、SoM 编号与坐标兜底的统一 GUI Agent 设计

**项目：** miniyu / group4_tools_os_skills  
**设计目标：** 提升浏览器与桌面 GUI 的视觉定位、点击可靠性和 Agent 操作成功率  
**设计日期：** 2026-09-11

---

# 1. 设计背景

目前 miniyu 已经具备较完整的浏览器控制、桌面控制与视觉理解能力。

现有设计中，浏览器侧已经拥有 CDP 浏览器控制、`browser_snapshot`、`browser_click`、`browser_type`、`browser_screenshot`、`mouse_click` 等能力；桌面侧已经拥有 `take_screenshot`、`screen_inspect`、`click_at` 等能力，同时 Agent 还可以根据 `supports_vision` 决定是否将截图直接交给视觉模型。项目现有浏览器与桌面能力的整体基础已经比较完整。

但目前最大的共同问题是：

> **模型看到的图像坐标，与实际能够被工具可靠操作的目标，没有形成统一、明确的对应关系。**

具体表现为三条链路：

### 1.1 桌面全屏链路

```text
take_screenshot
      ↓
PIL.ImageGrab.grab()
      ↓
桌面全屏裸图
      ↓
screen_inspect
      ↓
视觉模型
      ↓
模型自行猜测 (x,y)
      ↓
click_at(x,y)
```

这里的主要问题是：

> 模型直接从裸截图中“脑补”绝对坐标。

桌面截图没有元素编号、没有目标引用、没有机器可验证的视觉锚点，因此模型输出坐标本质上属于近似定位。

---

### 1.2 浏览器视觉链路

```text
browser_screenshot
      ↓
浏览器页面裸图
      ↓
视觉模型
      ↓
模型自行猜测页面坐标
      ↓
mouse_click(x,y)
```

这条链路同样存在视觉坐标误差。

---

### 1.3 浏览器结构化链路

当前浏览器更常用的是：

```text
browser_snapshot
      ↓
DOM / 文本索引
      ↓
browser_click(index)
```

这一链路比纯视觉坐标可靠，但它又存在另一个问题：

> **模型知道“哪个元素可以点”，却不一定知道“这个元素在页面视觉布局中的哪个位置”。**

也就是说，目前实际上形成了：

```text
DOM/index ─────────→ 可操作
       │
       X
       │
页面截图 ──────────→ 可观察
```

两边并没有真正接起来。

---

# 2. 现有系统中另一个重要设计盲区

当前 Agent 的 `_GUI_TOOLS` 在浏览器动作执行后，会自动调用 `_capture_and_add_image` 给视觉模型提供下一步观察结果。

但是当前这条路径使用的是：

```text
take_screenshot
```

即：

> **桌面全屏截图**

而不是：

```text
browser_screenshot
```

即：

> **当前浏览器页面截图**

所以当 Agent 正在完成浏览器任务时，浏览器按钮点击完成以后，模型得到的实际上是：

```text
整个 Windows 桌面
    ↓
Edge/Chrome 窗口
    ↓
当前网页
```

而不是：

```text
当前网页 viewport
```

这会产生不必要的视觉信息和尺度问题。

因此，本次增强不仅需要增加 SoM，还应该顺便修正：

> **浏览器 Agent 的视觉反馈应该优先使用浏览器页面级截图，而不是桌面全屏截图。**

---

# 3. 本方案总体设计思想

不采用“所有 GUI 都使用同一种定位技术”的方式。

根据环境是否存在 DOM / Accessibility Tree，将定位能力分为三级：

```text
                    Agent UI 定位层
                          │
           ┌──────────────┼──────────────┐
           │              │              │
           ▼              ▼              ▼
      Level 1          Level 2        Level 3
    DOM / AX Ref       SoM视觉定位      XY坐标
      主通道            视觉补充         最后兜底
```

核心原则：

> **结构化定位优先，视觉定位辅助，坐标定位兜底。**

即：

### 浏览器普通页面

```text
Accessibility / DOM
        ↓
      ref
        ↓
     click/type
```

### 浏览器视觉复杂页面

```text
DOM/AX
  +
Screenshot
  +
SoM
  ↓
视觉选择目标
  ↓
ref
  ↓
实际执行
```

### 桌面 GUI

```text
Screenshot
   ↓
OCR / Vision / Future OmniParser
   ↓
视觉目标
   ↓
XY 坐标
   ↓
click_at
```

这一方向与当前 Playwright MCP 的思路基本一致：普通浏览器交互以 accessibility snapshot 和元素 ref 为主要定位手段，视觉上下文重要时再同时提供 screenshot。Playwright MCP 的 ref 具有明确的生命周期，页面发生变化后旧 ref 会失效，需要重新 snapshot。

---

# 4. 为什么不能把 SoM 设成所有浏览器操作的必经步骤

原始设计倾向于：

```text
browser_inspect
      ↓
选择编号
      ↓
browser_inspect_click(num)
```

这对于视觉复杂页面非常合理，但不应该强制所有操作都走这条路线。

例如：

```text
用户：
打开百度并搜索“湖南大学”
```

完全可以：

```text
browser_snapshot
    ↓
textbox "搜索"
    ↓
ref=e5
    ↓
browser_type(target=e5)
    ↓
browser_click(target=e8)
```

这里没有必要让视觉模型先看一次图片。

Playwright MCP 当前明确采用这一思路：snapshot 提供带 ref 的结构化元素，工具直接使用 ref 操作；如果页面视觉布局确实重要，再组合 screenshot。

因此 miniyu 应该让 Agent 自己判断：

```text
结构化信息已经足够？
        │
       是
        ↓
DOM/AX Ref 操作

       否
        ↓
browser_inspect
        ↓
SoM视觉定位
```

这样可以减少：

- 视觉模型调用次数
- 图片 token 消耗
- Agent 推理延迟
- 无意义的截图处理

---

# 5. SoM 的正确定位：视觉锚点，而不是新的元素身份系统

这是本方案与原方案相比最重要的改动之一。

不推荐：

```text
① → 元素1
② → 元素2
③ → 元素3
```

然后 Agent：

```text
browser_inspect_click(num=3)
```

直接再次按照 DOM 排序寻找“第 3 个元素”。

原因是：

> **编号不稳定。**

例如第一次：

```text
1 搜索框
2 搜索按钮
3 登录
4 设置
```

页面发生一点变化后重新排序：

```text
1 搜索框
2 登录
3 搜索按钮
4 设置
```

如果重新通过 `num=2` 找元素，原来想点“搜索按钮”，最后可能变成“登录”。

因此：

> **数字编号只能表示“截图上的视觉位置”，不能直接作为元素的长期身份。**

---

# 6. 推荐的数据关系：num → ref → element

SoM 应该建立这样的关系：

```text
                    screenshot
                        │
                        ▼
                      [7]
                       │
                       ▼
                   visual_num
                       │
                       ▼
                     ref=e17
                       │
                       ▼
                    DOM element
```

例如：

```json
{
  "num": 7,
  "ref": "e17",
  "role": "button",
  "name": "搜索",
  "bbox_css": [820, 42, 86, 36],
  "center_css": [863, 60],
  "disabled": false
}
```

模型只需要决定：

```text
点击 7
```

但系统内部实际使用：

```text
7
↓
当前 inspect session 中的 ref=e17
↓
resolve ref
↓
验证当前元素
↓
执行 click
```

这里的 `ref` 才是真正用于执行动作的目标身份。

Playwright MCP 当前采用的就是这种 ref 模型；ref 在单次 snapshot 生命周期内唯一，页面变化后会失效，工具如果发现 ref stale，则要求重新获取 snapshot。

Playwriter 的视觉标签方案也采取了类似思路：截图给出视觉编号，但最终通过 accessibility ref，例如 `aria-ref=e5`，完成实际元素操作。

---

# 7. 浏览器侧最终工具架构

建议保留现有能力，同时增加统一的目标体系。

## 7.1 browser_snapshot

用途：

> 浏览器默认结构化感知。

返回：

```json
{
  "url": "...",
  "title": "...",
  "elements": [
    {
      "ref": "e5",
      "role": "textbox",
      "name": "搜索"
    },
    {
      "ref": "e8",
      "role": "button",
      "name": "搜索"
    }
  ]
}
```

默认优先使用。

---

# 8. browser_inspect

用途：

> 当结构化信息不足以让 Agent 理解页面空间关系时，生成带编号的视觉截图。

完整流程：

```text
Page.captureScreenshot
        ↓
获取当前页面截图
        +
Runtime.evaluate
        ↓
获取当前可交互 DOM 元素
        ↓
过滤
        ↓
建立 ref
        ↓
PIL 绘制编号
        ↓
返回 screenshot + elements
```

返回：

```json
{
  "image_path": "...",
  "elements": [
    {
      "num": 1,
      "ref": "e5",
      "role": "textbox",
      "name": "搜索",
      "bbox_css": [...],
      "center_css": [...]
    },
    {
      "num": 2,
      "ref": "e8",
      "role": "button",
      "name": "搜索",
      "bbox_css": [...],
      "center_css": [...]
    }
  ]
}
```

---

# 9. browser_click

建议不要再让 Agent 明确区分很多种点击工具。

统一设计：

```json
{
  "target": "e8"
}
```

或者 SoM 状态下：

```json
{
  "target": "som:2"
}
```

系统内部自动完成：

```text
som:2
   ↓
当前 inspect session
   ↓
num=2
   ↓
ref=e8
   ↓
验证 ref 是否仍然有效
   ↓
执行动作
```

这样 SoM 是视觉层能力，而不是另一套完全独立的点击体系。

---

# 10. browser_type

同样建议基于 ref：

```json
{
  "target": "e5",
  "text": "湖南大学"
}
```

当 Agent 是通过 SoM 找到输入框时：

```text
som:3
  ↓
ref=e5
  ↓
browser_type(target=e5)
```

这样可以保证：

> **视觉负责找，结构化引用负责执行。**

---

# 11. 坐标定位不删除，而是降级为最后一级

某些页面确实存在：

```text
Canvas
SVG复杂图形
地图
白板
游戏界面
图片式按钮
特殊第三方控件
```

这类目标不一定可以通过 DOM/AX 得到可靠的语义。

这时：

```text
DOM/AX
   ↓
无法可靠定位
   ↓
SoM
   ↓
仍然无法确定
   ↓
XY
```

因此：

```text
browser_mouse_click(x,y)
```

仍然保留。

但是 Agent System Prompt 应明确：

> **不要优先使用坐标。**

而不是把坐标工具完全删除。

---

# 12. 浏览器坐标系统必须统一

这是实现阶段最容易出现 bug 的地方之一。

系统当前存在：

```text
桌面截图
↓
物理像素

浏览器截图
↓
device pixel

DOM getBoundingClientRect()
↓
CSS viewport pixel

CDP Input.dispatchMouseEvent
↓
CSS viewport pixel
```

因此必须明确区分。

---

## 12.1 DOM 坐标

`getBoundingClientRect()`：

```text
CSS viewport pixels
```

例如：

```text
x=500
y=300
```

---

## 12.2 CDP 鼠标坐标

CDP `Input.dispatchMouseEvent` 的 `x/y` 定义为：

> 相对于主 frame viewport 的 CSS pixels。

因此不要把 device pixels 直接当成 CDP 的 x/y。

---

## 12.3 截图绘制坐标

如果截图本身是 device-pixel PNG：

```text
screenshot_px = css_px × dpr
```

只有在：

```text
DOM bbox
       ↓
PIL绘制
```

这一层转换。

不要把这个转换后的 device pixel 坐标继续当成 CDP click 参数。

---

# 13. 推荐统一保存 CSS 坐标

核心数据结构推荐：

```json
{
  "bbox_css": [x, y, width, height],
  "center_css": [cx, cy]
}
```

而不是：

```json
{
  "center_device_px": [...]
}
```

系统只在需要绘图时转换：

```text
CSS bbox
   ↓
× screenshot scale
   ↓
PIL 绘制
```

执行 CDP click 时则：

```text
center_css
   ↓
Input.dispatchMouseEvent
```

这样可以避免：

```text
DOM DPR转换
+
mouse_click内部再次DPR转换
```

造成双重缩放。

---

# 14. 桌面截图链路的增强方案

桌面没有 DOM，因此无法使用浏览器同样的 ref 机制。

桌面推荐：

```text
take_screenshot
       ↓
screen_inspect
       ↓
视觉/OCR
       ↓
生成 UI target
       ↓
编号/坐标
       ↓
click_at
```

即：

```text
桌面：
Screenshot → Visual Grounding → XY
```

与浏览器：

```text
浏览器：
DOM/AX → Ref
         ↓
       SoM
         ↓
        XY
```

形成两套不同的定位策略。

---

# 15. 桌面侧也建议引入 SoM 思路

虽然桌面目前不建议马上引入 YOLO/OmniParser，但可以先把 Agent 接口设计成：

```text
screen_inspect
    ↓
screenshot
    ↓
视觉模型
    ↓
UI targets
```

将来增加：

```text
OCR
+
目标检测
+
SoM
```

也不需要改 Agent 上层接口。

未来可以变成：

```json
{
  "num": 12,
  "type": "button",
  "text": "发送",
  "bbox": [1250, 760, 80, 40],
  "center": [1290, 780]
}
```

最终：

```text
screen_click(target=12)
```

这样浏览器和桌面都可以拥有类似的“目标对象”概念。

---

# 16. 浏览器视觉反馈必须与桌面视觉反馈分离

现有 `_capture_and_add_image` 应增加场景判断。

推荐：

```text
GUI Tool
   │
   ├── browser action
   │       ↓
   │   browser_screenshot
   │
   └── desktop action
           ↓
       take_screenshot
```

而不是：

```text
所有 GUI action
      ↓
take_screenshot
```

---

# 17. 更进一步：浏览器操作后的截图默认使用“页面截图”

浏览器 Agent 执行：

```text
browser_click
browser_type
browser_select
browser_scroll
```

之后，如果 Agent 需要视觉反馈：

```text
browser_screenshot
```

直接提供：

```text
当前 viewport
```

必要时再加：

```text
browser_snapshot
```

形成：

```text
页面结构
+
页面视觉
```

而不是：

```text
整个 Windows 桌面
```

这样可以显著降低视觉输入中的无关内容。

---

# 18. browser_inspect 的元素提取逻辑

建议第一版元素来源：

```text
a[href]
button
input
textarea
select

[role=button]
[role=link]
[role=tab]
[role=checkbox]
[role=radio]
[contenteditable]

[onclick]
[tabindex]
```

对每个元素：

```text
getBoundingClientRect()
        ↓
display / visibility
        ↓
width / height
        ↓
viewport intersection
        ↓
disabled
        ↓
aria-label
title
placeholder
innerText
value
```

生成：

```json
{
  "ref": "...",
  "tag": "...",
  "role": "...",
  "text": "...",
  "bbox_css": [...],
  "center_css": [...],
  "disabled": false
}
```

上传的原方案中也已经确定了类似的元素来源、可见性过滤、最大元素数量和 DOM + PIL 的基本技术路线，因此这些内容可以直接作为 P1 的基础。

---

# 19. max_elements 不应该只是简单截断

不能单纯：

```python
elements[:40]
```

因为可能把真正重要的元素截掉。

建议排序优先级：

```text
1. button / input / select / textarea
2. role=button / link / tab
3. 可见链接
4. contenteditable
5. tabindex
6. 其他可疑交互元素
```

然后再：

```text
最多 30~40 个
```

同时尽可能根据当前任务关键词进行候选筛选。

例如 Agent 正在寻找：

```text
“登录”
```

可以先通过 snapshot/find：

```text
browser_find("登录")
```

得到候选元素，再重点视觉标注。

Playwright MCP 当前也提供 snapshot search，用于大型页面只返回匹配节点，而不是让模型阅读整个页面。

---

# 20. iframe 设计

第一版可以不完整实现 iframe 穿透。

但是数据结构必须提前留出：

```json
{
  "ref": "f1e17",
  "frame_id": "f1",
  "element_id": "e17"
}
```

不要设计成：

```text
全局 num → 全局坐标
```

否则以后加入 iframe 时需要重新设计整个点击体系。

Playwright MCP 当前的 ref 本身就支持 frame 前缀，例如 `f1e12`，说明 frame scope 可以直接融入 ref 体系。

---

# 21. SoM 编号的生命周期

推荐定义：

```text
browser_inspect()
      ↓
生成 InspectSession
      ↓
num → ref 映射
      ↓
模型选择 num
      ↓
browser_click(som:num)
      ↓
检查 session 是否仍然有效
```

如果：

```text
页面导航
DOM大规模变化
浏览器滚动导致目标离开当前状态
ref失效
```

则：

```text
当前 SoM session 作废
        ↓
要求重新 browser_inspect
```

不要尝试：

```text
旧 num
↓
重新排序
↓
猜它对应哪个元素
```

---

# 22. Agent 的决策规则

建议将浏览器操作规则写入 System Prompt。

核心规则：

> **1. 浏览器页面默认优先使用 `browser_snapshot` 获取结构化元素 ref。**

> **2. 如果目标能够通过 ref 明确确定，不需要调用视觉能力。**

> **3. 当空间布局、图标、Canvas、图片、复杂组件等无法仅靠结构化信息确定时，调用 `browser_inspect`。**

> **4. `browser_inspect` 返回的数字是视觉标签，不是永久元素 ID；实际操作必须使用该 inspect session 关联的 ref。**

> **5. 页面发生导航或明显变化后，旧 snapshot/ref/SoM session 均视为可能失效，需要重新获取。**

> **6. 不要直接根据截图猜测 XY 坐标。**

> **7. 只有 DOM/AX 与 SoM 都无法可靠定位时，才使用 XY 鼠标操作。**

---

# 23. Agent 的标准操作循环

最终推荐：

```text
用户任务
   ↓
browser_snapshot
   ↓
能否直接确定元素？
   │
   ├── 是
   │    ↓
   │  ref操作
   │
   └── 否
        ↓
 browser_inspect
        ↓
  带编号截图 + elements
        ↓
    Vision Model
        ↓
      选择 num
        ↓
    num → ref
        ↓
    验证 ref
        ↓
    执行动作
        ↓
 browser_snapshot / screenshot
        ↓
    验证结果
        ↓
    下一步
```

---

# 24. Action Verification：必须增加的一层

本方案建议不要把：

```text
click
```

定义成：

```text
“鼠标已经发出点击事件”
```

而应该定义成：

```text
“动作已经执行，并且系统观察到了结果”
```

例如：

```json
{
  "action": "click",
  "target": "e17",
  "executed": true,
  "page_changed": true,
  "url_changed": true
}
```

或者：

```json
{
  "action": "click",
  "target": "e17",
  "executed": true,
  "page_changed": false,
  "new_snapshot": "..."
}
```

这样 Agent 才能真正判断：

```text
“点击搜索”成功
```

还是：

```text
“点击了，但页面没有发生预期变化”
```

这比单纯追求 SoM 点击精度更加重要。

---

# 25. 浏览器与桌面的最终统一模型

整个项目最终可以抽象出一个统一的：

```text
UITarget
```

例如：

```python
UITarget(
    source="browser_dom",
    ref="e17",
    bbox_css=[...],
    role="button",
    name="搜索",
)
```

SoM：

```python
UITarget(
    source="browser_som",
    ref="e17",
    visual_num=7,
    bbox_css=[...],
    role="button",
    name="搜索",
)
```

桌面：

```python
UITarget(
    source="desktop_vision",
    bbox=[...],
    center=[...],
    text="发送",
)
```

这样 Agent 不需要知道底层到底是：

```text
DOM
Accessibility
SoM
OCR
YOLO
视觉模型
```

只需要知道：

```text
这是一个可以操作的 UI Target。
```

---

# 26. 与现有项目能力的对应关系

| 现有能力 | 本方案中的定位 |
|---|---|
| `browser_snapshot` | 浏览器主定位通道 |
| `browser_click(index)` | 可逐步演进为 ref-based click |
| `browser_type` | 基于 ref 的输入操作 |
| `browser_screenshot` | 浏览器视觉观察 |
| `mouse_click` | 浏览器特殊场景坐标兜底 |
| `take_screenshot` | 桌面视觉观察 |
| `screen_inspect` | 桌面目标理解 |
| `click_at` | 桌面最终执行 |
| `vision_bridge` | 无视觉主模型时的视觉降级 |
| `_GUI_TOOLS` | 统一 Agent GUI 工具调度 |
| artifacts | SoM 截图、日志和审计产物 |

---

# 27. 推荐的文件改动

## 第一阶段

### `core/browser_controller.py`

新增：

```text
annotated_screenshot()
_get_accessible_elements()
_resolve_ref()
_get_element_bbox()
```

同时整理现有：

```text
browser_snapshot()
browser_click()
mouse_click()
```

使坐标和 ref 概念明确分离。

---

## 第二阶段

### `core/tool_registry.py`

增加：

```text
browser_inspect
```

必要时增加：

```text
browser_click(target)
```

统一处理：

```text
ref
som:num
selector
```

---

## 第三阶段

### `core/agent.py`

调整：

```text
_GUI_TOOLS
_capture_and_add_image
SYSTEM_PROMPT
```

关键改动：

```text
browser action
↓
browser_screenshot
```

而：

```text
desktop action
↓
take_screenshot
```

---

## 第四阶段

### 测试

重点增加：

```text
DPR测试
CSS/device pixel测试
ref生命周期测试
SoM num→ref测试
页面变化导致 ref stale
iframe ref
截图编号绘制
browser/desktop 截图分流
action verification
```

---

# 28. 推荐实施顺序

## P0：统一数据模型

先明确：

```text
UI target
ref
frame_id
bbox_css
center_css
source
```

这一阶段不需要视觉模型。

---

## P1：修复浏览器视觉反馈

把：

```text
browser action
        ↓
take_screenshot
```

改成：

```text
browser action
        ↓
browser_screenshot
```

首先解决现有系统已经存在的设计盲区。

---

## P2：完善 browser_snapshot/ref

让浏览器普通任务尽可能：

```text
snapshot
↓
ref
↓
click/type
```

完成低成本、高可靠的基础链路。

---

## P3：加入 browser_inspect SoM

实现：

```text
页面截图
+
DOM元素
+
视觉编号
+
num→ref
```

这一阶段就是本方案最核心的新能力。

---

## P4：Agent 混合决策

让 Agent 自己判断：

```text
DOM 足够 → DOM
DOM 不足 → SoM
SoM 仍不足 → XY
```

---

## P5：Action Verification

增加：

```text
动作后新 snapshot
+
URL
+
页面变化
+
必要时截图
```

让 Agent 从“会点”升级到“知道自己点对了没有”。

---

## P6：复杂页面

再逐渐增加：

```text
iframe
Shadow DOM
Canvas
拖拽
复杂视觉组件
```

---

# 29. 是否引入 OmniParser / YOLO

现阶段：

> **不建议用于浏览器主链路。**

原因不是这些模型没有价值，而是浏览器已经拥有：

```text
DOM
Accessibility
bbox
role
name
```

这类信息。

对于浏览器页面：

```text
DOM → 元素框
```

比：

```text
Screenshot → YOLO → 元素框
```

更直接。

因此 OmniParser 更适合未来：

```text
桌面 GUI
      ↓
没有 DOM
      ↓
只能看到 screenshot
```

的场景。

本项目目前采用浏览器 DOM + PIL SoM，不需要增加新的重型本地视觉模型，这也符合现有项目“无独显、复用现有 CDP + vision bridge”的硬件约束。

---

# 30. 最终架构图

```text
                         miniyu Agent
                              │
                              ▼
                    ┌───────────────────┐
                    │ UI Target Decision│
                    └─────────┬─────────┘
                              │
              ┌───────────────┼────────────────┐
              │               │                │
              ▼               ▼                ▼
         Browser DOM      Browser Visual     Desktop
              │               │                │
       browser_snapshot   browser_inspect   screen_inspect
              │               │                │
              ▼               ▼                ▼
             Ref       Screenshot + SoM      Vision/OCR
              │               │                │
              │          num → ref             │
              │               │                │
              └──────────┬────┘                │
                         │                     │
                         ▼                     ▼
                    DOM / CDP             click_at
                    action                 XY
                         │
                         ▼
                  Action Verification
                         │
                         ▼
                   New UI State
                         │
                         ▼
                    Agent Next Step
```

---

# 31. 最终设计原则

整个系统最终遵循以下原则：

### 原则 1：结构化信息优先

浏览器有 DOM/Accessibility，就优先使用它。

### 原则 2：视觉用于理解空间关系

截图不是用来让模型凭空猜坐标，而是帮助模型理解：

```text
“哪个视觉元素是我要的东西？”
```

### 原则 3：数字不是元素身份

SoM：

```text
7
```

只是视觉标签。

真正的执行对象：

```text
ref=e17
```

---

### 原则 4：坐标是执行底层能力，不是 Agent 的主要推理接口

尽量让 Agent：

```text
选择 target
```

而不是：

```text
猜 x=812 y=493
```

---

### 原则 5：CSS pixel 与 device pixel 分离

统一保存：

```text
bbox_css
center_css
```

只在截图绘制层进行像素缩放。

CDP 鼠标事件按 viewport CSS pixels 执行。

---

### 原则 6：任何页面变化都可能使旧目标失效

```text
navigation
SPA update
DOM replacement
major scroll
```

之后重新：

```text
snapshot / inspect
```

---

### 原则 7：动作完成不等于任务完成

必须观察：

```text
action
↓
state
↓
verification
```

而不是只判断：

```text
鼠标事件发送成功
```

---

# 32. 最终结论

本项目不应该继续沿着：

```text
截图
↓
视觉模型
↓
直接猜 XY
```

这条路线发展。

也不应该变成：

```text
所有浏览器操作
↓
先截图
↓
SoM
↓
编号点击
```

而应该形成一个三级定位系统：

```text
             ┌────────────────────┐
             │  Level 1           │
             │  DOM / AX / Ref    │
             │  默认主通道         │
             └─────────┬──────────┘
                       │
             无法可靠定位
                       ↓
             ┌────────────────────┐
             │  Level 2           │
             │  Screenshot + SoM  │
             │  视觉辅助通道       │
             └─────────┬──────────┘
                       │
             仍无法可靠定位
                       ↓
             ┌────────────────────┐
             │  Level 3           │
             │  XY Mouse          │
             │  最终兜底           │
             └────────────────────┘
```

其中：

**浏览器：**

```text
DOM/AX → Ref
       ↓
       SoM
       ↓
       XY
```

**桌面：**

```text
Screenshot
    ↓
Vision/OCR
    ↓
XY
```

最终两边统一抽象成：

```text
UITarget
    ↓
Action
    ↓
Verification
```

这比单纯增加一个 `browser_inspect` 更重要。

`browser_inspect` 本身只是这个体系中的视觉模块；真正值得作为 miniyu 架构升级的是：

> **把“模型看到的目标”和“工具实际操作的目标”统一成同一个 UI Target，并让 DOM/AX、SoM、坐标都成为不同的定位来源。**

这也是目前成熟浏览器 Agent 方案比较明确的发展方向：Playwright MCP 把 accessibility refs 作为结构化主通道，并在视觉上下文需要时组合截图；Playwriter 则进一步把视觉标签和 accessibility ref 结合起来。

---

## 推荐最终落地后的工具形态

```text
browser_snapshot()
        ↓
   结构化页面状态

browser_inspect()
        ↓
   页面截图 + SoM + ref

browser_click(target)
        ↓
   ref / som:num / selector

browser_type(target, text)
        ↓
   ref / som:num

browser_mouse(x, y)
        ↓
   特殊视觉场景

screen_inspect()
        ↓
   桌面截图 + 视觉理解

click_at(x, y)
        ↓
   桌面最终坐标执行
```

**核心不是“做一个 SoM 工具”，而是把现有 miniyu 的三条截图/操作链路真正接起来。**