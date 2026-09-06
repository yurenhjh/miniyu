"""
skill_library.py
第4组：OS Skills 技能库
负责封装高级操作系统能力（文件整理、系统信息、临时清理、文件搜索、分析工具等）
"""

import fnmatch
import hashlib
import json
import os
import re
import shutil
import platform
import time
import zipfile
from collections import defaultdict
from pathlib import Path

from core.app_controller import get_controller
from core import safety


# =====================================================
# 面向 Agent 的“组合技能”白名单（Agent 白名单模式）
# =====================================================
# 这些技能会被叠加在 ToolRegistry 的 57 个工具之上，以 OpenAI function 形式暴露给
# 大模型直接调用（run/run_stream 传给 LLM 的 tools = registry.openai + 这里的技能）。
# 入选条件：
#   1. 高层次——模型应该整段调用，而不是拆成激活窗口/点击/输入等零散原语；
#   2. 参数全部可 JSON 序列化（Python 闭包等无法经 tool_call 传输，只能由技能内部构造）。
# 其余技能（文件整理/删除/索引等）仍只供 ExecutionEngine / 上层代码直接调用，不暴露给模型，
# 避免 25 个技能与 57 个底层工具大面积重叠、模型误选低层原语。
_AGENT_SKILL_SCHEMAS = {
    "app_send_message": {
        "description": (
            "在 IM 桌面应用（QQ 等，经典布局）中『搜索指定会话/群 → 发送一条消息』的组合技能。"
            "动作序列：激活应用 → 应用内搜索框输入关键词 → 逐行点选搜索结果，每行用屏幕 OCR "
            "核对聊天标题确实含该关键词，核对通过才继续（不会发到错误会话）→ 定位聊天输入框 "
            "→ 输入消息正文并回车发送。发送是对外可见、不可撤回的高危操作，执行前会请求用户确认。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "IM 应用进程名/标题关键字（默认 QQ）",
                    "default": "QQ",
                },
                "search_keyword": {
                    "type": "string",
                    "description": "要搜索并进入的会话/群名称关键词，必须与用户说的群名一致（如完整群名）",
                },
                "message": {
                    "type": "string",
                    "description": "要发送的消息正文",
                },
                "verify_ocr": {
                    "type": "boolean",
                    "description": "发送前用屏幕 OCR 核对当前会话标题确实含关键词，默认开启；不要关闭",
                    "default": True,
                },
            },
            "required": ["search_keyword", "message"],
        },
    },
    "send_email": {
        "description": (
            "发送一封电子邮件（SMTP），发出后自动用 IMAP 回读发件箱『已发送』核验已落库；"
            "若 config.yaml 的 email 段配了 verify_inbox（收件侧邮箱的 IMAP），还会再轮询"
            "收件人收件箱确认『确实到达』。对外可见、不可撤回的高危操作，执行前会请求用户确认。"
            "发信账号与授权码从 config.yaml 的 email 段读取，不要把授权码放进参数。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "收件人邮箱地址（可多个，逗号分隔），必须与用户说的一致",
                },
                "subject": {
                    "type": "string",
                    "description": "邮件主题",
                },
                "body": {
                    "type": "string",
                    "description": "邮件正文（纯文本）",
                },
                "cc": {
                    "type": "string",
                    "description": "可选抄送邮箱（逗号分隔）",
                },
                "verify": {
                    "type": "boolean",
                    "description": "发送后是否自动 IMAP 回读核验（已发送/到达），默认开启；不要关闭",
                    "default": True,
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
}


_OCR_QUESTION = (
    "只看这一横条里的聊天/会话标题文字，原样输出标题，不要解释。"
    "如果看不到任何标题就输出'无'。"
)


def _resolve_vision_js():
    """定位外部 node 视觉桥脚本（旧通道，可选）：环境变量 AGENT_VISION_JS 优先，
    否则 ~/.claude/skills/qwen-vision/vision.js"""
    env = os.environ.get("AGENT_VISION_JS")
    if env:
        return env
    p = Path.home() / ".claude" / "skills" / "qwen-vision" / "vision.js"
    return str(p) if p.exists() else None


def _crop_title_band(screenshot_path, rect, out_path):
    """裁出聊天标题横带（避开会话列/成员列），供 OCR 读取『当前会话名』"""
    from PIL import Image
    im = Image.open(screenshot_path).convert("RGB")
    L, T, W, H = rect["left"], rect["top"], rect["width"], rect["height"]
    x0 = L + int(W * 0.30)
    x1 = L + int(W * 0.92)
    y0 = T + int(H * 0.07)
    y1 = T + int(H * 0.14)
    im.crop((x0, y0, x1, y1)).save(out_path)
    return out_path


def _make_node_verify(keyword, rect, vision_js):
    """旧通道：用外部 node 视觉桥脚本做 OCR（显式 vision_js / AGENT_VISION_JS 时走）"""
    import subprocess
    import tempfile
    tmp = tempfile.gettempdir()

    def verify(screenshot_path):
        crop = _crop_title_band(screenshot_path, rect, os.path.join(tmp, "qq_ocr_title.png"))
        q = subprocess.run(
            ["node", vision_js, crop, _OCR_QUESTION],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        text = (q.stdout + q.stderr).strip()
        return keyword in text

    return verify


def make_ocr_verify(keyword, rect, vision_js=None):
    """
    构造“发送前校验”闭包：屏幕 OCR 读取聊天标题，必须含 keyword 才放行（返回 True）。

    视觉源（优先级）：
      1. 显式传 vision_js → 用那个外部 node 视觉桥脚本（旧通道）；
      2. 默认走**项目内视觉桥** core.vision_bridge：它的 describe_image 会自动选视觉源——
         主对话 llm 自己有视觉（llm.supports_vision=true）→ 直接用主对话模型读裁剪出的
         标题图（不花第二个 key）；主对话是纯文本（false）→ 用 config.yaml 里 vision_bridge
         段配的“有视觉”模型。别人拿到项目两种填法都能 OCR：
             - 只填一个有视觉的 key 在 llm；
             - 或 llm 填纯文本、再单独给 vision_bridge 填一个有视觉的 key。
      3. 主 llm 无视觉 且 vision_bridge 也没配好 → 退回外部 node 脚本
         （AGENT_VISION_JS / ~/.claude 那份）；再没有就抛错。

    供技能本体 verify_ocr=True 或外部代码（examples/demo、Agent 层）复用；
    找不到任何视觉源时直接抛错（VisionBridgeError，附 config.yaml 配置指引），
    宁可失败也绝不“盲发第一行”。
    """
    from core import vision_bridge as _vb

    # 1) 显式外部 node 桥（旧通道）
    if vision_js is not None:
        if not Path(vision_js).exists():
            raise RuntimeError(
                f"指定的视觉桥脚本不存在：{vision_js}。"
                "可用环境变量 AGENT_VISION_JS 指定脚本路径，或改传 verify 回调 / verify_ocr=False。")
        return _make_node_verify(keyword, rect, vision_js)

    # 2) 默认：项目内视觉桥（config.yaml 的 vision_bridge 段）
    try:
        _vb.require_vision()
    except _vb.VisionBridgeError:
        # 3) 兼容旧通道：没配 vision_bridge 但机器上还有外部 node 桥 → 用它
        legacy = _resolve_vision_js()
        if legacy and Path(legacy).exists():
            return _make_node_verify(keyword, rect, legacy)
        raise  # VisionBridgeError（RuntimeError 子类，带 config.yaml 配置指引）

    def verify(screenshot_path):
        import tempfile
        crop = _crop_title_band(
            screenshot_path, rect, os.path.join(tempfile.gettempdir(), "qq_ocr_title.png"))
        text = _vb.describe_image(crop, _OCR_QUESTION)
        return keyword in text

    return verify


class SkillLibrary:
    """
    OS Skills 技能库

    提供高级系统技能：
    - organize_downloads : 自动整理下载目录
    - system_info        : 获取系统信息
    - cleanup_temp       : 清理临时文件
    - search_file        : 文件搜索
    - find_large_files   : 找出最大 N 个文件
    - find_recent_files  : 找最近修改的文件
    - summarize_files    : 目录统计
    - cleanup_by_type    : 按扩展名批量清理
    - batch_archive      : 批量压缩/解压
    - duplicate_finder   : 按大小发现疑似重复
    - rebuild_index      : 一键重建并落盘索引
    - query_index        : 索引文件名模糊查询
    """

    def __init__(self):
        self.skills = {}
        self._confirm_handler = None
        self._register_defaults()

    @property
    def app(self):
        """惰性创建应用控制器（应用操作技能首次调用时才实例化）"""
        if not hasattr(self, "_app"):
            self._app = get_controller()
        return self._app

    @property
    def browser(self):
        """惰性创建浏览器结构化控制器（浏览器技能首次调用时才实例化）"""
        if not hasattr(self, "_browser"):
            from core.browser_controller import BrowserController
            self._browser = BrowserController()
        return self._browser

    # =====================================================
    # Skill注册与管理
    # =====================================================

    def _register_defaults(self):
        """注册默认技能"""
        self.register("organize_downloads", self.organize_downloads)
        self.register("system_info", self.system_info)
        self.register("cleanup_temp", self.cleanup_temp)
        self.register("search_file", self.search_file)
        self.register("find_large_files", self.find_large_files)
        self.register("find_recent_files", self.find_recent_files)
        self.register("summarize_files", self.summarize_files)
        self.register("cleanup_by_type", self.cleanup_by_type)
        self.register("batch_archive", self.batch_archive)
        self.register("duplicate_finder", self.duplicate_finder)
        self.register("rebuild_index", self.rebuild_index)
        self.register("query_index", self.query_index)
        self.register("trash_file", self.trash_file)
        self.register("restore_file", self.restore_file)
        self.register("empty_trash", self.empty_trash)
        self.register("safe_delete", self.safe_delete)
        self.register("deduplicate_files", self.deduplicate_files)
        self.register("backup_file", self.backup_file)
        self.register("restore_backup", self.restore_backup)
        self.register("smart_organize", self.smart_organize)
        self.register("app_open", self.app_open)
        self.register("app_send_message", self.app_send_message)
        self.register("send_email", self.send_email)
        self.register("browser_search", self.browser_search)
        self.register("browser_extract", self.browser_extract)

    def register(self, name, func):
        """注册技能"""
        self.skills[name] = func

    def unregister(self, name):
        """注销技能"""
        self.skills.pop(name, None)

    def list_skills(self):
        """列出所有已注册技能"""
        return list(self.skills.keys())

    # ---- Agent 白名单技能（可被大模型直接调用） ----

    def openai_skill_names(self):
        """返回白名单内且已注册、可被 Agent/大模型直接调用的技能名"""
        return [n for n in self.list_skills() if n in _AGENT_SKILL_SCHEMAS]

    def is_agent_skill(self, name):
        """name 是否是白名单技能（可经 Agent 作为 function-calling 调用）"""
        return name in _AGENT_SKILL_SCHEMAS and name in self.skills

    def list_openai_tools(self):
        """白名单组合技能 → OpenAI Function Calling 格式（供 Agent 叠加给 LLM）"""
        out = []
        for name in self.openai_skill_names():
            spec = _AGENT_SKILL_SCHEMAS[name]
            meta = self.get_skill_meta(name)
            out.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec.get("description") or meta["description"],
                    "parameters": spec.get("parameters", {"type": "object", "properties": {}}),
                },
            })
        return out


    # =====================================================
    # 安全分级 + 确认门
    # =====================================================

    def set_confirm_handler(self, fn):
        """注入确认处理器 fn(preview) -> bool；拒绝时抛 safety.ConfirmationDenied"""
        self._confirm_handler = fn

    def get_skill_meta(self, name):
        """获取技能的元数据（风险/类别/描述）"""
        return safety.get_meta("skill", name)

    def list_skills_meta(self):
        """列出所有技能及其元数据"""
        return {name: self.get_skill_meta(name) for name in self.list_skills()}

    def run_skill_safely(self, name, params=None, confirm_handler=None):
        """
        带确认门的技能调用：high 风险技能执行前先征求许可。
        """
        params = params or {}
        meta = self.get_skill_meta(name)

        if safety.requires_confirmation(meta["risk"]):
            handler = confirm_handler or self._confirm_handler
            if handler is None:
                return {
                    "success": False,
                    "skill": name,
                    "error": "该技能不可逆，需要先配置确认处理器",
                    "error_code": "CONFIRMATION_REQUIRED",
                }
            pv = safety.preview("skill", name, params, meta)
            try:
                if not handler(pv):
                    return {
                        "success": False,
                        "skill": name,
                        "error": "用户拒绝执行",
                        "error_code": "CONFIRMATION_DENIED",
                    }
            except safety.ConfirmationDenied:
                return {
                    "success": False,
                    "skill": name,
                    "error": "用户拒绝执行",
                    "error_code": "CONFIRMATION_DENIED",
                }

        return self.call(name, params)

    def call(self, name, params=None):
        """
        调用指定技能

        参数：
            name: 技能名称
            params: 参数字典

        返回：
            {"success": True/False, "skill": name, "result": ...}
            或 {"success": False, "error": "..."}
        """
        params = params or {}
        if name not in self.skills:
            return {
                "success": False,
                "skill": name,
                "error": f"Skill不存在: {name}",
                "error_code": "SKILL_NOT_FOUND",
            }
        try:
            result = self.skills[name](**params)
            return {
                "success": True,
                "skill": name,
                "result": result
            }
        except Exception as e:
            return {
                "success": False,
                "skill": name,
                "error": str(e),
                "error_code": "SKILL_ERROR",
            }

    # =====================================================
    # Skill实现
    # =====================================================

    def organize_downloads(self, path=None):
        """
        自动整理下载目录

        根据文件后缀分类到不同子目录：
        - 文档: .pdf, .doc, .docx, .txt
        - 图片: .jpg, .jpeg, .png
        - 压缩包: .zip, .tar, .gz

        参数：
            path: 要整理的目录路径（默认：当前系统的下载目录）
                  Windows: C:\\Users\\xxx\\Downloads
                  Linux:   /home/xxx/Downloads
        """
        if path is None:
            from core.utils import get_downloads_path
            path = get_downloads_path()
        path = Path(path).expanduser()
        if not path.exists():
            raise FileNotFoundError(path)

        categories = {
            "文档": [".pdf", ".doc", ".docx", ".txt"],
            "图片": [".jpg", ".jpeg", ".png"],
            "压缩包": [".zip", ".tar", ".gz"]
        }

        count = 0
        for file in path.iterdir():
            if not file.is_file():
                continue
            for category, extensions in categories.items():
                if file.suffix.lower() in extensions:
                    folder = path / category
                    folder.mkdir(exist_ok=True)
                    shutil.move(str(file), str(folder / file.name))
                    count += 1
                    break

        return f"整理完成，共处理 {count} 个文件"

    def system_info(self):
        """
        获取系统信息

        返回系统、版本、硬件架构、Python版本、CPU信息
        """
        return {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu": platform.processor()
        }

    def cleanup_temp(self):
        """
        清理临时目录

        Windows: 清理 %TEMP% 目录
        Linux:   清理 /tmp 目录

        注意：仅清理临时文件，跳过无法删除的项目
        """
        from core.utils import get_temp_path
        temp_path = get_temp_path()
        count = 0

        if temp_path.exists():
            for item in temp_path.iterdir():
                try:
                    if item.is_file():
                        item.unlink()
                        count += 1
                    elif item.is_dir():
                        shutil.rmtree(item)
                        count += 1
                except Exception:
                    continue

        return f"清理完成，删除 {count} 个临时项目"

    def search_file(self, directory, keyword, mode="substring"):
        """
        文件搜索技能（支持精确/子串/通配符/正则模糊匹配）

        递归搜索指定目录中文件名匹配关键字的文件

        参数：
            directory: 搜索目录
            keyword:   搜索关键字（不区分大小写）
            mode:      匹配模式，可选：
                       - "substring"（默认）: 关键字作为子串包含
                       - "exact":             文件名完全等于关键字（不区分大小写）
                       - "wildcard":          通配符模糊（支持 * ? [..]）
                       - "regex":             正则表达式匹配

        返回：
            匹配文件绝对路径列表
        """
        directory = Path(directory)
        if not directory.exists():
            raise FileNotFoundError(directory)
        if not directory.is_dir():
            raise NotADirectoryError(directory)

        result = []
        for file in directory.rglob("*"):
            if file.is_file() and self._name_matches(file.name, keyword, mode):
                result.append(str(file))

        return result

    def _name_matches(self, name, keyword, mode):
        """
        私有辅助：按 mode 判断文件名是否匹配关键字

        模式：
            substring / exact / wildcard / regex
        统一不区分大小写。
        """
        name_l = name.lower()
        kw = keyword.lower()

        if mode == "exact":
            return name_l == kw
        if mode == "wildcard":
            return fnmatch.fnmatchcase(name_l, kw) or fnmatch.fnmatch(name, keyword)
        if mode == "regex":
            return re.search(keyword, name, re.IGNORECASE) is not None
        if mode == "substring":
            return kw in name_l
        raise ValueError(f"非法匹配模式: {mode}（可选 substring / exact / wildcard / regex）")

    # =====================================================
    # 新增技能：文件分析
    # =====================================================

    def find_large_files(self, parent, top=10):
        """
        找出目录（递归）下最大的 N 个文件

        参数：
            parent: 搜索目录
            top:    返回的最大文件数，默认 10

        返回：
            [{"path": <绝对路径>, "size": <字节数>}, ...]（按大小降序）
        """
        parent = Path(parent)
        if not parent.exists():
            raise FileNotFoundError(parent)
        if not parent.is_dir():
            raise NotADirectoryError(parent)

        files = [
            {"path": str(p.absolute()), "size": p.stat().st_size}
            for p in parent.rglob("*")
            if p.is_file()
        ]
        files.sort(key=lambda x: x["size"], reverse=True)
        return files[:top]

    def find_recent_files(self, parent, days=7):
        """
        找最近修改的文件（递归）

        参数：
            parent: 搜索目录
            days:   时间窗（天内），默认 7

        返回：
            [<绝对路径>, ...]（按修改时间降序）
        """
        import time

        parent = Path(parent)
        if not parent.exists():
            raise FileNotFoundError(parent)
        if not parent.is_dir():
            raise NotADirectoryError(parent)

        cutoff = time.time() - days * 86400
        files = [
            p for p in parent.rglob("*")
            if p.is_file() and p.stat().st_mtime >= cutoff
        ]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [str(p.absolute()) for p in files]

    def summarize_files(self, parent):
        """
        目录统计（递归）：文件数、子目录数、总大小、扩展名分布

        参数：
            parent: 目标目录

        返回：
            {"file_count": int, "dir_count": int,
             "total_size": int, "ext_stats": {<ext>: count}}
        """
        parent = Path(parent)
        if not parent.exists():
            raise FileNotFoundError(parent)
        if not parent.is_dir():
            raise NotADirectoryError(parent)

        file_count = 0
        dir_count = 0
        total_size = 0
        ext_stats = defaultdict(int)

        for p in parent.rglob("*"):
            if p.is_dir():
                dir_count += 1
            else:
                file_count += 1
                total_size += p.stat().st_size
                ext_stats[p.suffix.lower() or "(无扩展名)"] += 1

        return {
            "file_count": file_count,
            "dir_count": dir_count,
            "total_size": total_size,
            "ext_stats": dict(ext_stats),
        }

    def cleanup_by_type(self, parent, extensions=(".tmp",)):
        """
        按扩展名批量清理（递归删除匹配文件，不进入子目录清理后的递归）

        参数：
            parent:      目标目录
            extensions:  要删除的扩展名集合（不区分大小写），默认 (".tmp",)

        返回：
            删除的文件绝对路径列表
        """
        parent = Path(parent)
        if not parent.exists():
            raise FileNotFoundError(parent)
        if not parent.is_dir():
            raise NotADirectoryError(parent)

        wanted = {e.lower() if e.startswith(".") else "." + e.lower()
                  for e in extensions}
        removed = []
        for p in parent.rglob("*"):
            if p.is_file() and p.suffix.lower() in wanted:
                p.unlink()
                removed.append(str(p.absolute()))
        return removed

    def batch_archive(self, items, dest_dir=None, action="compress"):
        """
        批量压缩/解压

        参数：
            items:    路径列表
                      compress：目录路径列表
                      extract ：zip 文件路径列表
            dest_dir: 目标目录（compress 时 zip 存放处 / extract 时解压处），
                      默认取各输入项所在目录
            action:   "compress"（默认）或 "extract"

        返回：
            compress -> {<源目录>: <zip绝对路径>, ...}
            extract  -> {<zip路径>: <解压目录绝对路径>, ...}

        边界：
            - 非法 action 报错
            - compress 源必须为存在的目录；extract 源必须为存在的 zip 文件
        """
        if action not in ("compress", "extract"):
            raise ValueError(f"非法 action: {action}（可选 compress / extract）")

        result = {}
        for item in items:
            p = Path(item)
            if action == "compress":
                if not p.exists():
                    raise FileNotFoundError(p)
                if not p.is_dir():
                    raise NotADirectoryError(p)
                dest_dir_obj = Path(dest_dir) if dest_dir else p.parent
                dest_dir_obj.mkdir(parents=True, exist_ok=True)
                base = str(dest_dir_obj / p.name)
                zip_path = shutil.make_archive(base, "zip", root_dir=str(p))
                result[str(p)] = str(Path(zip_path).absolute())
            else:
                if not p.exists() or not p.is_file():
                    raise FileNotFoundError(p)
                dest_dir_obj = Path(dest_dir) if dest_dir else p.parent
                dest_dir_obj.mkdir(parents=True, exist_ok=True)
                out = dest_dir_obj / (p.stem + "_extracted")
                if out.exists():
                    raise FileExistsError(out)
                with zipfile.ZipFile(p, "r") as zf:
                    zf.extractall(str(out))
                result[str(p)] = str(out.absolute())
        return result

    def duplicate_finder(self, parent):
        """
        按大小分组发现疑似重复文件

        参数：
            parent: 搜索目录（递归）

        返回：
            [[{"path", "size"}, ...], ...] 每组为大小相同的文件（组内 ≥2 才返回）
        """
        parent = Path(parent)
        if not parent.exists():
            raise FileNotFoundError(parent)
        if not parent.is_dir():
            raise NotADirectoryError(parent)

        by_size = defaultdict(list)
        for p in parent.rglob("*"):
            if p.is_file():
                by_size[p.stat().st_size].append(
                    {"path": str(p.absolute()), "size": p.stat().st_size})

        # 只保留组内 ≥2 的疑似重复组
        groups = [entries for entries in by_size.values() if len(entries) >= 2]
        return groups

    # =====================================================
    # 新增技能：索引
    # =====================================================

    def rebuild_index(self, parent, path=None):
        """
        一键重建并落盘索引

        递归收集目录内文件元信息并写入 JSON（格式与 index_files 工具一致），
        便于后续 query_index 快速查询而无需再次遍历磁盘。

        参数：
            parent: 目标目录
            path:   索引 JSON 保存路径；未指定时默认存于 parent 下的 {目录名}_index.json

        返回：
            {"index_path": <绝对路径>, "count": <条目数>}
        """
        parent = Path(parent)
        if not parent.exists():
            raise FileNotFoundError(parent)
        if not parent.is_dir():
            raise NotADirectoryError(parent)

        if path is None:
            path = parent.parent / f"{parent.name}_index.json"
        path = Path(path)

        entries = []
        for p in parent.rglob("*"):
            if p.is_dir():
                size = None
                item_type = "directory"
            else:
                size = p.stat().st_size
                item_type = "file"
            entries.append({
                "path": str(p.absolute()),
                "name": p.name,
                "type": item_type,
                "size": size,
                "mtime": p.stat().st_mtime,
            })

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)

        return {"index_path": str(path.absolute()), "count": len(entries)}

    def query_index(self, path, keyword, mode="substring"):
        """
        对已建索引做文件名匹配查询（复用索引，不遍历磁盘）

        参数：
            path:    由 rebuild_index 生成的索引 JSON 路径
            keyword: 文件名匹配关键字（不区分大小写）
            mode:    匹配模式，可选 substring（默认）/ exact / wildcard / regex

        返回：
            匹配的索引条目列表（含 path/name/type/size/mtime）
        """
        path = Path(path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(path)

        with open(path, "r", encoding="utf-8") as f:
            entries = json.load(f)

        return [e for e in entries if self._name_matches(e["name"], keyword, mode)]

    # =====================================================
    # 新增技能：回收站（防永久删除）
    # =====================================================

    @staticmethod
    def _trash_dir():
        """回收站目录（存放被删文件 + 元数据索引）"""
        td = Path.home() / ".reasonix_trash"
        td.mkdir(parents=True, exist_ok=True)
        return td

    @staticmethod
    def _trash_index_path():
        return Path.home() / ".reasonix_trash" / "index.json"

    def _load_trash_index(self):
        p = self._trash_index_path()
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_trash_index(self, index):
        with open(self._trash_index_path(), "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

    def trash_file(self, path):
        """
        将文件移入回收站（非永久删除，可恢复）

        参数：
            path: 要移入回收站的文件路径

        返回：
            {"trashed_path": <回收站内绝对路径>, "original_path": <原绝对路径>}
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(p)
        if p.is_dir():
            raise NotADirectoryError(p)

        td = self._trash_dir()
        dest = td / p.name
        # 同名冲突时加后缀
        n = 0
        base = dest
        while dest.exists():
            n += 1
            dest = td / f"{base.stem}_{n}{base.suffix}"

        shutil.move(str(p), str(dest))

        index = self._load_trash_index()
        index[str(dest.absolute())] = str(p.absolute())
        self._save_trash_index(index)

        return {
            "trashed_path": str(dest.absolute()),
            "original_path": str(p.absolute()),
        }

    def restore_file(self, path):
        """
        从回收站恢复文件到原位置

        参数：
            path: 回收站中的文件路径（或原路径）

        返回：
            恢复后的原文件绝对路径
        """
        td = self._trash_dir()
        target = Path(path)
        # 若传的是原路径，则查索引找到回收站位置的对应文件
        index = self._load_trash_index()
        if str(target.absolute()) in index.values():
            trashed = [k for k, v in index.items() if v == str(target.absolute())]
            target = Path(trashed[0])
        if not target.exists() or target.parent != td:
            raise FileNotFoundError(target)

        original = Path(index.get(str(target.absolute()), str(td / target.name)))
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(original))

        # 更新索引
        index.pop(str(target.absolute()), None)
        self._save_trash_index(index)
        return str(original.absolute())

    def empty_trash(self, confirm=False):
        """
        清空回收站（永久删除，需要 confirm=True）

        参数：
            confirm: 必须为 True 才会真正清空，否则拒绝

        返回：
            提示信息
        """
        if not confirm:
            raise PermissionError("清空回收站需要二次确认（confirm=True）")
        td = self._trash_dir()
        for item in td.iterdir():
            if item.name == "index.json":
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        # 清空索引
        self._save_trash_index({})
        return f"回收站已清空（{td}）"

    def safe_delete(self, path, confirm=False):
        """
        安全删除（带预览 + 确认，默认移入回收站而非永久删除）

        参数：
            path:    要删除的文件或目录
            confirm: 必须为 True 才执行；False 时只返回预览信息

        返回：
            预览：{requires_confirmation, operation, affected_path}
            执行：回收站 / 删除结果
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(p)
        if not confirm:
            return {
                "requires_confirmation": True,
                "operation": "delete",
                "affected_path": str(p.absolute()),
                "hint": "传入 confirm=True 才会执行",
            }
        if p.is_dir():
            td = self._trash_dir()
            dest = td / p.name
            shutil.move(str(p), str(dest))
            return {"success": True, "moved_to_trash": str(dest.absolute())}
        return self.trash_file(path)

    def deduplicate_files(self, directory, keep="newest", confirm=False):
        """
        按文件内容哈希去重（移动到回收站，而非直接删除）

        参数：
            directory: 搜索目录（递归）
            keep:      保留规则：newest（保留最新，默认）/ oldest / first
            confirm:   必须为 True 才真正移入回收站；否则只返回去重预览

        返回：
            预览或去重结果 {removed: [...], kept: [...]}
        """
        d = Path(directory)
        if not d.exists():
            raise FileNotFoundError(d)

        by_hash = defaultdict(list)
        for item in d.rglob("*"):
            if not item.is_file():
                continue
            h = hashlib.sha256()
            with open(item, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            by_hash[h.hexdigest()].append(item)

        duplicate_groups = [g for g in by_hash.values() if len(g) > 1]

        removed = []
        kept = []
        for group in duplicate_groups:
            if keep == "newest":
                group.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            elif keep == "oldest":
                group.sort(key=lambda p: p.stat().st_mtime)
            # keep="first" 保持发现顺序，保留第一个
            keeper = group[0]
            kept.append(str(keeper.absolute()))
            for dup in group[1:]:
                if confirm:
                    self.trash_file(str(dup))
                removed.append(str(dup.absolute()))

        return {"removed": removed, "kept": kept, "groups": len(duplicate_groups)}

    # =====================================================
    # 新增技能：备份 / 恢复
    # =====================================================

    def backup_file(self, path):
        """
        备份文件（默认生成同目录 {名}.bak，再加时间戳变体）

        参数：
            path: 要备份的文件

        返回：
            备份文件绝对路径
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(p)
        if p.is_dir():
            raise NotADirectoryError(p)

        backup = p.parent / (p.stem + ".bak" + p.suffix)
        n = 0
        base = backup
        while backup.exists():
            n += 1
            backup = p.parent / f"{p.stem}.bak{n}{p.suffix}"
        shutil.copy2(p, backup)
        return str(backup.absolute())

    def restore_backup(self, backup_path, output=None):
        """
        从备份恢复文件

        参数：
            backup_path: 备份文件路径
            output:      恢复目标路径；未指定则恢复为去掉 .bak 后缀的原名

        返回：
            恢复后的文件绝对路径
        """
        b = Path(backup_path)
        if not b.exists():
            raise FileNotFoundError(b)
        if output:
            out = Path(output)
        else:
            out = b.parent / (b.name.replace(".bak", ""))
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(b, out)
        return str(out.absolute())

    # =====================================================
    # 新增技能：智能整理（组合型 Agent Skill）
    # =====================================================

    def smart_organize(self, directory, by="type"):
        """
        智能整理目录（识别类型/日期 → 建子目录 → 移动 → 生成报告）

        参数：
            directory: 目标目录
            by:        整理方式：type（按扩展名分类，默认）或 date（按年月）

        返回：
            {"moved": [<源绝对路径>, ...], "report": {<目标子目录>: 数量}}
        """
        d = Path(directory)
        if not d.exists():
            raise FileNotFoundError(d)
        if not d.is_dir():
            raise NotADirectoryError(d)

        categories = {
            "文档": [".pdf", ".doc", ".docx", ".txt", ".md"],
            "图片": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"],
            "视频": [".mp4", ".avi", ".mov", ".mkv"],
            "音频": [".mp3", ".wav", ".flac", ".aac"],
            "压缩包": [".zip", ".tar", ".gz", ".rar", ".7z"],
            "程序": [".exe", ".msi", ".apk", ".py", ".js", ".sh"],
        }
        report = defaultdict(int)
        moved = []

        for item in d.iterdir():
            if not item.is_file():
                continue
            if by == "date":
                t = time.localtime(item.stat().st_mtime)
                dest_name = f"{t.tm_year:04d}-{t.tm_mon:02d}"
            else:
                low = item.suffix.lower()
                dest_name = "其他"
                for cat, exts in categories.items():
                    if low in exts:
                        dest_name = cat
                        break
            dest = d / dest_name
            dest.mkdir(exist_ok=True)
            target = dest / item.name
            shutil.move(str(item), str(target))
            moved.append(str(target.absolute()))
            report[dest_name] += 1

        return {"moved": moved, "report": dict(report)}

    # =====================================================
    # 新增技能：应用操作（组合型 Agent Skill）
    # =====================================================

    def app_open(self, app_name):
        """
        激活（或查找）指定桌面应用

        先按进程名查找，找不到再按窗口标题关键字查找；
        找到后激活到前台。

        参数：
            app_name: 应用进程名或窗口标题关键字（如 "QQ"、"chrome"、"文件资源管理器"）

        返回：
            {"window": {"hwnd", "title", "pid"}, "activated": True}
        """
        win = self.app.find_window(process=app_name)
        if win is None:
            win = self.app.find_window(title=app_name)
        if win is None:
            raise LookupError(f"未找到应用: {app_name}")
        self.app.activate_window(win["hwnd"])
        return {"window": win, "activated": True}

    def app_send_message(self, app_name, search_keyword, message, verify=None,
                         layout="qq_classic", max_result_rows=5,
                         verify_ocr=False, vision_js=None):
        """
        在 IM 应用（如 QQ）中搜索目标并发送消息（组合型 Agent Skill）

        真机验证过的编排流程（桌面版 QQ 经典布局；不再使用会被 QQ 判定为
        "查看资料卡"的 Ctrl+F + Enter 键盘路线）：

            1. find_window → activate_window（前台锁定容错）
            2. 探针定位顶部搜索框（点击 + 粘贴探针文本 + Ctrl+A/C 读回，
               只有焦点确实落进文本框时剪贴板才含探针串 → 规避假阳性）
            3. send_text(search_keyword) 输入关键词，等待下拉结果
            4. 从搜索框下方逐行向下点"候选结果行"；每点一行截一张图交给
               verify(screenshot_path) 校验（如视觉 OCR 比对群名标题），
               命中才继续；未命中继续点下一行；无 verify 时点第一行直接继续
            5. 底部探针定位聊天输入框（未找到说明没进入会话 → 报错，不发送）
            6. Ctrl+A 覆盖 → send_text(message) → Enter 发送
            7. take_screenshot 收尾取证

        参数：
            app_name:        应用进程名 / 标题关键字（如 "QQ"）
            search_keyword:  要搜索的会话 / 群名关键字（如 "一中兄弟会"）
            message:         要发送的消息内容（如 "你好"）
            verify:          可选校验回调 verify(screenshot_path) -> bool。
                             用于"发送前确认进入了正确会话"（推荐用视觉 OCR 比对
                             聊天标题与 search_keyword）；返回 False 则继续试下一行，
                             试完仍不命中则抛错不发送。verify 与 verify_ocr 都不给时
                             才点第一行直接继续（适合调用方已确认目标的场景）。
            layout:          布局模板，默认 qq_classic（按窗口比例取搜索框/结果/输入框区域）
            max_result_rows: 最多向下试几行搜索结果（默认 5）
            verify_ocr:      默认 False。True 且未显式传 verify 时，技能内部自动构造一个
                             "屏幕 OCR 门"（make_ocr_verify：逐行裁剪聊天标题横带交视觉源
                             OCR，标题必须含 search_keyword 才放行）。默认走**项目内视觉桥**
                             core.vision_bridge：它按 llm.supports_vision 自动选源——主对话
                             有视觉(true)直接用主对话模型看图；纯文本(false)则用 config.yaml
                             的 vision_bridge 段（独立第二个视觉 API）；都没配好会明确报错
                             （附指引）、绝不盲发第一行。给 Agent/大模型调用时兜底用——
                             因为 tool_call 无法携带 Python 闭包（verify），只能靠此开关。
            vision_js:       可选旧通道：外部 node 视觉桥脚本路径（显式给才走 node，否则走
                             项目内视觉桥 core.vision_bridge 的自动选源）。

        返回：
            {"window", "search_keyword", "message", "screenshot",
             "search_box", "input_box", "verify_result"}

        说明：
            - 本技能只调用 app_controller 的原子能力（find/activate/click_at/
              send_text/send_hotkey/set_clipboard/read_clipboard/get_window_rect/
              take_screenshot），本身不依赖视觉模型；识别"哪个会话是正确的"可
              由调用方注入 verify 回调，或开 verify_ocr 让技能自动构造视觉 OCR 门
              （core.vision_bridge 自动选源：llm.supports_vision=true → 主对话模型看图；
              false → config.yaml 的 vision_bridge 段配的有视觉模型。没配会明确报错
              而不是盲发。因为纯文本主对话时走的是 vision_bridge 独立视觉 API，
              所以主模型哪怕是纯文本也能 OCR 核对）。
            - 该技能风险分级为 HIGH（向外发送），OSServiceAPI / run_skill_safely
              会先走确认门。
        """
        # 1) 定位并激活目标窗口
        win = self.app.find_window(process=app_name)
        if win is None:
            win = self.app.find_window(title=app_name)
        if win is None:
            raise LookupError(f"未找到应用窗口: {app_name}")
        hwnd = int(win["hwnd"])
        self.app.activate_window(hwnd)
        time.sleep(0.8)

        # 以窗口矩形为锚计算布局比例（位置变化不失效）
        rect = self.app.get_window_rect(hwnd)
        L, T, W, H = rect["left"], rect["top"], rect["width"], rect["height"]
        profile = self._layout_profile(layout)

        # 默认 OCR 门：verify_ocr=True 且调用方没显式传 verify 闭包时，由技能内部构造
        # （make_ocr_verify 用本窗口 rect 裁聊天标题横带 → 项目内视觉桥 core.vision_bridge
        # 自动选源：主 llm 有视觉则主模型看图，否则 vision_bridge 段独立视觉 API）。
        # 没配好会在此直接抛错（附指引），宁可失败也不“盲发第一行”到错误会话。
        if verify is None and verify_ocr:
            verify = make_ocr_verify(search_keyword, rect, vision_js=vision_js)

        # 2) 探针定位搜索框（文本字段）
        search_box = self._probe_text_field(
            x=L + int(W * profile["search_x_frac"]),
            y_from=T + int(H * profile["search_y_frac"][0]),
            y_to=T + int(H * profile["search_y_frac"][1]),
            step=6,
        )
        if search_box is None:
            raise RuntimeError("未能定位搜索框（探针未命中任何文本框）")
        self.app.click_at(search_box[0], search_box[1])
        time.sleep(0.3)
        self.app.send_text(search_keyword)
        time.sleep(1.0)   # 等待下拉结果出现

        # 3) 向下逐行点搜索结果，交给 verify 校验（如有）
        x_res = L + int(W * profile["result_x_frac"])
        row_start = search_box[1] + int(H * profile["result_row_gap_frac"])
        row_step = max(8, int(H * profile["result_row_step_frac"]))
        clicked_row = None
        verify_result = None
        for k in range(max_result_rows):
            y = row_start + k * row_step
            self.app.click_at(x_res, y)
            time.sleep(0.6)
            shot = self.app.take_screenshot()
            verify_result = None
            if verify is not None:
                ok = verify(shot)
                verify_result = bool(ok)
                if not ok:
                    continue   # 不是目标行，试下一行
            clicked_row = y
            break
        if clicked_row is None:
            raise RuntimeError(f"未能在搜索结果中确认目标会话（verify 对 {max_result_rows} 行均未通过），未发送任何消息")

        # 4) 底部探针定位聊天输入框
        input_box = self._probe_text_field(
            x=L + int(W * profile["input_x_frac"]),
            y_from=rect["bottom"] - int(H * profile["input_y_from_bottom_frac"]),
            y_to=rect["bottom"] - int(H * profile["input_y_to_from_bottom_frac"]),
            step=-profile["input_probe_step"],
            skip_marker=search_keyword,
        )
        if input_box is None:
            raise RuntimeError("点选结果后未能定位到聊天输入框（可能未进入会话），未发送任何消息")
        self.app.click_at(input_box[0], input_box[1])
        time.sleep(0.4)

        # 5) 覆盖输入框旧内容 → 粘贴消息 → 发送
        self.app.send_hotkey(["ctrl", "a"])
        time.sleep(0.15)
        self.app.send_text(message)
        time.sleep(0.2)
        self.app.send_hotkey(["enter"])
        time.sleep(1.0)

        screenshot = self.app.take_screenshot()

        return {
            "window": win,
            "search_keyword": search_keyword,
            "message": message,
            "screenshot": screenshot,
            "search_box": search_box,
            "input_box": input_box,
            "verify_result": verify_result,
        }

    # ------------------------------------------------------------------
    # app_send_message 私有辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _layout_profile(layout):
        """按布局模板返回关键区域比例（相对窗口矩形，兼容不同分辨率/位置）"""
        if layout == "qq_classic":
            return {
                "search_x_frac": 0.14,        # 搜索框横向位置（会话列偏左）
                "search_y_frac": (0.06, 0.15),  # 搜索框纵向扫描带（窗口上部）
                "result_x_frac": 0.14,        # 搜索结果行同一列
                "result_row_gap_frac": 0.02,  # 首行距搜索框下缘
                "result_row_step_frac": 0.025,  # 行间距
                "input_x_frac": 0.50,         # 聊天输入框在窗口横向中间
                "input_y_from_bottom_frac": 0.03,   # 从底部向上扫的起点
                "input_y_to_from_bottom_frac": 0.22,  # 向上扫的终点
                "input_probe_step": 6,
            }
        raise ValueError(f"未知布局模板: {layout}")

    def _probe_text_field(self, x, y_from, y_to, step, marker=None, skip_marker=None):
        """
        探针扫描一条纵向坐标带，返回命中的文本框 (x, y)；未命中返回 None

        机制：
            clip清空 → click(x,y) → 粘贴 marker → Ctrl+A → 清空 clip
            → Ctrl+C → 读回。若焦点确实落在文本框，读回含 marker；
            否则剪贴板保持为空 → 规避"粘贴源本身就把 marker 放进了
            剪贴板"造成的假阳性。命中时会把刚贴进去的 marker 一并清掉
            （Ctrl+A + Delete），让返回后的文本框保持干净，不污染后续输入。
        参数：
            x:            固定横坐标
            y_from/y_to:  扫描起点 / 终点（step 为负则从下往上）
            step:         扫描步长（像素）
            marker:       探针文本，默认含随机尾巴避免与旧内容撞车
            skip_marker:  若文本框已含该串（如刚搜的关键词），视为命中可直接复用
        """
        app = self.app
        if marker is None:
            marker = f"G4probe{os.getpid()}@{int(time.time() * 1000) % 100000}"
        y = y_from
        guard = 0
        while guard < 400:
            app.set_clipboard("")
            time.sleep(0.05)
            app.click_at(x, int(y))
            time.sleep(0.25)
            app.send_text(marker)          # 粘贴源（若焦点在文本框则插入 marker）
            time.sleep(0.12)
            app.send_hotkey(["ctrl", "a"])
            time.sleep(0.08)
            app.set_clipboard("")          # 清空：只有真实 Ctrl+C 成功才会再次写入
            time.sleep(0.08)
            app.send_hotkey(["ctrl", "c"])
            time.sleep(0.12)
            text = app.read_clipboard() or ""
            if skip_marker and skip_marker in text and marker not in text:
                return (int(x), int(y))
            if marker in text:
                # 命中：清掉刚粘贴进文本框的探针串再返回。否则文本框里残留
                # "G4probe…<真实关键词>" 拼接内容，QQ 拿整串去搜索——上次真机
                # 测试之所以"搜到"，只是因为目标会话恰好已打开被排到了顶部，
                # 是碰运气而非真命中。
                # 只在确认命中时发送 Ctrl+A/Delete（此刻焦点确实在该文本框内），
                # 未命中位置不清理，避免误删列表/会话内容。
                time.sleep(0.05)
                app.send_hotkey(["ctrl", "a"])
                time.sleep(0.08)
                app.send_hotkey(["delete"])
                time.sleep(0.08)
                app.set_clipboard("")
                return (int(x), int(y))
            # 回到上一步的剪贴板污染处理
            y += step
            if (step > 0 and y > y_to) or (step < 0 and y < y_to):
                break
            guard += 1
        return None

    # =====================================================
    # 新增技能：浏览器结构化操作（组合型 Agent Skill）
    # =====================================================

    def browser_search(self, query, engine="bing"):
        """
        结构化网络搜索：打开搜索引擎 → 快照定位输入框 → 输入 → 回车 → 读取结果

        参数：
            query:  搜索关键词
            engine: 搜索引擎，可选 bing / baidu，默认 bing

        返回：
            {"query", "engine", "url", "text_snippet"}

        说明：
            输入框定位不依赖硬编码选择器，而是 browser_snapshot 返回的元素索引
            （按 tag/role 找一个文本框），规避选择器易碎、引擎改版问题。
        """
        urls = {"bing": "https://www.bing.com/", "baidu": "https://www.baidu.com/"}
        if engine not in urls:
            raise ValueError(f"暂不支持搜索引擎: {engine}（可选 {list(urls)}）")

        b = self.browser
        if not b.connected:
            b.launch()

        b.navigate(urls[engine])
        b.wait_for(selector="input, textarea", timeout=10)
        items = b.snapshot()
        input_index = self._first_input_index(items)
        if input_index is None:
            raise LookupError("未在页面中找到搜索输入框")

        b.type_text(query, index=input_index)
        b.press_enter()
        b.wait_for(text=query, timeout=10)
        text = b.read_text()

        return {
            "query": query,
            "engine": engine,
            "url": urls[engine],
            "text_snippet": text[:500],
        }

    @staticmethod
    def _first_input_index(items):
        """从快照清单里挑第一个可输入的文本框索引"""
        for it in items:
            itype = (it.get("type") or "").lower()
            if it.get("tag") in ("input", "textarea") and \
                    itype not in ("hidden", "submit", "button", "checkbox", "radio"):
                return it["index"]
        return None

    def browser_extract(self, url, selector=None):
        """
        打开网页并提取内容（结构化读取）

        参数：
            url:      目标网页
            selector: 可选 CSS 选择器；未指定则提取整个页面正文

        返回：
            {"url", "selector", "text"}
        """
        b = self.browser
        if not b.connected:
            b.launch()

        b.navigate(url)
        b.wait_for(selector=selector or "body", timeout=10)
        text = b.read_text(selector=selector)

        return {"url": url, "selector": selector, "text": text[:2000]}

    # =====================================================
    # 邮件：发送 + 自动回读核验（组合型 Agent Skill）
    # =====================================================

    def send_email(self, to, subject, body, cc=None, verify=True):
        """
        发送一封邮件并自动回读核验（SMTP 发信 + IMAP 回读，组合型 Agent Skill）

        编排流程：
            1. 从 config.yaml 的 email 段读发件账号/授权码（没配好 → 报中文指引，不发）；
            2. SMTP 发送（自动生成唯一 Message-ID 头）；
            3. verify=True（默认）时用同一账号的 IMAP 回读发件箱『已发送』，确认该信确实
               落库；若 email 段还配了 verify_inbox（收件侧邮箱的 IMAP），再轮询收件人
               收件箱，核验『确实到达』（双端闭环）。回读未命中如实返回 found=False，
               不把没验到当成功。

        参数：
            to:      收件人邮箱（可多个，逗号分隔）
            subject: 邮件主题
            body:    正文（纯文本）
            cc:      可选抄送（逗号分隔）
            verify:  是否发送后自动 IMAP 回读核验（默认 True；tool_call 无法携带闭包，
                     技能内部自做核验，因此没有理由时建议保持开启）

        返回：
            {"to", "subject", "message_id", "smtp_accepted", "sent_verified",
             "sent_verified_folder", "delivery_verified", ...}

        说明：
            - 发信账号与授权码从 config.yaml 的 email 段读取，绝不经 tool_call 参数暴露；
            - 本技能风险分级 HIGH（对外发送、不可撤回），OSServiceAPI / run_skill_safely
              会先走确认门；
            - 依赖 core.email_client（纯 stdlib）：协议层任何失败抛 MailError 并带中文指引，
              配置没配好会在"发之前"就报错，不会假装已发送。
        """
        from core import email_client
        sec = email_client.email_section()

        # 发信（内部 fail-closed：email 段没配好会在发之前就抛 MailError 指引）
        sent = email_client.smtp_send(to=to, subject=subject, body=body, cc=cc, email_cfg=sec)

        result = {
            "to": sent["to"],
            "cc": sent.get("cc", ""),
            "subject": sent["subject"],
            "message_id": sent["message_id"],
            "smtp_accepted": True,
        }

        # 回读核验（只在发送成功后做；回读本身的失败如实记录，不把没验到当成功）
        sent_verified = None
        sent_folder = None
        delivery_verified = None
        if verify:
            try:
                sv = email_client.imap_verify_sent(
                    msg_id=sent["message_id"], subject=sent["subject"], email_cfg=sec)
                sent_verified = bool(sv.get("found"))
                sent_folder = sv.get("folder")
            except email_client.MailError as e:
                sent_verified = False
                result["verify_error"] = f"回读『已发送』失败：{e}"
            # 双端闭环（可选）：email.verify_inbox 配了收件侧账号才核验"确实到达"
            if ((sec.get("verify_inbox") or {}).get("username") or "").strip():
                try:
                    av = email_client.imap_verify_arrival(
                        msg_id=sent["message_id"], subject=sent["subject"])
                    delivery_verified = bool(av.get("found"))
                except email_client.MailError as e:
                    delivery_verified = False
                    result["delivery_verify_error"] = f"回读收件人收件箱失败：{e}"

        result.update({
            "sent_verified": sent_verified,
            "sent_verified_folder": sent_folder,
            "delivery_verified": delivery_verified,
        })
        return result
