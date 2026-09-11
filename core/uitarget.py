"""
uitarget.py — 统一 UI 目标数据模型（DOM-SoM-坐标混合架构 P0）

把「模型看见的目标」和「工具实际操作的目标」统一成一个 UITarget，
用 source 区分定位通道：browser_dom / browser_som / desktop_vision。

关键约定（P0 数据模型，见 docs 方案第 12~13 节）：
- bbox_css / center_css 统一保存 **CSS viewport 像素**，只在截图绘图层按 DPR 转 device 像素。
- CDP `Input.dispatchMouseEvent` 的 x/y 就是视口 CSS 像素，因此执行层直接用 center_css，
  不要再乘一次 DPR，否则双重缩放点错位置。
- ref 是元素的**稳定身份**，不是快照数组下标（DOM 重排会让下标漂移）。
  浏览器侧形如 `e<id>`（当前 frame）/ `f<frame>e<id>`（带 iframe 前缀，为 P6 预留）。
  稳定 id 优先取元素 id / 结构路径签名，不再用递增数字。
"""

import re
from dataclasses import dataclass, field as _dc_field
from typing import List, Optional, Tuple


# 兼容 `f1e17`（frame 前缀）+ `e17`（无 frame）两种 ref 形态
_REF_RE = re.compile(r"^(?:f([A-Za-z0-9_-]+))?e([A-Za-z0-9_:\-]+)$")


def format_ref(element_id, frame_id: Optional[str] = None) -> str:
    """由元素 id / 稳定签名生成 ref；/ e <id>`；带 iframe 前缀为 `f<frame>e<id>`。"""
    eid = f"e{element_id}"
    return f"f{frame_id}{eid}" if frame_id else eid


def parse_ref(ref) -> Tuple[Optional[str], Optional[str]]:
    """解析 ref，返回 (frame_id, element_id)；不是合法 ref 返回 (None, None)。"""
    if not isinstance(ref, str):
        return (None, None)
    m = _REF_RE.match(ref)
    if not m:
        return (None, None)
    return (m.group(1), m.group(2))


def is_ref(ref) -> bool:
    """判断字符串是否像一个浏览器元素 ref（e17 / f1e17）。"""
    return isinstance(ref, str) and _REF_RE.match(ref) is not None


def parse_som(target) -> Optional[int]:
    """把 `som:2` 解析成视觉编号 num；非 som 形式返回 None。"""
    if isinstance(target, str) and target.startswith("som:"):
        num = target[len("som:"):].strip()
        if num.isdigit():
            return int(num)
    return None


def is_som(target) -> bool:
    return parse_som(target) is not None


def css_center(bbox: Optional[List[float]]) -> Optional[List[float]]:
    """由 bbox [x, y, w, h] 求 CSS 中心 [cx, cy]。"""
    if not bbox or len(bbox) < 4:
        return None
    return [bbox[0] + bbox[2] / 2.0, bbox[1] + bbox[3] / 2.0]


def bbox_to_device(bbox, dpr: float) -> Optional[List[float]]:
    """CSS bbox → device 像素 bbox（仅截图绘图/坐标层使用）。"""
    if not bbox or not dpr:
        return None
    return [bbox[0] * dpr, bbox[1] * dpr, bbox[2] * dpr, bbox[3] * dpr]


def _so_sensitivity(item):
    """交互元素的优先级评分，越小越优先（对应那么 JS 的排序目标：
    button/input/select/textarea 最优先，其次 role=button/link/tab，再次可见链接/可编辑等）。"""
    tag = (item.get("tag") or "").lower()
    role = (item.get("role") or "").lower()
    if tag in ("button", "input", "select", "textarea"):
        return 0
    if role in ("button", "link", "tab", "checkbox", "radio"):
        return 1
    if tag == "a":
        return 2
    if item.get("editable"):
        return 3
    if item.get("onclick"):
        return 4
    if item.get("tabindex") is not None:
        return 5
    return 9


@dataclass
class UITarget:
    """可操作的 UI 目标。source 区分定位来源；ref 是稳定身份；visual_num 只是视觉标签。"""

    source: str = "browser_dom"             # browser_dom / browser_som / desktop_vision
    ref: Optional[str] = None               # e<id> / f<frame>e<id>
    frame_id: Optional[str] = None          # 预留：iframe 前缀，P6 启用
    element_id: Optional[str] = None        # 稳定元素 id
    visual_num: Optional[int] = None        # SoM 视觉编号（仅为截图标签，非身份）
    role: str = ""
    name: str = ""
    tag: str = ""
    bbox_css: Optional[List[float]] = None  # [x, y, w, h]
    center_css: Optional[List[float]] = None
    disabled: bool = False
    extra: dict = _dc_field(default_factory=dict)

    # ---- 构造 / 转换 ---------------------------------------------------

    @classmethod
    def from_js(cls, item: dict, dpr: Optional[float] = 1.0, source: str = "browser_dom"):
        """由 _INSPECT_JS 返回的一个元素 dict 构造 UITarget。

        item 应含：ref / tag / role / name / disabled / x / y / w / h
        （x/y/w/h 为 CSS viewport 像素）。bbox/center_css 直接存 CSS 像素。
        """
        bbox = [item.get("x", 0), item.get("y", 0), item.get("w", 0), item.get("h", 0)]
        return cls(
            source=source,
            ref=item.get("ref"),
            frame_id=item.get("frame_id"),
            element_id=item.get("element_id") or (parse_ref(item.get("ref") or ""))[1],
            role=item.get("role") or "",
            name=item.get("name") or "",
            tag=(item.get("tag") or ""),
            bbox_css=bbox if item.get("w") else None,
            center_css=css_center(bbox) if item.get("w") else None,
            disabled=bool(item.get("disabled")),
            extra={"onclick": bool(item.get("onclick")),
                   "tabindex": item.get("tabindex"),
                   "editable": bool(item.get("editable"))},
        )

    def to_dict(self, include_geo: bool = True) -> dict:
        d = {
            "num": self.visual_num,
            "ref": self.ref,
            "frame_id": self.frame_id,
            "role": self.role,
            "name": self.name,
            "tag": self.tag,
            "disabled": self.disabled,
            "source": self.source,
        }
        if include_geo:
            d["bbox_css"] = self.bbox_css
            d["center_css"] = self.center_css
        return d

    def as_summary(self) -> str:
        """给 Agent 看的一行摘要。"""
        num = f"[{self.visual_num}] " if self.visual_num is not None else ""
        name = self.name or "(无文本)"
        return f"{num}{name} ({self.role}/{self.tag} ref={self.ref})"

    @property
    def is_som(self) -> bool:
        return self.visual_num is not None


def summarize_targets(targets: List[UITarget]) -> str:
    """把一组 UITarget 压成 Agent 易读的多行文本（供无视觉场景描述）。"""
    return "\n".join(t.as_summary() for t in targets)