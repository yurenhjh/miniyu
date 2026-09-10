# 第 4 组 Web 演示记录（6 场景实测）

- **日期**：2026-09-10
- **方式**：启动 `examples/miniyu_web.py`（http://localhost:5000），用脚本 `examples/demo_web_scenarios.py` 走与浏览器完全相同的 `/chat` + SSE 链路，逐条提交用户指令，驱动真实 LLM（qwen3.7-flash）function-calling 完成任务。
- **结论**：设计书演示场景表 6 个场景**全部一次跑通**，工具调用链与验收标准一一对应，且经磁盘实体验证（非仅依赖模型自述）。

---

## 场景演示结果

| 场景 | 用户指令（原文） | 实测工具调用链 | 验收标准核验 |
|------|------------------|----------------|--------------|
| 1 打开文件管理器 | 打开文件管理器 | `run_command(explorer)` | ✅ 资源管理器窗口打开 |
| 2 导航到目录 | 导航到 `D:\...\group4_tools_os_skills` | `run_command(explorer "路径")` + `list_directory` | ✅ 列出目标目录内容，与 GUI 定位一致 |
| 3 创建文件夹 | 创建 `project` 文件夹 | `create_directory` | ✅ 磁盘实检 `project/` 存在 |
| 4 文本编辑 | 打开 test.txt 并添加一行 | `read_text_file → edit_file → read_text_file(复核)` | ✅ 磁盘实检已追加 `demo line from miniyu` |
| 5 窗口切换 | 切换到浏览器 | `list_windows → activate_window(hwnd)` | ✅ 激活 Microsoft Edge（localhost:5000）并置于前台 |
| 6 整理下载目录 | 整理 demo_downloads 按类型分类 | `list_directory + get_path_metadata×4 + create_directory×4 + move_path×4`（共 14 次调用） | ✅ 文件按类型移入 代码/图片/文本/文档 子文件夹 |

---

## 场景 6「整理下载目录」完整工具链（复杂任务重点）

```
list_directory(demo_downloads)
 → get_path_metadata(main.py / notes.txt / photo.jpg / report.pdf)   # 摸清类型
 → create_directory(代码)  create_directory(文本)
   create_directory(图片)  create_directory(文档)                     # 按类型建分类夹
 → move_path(main.py→代码) move_path(notes.txt→文本)
   move_path(photo.jpg→图片)  move_path(report.pdf→文档)               # 逐文件归类
 → list_directory(复核)
```
模型自主完成「扫描 → 分类 → 创建文件夹 → 移动」完整闭环，零人工干预。

---

## 说明

- 场景 5 选用现存浏览器窗口，验证 `activate_window` 窗口切换能力。
- 场景 6 在隔离的 `demo_downloads/` 测试目录执行（4 个混合类型样本），避免改动真实下载目录。
- 服务运行期间无 Traceback，`/status` 全程 200。
- 演示驱动脚本：`examples/demo_web_scenarios.py`（可复现，`python` 直接运行）。