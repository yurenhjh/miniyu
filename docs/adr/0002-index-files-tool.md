# 新增 `index_files` 工具：可持久化的文件元信息索引

第4组工具层已有非递归的目录列出（`list_directory`/`sort_directory`），但缺少"递归收集全目录文件元信息并可跨调用复用"的能力。我们决定新增一个 `index_files` 工具（工具数由 14 增至 15），既可在当次构建索引清单，也可指定 `persist`/`load` 将索引落盘与复读。

决策要点：

- **单工具、参数区分行为**（`index_files(path, recursive, persist, load)`）——与 `sort_directory` 同层风格一致，接口少；传 `load` 时直接读 JSON 返回、不遍历文件系统，传 `persist` 时写盘，两种模式用参数切换而非并列工具。
- **递归收集 + 绝对路径条目** `{path, name, type, size, mtime}`——条目结构与 `sort_directory` 同构（目录 size 为 None），但 `path` 用绝对路径、默认递归覆盖子目录，满足"全量快照"语义。
- **持久化采用 JSON**（零依赖、跨平台、人类可读、便于测试断言），而非 pickle——确保索引可被其他语言/检查点材料直接查看。
- **与 `sort_directory` 独立实现**——`sort_directory` 是非递归单层视图，`index_files` 是递归全量快照，语义不同，不互相调用避免耦合。
- **边界与防呆**：空目录返回空列表；path 不存在/非目录、persist/load 失败均报错（由 `call` 捕获返回 error）；不设条目数上限（课程设计规模）。

加载模式依赖 index 文件内容即为 `[{path,name,type,size,mtime}]`，保证持久化与构建两条路径的返回结构一致。
