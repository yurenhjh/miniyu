# 搜索能力增强：search_file / query_index 支持精确与模糊匹配

原有 `search_file` 与 `query_index` 仅做**文件名子串包含匹配**（`keyword in name`，不区分大小写）——既非精确搜索，也不支持通配符/正则等模糊模式。而"模糊搜索"在不同场景含义不同（通配符、正则、相似度），我们决定给两个技能增加 **`mode` 参数** 统一支持最常见的三种匹配方式。

决策要点：

- **新增 `mode` 参数**，可选 `substring`（默认，保持向后兼容）/ `exact` / `wildcard` / `regex`：
  - `substring`：子串包含（原有行为，不破坏既有调用与 Mock）
  - `exact`：文件名完全相等（不区分大小写）
  - `wildcard`：通配符模式（`*` 任意、`?` 单字符、`[..]` 字符集），用标准库 `fnmatch`
  - `regex`：正则表达式，用标准库 `re.search(..., re.IGNORECASE)`
- **统一匹配逻辑抽取为私有方法 `_name_matches(name, keyword, mode)`**，供 `search_file`（遍历磁盘）与 `query_index`（读索引不遍历磁盘）共用，避免两处重复实现漂移。
- **`exact` 采用整名相等**而非子串，符合"精确搜索"语义；非法 `mode` 明确报错。
- 选择 `fnmatch`/`re`（均标准库、零依赖），未采用编辑距离/相似度模糊——那是另一类"容忍拼写错误"的模糊，当前需求以模式匹配为准，需要时再扩展。

该增强不改变既有技能契约（`mode` 默认值保证兼容），`search_file` 与 `query_index` 的调用方式保持不变。
