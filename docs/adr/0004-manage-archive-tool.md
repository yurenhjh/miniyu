# `archive_directory` 演进为 `manage_archive`：压缩 / 解压一体工具

第4组最初交付了只做压缩的 `archive_directory`（工具数 15，见 ADR 0003）。第3周联调反馈需要同时具备解压能力。我们决定把该工具**改名并扩展为双向能力** `manage_archive`（工具数仍为 16），统一通过 `action` 参数区分压缩与解压。

决策要点：

- **单工具双能力（`action="compress" | "extract"`）而非两个姊妹工具**——压缩/解压是同一资源的两个方向，共用一个入口可减少工具面、便于 Agent 用统一签名；内部拆为 `_compress_archive` / `_extract_archive` 两个私有方法隔离实现。
- **压缩沿用 `shutil.make_archive` 与既有语义**（`root_dir=src`、`.zip` 自动补齐、默认 `{目录名}_archive.zip`、覆盖式写入）。
- **解压默认目标 `{zip名}_extracted`**（与压缩的默认命名对称）；**目标已存在则报错而非覆盖**——避免误覆盖/误删数据，这是压缩(覆盖)与解压(防覆盖)刻意的不对称，意在安全。
- **非法 `action` 明确报错**，与 `sort_directory` 的非法排序键处理一致。
- `archive_directory` 名称被移出工具表；对外契约以 `manage_archive` 为准。

该演进被记录为 ADR-0004，ADR-0003 中关于压缩语义的部分仍有效。
