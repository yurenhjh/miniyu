"""
classifier.py
第4组：任务分类器

在“自然语言意图 → 执行”之间加一层判定，决定执行策略：

    auto     常规、确定性强 → 直接执行
    confirm  高副作用（不可逆/对外可见）→ 执行前逐步确认（最后一步许可）
    warn     专业/创意/低置信度 → 提醒“可能做不好”，取得许可后仍尽力执行

说明：
    本模块只做“分类”，不直接把话术翻译成工具调用；把意图拆成具体工具序列
    是第3组 Agent 的职责，本分类器的输出供其与执行引擎（executor.py）衔接。

    专业/创意类任务不禁止——classify 返回 decision=warn，由上层配合
    executor.execute(..., warning=...) 实现“提醒 + 许可 + 尽力而为”。
"""


# 专业/创意/低置信度关键词（画图、建模、剪辑等需要人类技能与审美的任务）
CREATIVE_KEYWORDS = (
    "画图", "绘画", "画画", "建模", "3d", "blender", "ps", "photoshop",
    "cad", "剪辑", "剪视频", "渲染", "海报", "配色", "作曲", "作词",
    "设计", "排版", "修图", "特效", "动画", "调色",
)

# 高副作用关键词（不可逆 / 对外可见 / 影响面大）
HIGH_RISK_KEYWORDS = (
    "删除", "永久", "清空", "发送", "发布", "格式化", "卸载", "覆盖",
    "转账", "付款", "下单", "提交", "执行", "rm ", "定时", "群发",
)


def classify(text):
    """
    对任务描述分类。

    参数：
        text: 自然语言任务描述

    返回：
        {
            "decision":   "auto" | "confirm" | "warn",
            "confidence": 0.0~1.0,
            "reason":     决策依据,
            "hint":       给用户的提醒（warn 时非空）,
            "creative":   是否专业/创意类,
            "high_risk":  是否高副作用,
        }
    """
    text = (text or "").strip()
    lowered = text.lower()

    creative = any(k in lowered for k in CREATIVE_KEYWORDS)
    high_risk = any(k in lowered for k in HIGH_RISK_KEYWORDS)

    if creative:
        return {
            "decision": "warn",
            "confidence": 0.35,
            "reason": "命中专业/创意类关键词，agent 通常无法保证效果",
            "hint": "这类任务需要专业技能与审美判断，我可能做不好或结果不理想；"
                    "如果你同意，我会尽力尝试。是否继续？",
            "creative": True,
            "high_risk": high_risk,
        }

    if high_risk:
        return {
            "decision": "confirm",
            "confidence": 0.8,
            "reason": "命中高副作用关键词，执行前需逐步确认",
            "hint": "该任务包含不可逆/对外可见的操作，执行到最后一步前我会先征求你的许可。",
            "creative": False,
            "high_risk": True,
        }

    return {
        "decision": "auto",
        "confidence": 0.95,
        "reason": "常规任务，确定性强，直接执行",
        "hint": None,
        "creative": False,
        "high_risk": False,
    }