# 新增 `sort_directory` 工具：目录内容排序(带元信息)

第4组工具层原来只有未排序的 `list_directory`，上层 Agent 需要"按文件名/大小/时间排序并附带元信息"的能力。我们决定新增一个独立的 `sort_directory` 工具 (工具数由 13 增至 14)，而非扩展现有的 `list_directory`，以保持已联调契约 `list_directory` 的稳定。

决策要点：

- **作为独立工具而非扩展 `list_directory`**——`list_directory` 已是第3组联调的稳定契约，动它有回归风险；排序作为独立能力供 Agent 直接决定是否需要元信息。
- **条目恒定四字段** `{name, type, size, mtime}`——字段恒定可让消费方无分支判断；其中**目录的 `size` 一律为 `None`**（目录没有有意义的单值大小），`mtime` 对目录仍返回。
- **排序键可选 `name` / `size` / `mtime`，默认 `name`**——覆盖最常见的三组排序诉求，不引入多级排序复杂度。
- **目录始终优先于文件，且不受 `reverse` 影响；`reverse` 只作用于主排序键，次键 `name` 恒升序兜底**——保证结果确定性，符合 `ls` 直觉，避免 reverse 时顺序反复。

实现使用自定义比较器 (`functools.cmp_to_key`) 精确控制该语义，排序逻辑抽取为私有 `_sorted_items` 供内部复用。
