# 技能库扩展：新增 8 个文件分析/归档/索引技能

第4组技能库原有 4 个技能（整理下载、系统信息、清临时、搜文件）。按任务书"SkillLibrary 封装系统管理操作"的职责，我们一次性新增 8 个面向文件分析的技能（技能数由 4 增至 12），并提供了完善的 Mock 与测试。

决策要点：

- **技能保持独立于工具层（不持 ToolRegistry 引用）**——与既有 4 个技能一致，用标准库（`pathlib`/`shutil`/`zipfile`/`json`）自足实现，规避与工具层的循环依赖；但契约与对应工具保持一致（如 `rebuild_index` 生成的 JSON 与 `index_files` 工具同构）。
- **`find_large_files` / `find_recent_files` / `summarize_files` / `duplicate_finder`**——均为只读分析，返回可序列化结构（列表/字典），便于上层 Agent 直接消费；`cleanup_by_type` 是有副作用的删除，返回被删文件清单。
- **`batch_archive(items, action)`**——一次处理多个目录/zip，返回 `{输入路径: 结果路径}` 映射，压缩/解压语义与 `manage_archive` 工具一致（默认命名、解压目标已存在报错防覆盖）。
- **`rebuild_index` / `query_index`**——演示"索引→快速查询"模式：一次落盘 JSON 索引，后续查询**复用索引而不再遍历磁盘**；`query_index` 只做内存内的文件名模糊匹配，符合索引的核心价值。
- 所有技能沿用 `SkillLibrary.call()` 统一错误包装（异常→`{"success": False}`）。

提供 Mock 全量实现（`mock/mock_skills.py`）以保证跨组联调接口稳定。
