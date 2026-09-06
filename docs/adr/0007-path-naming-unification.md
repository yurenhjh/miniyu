# 工具命名归位：统一文件/目录词形并引入隐藏别名

第4组工具层由 16 扩到 31 个工具后，"文件 vs 文件夹"命名出现混淆：`move_file`/`rename_file`/`get_file_metadata` 名为文件但 `shutil.move`/`Path.rename`/元数据实际都支持目录（名不符实）；目录类工具同时存在 `folder`(`create_folder`)与 `directory`(`list_directory`)两种词形。第3周与第3组联调尚未开始，是改名的零成本窗口。

决策要点：

- **命名归位为主，最小精准改动**：将实际支持目录的 3 个工具改为 `_path` 后缀（`move_file`→`move_path`、`rename_file`→`rename_path`、`get_file_metadata`→`get_path_metadata`），并把目录词形统一为 `directory`（`create_folder`→`create_directory`、`delete_folder`→`delete_directory`、`get_folder_metadata`→`get_directory_metadata`）。
- **`delete_file`/`copy_file` 保持 `_file` 不动**：`copy_file`(shutil.copy2) 与 `delete_file`(unlink) 语义就是"仅文件"，改名或放宽到目录会引入 `rmtree` 误删风险，故刻意保持仅文件（安全设计）。
- **旧名保留为隐藏别名**（`aliases` 映射）：旧名仍可被 `call` 调用并归一化为规范名统计，但 `list_tools()` 只返回规范名——零破坏联调、不复发混淆。
- **术语沉淀**：新增 `CONTEXT.md` 记录 `_file`/`_directory`/`_path` 后缀约定。

该决策在第3组联调开始前落地，之后契约以规范名为准；旧名作为兼容别名保留。
