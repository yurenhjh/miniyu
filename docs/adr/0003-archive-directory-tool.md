# 新增 `archive_directory` 工具：把目录打包为 zip

第4组工具层此前没有任何归档能力，上层 Agent 需要把目录一次性打包成 zip 以便备份/传输。我们决定新增一个 `archive_directory` 工具（工具数由 15 增至 16），作为又一个文件夹操作类工具。

决策要点：

- **API 形态** `archive_directory(src_dir, dest_zip=None)`——与 `list_directory`/`sort_directory`/`index_files` 的 `*_directory` 命名一致；`dest_zip` 可选，未指定时同级生成 `{目录名}_archive.zip`，降低调用方负担。
- **底层用 `shutil.make_archive`**（而非手写 `zipfile` 遍历）——简洁、自动处理递归与压缩，符合项目"尽量用标准库"的取向；扩展名 `.zip` 由调用方省略时自动补齐。
- **`root_dir=src_dir`**——使 zip 内路径相对源目录根（解压即得该目录内容，而非嵌套一层源目录名），这是最常见的归档语义。
- **无扩展名自动补 `.zip`**；目标已存在则覆盖。
- **边界**：`src_dir` 不存在/非目录报错；返回生成的 zip 绝对路径，便于 Agent 直接使用。

该工具不依赖网络，纯本地文件系统操作，可安全纳入 Sandbox 测试。
