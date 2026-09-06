# Group4 Tools + OS Skills

第4组的工具服务层：为上层 Agent 提供统一的文件系统操作（Tool）与高级任务（Skill）能力。

## Language

**File（文件）**:
单个文件实体。
_Avoid_: 文件/目录混称时用 "file" 指代目录

**Directory（目录）**:
文件夹实体。本项目统一用 `directory` 作为规范词，避免 `folder` 混用。
_Avoid_: folder

**Path（路径）**:
既指文件也可指目录的通用位置。工具名以 `_path` 结尾表示文件与目录皆可操作。
_Avoid_: 用 `_file` 结尾命名实际操作目录的工具

**Tool 命名后缀约定**:
- `_file` = 仅操作单个文件（如 `delete_file`、`compare_files`）
- `_directory` = 操作目录（如 `list_directory`、`create_directory`）
- `_path` = 文件与目录都支持（如 `move_path`、`rename_path`、`get_path_metadata`）
_Avoid_: `_folder`；用 `_file` 命名支持目录的工具

**隐藏别名（Hidden Alias）**:
为向后兼容保留的旧工具名。别名仍可被 `call` 调用（结果归一化为规范名），但不出现在 `list_tools()` 中。
_Avoid_: 在工具列表中展示过时旧名

**Skill（技能）**:
组合多个底层操作或标准库能力完成的高级任务（如 `safe_delete`、`smart_organize`）。
