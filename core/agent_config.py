"""
agent_config.py
第4组：Agent 配置管理
读取 config.yaml + 环境变量覆盖，提供统一配置接口

优先级：环境变量 > 配置文件 > 内置默认值
"""

import os
import re
from pathlib import Path
from typing import Any


# 内置默认值
DEFAULTS = {
    "llm": {
        "provider": "deterministic",
        "base_url": "",
        "api_key": "",
        "model": "",
        "supports_vision": False,
    },
    "agent": {
        "max_steps": 15,
        "confirm_high_risk": True,
        "history_window": 20,
    },
    # 邮件（email）：SMTP 发送 + IMAP 回读核验（send_email 技能用）。默认值按 QQ 邮箱
    # 填好，换服务商改 host/port/ssl 即可。username/auth_code 默认留空，由 config.yaml /
    # 环境变量提供（授权码是敏感字段，不入库）。
    "email": {
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "smtp_ssl": True,
        "imap_host": "imap.qq.com",
        "imap_port": 993,
        "imap_ssl": True,
        "username": "",
        "auth_code": "",
        "timeout": 20,
    },
}

# 环境变量映射：AGENT_xxx → 配置路径
ENV_MAP = {
    "AGENT_LLM_PROVIDER": ("llm", "provider"),
    "AGENT_LLM_BASE_URL": ("llm", "base_url"),
    "AGENT_LLM_API_KEY": ("llm", "api_key"),
    "AGENT_LLM_MODEL": ("llm", "model"),
    "AGENT_LLM_VISION": ("llm", "supports_vision"),
    "AGENT_MAX_STEPS": ("agent", "max_steps"),
    "AGENT_CONFIRM_HIGH_RISK": ("agent", "confirm_high_risk"),
    "AGENT_AUTHORIZATION": ("agent", "authorization"),
    "AGENT_HISTORY_WINDOW": ("agent", "history_window"),
    "AGENT_EMAIL_SMTP_HOST": ("email", "smtp_host"),
    "AGENT_EMAIL_SMTP_PORT": ("email", "smtp_port"),
    "AGENT_EMAIL_SMTP_SSL": ("email", "smtp_ssl"),
    "AGENT_EMAIL_IMAP_HOST": ("email", "imap_host"),
    "AGENT_EMAIL_IMAP_PORT": ("email", "imap_port"),
    "AGENT_EMAIL_IMAP_SSL": ("email", "imap_ssl"),
    "AGENT_EMAIL_USERNAME": ("email", "username"),
    "AGENT_EMAIL_AUTH_CODE": ("email", "auth_code"),
    "AGENT_EMAIL_TIMEOUT": ("email", "timeout"),
}

# 布尔值映射
_TRUTHY = ("true", "1", "yes", "on")


def _find_config() -> Path:
    """从项目根目录查找 config.yaml"""
    # 当前文件在 core/ 下，项目根是 core/..
    here = Path(__file__).resolve().parent
    for candidate in [
        here / ".." / "config.yaml",
        here / ".." / "config.yml",
        Path.cwd() / "config.yaml",
        Path.cwd() / "config.yml",
    ]:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    return here / ".." / "config.yaml"  # 不存在也返回默认路径


def _load_yaml(path: Path) -> dict:
    """加载 YAML 配置文件（无外部依赖的手动解析，仅支持简单键值）"""
    if not path.exists():
        return {}
    try:
        import yaml as _yaml
        with open(path, "r", encoding="utf-8") as f:
            return _yaml.safe_load(f) or {}
    except ImportError:
        # 无 pyyaml 库时尝试手动解析（简易版）
        return _manual_parse_yaml(path)


def _manual_parse_yaml(path: Path) -> dict:
    """简易手动 YAML 解析（仅支持 nested key: value）"""
    result = {}
    current_section = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip()
            if not stripped or stripped.startswith("#"):
                continue
            if not stripped.startswith(" ") and not stripped.startswith("\t"):
                current_section = stripped.rstrip(":").strip()
                if current_section:
                    result[current_section] = {}
            elif current_section and ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip().strip("\"'")
                if key:
                    result[current_section][key] = val
    return result


def _coerce(value: Any, target_type):
    """类型转换"""
    if target_type == bool:
        if isinstance(value, bool):
            return value
        return str(value).lower() in _TRUTHY
    if target_type == int:
        return int(value)
    return value


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个字典"""
    result = base.copy()
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _apply_env_overrides(cfg: dict) -> dict:
    """应用环境变量覆盖"""
    for env_key, (section, field) in ENV_MAP.items():
        val = os.environ.get(env_key)
        if val is not None:
            # 确定目标类型
            default_val = DEFAULTS.get(section, {}).get(field)
            target_type = type(default_val) if default_val is not None else str
            cfg.setdefault(section, {})[field] = _coerce(val, target_type)
    return cfg


def load_config() -> dict:
    """
    加载配置，优先级：环境变量 > 配置文件 > 内置默认值

    返回完整配置字典，调用方用 cfg["llm"]["provider"] 访问。
    """
    # 1. 内置默认值
    cfg = _deep_merge({}, DEFAULTS)

    # 2. 配置文件覆盖
    config_path = _find_config()
    yaml_cfg = _load_yaml(config_path)
    cfg = _deep_merge(cfg, yaml_cfg)

    # 3. 环境变量覆盖
    cfg = _apply_env_overrides(cfg)

    return cfg


def _set_config_field(section: str, key: str, value: str) -> Path:
    """
    文本级把 config.yaml 顶层 <section> 段里的 <key> 行改成 "value"。

    用文本级替换而不是整体 YAML dump，避免丢失文件里的中文注释；
    未找到 <key> 行时在该段首部补一行（两空格缩进）。
    返回被修改的配置文件路径。
    """
    path = _find_config()
    if not path.exists():
        raise FileNotFoundError(f"config 文件不存在：{path}")

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    # 定位顶层 <section>: 段
    sec_idx = None
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(section)}\s*:", line):
            sec_idx = i
            break
    if sec_idx is None:
        raise ValueError(f"config.yaml 中没有 {section} 段，无法写入 {key}")

    replaced = False
    for i in range(sec_idx + 1, len(lines)):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[:1].isspace():  # 遇到下一个顶层键，离开该段
            break
        m = re.match(rf"^(\s*){re.escape(key)}\s*:\s*.*?(\r?\n)$", line)
        if m:
            indent, newline = m.group(1), m.group(2)
            lines[i] = f'{indent}{key}: "{value}"{newline}'
            replaced = True
            break

    if not replaced:
        # 该段里没有 <key> 行，则在段首补一行
        lines.insert(sec_idx + 1, f'  {key}: "{value}"\n')

    path.write_text("".join(lines), encoding="utf-8")
    return path


def set_config_model(model: str) -> Path:
    """
    把 llm.model 写回 config.yaml（仅改 model 行，保留注释与其它字段）。

    用文本级替换而不是整体 YAML dump，避免丢失文件里的中文注释。
    返回被修改的配置文件路径。
    """
    return _set_config_field("llm", "model", model)


def set_config_authorization(level: str) -> Path:
    """
    把 agent.authorization 写回 config.yaml（三档授权档位：base/advanced/full）。

    文本级替换保留中文注释与其它字段；运行中的 agent 由调用方另行热更新。
    返回被修改的配置文件路径。
    """
    return _set_config_field("agent", "authorization", level)


def get_api_key() -> str:
    """
    获取 API key（统一入口，优先从环境变量读，避免写入代码）
    """
    return os.environ.get("AGENT_LLM_API_KEY") or load_config()["llm"]["api_key"] or ""