"""
safety.py
第4组：安全分级与确认机制

按“副作用的可逆性 / 对外影响力”给每个工具和技能标注风险等级，
作为“执行前是否要征求用户许可”的统一依据。

风险等级（由低到高）：
    read_only   只读，无副作用
    low         可逆副作用（复制/移动/重命名/新建/打开等）
    medium      有意义、可回退或需谨慎的副作用（覆盖写入/移动文件/网页点击等）
    high        不可逆或对外可见的副作用（发送消息/永久删除/执行命令等）

设计目标：
    - 多步操作尽量“一句话”让 agent 自主完成；
    - 不可逆的“最后一步”在执行前暂停，取得用户许可才动手；
    - 专业/创意类任务不禁止，而是“低置信度提醒 + 许可后尽力而为”（见 classifier.py）。
"""

READ_ONLY = "read_only"
LOW = "low"
MEDIUM = "medium"
HIGH = "high"

RISK_LEVELS = (READ_ONLY, LOW, MEDIUM, HIGH)
_RISK_ORDER = {level: i for i, level in enumerate(RISK_LEVELS)}


class ConfirmationDenied(Exception):
    """执行在确认门处被用户拒绝"""


def requires_confirmation(risk):
    """high 风险（不可逆 / 对外可见）默认需要在执行前征求许可"""
    return risk == HIGH


# 授权档位（agent.authorization）：决定哪些高危操作在执行前要征求用户许可
AUTHZ_BASE = "base"            # 基础授权：所有 HIGH 都确认（现状默认）
AUTHZ_ADVANCED = "advanced"    # 高级授权：只对"永久删除文件"类确认，其余 HIGH 自动放行
AUTHZ_FULL = "full"            # 全自动：从不确认（含删除）
AUTHZ_LEVELS = (AUTHZ_BASE, AUTHZ_ADVANCED, AUTHZ_FULL)
AUTHZ_LABELS = {
    AUTHZ_BASE: "基础授权",
    AUTHZ_ADVANCED: "高级授权",
    AUTHZ_FULL: "全自动",
}

# 高级档下仍要确认的"永久删除类"：单文件/目录删除 + 按扩展名批量删 + 清空回收站
# （与上面 TOOL_META / SKILL_META 的 HIGH 定义一一对应，勿漏改）
_PERMANENT_DELETE = {
    "delete_file",
    "delete_directory",
    "cleanup_by_type",
    "empty_trash",
}


def should_confirm(level, name, risk):
    """按授权档位决定某工具/技能执行前是否要征求许可（Agent 运行时确认门的判断源）

    level: AUTHZ_BASE / AUTHZ_ADVANCED / AUTHZ_FULL（未知值按 base 处理，维持现状）
    name:  工具/技能名（advanced 档下区分"永久删除"与其余 HIGH）
    risk:  READ_ONLY / LOW / MEDIUM / HIGH
    """
    if risk != HIGH:
        return False
    if level == AUTHZ_FULL:
        return False
    if level == AUTHZ_ADVANCED:
        return name in _PERMANENT_DELETE
    return True


def resolve_authz_level(agent_cfg) -> str:
    """从 agent 配置段解析当前授权档位。

    agent.authorization 字段优先（base/advanced/full）；
    未显式配置时兼容旧字段：confirm_high_risk=False 等价"全自动"；默认（True/缺省）= 基础授权。
    """
    cfg = agent_cfg or {}
    authz = cfg.get("authorization")
    if authz in AUTHZ_LEVELS:
        return authz
    if cfg.get("confirm_high_risk", True) is False:
        return AUTHZ_FULL
    return AUTHZ_BASE


# 工具风险分级表：name -> (risk, category, description)
TOOL_META = {
    # 文件操作
    "copy_file": (LOW, "文件操作", "复制文件"),
    "move_path": (LOW, "文件操作", "移动文件/目录"),
    "delete_file": (HIGH, "文件操作", "永久删除文件"),
    "rename_path": (LOW, "文件操作", "重命名文件/目录"),
    "create_file": (LOW, "文件操作", "创建空文件"),
    # 文件夹操作
    "create_directory": (LOW, "文件夹操作", "创建目录"),
    "delete_directory": (HIGH, "文件夹操作", "永久删除目录"),
    "list_directory": (READ_ONLY, "文件夹操作", "查看目录内容"),
    "sort_directory": (READ_ONLY, "文件夹操作", "排序目录内容"),
    "index_files": (LOW, "文件夹操作", "递归建立文件索引"),
    "manage_archive": (MEDIUM, "文件夹操作", "打包/解压 zip"),
    # 文件查询
    "file_exists": (READ_ONLY, "文件查询", "判断文件是否存在"),
    "get_file_size": (READ_ONLY, "文件查询", "获取文件大小"),
    "get_path_metadata": (READ_ONLY, "文件查询", "获取路径完整元数据"),
    "get_directory_metadata": (READ_ONLY, "文件查询", "获取目录统计信息"),
    "check_file_access": (READ_ONLY, "文件查询", "检查文件读写执行权限"),
    # 完整性
    "file_checksum": (READ_ONLY, "完整性", "计算文件哈希"),
    "compare_files": (READ_ONLY, "完整性", "比较两个文件差异"),
    # 文本文件
    "read_text_file": (READ_ONLY, "文本文件", "读取文本文件"),
    "write_text_file": (MEDIUM, "文本文件", "写入/覆盖文本文件"),
    # 批量操作
    "batch_copy": (LOW, "批量操作", "批量复制多个文件"),
    "batch_move": (LOW, "批量操作", "批量移动多个文件"),
    # 磁盘管理
    "disk_usage": (READ_ONLY, "磁盘管理", "查询磁盘空间使用"),
    "directory_size": (READ_ONLY, "磁盘管理", "查询目录占用空间"),
    "find_empty_files": (READ_ONLY, "磁盘整理", "查找空文件"),
    "find_empty_directories": (READ_ONLY, "磁盘整理", "查找空目录"),
    "find_old_files": (READ_ONLY, "磁盘整理", "查找长期未修改文件"),
    "find_large_files": (READ_ONLY, "磁盘整理", "查找大文件"),
    "search_files": (READ_ONLY, "磁盘整理", "按名称模式搜索文件"),
    # 编程开发
    "search_in_files": (READ_ONLY, "编程开发", "在文件内容中搜索关键词/正则"),
    "edit_file": (MEDIUM, "编程开发", "局部编辑文本文件（查找并替换）"),
    # 图片处理
    "get_image_metadata": (READ_ONLY, "图片处理", "读取图片元数据/EXIF"),
    "rotate_image": (MEDIUM, "图片处理", "旋转图片并写出结果"),
    # 系统工具
    "current_directory": (READ_ONLY, "系统工具", "获取当前路径"),
    "run_command": (HIGH, "系统工具", "执行系统命令"),
    # 应用操作
    "list_windows": (READ_ONLY, "应用操作", "枚举桌面窗口"),
    "find_window": (READ_ONLY, "应用操作", "按标题/进程查找窗口"),
    "activate_window": (LOW, "应用操作", "激活窗口到前台"),
    "send_hotkey": (MEDIUM, "应用操作", "发送快捷键组合"),
    "send_text": (MEDIUM, "应用操作", "向焦点输入文本"),
    "take_screenshot": (READ_ONLY, "应用操作", "屏幕截图"),
    "click_at": (MEDIUM, "应用操作", "鼠标点击屏幕坐标（可能触发动作）"),
    # 浏览器控制
    "browser_launch": (LOW, "浏览器控制", "启动并连接浏览器"),
    "browser_close": (LOW, "浏览器控制", "关闭浏览器连接"),
    "browser_navigate": (LOW, "浏览器控制", "导航到指定 URL"),
    "browser_snapshot": (READ_ONLY, "浏览器控制", "提取可交互元素索引"),
    "browser_click": (MEDIUM, "浏览器控制", "点击元素（可能触发动作）"),
    "browser_type": (LOW, "浏览器控制", "向元素输入文本"),
    "browser_read_text": (READ_ONLY, "浏览器控制", "读取页面文本"),
    "browser_screenshot": (READ_ONLY, "浏览器控制", "页面截图"),
    "browser_wait": (READ_ONLY, "浏览器控制", "等待元素/文本出现"),
    # 系统管理：进程
    "list_processes": (READ_ONLY, "系统管理", "列出当前运行的进程"),
    "get_process_info": (READ_ONLY, "系统管理", "获取指定进程的详细信息"),
    "kill_process": (HIGH, "系统管理", "强制终止指定进程"),
    "terminate_process": (HIGH, "系统管理", "优雅终止指定进程"),
    # 系统管理：网络
    "network_status": (READ_ONLY, "系统管理", "获取网络接口状态"),
    "ping_host": (READ_ONLY, "系统管理", "测试主机网络连通性"),
    # 系统管理：环境变量
    "list_env_vars": (READ_ONLY, "系统管理", "列出环境变量"),
    "get_env_var": (READ_ONLY, "系统管理", "获取指定环境变量的值"),
}

# 技能风险分级表：name -> (risk, category, description)
SKILL_META = {
    "organize_downloads": (MEDIUM, "文件整理", "整理下载目录（移动文件）"),
    "system_info": (READ_ONLY, "系统信息", "获取系统信息"),
    "cleanup_temp": (MEDIUM, "清理", "清理临时文件"),
    "search_file": (READ_ONLY, "搜索", "文件搜索"),
    "find_large_files": (READ_ONLY, "分析", "找出最大 N 个文件"),
    "find_recent_files": (READ_ONLY, "分析", "找最近修改的文件"),
    "summarize_files": (READ_ONLY, "分析", "目录统计"),
    "cleanup_by_type": (HIGH, "清理", "按扩展名批量删除文件"),
    "batch_archive": (MEDIUM, "归档", "批量压缩/解压"),
    "duplicate_finder": (READ_ONLY, "分析", "发现疑似重复文件"),
    "rebuild_index": (LOW, "索引", "重建并落盘索引"),
    "query_index": (READ_ONLY, "索引", "索引模糊查询"),
    "trash_file": (LOW, "回收站", "移入回收站（可恢复）"),
    "restore_file": (LOW, "回收站", "从回收站恢复"),
    "empty_trash": (HIGH, "回收站", "清空回收站（不可逆）"),
    "safe_delete": (MEDIUM, "删除", "安全删除（默认进回收站）"),
    "deduplicate_files": (MEDIUM, "去重", "按哈希去重（移入回收站）"),
    "backup_file": (LOW, "备份", "备份文件"),
    "restore_backup": (LOW, "备份", "从备份恢复"),
    "smart_organize": (MEDIUM, "整理", "智能整理（移动文件）"),
    "app_open": (LOW, "应用", "打开指定应用"),
    "app_send_message": (HIGH, "应用", "在应用内发送消息（对外可见、不可撤回）"),
    "read_qq_chat": (READ_ONLY, "应用", "读取 QQ 会话最近聊天记录（只读、不外发）"),
    "send_email": (HIGH, "邮件", "发送邮件（对外可见、不可撤回，发后自动 IMAP 回读核验）"),
    "browser_search": (LOW, "浏览器", "结构化网络搜索"),
    "browser_extract": (READ_ONLY, "浏览器", "网页内容提取"),
}


def get_meta(kind, name):
    """返回某工具/技能的元数据（风险、类别、描述），未知名字给安全的默认值"""
    table = TOOL_META if kind == "tool" else SKILL_META
    risk, category, description = table.get(name, (LOW, "通用", name))
    return {
        "kind": kind,
        "name": name,
        "risk": risk,
        "category": category,
        "description": description,
    }


def preview(kind, name, params, meta):
    """构造发给确认处理器的“动作预告”"""
    return {
        "kind": kind,
        "name": name,
        "risk": meta["risk"],
        "category": meta["category"],
        "description": meta["description"],
        "requires_confirmation": requires_confirmation(meta["risk"]),
        "params": params,
    }


def cli_confirm(pv, prompt="是否执行？(y/n) "):
    """命令行确认处理器：打印动作预告，等待用户输入"""
    print("[确认] 即将执行：")
    print(f"  类型: {pv['kind']}  名称: {pv['name']}")
    print(f"  风险: {pv['risk']}  说明: {pv['description']}")
    if pv.get("describe"):
        print(f"  动作: {pv['describe']}")
    if pv.get("params"):
        print(f"  参数: {pv['params']}")
    answer = input(prompt).strip().lower()
    if answer not in ("y", "yes", "是", "同意", "允许"):
        raise ConfirmationDenied(pv.get("name", ""))
    return True