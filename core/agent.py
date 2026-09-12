"""
agent.py
第4组：Agent 编排层（ReAct 循环）

核心职责：
  1. 接收用户自然语言指令
  2. 通过 LLM 决定调用哪些工具
  3. 执行工具并返回结果给 LLM
  4. 循环直到 LLM 给出最终回复

设计参考：
  - ReAct（Reasoning + Acting）模式
  - OpenAI Function Calling / 工具调用格式
  - Microsoft UFO² 的 HostAgent 编排思路
  - ChatGPT 多会话管理 + 流式输出
"""

import io
import json
import os
import re
import base64
import shutil
import tempfile
import time
from typing import Callable, Optional, Generator

from core.agent_config import load_config
from core.conversation import Conversation, SessionManager
from core.llm_client import LLMClient, DeterministicBrain, OpenAICompatibleClient, FailoverClient, create_llm_client, ChatResponse
from core.os_service_api import OSServiceAPI
from core.safety import (
    get_meta, preview, ConfirmationDenied,
    should_confirm, resolve_authz_level,
)


def _now_ms() -> str:
    """带毫秒的人类可读时间戳（run_log 的时间标记）"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()) + \
        f".{int(time.time() * 1000) % 1000:03d}"


_SENSITIVE_KEYS = ("password", "passwd", "token", "secret", "api_key",
                   "authorization", "cookie")


def _sanitize_args(args) -> str:
    """把工具参数压成可记录字符串：截断长文本、脱敏密钥类字段。"""
    if not isinstance(args, dict):
        return re.sub(r"\s+", " ", str(args))[:200]
    out = {}
    for k, v in args.items():
        if any(s in str(k).lower() for s in _SENSITIVE_KEYS):
            out[k] = "***"
        elif isinstance(v, str):
            out[k] = v[:80]
        else:
            out[k] = v
    try:
        return json.dumps(out, ensure_ascii=False)[:300]
    except Exception:
        return str(args)[:300]


# GUI 类工具列表（执行后建议截图回传 LLM）
_GUI_TOOLS = {
    "click_at", "send_hotkey", "send_text",
    "browser_launch", "browser_close", "browser_navigate",
    "browser_click", "browser_type", "browser_inspect", "browser_find",
    "activate_window",
}

# Observation 生命周期：发送给 LLM 的历史里最多保留最近 N 张观测截图，
# 更旧的图片裁剪掉（发现 Observation 生命周期是 token 膨胀主因后加入）。
# 裁剪只作用于当次请求载荷，对话持久记录与产物目录中的原图完整保留。
_MAX_HISTORY_IMAGES = 2

# 纯对话模式 system prompt（本地小模型，tool_free）：
# 不传工具也不带工具守则（实测 63 个工具 schema ≈ 7.7K token，CPU 模型光 prompt eval
# 就要 33~150s+）；如实说明能力边界，引导用户切在线模型做实际操作
_CHAT_SYSTEM_PROMPT = (
    "你是 miniyu，一个运行在用户本机的桌面 AI 助手。当前使用本地模型，处于纯对话模式，"
    "没有接入任何工具，请直接用中文回答问题、聊天、写作和解释概念。\n"
    "注意：\n"
    "1. 你无法执行文件操作、系统控制等实际任务，也无法联网获取实时信息（天气/新闻/股价等）；"
    "涉及这些时如实说明，并建议用户切换到在线模型（如阿里云百炼）后再操作；\n"
    "2. 不要声称自己调用了工具或已联网，不要编造实时数据；\n"
    "3. 长文创作时直接开始写，不要反复确认。"
)

# 注入给真实 LLM 的 system prompt（仅 openai_compatible 模式生效，离线脑不注入以免干扰关键词匹配）
SYSTEM_PROMPT = (
    "你是 miniyu，一个运行在用户本机的桌面 AI 助手。用户会用自然语言请你完成文件管理、"
    "系统操作、应用控制、信息获取等任务。\n"
    "工具使用守则：\n"
    "1. 只在需要实际操作时调用已提供的工具，工具名和参数必须来自给定列表，不得编造工具名、路径或参数；\n"
    "2. 参数里的路径尽量使用用户给出的绝对路径，不明确时先向用户确认；\n"
    "3. 删除文件、向外发送消息等破坏性或外发操作，如非用户明确要求，先说明风险再执行；\n"
    "4. 每次工具调用后都会收到真实结果，请据此决定下一步；\n"
    "5. 任务完成时，用中文自然语言把关键结果总结给用户，不要只罗列工具调用；\n"
    "6. 往 QQ 等即时通讯软件里『找某个会话/群』时，先调用 read_qq_chat 读取该会话最近可见的聊天记录"
    "（它会自己搜索、用屏幕 OCR 核对会话标题、把最近几条消息转写回来；只读、不外发）。"
    "需要回复时，再基于读到的内容组织一句话，用 app_send_message 发出去"
    "（它同样先 OCR 核对标题再发送）。两者都是整体组合技能，"
    "绝不拆成『激活窗口/点击/输入文字/回车』等零散步骤——那样可能发到错误的会话。"
    "若用户只要求发一句已给定的内容、无需先看历史，可直接用 app_send_message。\n"
    "7. 当你对当前执行状态不确定（工具结果含糊/要看界面再决定下一步/上一步可能出错想看清原因，"
    "或需要读屏幕上某段文字/聊天内容）时，先调用 screen_inspect 截一张当前屏幕来看，"
    "再决定下一步；不要因为界面情况不明就回复『做不到/太复杂/无法继续』。\n"
    "8. 发邮件必须调用 send_email 这一个组合技能（它会 SMTP 发信并自动 IMAP 回读核验已发送/到达），"
    "收件人与主题要跟用户说的完全一致，正文按用户要求写；发信账号与授权码在 config.yaml 的 email 段配好。\n"
    "9. 需要『联网查资料 / 最新信息 / 外部事实 / 你不确定』时，先用 browser_search 搜索并读返回的"
    "结果摘要；想细读某一条结果，再把对应网址交给 browser_extract 打开取正文。联网结果来自互联网，"
    "注意时效与来源可信度，引用时应说明出处，不要编造没搜到的内容；一次搜不到就换关键词再搜。\n"
    "10. 用户提到『本地图片 / 图片路径 / 图片里的问题 / 识别图片内容』时，直接用 read_image 读取该图片"
    "（参数 path 传用户给的路径或桌面等常见位置的文件名）；你有视觉时会直接看到原图，没有视觉时系统会"
    "用视觉桥转成文字描述。**不要**为了让模型看图而先打开图片再 screen_inspect 截图——那既多此一举"
    "又可能被其他窗口遮挡。只有需要看『当前屏幕/当前界面』时才用 screen_inspect。\n"
    "11. 处理编程/项目任务（改代码、查 bug、写脚本、理解项目结构）时，遵循『先侦察、后动手』：先用 "
    "list_directory、search_files（按文件名找）、search_in_files（按内容搜关键词/报错信息）摸清项目结构"
    "和目标代码位置，再用 read_text_file 读相关文件（大文件用 max_bytes 限长分段读），理解清楚后再修改；"
    "小改动用 edit_file 精确替换（old_text 必须与文件内容逐字一致，含缩进空格），新建文件或大段重写才用 "
    "write_text_file；需要运行/测试代码时用 run_command（如 python xxx.py、pytest、git status），"
    "运行报错就把报错信息作为关键词喂回 search_in_files / read_text_file 定位根因。改完尽量实际运行验证，"
    "最后用中文总结改了哪些文件、为什么改。不要凭猜测整文件重写。"
    "12. 操作网页元素时遵循『结构化优先、视觉辅助、坐标兜底』，先 Ref、再 Find、再 Snapshot、"
    "视觉需要才 SoM、最后才坐标。默认先 browser_snapshot 拿到带 ref 的元素清单，能确定就用 "
    "browser_click(target='e...'，或页面/浏览器返回的 ref 字符串) / browser_type(target='e...', text=...)"
    "操作，不要一上来就截图。若只需在页面里找某个按钮/链接/输入框，优先用 browser_find（按目标文字/"
    "名称/角色局部搜索，返回可点击的 ref），大页面别请求整页结构。只有空间布局/图标/Canvas 等无法仅靠"
    "结构化信息确定时，才调用 browser_inspect 截一张带编号覆盖层的图，对着图选编号后用"
    "browser_click(target='som:N') 或 browser_type(target='som:N', text=...) 操作。编号只是截图上的"
    "视觉标签、不是元素真实身份；页面导航或明显变化后旧编号会失效——若点击/输入返回『SoM 会话失效』"
    "的报错，就重新 browser_inspect 再选编号，不要凭老编号硬点（注意：这仅适用于 som:N 视觉编号失效；"
    "对『元素/输入目标找不到』的结构失效，见规则 15，应先 browser_find 而不是 inspect）。不要凭截图瞎猜 x/y 坐标，只有 DOM 和 "
    "SoM 都定位不了的特殊页面（Canvas/地图/白板等）才允许用坐标点击。browser_click/browser_type 返回"
    "里的 url_changed / page_changed 是客观校验，请据此确认动作是否真的生效，再决定下一步。\n"
    "13. 一旦进入浏览器任务（调用过 browser_launch / browser_navigate），本会话就应一直留在浏览器上下文，"
    "只用 browser_* 系列工具完成网页操作，**禁止**中途改用桌面全局工具 click_at / send_text / send_hotkey / "
    "activate_window（系统在浏览器会话期间会自动拦截这些桌面工具并提示你切回 browser_*）。你不必也不该手动"
    "调用 activate_window 去激活窗口——AI 操作的浏览器会在导航后自动调到前台，用户自己就能看到执行过程；若希望"
    "再次把它唤回前台，直接用 browser_bring_to_front。页面空白或元素未加载时，按顺序 browser_wait → "
    "browser_refresh → browser_snapshot 逐步恢复，**绝不要**因此切换到桌面工具打开桌面版应用。对同一个交互目标"
    "最多重试 2 次，仍失败就先重新 browser_find / browser_snapshot 刷新页面状态再继续，不要无脑重试浪费 token。\n"
    "14. 读取网页文字/状态一律走结构化：browser_read_text / browser_snapshot / browser_find / 页面 title/URL，"
    "**不要**靠截图视觉去读 DOM 里已有的文字——例如「告诉我的回复/数字/提交是否成功」直接用 read_text/snapshot 取即可，"
    "无需截图。执行动作前想清楚再落一步，避免在同一目标上反复「先试 A 不行再试 B」式的长篇自我复述，那会白白烧 token；"
    "决定要试就一次做对，或直接切 browser_find / browser_inspect 重新获取状态再继续。\n"
    "15. 【定位失败恢复树】browser_type/browser_click 的目标找不到（stale）、不可输入（not_editable / disabled / "
    "readonly）时，**绝不要**直接升级到 browser_inspect / SoM。应先 browser_find（按 role=textbox / tag / 占位符）"
    "或 browser_snapshot 重新拿到结构化 ref，再按新 ref 操作。只有结构化手段无法可靠区分目标（纯图标、Canvas、"
    "空间重叠、无 DOM 语义）时才允许 browser_inspect。SoM 编号只用于『som:N 编号失效』，结构 ref 用于『查找/输入"
    "目标』，两者不得混用——避免出现 stale ref → inspect → 误点 → 弹窗 → 再 inspect → 再 find 的死循环式长恢复链"
    "浪费大量 token。目标不可编辑时放弃输入，不要反复尝试同一个不可编辑目标。\n"
    "16. 【Target Handle：用短句柄，别复制长 ref】browser_snapshot / browser_find / browser_inspect 返回的每条"
    "元素都带一个短 handle（形如 e3），它是当前页面状态下的**短生命周期引用**。点击/输入一律优先用这个 handle："
    "browser_click(target='e3') / browser_type(target='e3', text=...)。只能使用最近一次工具返回的 handle，**不得"
    "猜测、修改、拼凑或自行构造 handle，也不得把内部长 ref（如 ebody:0_div:0_main:... 这类结构路径型）直接复制成"
    "target**——长 ref 是给程序内部定位用的，模型复制极易丢字符导致点错目标；若误传长路径型 ref，工具会拒绝并提示。"
    "页面导航 / 刷新 / DOM 明显变化后，旧 handle 会失效（工具报 stale / 目标不存在），此时重新 browser_find 或 "
    "browser_snapshot 拿最新的 handle，不要复用旧 handle 或硬猜。\n"
)

# 服务端联网搜索（百炼 enable_search）生效时追加到 system prompt，显式覆盖守则第 9 条：
# 此刻本地 browser_search 已对模型隐藏，若仍按第 9 条指引会去调用不存在的工具；
# browser_extract 保留可用（服务端搜索只给摘要，深读网页正文还靠它）。
# 实测（examples/probe_bailian_search.py 场景 B/C）模型能直接用注入的搜索资料作答，
# 但措辞易写成『您提供的资料/知识库』，显式告知可让回复更自然。
_SERVER_SEARCH_HINT = (
    "\n补充（联网能力已升级，本条覆盖守则第 9 条）：你已内置服务端联网搜索，系统会在需要时"
    "自动把最新网络资料注入对话，无需自己搜索。涉及天气、新闻、价格、最新事件等实时信息时，"
    "直接依据已注入的搜索资料回答，自然注明信息来源与日期；不要调用 browser_search（当前不可用），"
    "也不要声称自己无法联网。若需要读取某网页的完整正文，仍可使用 browser_extract。"
)

# 联网搜索总开关（config.yaml 顶层 web_search.enabled）关闭时追加到 system prompt：
# 本地 browser_search / browser_extract 均已对模型隐藏，显式覆盖守则第 9 条，
# 引导模型基于自身知识作答并如实说明离线限制，而不是编造或调用不存在的工具。
_OFFLINE_HINT = (
    "\n补充（当前处于离线模式，本条覆盖守则第 9 条）：联网搜索功能已被用户关闭，"
    "browser_search / browser_extract 工具当前不可用。请基于自身知识回答问题；"
    "遇到天气、新闻、价格等实时信息时，如实告知用户自己当前无法联网获取，"
    "建议开启联网开关后再问，不要编造实时数据。"
)

# 百炼服务端代码解释器生效时追加到 system prompt：
# 云端沙箱会透明执行 Python（数学计算/数据分析）。注意百炼约束（官方文档 + 实测
# probe_bailian_code_interpreter.py）：解释器仅支持流式、且不能与本地函数工具同请求
# （"Agent mode does not support tools"）——因此只在"纯计算/纯对话"回合以 tools=None
# 走解释器，涉及本地资源的提问仍用本地工具，解释器自动让位。
_CODE_INTERPRETER_HINT = (
    "\n补充（计算能力已升级）：你已内置服务端代码解释器，当用户只要求纯计算/数据分析"
    "（数学题、大数运算、统计、公式推导验证，不涉及本地文件/系统操作）时，系统会以纯计算"
    "模式调用，在云端沙箱运行 Python 并返回精确结果，你直接给出结论即可，不必用 run_command"
    "调本地 Python。注意：需要本地文件、目录、系统操作时照常调用本地工具，不要因为解释器"
    "而跳过。"
)

# 纯计算/数据分析回合判定（配合百炼 code_interpreter 使用）：
# 命中计算关键词、且不涉及本地资源/系统操作时，该回合以 tools=None 走服务端解释器；
# 关键词命中保守（宁缺毋滥），避免把需要本地工具的任务误判成纯计算。
_PURE_CALC_RE = re.compile(
    r"计算|算一?下|算算|多少|次方|等于|数学|统计|求和|求值|平均值|中位数|众数|"
    r"概率|换算|平方根|开方|质数|因数|阶乘|等差数列|等比数列|圆周率|π|方程式?|"
    r"解方程|列竖式|笔算",
    re.IGNORECASE,
)
_LOCAL_REF_RE = re.compile(
    r"目录|文件夹|文件|桌面|截图|屏幕|打开|启动|运行|安装|发送|邮件|qq|微信|"
    r"浏览器|删除|复制|移动|重命名|进程|窗口|关闭|停止|结束|天气|新闻|股票|"
    r"网页|网址|链接|照片|图片|视频|音频|下载|上传|打印",
    re.IGNORECASE,
)


# Agent 视觉能力函数（"截图理解"）：让模型在不确定 / 复杂 / 出错时主动看一眼屏幕。
# 它不属于 SkillLibrary（不改变 57 工具 / 25 技能计数），而是 Agent 自带的可调函数：
#   视觉模型 → 截图以原图内联给模型看；无视觉模型 → 调项目内视觉桥（config.yaml 的
#   vision_bridge 段，独立视觉 API）把图转成文字描述。
_AGENT_VISION_TOOL = {
    "type": "function",
    "function": {
        "name": "screen_inspect",
        "description": (
            "截取当前屏幕并理解界面状态（只读）。当你对上一步操作结果拿不准、需要确认当前界面长什么样、"
            "进入更深的界面/选项前要确认位置、或上一步执行报错需要看清原因时调用它。"
            "如果你是视觉模型会直接看到截图；否则系统会用 OCR/视觉桥把屏幕关键文字转成描述返回给你。"
            "截图会存为过程产物（可随时让用户说『清理截图』删除），不影响用户文件。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "你想从屏幕确认的问题，例如：现在界面上有哪些选项？聊天标题是什么？"
                        "上一步为什么可能失败？留空则描述整体界面。"
                    ),
                },
            },
            "required": [],
        },
    },
}

# Agent 视觉能力函数（"读取本地图片"）：用户给出本地图片路径/想让模型看某张图时直接读原图。
# 与 screen_inspect（截当前屏幕）互补：read_image 读的是**文件**，不依赖屏幕上有不有打开。
#   视觉模型 → 原图以 base64 观测消息内联给模型看；无视觉模型 → 调项目内视觉桥转文字描述。
_AGENT_IMAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_image",
        "description": (
            "读取一张本地图片文件并理解其内容（只读，不修改文件）。"
            "用户提到『图片 / 图片路径 / 图片里的问题 / 识别图片文字或题目』时调用它，"
            "参数 path 传用户给出的路径（未给完整路径时先确认或在桌面等常见位置查找）。"
            "如果你是视觉模型会直接看到原图；否则系统会用 OCR/视觉桥把图转成文字描述返回。"
            "注意：读的是图片文件，不是当前屏幕——看当前屏幕请用 screen_inspect。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "本地图片文件的绝对路径（如 D:\\Users\\34808\\Desktop\\1.3.png）。",
                },
                "question": {
                    "type": "string",
                    "description": (
                        "想从图片确认的问题，例如：图片里是什么题目？哪里有错误？图中文字是什么？"
                        "留空则描述图片内容。"
                    ),
                },
            },
            "required": ["path"],
        },
    },
}

# Agent 工具："token 用量查询"。用户询问『用了多少 token / token 统计 / budget』时调用。
_AGENT_TOKEN_TOOL = {
    "type": "function",
    "function": {
        "name": "token_usage",
        "description": (
            "查询当前对话的 token 用量统计（只读）：含本轮合计、本对话累计、预算上限、"
            "执行步数以及每一步的 prompt/completion/total、携带图片数。"
            "用户在疑问'花了多少 token / 还剩多少 / 是不是消耗太大 / budget'时，直接调用并把结果告诉用户。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

# 用户触发"清理过程产物/截图"的自然语言关键词（命中即清空当前会话产物目录）
_ARTIFACT_CLEAN_PHRASES = (
    "清理截图", "清除截图", "清空截图",
    "清理产物", "清除产物", "清空产物", "清理产出",
    "删除过程产物", "清理过程产物",
)


class Agent:
    """
    miniyu — Agent 编排层

    miniyu 是你的桌面 AI 助手，通过自然语言理解你的意图，
    调用系统工具帮你完成文件管理、系统操作、应用控制等任务。

    支持多会话管理（对标 ChatGPT 的会话切换）和流式输出。

    用法：
        agent = Agent()
        result = agent.run("帮我整理桌面")
        print(result)

        # 多会话管理
        agent.create_session("工作")
        agent.run("帮我整理桌面")

        # 流式输出
        for chunk in agent.run_stream("磁盘空间"):
            print(chunk.text, end="")
    """

    name = "miniyu"

    def __init__(
        self,
        config: dict = None,
        api: OSServiceAPI = None,
        llm_client: LLMClient = None,
        confirm_handler: Callable = None,
        system_prompt: str = None,
    ):
        """
        初始化 miniyu。

        参数：
            config:          配置字典（默认从 load_config 读取）
            api:             OSServiceAPI 实例（默认新建）
            llm_client:      LLM 客户端（默认根据 config 创建）
            confirm_handler: 高危操作确认回调（接收 preview dict，返回 True=放行 / False=拒绝）
                             None 表示自动放行
            system_prompt:   注入真实 LLM 的 system prompt（默认内置 SYSTEM_PROMPT；
                             仅 openai_compatible 模式生效）
        """
        self.config = config or load_config()
        self.api = api or OSServiceAPI()
        self.llm = llm_client or create_llm_client(self.config)
        self.confirm_handler = confirm_handler
        self.system_prompt = system_prompt or SYSTEM_PROMPT

        # 联网搜索总开关（config.yaml 顶层 web_search.enabled，默认开）：
        # 管 百炼服务端搜索 + 本地 browser_search/browser_extract 三类联网能力。
        # 总开关是唯一源头：构造时同步进 LLM 客户端的 server_tools，
        # 避免外部传入的 client 与 config 开关状态不一致
        self.web_search_enabled = bool(
            self.config.get("web_search", {}).get("enabled", True))
        _ws_client = getattr(self.llm, "primary", self.llm)
        if hasattr(_ws_client, "server_tools"):
            _ws_client.server_tools["web_search"] = self.web_search_enabled

        agent_cfg = self.config.get("agent", {})
        memory_cfg = self.config.get("memory", {})

        # 多会话管理器
        storage_dir = memory_cfg.get("storage_dir", "conversations")
        self.sessions = SessionManager(storage_dir=storage_dir)
        # 历史全部载入内存（供 Web 侧栏浏览/切换/删除），然后新开一个空对话作为当前会话
        self.sessions.load_all()
        self.sessions.create()

        # 过程产物根目录：截图等 Agent/技能产生的工件独立存放（不混进对话记录与项目代码），
        # 便于同一任务内被下一个技能/工具复用，也便于按会话一句话清理。
        # 顺序：环境变量 AGENT_ARTIFACTS_DIR > config.memory.artifacts_dir > 系统临时目录。
        env_art = os.environ.get("AGENT_ARTIFACTS_DIR")
        if env_art:
            self.artifacts_root = env_art
        else:
            self.artifacts_root = memory_cfg.get(
                "artifacts_dir",
                os.path.join(tempfile.gettempdir(), "miniyu_artifacts"),
            )
        # 本轮（本次 run / run_stream）产生的产物清单 → 结束时提醒用户可清理
        self._turn_artifacts = []

        self.max_steps = agent_cfg.get("max_steps", 15)
        self.confirm_high_risk = agent_cfg.get("confirm_high_risk", True)
        # 授权档位（base/advanced/full）：决定哪些高危操作要弹确认。优先 agent.authorization；
        # confirm_high_risk 保留为旧字段兼容别名（其值并入 resolve 判定）。
        self.authz_level = resolve_authz_level(agent_cfg)
        self.window_size = agent_cfg.get("history_window", 20)
        # 工具结果回传 LLM 的最大字符数（token 优化：run_command 输出/文件内容可能巨大，
        # 全量回传既费 token 又拖慢请求；超出截断并附说明，模型可再针对性读取）
        self.tool_result_max_chars = int(agent_cfg.get("tool_result_max_chars", 4000))

        # 记忆压缩（对标 AutoGPT 的上下文窗口管理）
        self.auto_summarize = memory_cfg.get("auto_summarize", False)
        self.summarize_threshold = memory_cfg.get("summarize_threshold", 0.8)

        # 规划层（对标 AutoGPT 的任务分解）
        self.enable_planning = agent_cfg.get("enable_planning", False)

        # 人类中断回调（对标 LangGraph 的 human-in-the-loop）
        self.interrupt_handler = None  # set_interrupt_handler(handler)

        # 第5组系统协调层（审计 + RAG 执行轨迹）：
        #   enable_coordinator=False 时保持旧行为（无审计/RAG），默认开启。
        #   每次 run/run_stream 结束时记一条审计，并把"用户输入→动作→结果"存入
        #   RAG 知识库（轻量向量检索，供 Agent 参考历史做法，对标第5组职责）。
        coord_cfg = agent_cfg.get("coordinator", {})
        if coord_cfg.get("enabled", True):
            from core.coordinator import SystemCoordinator, AuditLog, RAGKnowledgeBase
            self.coordinator = SystemCoordinator(
                audit=AuditLog(path=coord_cfg.get("audit_path")),
                rag=RAGKnowledgeBase(max_docs=int(coord_cfg.get("rag_max_docs", 500))),
            )
        else:
            self.coordinator = None

    # 供 run/run_stream 结束时统一记录审计 + RAG 轨迹
    def _record_coordinator_trace(self, user_input: str, actions: list, result: str,
                                  ok: bool = True) -> None:
        if self.coordinator is None:
            return
        self.coordinator.audit.log(
            "agent_turn",
            {"input": self.coordinator.sandbox.privacy_protect(user_input),
             "ok": ok, "result": str(result)[:200]},
        )
        self.coordinator.rag.add_trace(
            input_text=user_input,
            actions=actions or [],
            result=str(result)[:500],
        )

    # ============================================================
    # 兼容属性：self.conversation → 当前会话
    # ============================================================

    @property
    def conversation(self) -> Conversation:
        """当前会话（兼容旧代码）"""
        return self.sessions.ensure_current()

    # ============================================================
    # 会话管理（委托 SessionManager）
    # ============================================================

    def create_session(self, title: str = "") -> str:
        """创建新会话，对标 ChatGPT 的 New Chat"""
        return self.sessions.create(title=title)

    def switch_session(self, session_id: str) -> bool:
        """切换到指定会话"""
        return self.sessions.switch(session_id)

    def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        return self.sessions.delete(session_id)

    def rename_session(self, session_id: str, title: str) -> bool:
        """重命名会话"""
        return self.sessions.rename(session_id, title)

    def list_sessions(self) -> list[dict]:
        """列出所有会话摘要"""
        return self.sessions.list()

    def switch_fallback_model(self, model_name: str) -> bool:
        """切换备用 LLM 的模型（运行时热切换，不重建 Agent）"""
        if isinstance(self.llm, FailoverClient):
            return self.llm.switch_fallback_model(model_name)
        return False

    @property
    def current_session_id(self) -> Optional[str]:
        return self.sessions.current_id

    # ============================================================
    # 人类中断（对标 LangGraph 的 human-in-the-loop）
    # ============================================================

    def set_interrupt_handler(self, handler):
        """
        设置人类中断回调。

        当 Agent 需要用户确认或输入时调用此回调。
        对标 LangGraph 的 human-in-the-loop 模式。

        handler 签名：handler(interrupt_type: str, context: dict) -> dict
        返回 dict 包含用户的决策。
        """
        self.interrupt_handler = handler

    def _interrupt(self, interrupt_type: str, context: dict) -> dict:
        """触发中断，回调用户"""
        if self.interrupt_handler:
            return self.interrupt_handler(interrupt_type, context)
        return {"confirmed": True, "input": ""}

    # ============================================================
    # 规划层（对标 AutoGPT 的任务分解）
    # ============================================================

    def _plan(self, user_input: str) -> list[dict]:
        """
        将复杂任务分解为子任务列表。

        仅当 enable_planning=True 且为非离线模式时使用。
        对标 AutoGPT 的 planning + execution 分离模式。

        返回子任务列表：[{"step": "1", "action": "整理文件", "tool": "sort_directory", ...}]
        """
        if not self.enable_planning or isinstance(self.llm, DeterministicBrain):
            return [{"step": "1", "action": user_input}]

        messages = [
            {"role": "system", "content": "你是一个任务规划助手。将用户的复杂指令分解为1-5个具体步骤，"
                                          "返回 JSON 数组，每项包含 step/action/tool 字段。"},
            {"role": "user", "content": f"分解任务：{user_input}"},
        ]
        try:
            resp = self.llm.chat(messages)
            if resp.text:
                import json
                text = resp.text.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("\n", 1)[0]
                if text.startswith("["):
                    steps = json.loads(text)
                    if isinstance(steps, list):
                        return steps
        except Exception:
            pass
        return [{"step": "1", "action": user_input}]

    def _compress_memory(self):
        """
        压缩对话记忆（当上下文接近上限时）。

        对标 AutoGPT 的内存压缩策略：
        当消息数超过窗口阈值时，用 LLM 总结早期对话。
        """
        if not self.auto_summarize or isinstance(self.llm, DeterministicBrain):
            return

        conv = self.conversation
        if len(conv.messages) < self.window_size * 2:
            return

        def summary_fn(old_messages):
            resp = self.llm.chat([
                {"role": "system", "content": "请用2-3句话总结以下对话的核心内容。"},
            ] + old_messages)
            return resp.text if resp.text else conv._simple_summary(old_messages)

        # 调用 get_summarized_window 触发压缩，同时设置 summary 字段
        conv.summary = summary_fn(conv.messages[:-self.window_size])

    # ============================================================
    # 主入口：非流式
    # ============================================================

    def run(self, user_input: str) -> str:
        """
        主入口：接收用户输入 → 执行 ReAct 循环 → 返回最终回复

        返回自然语言格式的回复。
        """
        if not user_input or not user_input.strip():
            return "请输入指令。"

        self._turn_artifacts = []
        self._turn_tool_calls: list[dict] = []
        self._turn_usage_trace: list = []
        self._run_log = None
        self.conversation.add_user(user_input)

        # 用户说"清理截图/清理产物"等 → 直接清当前会话过程产物，不经过 LLM
        clean_msg = self._try_cleanup_command(user_input)
        if clean_msg is not None:
            self.sessions.save()
            self._record_coordinator_trace(user_input, self._turn_tool_calls, clean_msg)
            return clean_msg

        # 规划层：分解复杂任务为子步骤（对标 AutoGPT）；
        # 纯对话模式（本地小模型）跳过——任务分解只有配合工具执行才有意义
        chat_mode = self._chat_mode()
        if chat_mode:
            self.current_plan = []
            self.current_plan_index = 0
            plan_context = ""
        else:
            self.current_plan = self._plan(user_input)
            self.current_plan_index = 0
            plan_context = ""
            if len(self.current_plan) > 1:
                step_summary = " → ".join(s.get("action", s.get("step", "")) for s in self.current_plan[:3])
                plan_context = f"[规划: {step_summary}]"
                self.conversation.add_assistant(content=plan_context)

        # 获取工具描述（MCP 格式）
        tools = self.api.list_tools_mcp()

        # 获取工具描述（OpenAI 格式，用于真实 LLM）
        # = 57 个底层工具 + 白名单组合技能（app_send_message、send_email 等） + 视觉能力函数 screen_inspect
        openai_tools = self._agent_openai_tools()

        for step in range(1, self.max_steps + 1):
            self._cur_step = step
            # ---- 1. 调用 LLM ----
            messages = self._request_messages()
            response = self.llm.chat(
                messages,
                tools=None if chat_mode else (
                    self._visible_tools(openai_tools) if self.llm.provider in ("openai_compatible", "failover") else None
                ),
            )

            # Token/想法/时间 记录（每步：prompt/completion/total + 携带图片数 + 模型的思考）
            try:
                pending = [tc["name"] for tc in (response.tool_calls or [])]
                self._record_llm_usage(
                    step, ",".join(pending) or "reply", messages,
                    reasoning=getattr(response, "reasoning", ""),
                )
            except Exception:
                pass

            if not response:
                return "Agent 内部错误：LLM 无响应。"

            # ---- 2. 处理 LLM 响应 ----
            if response.finish_reason == "tool_calls" and response.tool_calls:
                self.conversation.add_assistant(
                    tool_calls=[
                        {"name": tc["name"], "arguments": tc["arguments"]}
                        for tc in response.tool_calls
                    ]
                )

                tool_call_ids = self.conversation.last_tool_call_ids()
                for i, tc in enumerate(response.tool_calls):
                    name = tc["name"]
                    args = tc["arguments"]
                    call_id = tool_call_ids[i] if i < len(tool_call_ids) else ""

                    # 执行工具（含安全确认）
                    result = self._execute_one(name, args)
                    self._turn_tool_calls.append({
                        "tool": name, "args": args,
                        "ok": bool(result.get("success")),
                    })

                    # 用户拒绝高危操作 → 直接返回
                    if not result.get("success") and "拒绝" in result.get("error", ""):
                        msg = self._with_reminder(f"操作已取消：{result['error']}")
                        self._record_coordinator_trace(user_input, self._turn_tool_calls, msg)
                        return msg

                    self.conversation.add_tool_result(
                        tool_call_id=call_id,
                        name=name,
                        content=result,
                        max_chars=self.tool_result_max_chars,
                    )

                    # screen_inspect 原生视觉分支：tool 结果之后把截图补成观测消息（顺序满足 API 配对要求）
                    self._attach_inspection_observation(result)

                    # GUI 操作 + 视觉模型 → 截图回传（P1-B：仅"确需视觉/诊断"才截，
                    # 成功的结构化动作不再自动截图，避免旧图历史累积）
                    if name in _GUI_TOOLS and self.llm.supports_vision:
                        if result.get("success"):
                            if self._browser_action_needs_visual(name, result):
                                self._capture_and_add_image(
                                    note=f"已执行 {name}，当前屏幕如下，请结合它判断下一步。",
                                    browser=name.startswith("browser_"),
                                )
                        else:
                            self._capture_and_add_image(
                                note=f"执行 {name} 可能未成功，请结合截图看清原因再决定。",
                                browser=name.startswith("browser_"),
                            )

                # 关键修复：离线脑执行完工具后直接返回结果，不再循环调 LLM
                if isinstance(self.llm, DeterministicBrain):
                    summary = self._format_deterministic_result(response.tool_calls, result)
                    self.sessions.save()
                    self._record_coordinator_trace(user_input, self._turn_tool_calls, summary)
                    return self._with_reminder(summary)

            elif response.text:
                # 纯文本回复 → 最终回复
                self.conversation.add_assistant(content=response.text)
                self._compress_memory()  # 压缩记忆（对标 AutoGPT）
                self.sessions.save()
                self._record_coordinator_trace(user_input, self._turn_tool_calls, response.text)
                return self._with_reminder(response.text)

            else:
                msg = "Agent 无法理解 LLM 的响应，请重试。"
                self._record_coordinator_trace(user_input, self._turn_tool_calls, msg, ok=False)
                return msg

        # 超出最大步数
        summary = f"任务未能在 {self.max_steps} 步内完成，已自动终止。"
        self.conversation.add_assistant(content=summary)
        self._compress_memory()
        self.sessions.save()
        self._record_coordinator_trace(user_input, self._turn_tool_calls, summary, ok=False)
        return self._with_reminder(summary)

    # ============================================================
    # 流式入口
    # ============================================================

    def run_stream(self, user_input: str, stop_check=None):
        """
        流式执行：逐 token 产出回复。

        对标 ChatGPT 的逐字输出效果。
        前端可配合 SSE 直接转发。

        stop_check：可选回调 () -> bool，流式过程中每个 chunk 之间被调用，
        返回 True 时立即终止生成（保留已流出的部分文本入会话历史），
        用于 Web UI 的"停止生成"按钮打断死循环/超长输出。

        用法：
            for chunk in agent.run_stream("磁盘空间"):
                if chunk.finish_reason == "streaming":
                    print(chunk.text, end="")
                elif chunk.finish_reason == "tool_calls":
                    print(f"[调用工具: {chunk.tool_calls}]")
                elif chunk.finish_reason == "stop":
                    print(f"[完成: {chunk.text}]")

        产出：
            ChatResponse 对象，finish_reason 为 "streaming" 表示中间 token，
            "tool_calls" 表示工具调用，"stop" 表示最终回复。
        """
        if not user_input or not user_input.strip():
            yield ChatResponse(text="请输入指令。", finish_reason="stop")
            return

        self._turn_artifacts = []
        self._turn_tool_calls: list[dict] = []
        self._turn_usage_trace: list = []
        self._run_log = None
        self.conversation.add_user(user_input)

        # 用户说"清理截图/清理产物"等 → 直接清当前会话过程产物，不经过 LLM
        clean_msg = self._try_cleanup_command(user_input)
        if clean_msg is not None:
            # 先落盘再 yield 终止块：SSE 消费方收到 stop 即退出，生成器被弃置，
            # yield 之后的代码不会执行（所有终止路径同理）
            self.sessions.save()
            self._record_coordinator_trace(user_input, self._turn_tool_calls, clean_msg)
            yield ChatResponse(text=clean_msg, finish_reason="stop")
            return

        # 纯对话模式（本地小模型）：跳过规划层，不传工具
        chat_mode = self._chat_mode()
        openai_tools = self._agent_openai_tools()
        # 纯计算回合：百炼 code_interpreter 不与本地函数工具同请求（"Agent mode does
        # not support tools"）→ 该回合以 tools=None 走服务端解释器（云端沙箱跑 Python）；
        # 命中计算关键词且不涉及本地资源才触发，避免误判。
        code_interpreter_turn = (
            not chat_mode
            and getattr(self.llm, "server_code_interpreter_active", False)
            and bool(_PURE_CALC_RE.search(user_input))
            and not _LOCAL_REF_RE.search(user_input)
        )

        for step in range(1, self.max_steps + 1):
            self._cur_step = step
            # 轮次间隙也检查打断（长时间工具执行后进入下一轮 LLM 调用前）
            if stop_check is not None and stop_check():
                yield self._stopped_response("")
                return
            messages = self._request_messages()
            collected_text = ""
            thought_holder = {"reasoning": ""}

            llm_stream = self._tagged_stream(
                self.llm.chat_stream(
                    messages,
                    tools=None if (chat_mode or (code_interpreter_turn and step == 1)) else (
                        self._visible_tools(openai_tools) if self.llm.provider in ("openai_compatible", "failover") else None
                    ),
                ),
                step,
                messages,
                thought_holder,
            )
            stopped = False
            for chunk in llm_stream:
                if stop_check is not None and stop_check():
                    stopped = True
                    break
                if chunk.finish_reason == "reasoning":
                    # 思考 token 透传给前端（DeepSeek 式思考过程展示），不进对话历史
                    thought_holder["reasoning"] += getattr(chunk, "reasoning", "") or ""
                    yield chunk
                elif chunk.finish_reason == "streaming":
                    collected_text += chunk.text
                    yield chunk
                elif chunk.finish_reason == "tool_calls":
                    yield chunk
                    # 处理工具调用
                    self.conversation.add_assistant(
                        tool_calls=[
                            {"name": tc["name"], "arguments": tc["arguments"]}
                            for tc in chunk.tool_calls
                        ]
                    )

                    tool_call_ids = self.conversation.last_tool_call_ids()
                    for i, tc in enumerate(chunk.tool_calls):
                        name = tc["name"]
                        args = tc["arguments"]
                        call_id = tool_call_ids[i] if i < len(tool_call_ids) else ""
                        result = self._execute_one(name, args)
                        self._turn_tool_calls.append({
                            "tool": name, "args": args,
                            "ok": bool(result.get("success")),
                        })

                        if not result.get("success") and "拒绝" in result.get("error", ""):
                            self.sessions.save()
                            msg = self._with_reminder(f"操作已取消：{result['error']}")
                            self._record_coordinator_trace(user_input, self._turn_tool_calls, msg)
                            yield ChatResponse(text=msg, finish_reason="stop")
                            return

                        self.conversation.add_tool_result(
                            tool_call_id=call_id,
                            name=name,
                            content=result,
                        )

                        # screen_inspect 原生视觉分支：tool 结果之后把截图补成观测消息
                        self._attach_inspection_observation(result)

                        # GUI 操作 + 视觉模型 → 截图回传（P1-B：仅"确需视觉/诊断"才截）
                        if name in _GUI_TOOLS and self.llm.supports_vision:
                            if result.get("success"):
                                if self._browser_action_needs_visual(name, result):
                                    self._capture_and_add_image(
                                        note=f"已执行 {name}，当前屏幕如下，请结合它判断下一步。",
                                        browser=name.startswith("browser_"),
                                    )
                            else:
                                self._capture_and_add_image(
                                    note=f"执行 {name} 可能未成功，请结合截图看清原因再决定。",
                                    browser=name.startswith("browser_"),
                                )

                    # 离线脑执行完工具后直接返回
                    if isinstance(self.llm, DeterministicBrain):
                        summary = self._format_deterministic_result(chunk.tool_calls, result)
                        self.sessions.save()
                        self._record_coordinator_trace(user_input, self._turn_tool_calls, summary)
                        yield ChatResponse(text=self._with_reminder(summary), finish_reason="stop")
                        return

                    # 继续下一轮 ReAct 循环
                    break
                else:
                    # finish_reason == "stop"
                    if chunk.text:
                        self.conversation.add_assistant(content=chunk.text)
                    reminder = self._artifact_reminder()
                    if reminder:
                        yield ChatResponse(text=reminder, finish_reason="streaming")
                    self.sessions.save()
                    self._record_coordinator_trace(user_input, self._turn_tool_calls, chunk.text or "")
                    yield chunk
                    return
            else:
                # 内层流正常结束（没有 break）→ 说明是纯文本回复且已收集完
                # 补一个终止 chunk，避免外层又开新一轮重调 LLM 直到 max_steps。
                if collected_text:
                    self.conversation.add_assistant(content=collected_text)
                    reminder = self._artifact_reminder()
                    if reminder:
                        yield ChatResponse(text=reminder, finish_reason="streaming")
                    self.sessions.save()
                    self._record_coordinator_trace(user_input, self._turn_tool_calls, collected_text)
                    yield ChatResponse(finish_reason="stop")
                    return
                continue
            if stopped:
                # 用户打断：保留已流出的部分文本，落盘后以 stop chunk 收尾
                # （stop chunk 携带完整文本，SSE 消费方整体替换，防重复拼接）
                msg = self._stopped_response(collected_text)
                self._record_coordinator_trace(user_input, self._turn_tool_calls, msg.text, ok=False)
                yield msg
                return
            # 如果 break 了（tool_calls），继续下一轮
            continue

        # 超出最大步数
        summary = f"任务未能在 {self.max_steps} 步内完成，已自动终止。"
        self.conversation.add_assistant(content=summary)
        self.sessions.save()
        self._record_coordinator_trace(user_input, self._turn_tool_calls, summary, ok=False)
        yield ChatResponse(text=self._with_reminder(summary), finish_reason="stop")

    # ============================================================
    # 内部方法
    # ============================================================

    def _stopped_response(self, partial_text: str) -> "ChatResponse":
        """用户手动停止：部分文本 + 停止标记入会话并落盘，返回终止 chunk"""
        note = "⏹ 已手动停止生成。"
        content = f"{partial_text}\n\n{note}" if partial_text else note
        self.conversation.add_assistant(content=content)
        self.sessions.save()
        return ChatResponse(text=content, finish_reason="stop")

    def _chat_mode(self) -> bool:
        """
        纯对话模式判定：客户端声明 tool_free（本地端点默认），或
        FailoverClient 已降级/手动切到 tool_free 的本地端点。
        生效后：不传工具、跳过规划层、system prompt 换成 _CHAT_SYSTEM_PROMPT。
        """
        if getattr(self.llm, "tool_free", False):
            return True
        return bool(getattr(self.llm, "tool_free_active", False))

    def _request_messages(self) -> list:
        """
        构造发送给 LLM 的消息列表。

        真实 LLM 请求在对话历史前注入 system prompt（提升工具调用可靠性）；
        离线脑（DeterministicBrain）靠扫描全部 content 做关键词匹配，
        不注入 system，避免干扰意图识别。
        纯对话模式（本地小模型）→ 换轻量 _CHAT_SYSTEM_PROMPT（无工具守则）；
        联网状态三分支：服务端搜索生效 → +_SERVER_SEARCH_HINT；
        总开关关闭 → +_OFFLINE_HINT（均显式覆盖守则第 9 条）。
        """
        messages = self.conversation.get_window()
        messages = self._trim_history_images(messages)
        if self.llm.provider in ("openai_compatible", "failover"):
            # 完整保留工具调用历史（支持 function calling 的模型可续接多步任务）；
            # 不支持的历史格式由 OpenAICompatibleClient 收到 400 后自动清理重试
            if self._chat_mode():
                return [{"role": "system", "content": _CHAT_SYSTEM_PROMPT}] + messages
            system = self.system_prompt
            if not self.web_search_enabled:
                system += _OFFLINE_HINT
            elif getattr(self.llm, "server_web_search_active", False):
                system += _SERVER_SEARCH_HINT
            if getattr(self.llm, "server_code_interpreter_active", False):
                system += _CODE_INTERPRETER_HINT
            return [{"role": "system", "content": system}] + messages
        return messages

    def set_web_search_enabled(self, enabled: bool) -> None:
        """
        运行时切换联网搜索总开关（Web UI 顶栏按钮调用，不回写 config.yaml）。

        同步联动：主 LLM 客户端的 server_tools 开关（控制 enable_search 注入）
        + 重置探测标志（新状态下重新探测模型能力）。工具可见性与 system prompt
        由 _visible_tools()/_request_messages() 每步动态评估，即时生效。
        """
        self.web_search_enabled = bool(enabled)
        client = getattr(self.llm, "primary", self.llm)
        if hasattr(client, "server_tools"):
            client.server_tools["web_search"] = self.web_search_enabled
        if hasattr(client, "reset_runtime_flags"):
            client.reset_runtime_flags()

    def _execute_one(self, name: str, args: dict) -> dict:
        """执行单个工具/技能调用（含安全确认），并把每一步"操作"记入 run_log。

        白名单组合技能（如 app_send_message）走技能库（_execute_skill），
        其余走底层工具注册表（_execute_tool）；screen_inspect 是 Agent 自带的
        视觉能力函数（截图理解），单独分发。
        """
        start = time.time()
        result = self._dispatch_tool(name, args)
        self._log_action(name, args, result, time.time() - start)
        return result

    def _dispatch_tool(self, name: str, args: dict) -> dict:
        if name == "token_usage":
            return self._tool_token_usage(args)
        if name == "screen_inspect":
            return self._execute_screen_inspect(args)
        if name == "read_image":
            return self._execute_read_image(args)
        blocked = self._sandbox_guard(name, args)
        if blocked is not None:
            return blocked
        if self.api.is_agent_skill(name):
            return self._execute_skill(name, args)
        return self._execute_tool(name, args)

    def _sandbox_guard(self, name: str, args: dict) -> dict | None:
        """SecuritySandbox 第一层硬拦截：危险指令不可逆，绕过确认门直接拒绝。

        run_command 等带 cmd/command 参数的工具若命中 DANGEROUS_ACTIONS
        （rm/format/shutdown/dd/mkfs/fdisk/Stop-Computer 等）或受保护路径，
        直接返回拒绝结果并记审计，避免"确认弹窗被误点"的社交工程绕过。
        该拦截**与授权档位无关、也不依赖 coordinator 是否启用**：即使
        coordinator.enabled=false（无审计/RAG），也始终构造一个独立沙箱兜底，
        保证 shutdown/rm -rf/format 等破坏性指令在任何档位下都不会被执行。
        """
        if not isinstance(args, dict):
            return None
        cmd = ""
        for k in ("cmd", "command"):
            v = args.get(k)
            if isinstance(v, str) and v.strip():
                cmd = v.strip()
                break
        if not cmd:
            return None
        sandbox = getattr(self.coordinator, "sandbox", None)
        if sandbox is None:
            from core.coordinator import SecuritySandbox
            sandbox = SecuritySandbox()
        check = sandbox.check_permission(cmd, cmd)
        if check.get("approved"):
            return None
        return {
            "success": False,
            "tool": name,
            "error": check.get("reason", "安全沙箱拦截"),
        }

    def _execute_tool(self, name: str, args: dict) -> dict:
        """执行单个底层工具（含安全确认）"""
        meta = get_meta("tool", name)

        # 高危操作 + 当前授权档位要求确认 → 走确认门
        if should_confirm(self.authz_level, name, meta["risk"]):
            pv = preview("tool", name, args, meta)
            if self.confirm_handler:
                try:
                    self.confirm_handler(pv)
                except ConfirmationDenied:
                    return {
                        "success": False,
                        "tool": name,
                        "error": f"用户拒绝了高危操作：{name}",
                    }
                except Exception:
                    return {
                        "success": False,
                        "tool": name,
                        "error": f"用户拒绝了高危操作：{name}",
                    }

        # 执行工具
        try:
            result = self.api.execute_tool(name, args)
            return result if isinstance(result, dict) else {
                "success": True,
                "tool": name,
                "result": result,
            }
        except Exception as e:
            return {
                "success": False,
                "tool": name,
                "error": str(e),
            }

    def _execute_skill(self, name: str, args: dict) -> dict:
        """执行 Agent 白名单组合技能（HIGH 风险走确认门）"""
        # 发送/读取类技能默认开 OCR 门：模型没给 verify_ocr 键时补 True，让技能内部用屏幕
        # OCR 核对进入的是不是目标会话——防止发错会话 / 读错会话。显式传了 verify 闭包或
        # 显式 verify_ocr=False 时不覆盖（尊重调用方）。
        if name in ("app_send_message", "read_qq_chat") \
                and "verify" not in args and "verify_ocr" not in args:
            args["verify_ocr"] = True

        meta = get_meta("skill", name)

        # 高危操作 + 当前授权档位要求确认 → 走确认门
        if should_confirm(self.authz_level, name, meta["risk"]):
            pv = preview("skill", name, args, meta)
            if self.confirm_handler:
                try:
                    self.confirm_handler(pv)
                except ConfirmationDenied:
                    return {
                        "success": False,
                        "skill": name,
                        "error": f"用户拒绝了高危操作：{name}",
                    }
                except Exception:
                    return {
                        "success": False,
                        "skill": name,
                        "error": f"用户拒绝了高危操作：{name}",
                    }

        # 执行技能
        try:
            result = self.api.run_skill(name, args)
            return result if isinstance(result, dict) else {
                "success": True,
                "skill": name,
                "result": result,
            }
        except Exception as e:
            return {
                "success": False,
                "skill": name,
                "error": str(e),
            }

    # ============================================================
    # 过程产物（截图等）生命周期 + 截图理解（视觉通道）
    # ============================================================

    def _session_artifact_dir(self) -> str:
        """当前会话专属产物目录（不存在则创建）。截图等过程产物统一放这里。"""
        sid = self.current_session_id or "default"
        sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(sid)) or "default"
        d = os.path.join(self.artifacts_root, sid)
        os.makedirs(d, exist_ok=True)
        return d

    def _record_artifact(self, path: str):
        """登记一个本轮产物（用于结束提醒与清理统计）"""
        if path and path not in self._turn_artifacts:
            self._turn_artifacts.append(path)

    def _artifact_reminder(self) -> str:
        """本轮产生了过程产物 → 返回一句"可清理"提醒；否则空串"""
        if not self._turn_artifacts:
            return ""
        try:
            d = os.path.dirname(self._turn_artifacts[0])
        except Exception:
            d = self.artifacts_root
        return (
            f"\n\n📎 本轮为排查/操作生成了 {len(self._turn_artifacts)} 个过程产物"
            f"（含屏幕截图，可能带屏幕内容），存于 {d}。"
            f"如需删除，对我说『清理截图/清理产物』即可（/reset 也会自动清理）。"
        )

    def _with_reminder(self, text: str) -> str:
        """给最终回复附上产物提醒（仅在确实产生过产物时追加）"""
        self._finalize_run_consistency()
        r = self._artifact_reminder()
        note = self._usage_note()
        tail = (r + "\n").rstrip() + ("\n\n" + note if note else "")
        return text + tail if (r or note) else text

    # ============================================================
    # Token 用量记录（LLM Usage Trace）
    #   1) 每步一条 jsonl 写入会话产物目录 usage_trace<ts>.jsonl，供优化排查；
    #   2) _usage_note() 在最终回复末尾给用户看“本轮/本对话/预算”三栏；
    #   3) token_usage 工具可随时查询当前累计用量。
    # ============================================================

    def _primary_llm(self):
        """取实际发请求的底层 LLM 客户端（Failover 下返回 primary）"""
        return getattr(self.llm, "primary", self.llm)

    def _count_obs(self, messages) -> dict:
        """统计一条消息列表里的图片数与文本字符数（容忍多种 content 形态）"""
        images = chars = msgs = 0
        for m in messages or []:
            if not isinstance(m, dict):
                continue
            msgs += 1
            c = m.get("content")
            if isinstance(c, list):
                for part in c:
                    if isinstance(part, dict):
                        ptype = str(part.get("type", ""))
                        if ptype in ("image_url", "image", "image_content", "input_image") or "image" in ptype:
                            images += 1
                        else:
                            chars += len(str(part.get("text", "")))
            else:
                c = str(c or "")
                chars += len(c)
                if "data:image" in c:
                    images += c.count("data:image")
        return {"messages": msgs, "images": images, "chars": chars}

    def _trim_history_images(self, messages) -> list:
        """Observation 生命周期：保留最近 N 张观测截图，更旧的从 LLM 载荷剥离。

        只裁剪"图片部分"并保留其文字说明，且不写回对话持久记录（产物/审计仍保留原图）。
        """
        keep = getattr(self, "obs_max_history_images", _MAX_HISTORY_IMAGES) or 0
        if keep <= 0:
            return messages
        idx = [i for i, m in enumerate(messages)
               if isinstance(m.get("content"), list)
               and any(isinstance(p, dict) and "image_url" in p for p in m["content"])]
        if len(idx) <= keep:
            return messages
        drop = set(idx[:-keep])
        out = []
        for i, m in enumerate(messages):
            if i in drop:
                c = m.get("content")
                if isinstance(c, list):
                    newc = [p for p in c
                            if not (isinstance(p, dict) and "image_url" in p)]
                    m = dict(m)
                    m["content"] = newc or [{"type": "text", "text": "（历史截图已省略）"}]
            out.append(m)
        return out

    def _browser_action_needs_visual(self, name: str, result: dict) -> bool:
        """判断一次动作后是否需要自动截图回传（P1-B：结构化优于视觉）。

        桌面全局操作无结构化回读 → 保底截图；浏览器内：
        - launch/navigate：新页面需视觉定位 → 截图；
        - click 带来页面/URL 变化：重新定位 → 截图；
        - snapshot/find/type/wait/read 等结构化动作 → 不截图。
        """
        if not name.startswith("browser_"):
            return True
        if name in ("browser_launch", "browser_navigate"):
            return True
        if name in ("browser_click",):
            return bool(result.get("page_changed") or result.get("url_changed"))
        return False

    def _record_llm_usage(self, step: int, tool_hint: str, messages,
                          reasoning: str = "", elapsed_ms: float = 0.0):
        """记录本轮 LLM 请求：时间、想法(reasoning/文本)、token 用量与携带图片数。

        写入每次运行唯一的 run_log_*.jsonl（kind='llm'，与操作行合并成完整时间线）。
        """
        llm = self._primary_llm()
        usage = {}
        if hasattr(llm, "_last_usage"):
            usage = dict(llm._last_usage or {})
        details = usage.get("prompt_tokens_details") or {}
        img_tok = details.get("image_tokens", 0) if isinstance(details, dict) else 0
        comp_details = usage.get("completion_tokens_details") or {}
        reasoning_tok = int(
            usage.get("reasoning_tokens", 0)
            or comp_details.get("reasoning_tokens", 0)
            or 0
        ) if isinstance(comp_details, dict) else int(usage.get("reasoning_tokens", 0) or 0)
        obs = self._count_obs(messages)
        total_used = int(getattr(llm, "total_tokens_used", 0) or 0)
        row = {
            "kind": "llm",
            "ts": _now_ms(),
            "step": int(step),
            "tool_hint": tool_hint,
            "thought": (reasoning or "").strip(),
            "elapsed_ms": round(elapsed_ms, 1),
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
            "reasoning_tokens": reasoning_tok,
            "image_tokens": int(img_tok or 0),
            "message_count": obs["messages"],
            "image_count": obs["images"],
            "char_count": obs["chars"],
            "cum_total_used": total_used,
        }
        # run_log 去重（P2-4）：同一个 LLM request 的 token 快照 (step, cum, total, in, out)
        # 只会有一份；若流式 finally 与内联记录对同一 request 双写，就保留第一条，保证
        # sum(per-call total_tokens) == 末行 cum_total_used，避免污染后续 benchmark。
        if self._is_llm_row_duplicate(row):
            return
        self._turn_usage_trace.append(row)
        self._append_run_log(row)

    def _llm_row_fingerprint(self, row: dict):
        return (int(row.get("step", 0) or 0), int(row.get("cum_total_used", 0) or 0),
                int(row.get("total_tokens", 0) or 0), int(row.get("prompt_tokens", 0) or 0),
                int(row.get("completion_tokens", 0) or 0))

    def _is_llm_row_duplicate(self, row: dict) -> bool:
        """row 是否与 _turn_usage_trace 中最近一条 llm 行指纹重复（同一请求重复写入）。"""
        trace = getattr(self, "_turn_usage_trace", None) or []
        last = None
        for r in reversed(trace):
            if r.get("kind") == "llm":
                last = r
                break
        return last is not None and self._llm_row_fingerprint(last) == self._llm_row_fingerprint(row)

    def _finalize_run_consistency(self):
        """run 结束后自动校验 run_log：sum(per-call total_tokens) 必须等于末次 cum_total_used。

        不一致时向 run_log 追加 benchmark invalid 标记并要求修正，避免下游把
        含重复/丢失记录的 run 当成有效指标。运行内幂等（只校验一次）。
        """
        if getattr(self, "_run_consistency_done", False):
            return
        self._run_consistency_done = True
        rows = [r for r in getattr(self, "_turn_usage_trace", None) or [] if r.get("kind") == "llm"]
        if not rows:
            return
        per_call_sum = sum(int(r.get("total_tokens", 0) or 0) for r in rows)
        final_cum = int(rows[-1].get("cum_total_used", 0) or 0)
        valid = (per_call_sum == final_cum)
        self._append_run_log({
            "kind": "benchmark",
            "valid": bool(valid),
            "llm_calls": len(rows),
            "sum_per_call_total": per_call_sum,
            "final_cum_total_used": final_cum,
            "note": "" if valid else "INVALID: sum(per-call total_tokens) != 末次 cum_total_used，run_log 有重复/丢失记录",
        })

    def _log_action(self, name: str, args: dict, result: dict, elapsed: float):
        """记录一次工具/技能操作：时间、名称、参数、成功与否、耗时（kind='action'）。"""
        ok = bool(result.get("success"))
        detail = result.get("result") if ok else result.get("error", "")
        if isinstance(detail, (dict, list)):
            import json as _j
            try:
                detail = _j.dumps(detail, ensure_ascii=False)[:400]
            except Exception:
                detail = str(detail)[:400]
        else:
            detail = str(detail)[:400]
        row = {
            "kind": "action",
            "ts": _now_ms(),
            "step": int(getattr(self, "_cur_step", 0) or 0),
            "tool": name,
            "args": _sanitize_args(args),
            "ok": ok,
            "elapsed_ms": round(elapsed * 1000, 1),
            "result": detail,
        }
        self._append_run_log(row)

    def _append_run_log(self, rec: dict):
        """把一行追加到本次运行的 run_log_*.jsonl（会话产物目录）。"""
        self._ensure_run_log()
        try:
            with open(self._run_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _ensure_run_log(self) -> str:
        """惰性创建本次运行的 run_log 文件路径（每次运行一份）。"""
        if not getattr(self, "_run_log", None):
            self._run_log = os.path.join(
                self._session_artifact_dir(),
                f"run_log_{int(time.time() * 1000)}.jsonl",
            )
        return self._run_log

    def _usage_note(self) -> str:
        """生成供最终回复追加的 token 概览（本轮/本对话/预算）"""
        if not getattr(self, "_turn_usage_trace", None):
            return ""
        llm = self._primary_llm()
        turn = self._turn_usage_trace[-1]
        steps = len(self._turn_usage_trace)
        turn_total = turn.get("total_tokens", 0)
        # 无实际消耗（离线脑/本地 mock）时不显示，避免噪音
        if not turn_total or turn_total <= 0:
            return ""
        turn_images = sum(r.get("image_count", 0) for r in self._turn_usage_trace)
        cum = int(getattr(llm, "total_tokens_used", 0) or 0)
        cum_in = sum(int(r.get("prompt_tokens", 0) or 0) for r in self._turn_usage_trace)
        cum_out = sum(int(r.get("completion_tokens", 0) or 0) for r in self._turn_usage_trace)
        budget = getattr(llm, "max_total_tokens", None)
        img_part = f"，携带图片 {turn_images} 张" if turn_images else ""
        budget_part = f" / 预算 {budget}" if budget and budget > 0 else ""
        return (
            f"📊 Token 用量：本任务 {steps} 步、本轮合计约 {turn_total:,}{img_part}；"
            f"本对话累计约 {cum:,}（输入 {cum_in:,} / 输出 {cum_out:,}）{budget_part}。"
            f"想要分步明细或手动释放，可对我说『token』。"
        )

    def _tool_token_usage(self, args: dict) -> dict:
        """token_usage 工具：返回当前对话的 token 用量明细。"""
        llm = self._primary_llm()
        rows = list(getattr(self, "_turn_usage_trace", None) or [])
        per_step = [
            {k: r.get(k) for k in
             ("step", "tool_hint", "prompt_tokens", "completion_tokens",
              "total_tokens", "image_tokens", "image_count")}
            for r in rows
        ]
        return {
            "success": True,
            "summary": {
                "turn_total": sum(r.get("total_tokens", 0) for r in rows),
                "conversation_total": int(getattr(llm, "total_tokens_used", 0) or 0),
                "max_allowed": getattr(llm, "max_total_tokens", 0) or 0,
                "steps": len(rows),
                "images_this_turn": sum(r.get("image_count", 0) for r in rows),
            },
            "per_step": per_step,
        }

    def _tagged_stream(self, gen, step: int, messages, thought_holder=None):
        """包一层流式生成器：在流耗尽时记录该步 token 用量（finally 保证触发）。

        thought_holder 由外层 for 循环在收流时累积模型的 thinking，最终一并落盘。
        """
        try:
            yield from gen
        finally:
            try:
                reasoning = (thought_holder or {}).get("reasoning", "") or ""
                self._record_llm_usage(step, "stream", messages, reasoning=reasoning)
            except Exception:
                pass

    def _agent_openai_tools(self) -> list:
        """模型可见函数全集 = 59 底层工具 + 白名单组合技能 + 视觉能力函数 screen_inspect/read_image

        注意：本方法返回全集（供测试与统计引用）；服务端联网搜索生效时，
        由 _visible_tools() 在 ReAct 每一步动态隐藏本地 browser_search。
        """
        return self.api.list_tools_openai() + self.api.list_skills_openai() + \
            [dict(_AGENT_VISION_TOOL), dict(_AGENT_IMAGE_TOOL), dict(_AGENT_TOKEN_TOOL)]

    def _visible_tools(self, tools: list) -> list:
        """
        按联网搜索总开关（web_search.enabled）动态裁剪模型可见工具（每步评估）。

        三分支（DeepSeek 式一个开关管所有联网能力）：
          总开关关       → 隐藏 browser_search + browser_extract（完全离线问答）
          服务端搜索生效 → 隐藏 browser_search（被百炼 enable_search 替代；实测
                           probe_bailian_search.py 场景 D：不藏则模型会选质量差的本地搜索），
                           browser_extract 保留（服务端搜索只给摘要，深读正文还靠它）
          其余（开+非百炼端点）→ 全部可见，模型自行决定是否本地搜索
        API 拒绝 enable_search → llm_client 置 _server_search_rejected →
        server_web_search_active 变 False → 下一步自动还原本地工具兜底，无需重启。
        """
        if not self.web_search_enabled:
            hidden = {"browser_search", "browser_extract"}
        elif getattr(self.llm, "server_web_search_active", False):
            hidden = {"browser_search"}
        else:
            hidden = set()
        return [t for t in tools
                if t.get("function", {}).get("name") not in hidden]

    def _capture_and_add_image(self, note: str = "", browser: bool = False):
        """
        截图 → 存入过程产物目录 → 把【真实 base64】以观测消息加入对话（供视觉模型查看）。

        browser=True 时优先截"浏览器当前页面"（browser_screenshot），失败再退回桌面全屏
        （take_screenshot）。这是 DOM-SoM-坐标混合架构 P1 的"截图分流"修复：浏览器动作后
        模型应看到干净的页面 viewport，而不是"整块桌面里嵌个 Edge 窗口"。

        修复背景：早期实现把 take_screenshot 返回的【文件路径字符串】当 base64 直接拼进
        data:image/... URL，模型拿到的是坏图；这里改为读文件后再 base64。
        拿不到可读图片（如 mock / 测试环境无真截图）时静默返回 None。
        """
        try:
            shots = os.path.join(self._session_artifact_dir(), "shots")
            os.makedirs(shots, exist_ok=True)
            path = os.path.join(shots, f"shot_{int(time.time() * 1000)}.png")
            img_path = self._grab_screenshot(tool_browser=browser, path=path)
            if not img_path:
                return None
            with open(img_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            self.conversation.add_observation_image(note or "（当前界面截图）", b64)
            self._record_artifact(img_path)
            return img_path
        except Exception:
            return None

    def _grab_screenshot(self, tool_browser: bool, path: str):
        """按场景取截图：浏览器动作优先页面截图（browser_screenshot），取不到再退回桌面全屏。"""
        candidates = (["browser_screenshot", "take_screenshot"] if tool_browser
                      else ["take_screenshot"])
        for tool in candidates:
            scr = self.api.execute_tool(tool, {"output": path})
            img_path = scr.get("result") if scr.get("success") else None
            if not isinstance(img_path, str):
                img_path = (img_path or {}).get("path")
            if isinstance(img_path, str) and os.path.isfile(img_path):
                return img_path
        return None

    def _run_vision_bridge(self, image_path: str, question: str) -> Optional[str]:
        """调项目内视觉桥（config.yaml 的 vision_bridge 段，独立于主 llm 的视觉 API）
        把截图转成文字描述；视觉桥没配/失败返回 None（调用方给用户明确指引）。"""
        from core import vision_bridge as _vb
        try:
            return _vb.describe_image(image_path, question)
        except _vb.VisionBridgeError:
            return None
        except Exception:
            return None

    def _execute_screen_inspect(self, args: dict) -> dict:
        """screen_inspect：截当前屏幕并让 Agent 看懂。

        主模型有视觉（llm.supports_vision=true） → 返回 mode=image + 截图路径，
        调用方在 tool 结果之后把它以真实 base64 观测消息补进对话，模型下一轮直接看原图；
        主模型无视觉 → 调项目内视觉桥（config.yaml vision_bridge 段的独立视觉 API）
        把图转成文字描述，直接作为结果返回。
        截图一律先落到当前会话的过程产物目录（可说『清理截图』删除）。
        """
        question = (args or {}).get("question") or (
            "请描述当前屏幕：界面状态、关键文字/报错、大致布局，帮助判断下一步。")
        try:
            shots = os.path.join(self._session_artifact_dir(), "shots")
            os.makedirs(shots, exist_ok=True)
            path = os.path.join(shots, f"inspect_{int(time.time() * 1000)}.png")
            scr = self.api.execute_tool("take_screenshot", {"output": path})
            img_path = scr.get("result") if scr.get("success") else None
            if not isinstance(img_path, str):
                img_path = (img_path or {}).get("path")
            if not img_path or not os.path.isfile(img_path):
                return {"success": False, "screen_inspect": True,
                        "error": "截屏失败：没有拿到可读的图片文件。"}
            self._record_artifact(img_path)

            if getattr(self.llm, "supports_vision", False):
                # 原生视觉模型：图片随后由调用方以观测消息补进对话
                return {
                    "success": True, "screen_inspect": True, "mode": "image",
                    "result": {
                        "screenshot_path": img_path,
                        "note": f"[screen_inspect] {question}",
                    },
                }

            # 无视觉主模型：走项目内视觉桥（vision_bridge 段）→ 文字描述
            desc = self._run_vision_bridge(img_path, question)
            if desc is None:
                return {
                    "success": False, "screen_inspect": True,
                    "error": "当前主模型不支持看图，且视觉桥没配好。请在 config.yaml 的 "
                            "vision_bridge 段填一个有视觉的 API 模型（base_url/api_key/model，"
                            "可和主对话 llm 用不同 key/厂商），或改用有视觉的主模型 "
                            "（llm.supports_vision: true）。",
                }
            return {
                "success": True, "screen_inspect": True, "mode": "text",
                "result": {"screenshot_path": img_path, "description": desc},
            }
        except Exception as e:
            return {"success": False, "screen_inspect": True, "error": f"截图理解失败：{e}"}

    def _execute_read_image(self, args: dict) -> dict:
        """read_image：读取本地图片文件并让 Agent 看懂。

        与 screen_inspect 的区别：读的是**文件路径**（不依赖屏幕是否打开）；
        用户给出图片路径 / 让模型看某张图时用它。
        主模型有视觉 → mode=image + 图片路径，调用方以 base64 观测消息补进对话（模型直接看原图）；
        主模型无视觉 → 调项目内视觉桥（vision_bridge 段）转文字描述直接返回。
        图片本身不拷贝、不改动用户文件（会话过程产物目录只存截图类产物）。
        """
        path = (args or {}).get("path") or ""
        question = (args or {}).get("question") or "请描述这张图片的内容（文字、题目、问题等）。"
        path = path.strip().strip("\"'")
        if not path:
            return {"success": False, "read_image": True, "error": "缺少参数 path：请给出图片文件的路径。"}
        if not os.path.isfile(path):
            return {"success": False, "read_image": True,
                    "error": f"图片文件不存在：{path}。请确认路径正确，或直接说出文件名（如桌面上的 1.3.png）。"}
        try:
            if getattr(self.llm, "supports_vision", False):
                # 原生视觉模型：图片随后由调用方以观测消息补进对话
                return {
                    "success": True, "read_image": True, "mode": "image",
                    "result": {"image_path": path, "note": f"[read_image] {question}"},
                }

            # 无视觉主模型：走项目内视觉桥（vision_bridge 段）→ 文字描述
            desc = self._run_vision_bridge(path, question)
            if desc is None:
                return {
                    "success": False, "read_image": True,
                    "error": "当前主模型不支持看图，且视觉桥没配好。请在 config.yaml 的 "
                            "vision_bridge 段填一个有视觉的 API 模型（base_url/api_key/model），"
                            "或改用有视觉的主模型（llm.supports_vision: true）。",
                }
            return {
                "success": True, "read_image": True, "mode": "text",
                "result": {"image_path": path, "description": desc},
            }
        except Exception as e:
            return {"success": False, "read_image": True, "error": f"读取图片失败：{e}"}

    def _attach_inspection_observation(self, result: dict):
        """视觉分支：在 tool 结果之后，把截图/本地图片以真实 base64 观测消息补进对话。

        覆盖 screen_inspect（截图）与 read_image（本地图片文件）：两者在视觉模型下都返回
        mode=image + 图片路径，这里统一把原图读成 base64 追加为观测消息，模型下一轮直接看原图。
        """
        if not (result or {}).get("mode") == "image":
            return
        info = result.get("result") or {}
        p = info.get("screenshot_path") or info.get("image_path")
        if not p or not os.path.isfile(p):
            return
        try:
            with open(p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            self.conversation.add_observation_image(
                info.get("note") or "（当前界面截图）", b64)
        except Exception:
            pass

    def _cleanup_current_session_artifacts(self) -> int:
        """删除当前会话产物目录，返回删除的文件数；没有目录返回 0"""
        sid = self.current_session_id or "default"
        sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(sid)) or "default"
        d = os.path.join(self.artifacts_root, sid)
        n = 0
        if os.path.isdir(d):
            for _root, _dirs, files in os.walk(d):
                n += len(files)
            shutil.rmtree(d, ignore_errors=True)
        return n

    def _try_cleanup_command(self, user_input: str) -> Optional[str]:
        """用户说『清理截图/清理产物』等 → 清空当前会话产物目录并返回提示；否则返回 None"""
        text = re.sub(r"[\s，。,.!！?？/、：:;；]", "", user_input or "")
        if not any(p in text for p in _ARTIFACT_CLEAN_PHRASES):
            return None
        n = self._cleanup_current_session_artifacts()
        if n:
            return f"已清理当前会话的 {n} 个过程产物（截图等）。目录已删除，聊天记录不受影响。"
        return "当前会话没有需要清理的过程产物。"

    def reset(self):
        """重置当前会话：清空聊天记录，并联动清空当前会话过程产物"""
        self.conversation.clear()
        self._cleanup_current_session_artifacts()
        self.sessions.save()

    @property
    def provider(self) -> str:
        if isinstance(self.llm, FailoverClient):
            return self.llm.status["current_provider"]
        return self.llm.provider

    @property
    def model_name(self) -> str:
        """获取当前使用的模型名称"""
        if isinstance(self.llm, DeterministicBrain):
            return "离线脑（规则匹配）"
        if isinstance(self.llm, FailoverClient):
            status = self.llm.status
            if status.get("force_local"):
                return self._get_llm_model_name(self.llm.fallback)
            if status["degraded"]:
                provider_map = {"openai_compatible": "本地 Ollama", "deterministic": "离线脑"}
                current = provider_map.get(status["current_provider"], status["current_provider"])
                return f"{current}（⚠️ 降级至）"
            return self._get_llm_model_name(self.llm.primary)
        return self._get_llm_model_name(self.llm)

    @staticmethod
    def _get_llm_model_name(llm) -> str:
        if hasattr(llm, "model") and llm.model:
            return llm.model
        return llm.provider

    # ============================================================
    # 格式化方法
    # ============================================================

    def _format_deterministic_result(self, tool_calls: list, last_result: dict) -> str:
        """将离线脑执行结果格式化为可读文本"""
        lines = []
        for tc in tool_calls:
            name = tc["name"]
            args = tc["arguments"]
            lines.append(f"▸ 执行: {name}")

        if not last_result.get("success"):
            return f"执行失败：{last_result.get('error', '未知错误')}"

        result_data = last_result.get("result", last_result)
        tool_name = tool_calls[-1]["name"] if tool_calls else ""

        if tool_name == "disk_usage":
            return self._format_disk_usage(result_data)
        elif tool_name == "list_processes":
            return self._format_processes(result_data)
        elif tool_name == "network_status":
            return self._format_network(result_data)
        elif tool_name == "find_large_files":
            return self._format_large_files(result_data)
        elif tool_name in ("list_directory", "search_files"):
            return self._format_files(result_data)
        elif tool_name == "list_env_vars":
            return self._format_env_vars(result_data)
        else:
            if isinstance(result_data, dict):
                return "\n".join(f"{k}: {v}" for k, v in result_data.items())
            if isinstance(result_data, list):
                items = "\n".join(str(item) for item in result_data[:20])
                if len(result_data) > 20:
                    items += f"\n... 还有 {len(result_data) - 20} 项"
                return items
            return str(result_data)

    @staticmethod
    def _format_disk_usage(data) -> str:
        """格式化磁盘使用信息"""
        if isinstance(data, dict):
            total = data.get("total", 0)
            used = data.get("used", 0)
            free = data.get("free", 0)
            percent = data.get("percent_used", 0)
            total_gb = total / (1024**3) if total else 0
            used_gb = used / (1024**3) if used else 0
            free_gb = free / (1024**3) if free else 0
            return (
                f"磁盘使用情况：\n"
                f"  总容量: {total_gb:.1f} GB\n"
                f"  已用: {used_gb:.1f} GB ({percent}%)\n"
                f"  剩余: {free_gb:.1f} GB"
            )
        return str(data)

    @staticmethod
    def _format_processes(data) -> str:
        """格式化进程列表"""
        if isinstance(data, list):
            lines = ["进程列表（按 CPU 占用排序）："]
            for i, p in enumerate(data[:15], 1):
                name = p.get("name", p.get("Name", "?"))
                pid = p.get("pid", p.get("PID", "?"))
                cpu = p.get("cpu_percent", p.get("CPU", 0))
                mem = p.get("memory_percent", p.get("Memory", 0))
                lines.append(f"  {i}. {name} (PID: {pid}) CPU: {cpu}% 内存: {mem}%")
            if len(data) > 15:
                lines.append(f"  ... 共 {len(data)} 个进程")
            return "\n".join(lines)
        return str(data)

    @staticmethod
    def _format_network(data) -> str:
        """格式化网络状态"""
        if isinstance(data, dict):
            lines = ["网络状态："]
            for k, v in data.items():
                lines.append(f"  {k}: {v}")
            return "\n".join(lines)
        return str(data)

    @staticmethod
    def _format_large_files(data) -> str:
        """格式化大文件列表"""
        if isinstance(data, list):
            lines = ["大文件列表："]
            for i, f in enumerate(data[:20], 1):
                path = f.get("path", f.get("Path", "?"))
                size = f.get("size_mb", f.get("Size", 0))
                lines.append(f"  {i}. {path} ({size} MB)")
            if len(data) > 20:
                lines.append(f"  ... 共 {len(data)} 个文件")
            return "\n".join(lines)
        return str(data)

    @staticmethod
    def _format_files(data) -> str:
        """格式化文件列表"""
        if isinstance(data, list):
            lines = [f"文件列表（共 {len(data)} 项）："]
            for i, f in enumerate(data[:30], 1):
                if isinstance(f, dict):
                    name = f.get("name", f.get("Name", "?"))
                    ftype = f.get("type", f.get("Type", ""))
                    lines.append(f"  {i}. [{ftype}] {name}")
                else:
                    lines.append(f"  {i}. {f}")
            if len(data) > 30:
                lines.append(f"  ... 还有 {len(data) - 30} 项")
            return "\n".join(lines)
        return str(data)

    @staticmethod
    def _format_env_vars(data) -> str:
        """格式化环境变量"""
        if isinstance(data, list):
            lines = ["环境变量："]
            for i, env in enumerate(data[:20], 1):
                key = env.get("key", env.get("Key", "?"))
                val = env.get("value", env.get("Value", ""))
                display = val[:60] + "..." if len(str(val)) > 60 else val
                lines.append(f"  {key}={display}")
            if len(data) > 20:
                lines.append(f"  ... 共 {len(data)} 个环境变量")
            return "\n".join(lines)
        return str(data)