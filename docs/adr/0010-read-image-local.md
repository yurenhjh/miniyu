# 本地图片直读（read_image）：模型"能截屏≠能读文件"

日期：2026-09-08。背景来自用户实测反馈："让它看 `D:\桌面\1.3.png` 里的题，它说读不了图片，
只能靠截屏才识别到；既然能读自己截的图，为什么不能直接读图片？"

## 问题：模型没有"给路径读文件"的视觉通道

1. 模型可见函数里与视觉相关的只有 `screen_inspect`（ADR 0009）——它的语义是**截取当前屏幕**
   再交给视觉源理解。屏幕像素与文件 I/O 是两条路：能看截屏 ≠ 能读任意本地图片文件。
2. 面对"看看某张图片里的问题"，模型只能退化为"请你先把图打开"或"调 screen_inspect 碰运气"，
   体验割裂且常常失败（图片未打开在屏幕上时截屏根本看不到）。
3. 视觉源本身是现成的（`llm.supports_vision` 主模型原生视觉 / `vision_bridge` 独立视觉 API），
   缺的只是"把本地文件喂给视觉源"的入口。

## 决策

- **新增 Agent 自带视觉能力函数 `read_image(path)`**（与 `screen_inspect` 并列，不是
  SkillLibrary 技能，不改变工具/技能计数语义；模型可见函数全集 = 59 工具 + 5 白名单技能
  + screen_inspect/read_image 视觉双通道）。
- **双通道自适应**（复用 ADR 0009 的统一视觉源）：
  - `llm.supports_vision: true`（主模型有原生视觉，当前 qwen3.5-plus）→ 读取文件做真实
    base64，以 `image_url` 内联发给主模型直接看图；
  - `false`（无视觉主模型）→ 调 `core/vision_bridge` 把图转成文字描述返回；
  - 文件不存在 / 两个视觉源都没配好 → **明确报错**（VisionBridgeError 带指引），绝不瞎编。
- **只读、无确认门**（与 screen_inspect 同级）。
- SYSTEM_PROMPT 新增守则：涉及"看某张图片/截图里的内容"时优先 `read_image(路径)`，
  而不是先让用户打开图片再截屏。

## 影响

- 用户可直接说"看 D:\xxx.png 里的题目"并附路径，模型一步到位读文件分析。
- 视觉能力函数从 1 个扩到 2 个；模型可见函数集 64 → 66。
- 测试：`tests/test_agent_vision.py` 增补 read_image 双通道 mock 与文件缺失报错用例。

## 相关

- ADR 0009（screen_inspect / 视觉通道 / 过程产物）
- `core/vision_bridge.py`、`core/agent.py`（_AGENT_IMAGE_TOOL / _execute_read_image）
