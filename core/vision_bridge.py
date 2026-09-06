"""
vision_bridge.py —— 统一的"看图"视觉源（项目内，替代对外部 node 脚本的依赖）
========================================================================

把"本地一张图（截图 / 裁剪出的标题条）+ 一句话问题"交给某个**有视觉**的模型，
让模型把图里内容用文字讲出来 / 做 OCR，返回文字。

两种"看图"方式由 config.yaml 的 llm.supports_vision 决定（二选一，绝不两路都猜）：
  1. llm.supports_vision = true（主对话模型自己有视觉，如 qwen3.5-plus）
     → 图直接交给**主对话模型本身**（llm 段的 base_url/api_key/model）处理，
       不需要第二个 key；
  2. llm.supports_vision = false（主对话是纯文本模型，如部分 DeepSeek / 离线脑）
     → 走 **vision_bridge 段**配的独立视觉 API（config.yaml 顶层 vision_bridge，
       可与主对话不同 key/厂商，用一个便宜/可用的视觉模型即可）。

这样别人拿到项目有两种填法都能全功能：
  - 只填一个【有视觉】的 key 在 llm（supports_vision: true）→ 看图由主模型自己来；
  - 或 llm 填纯文本模型（supports_vision: false），再单独给 vision_bridge 填一个
    有视觉的 key → 看图走视觉桥。
两种源都没配好 → 抛 VisionBridgeError，给出上面的指引，绝不瞎编。

本项目里用它做两件事：
  1. QQ「搜索+发送」技能发送前的"屏幕 OCR 核对门"（make_ocr_verify 的默认实现）；
  2. Agent screen_inspect 在"主模型无视觉（supports_vision=false）"时，把截图转成文字描述
     （主模型有视觉时 screen_inspect 走原生看图通道，不经这里）。

配置参考（config.yaml / config.yaml.example）：
    llm:
        provider: openai_compatible
        ...
        supports_vision: true          # 视觉源一：主模型自己看
    vision_bridge:                     # 视觉源二：主模型无视觉时用（独立第二个 API）
        base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
        api_key:  <有视觉的 key>
        model:    qwen-vl-max          # 换成真正支持读图的模型
"""

import base64
import os
from pathlib import Path
from typing import Optional, Union

from core.agent_config import load_config
from core.llm_client import OpenAICompatibleClient

# config.yaml 顶层段名：当主对话(llm)没有视觉时，看图走的独立视觉 API
_VISION_SECTION = "vision_bridge"


class VisionBridgeError(RuntimeError):
    """视觉源不可用：主模型没视觉 且 vision_bridge 没配 / 配错 / 请求失败。提示用户而不是瞎猜。"""


_GUIDE = (
    "看图 / OCR 识别需要视觉能力（当前视觉桥没配好）：主对话 llm 没开视觉，"
    "也没找到可用的 vision_bridge 段。二选一补上即可：\n"
    "  A. 让主对话(llm)自己看图：llm 段填一个有视觉的 key，并把 llm.supports_vision 设为 true"
    "（图片直接由主对话模型处理，不需要第二个 key）；\n"
    "  B. 保留纯文本主对话，另配『视觉桥』：给 config.yaml 单独加 vision_bridge 段，"
    "填一个有视觉的独立 API（base_url / api_key / model，可用更便宜的视觉模型，如 qwen-vl-max）。\n"
    "两种都没配时本功能不可用，会明确报错，不会假装看懂。"
)


def _source(config: dict) -> dict:
    """按 llm.supports_vision 选视觉源：返回 {'name': 'llm'|'vision_bridge', 'section': <配置段>}。

    规则（与文档一致）：
      - 主对话是 openai_compatible 且 supports_vision=true 且已填 key → 用主模型(llm)处理图；
      - 否则 → 用 vision_bridge 段（独立视觉 API）；
      - 都不可用 → 抛 VisionBridgeError 并给出指引。
    """
    llm = config.get("llm", {}) or {}
    if (llm.get("provider") == "openai_compatible"
            and (llm.get("api_key") or "").strip()
            and llm.get("supports_vision")):
        return {"name": "llm", "section": llm}

    vb = config.get(_VISION_SECTION, {}) or {}
    if ((vb.get("base_url") or "").strip()
            and (vb.get("api_key") or "").strip()
            and (vb.get("model") or "").strip()):
        return {"name": "vision_bridge", "section": vb}

    raise VisionBridgeError(_GUIDE)


def require_vision(config: Optional[dict] = None) -> dict:
    """校验有没有可用的视觉源；有则返回被选中的那个配置段（llm 段或 vision_bridge 段），没有则抛指引。"""
    cfg = config if config is not None else load_config()
    return _source(cfg)["section"]


def describe_image(image: Union[str, os.PathLike, bytes, bytearray],
                   question: str,
                   config: Optional[dict] = None) -> str:
    """
    把一张图交给选定的视觉源，问一句话，返回模型的文字回答。

    视觉源自动选择（见模块说明）：主对话 supports_vision=true → 主模型本身；
    否则 → vision_bridge 段。两种源都没配 / 请求失败 → 抛 VisionBridgeError。

    参数：
        image:    图片文件路径 或 原始图片字节（bytes）
        question: 对图的问题（如"原样输出这一横条里的标题文字"）
        config:   配置字典（默认 load_config()）

    返回：
        模型输出的文字。
    """
    cfg = config if config is not None else load_config()
    src = _source(cfg)
    sec = src["section"]
    timeout = int(sec.get("timeout") or cfg.get("llm", {}).get("timeout") or 60)
    client = OpenAICompatibleClient(
        base_url=sec.get("base_url", ""),
        api_key=sec.get("api_key", ""),
        model=sec.get("model", ""),
        supports_vision=True,
        timeout=timeout,
    )

    # 读图 → base64
    if isinstance(image, (bytes, bytearray)):
        raw = bytes(image)
    elif isinstance(image, os.PathLike) or isinstance(image, str):
        p = Path(image)
        if not p.is_file():
            raise VisionBridgeError(f"图片文件不存在：{p}")
        raw = p.read_bytes()
    else:
        raise VisionBridgeError(
            f"不支持的 image 类型：{type(image).__name__}（请传文件路径或 bytes）")
    if not raw:
        raise VisionBridgeError("图片内容为空，无法识别。")
    b64 = base64.b64encode(raw).decode("ascii")

    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": question or "请描述这张图。"},
        ],
    }]
    resp = client.chat(messages=messages)

    # chat() 在网络/额度/超时等出错时会把异常吞成"无 raw"的 ChatResponse → 转抛，不静默
    text = (resp.text or "").strip()
    if resp.raw is None and not resp.tool_calls:
        raise VisionBridgeError(
            f"看图请求失败（视觉源：{src['name']} / {sec.get('model')}），返回：\n"
            f"{text or '（无返回内容）'}\n请检查网络 / 该模型是否真的有视觉 / 额度 / key 配置。")
    if not text:
        raise VisionBridgeError("看图请求返回了空内容，无法识别。")
    return text
