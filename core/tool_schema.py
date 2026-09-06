"""
tool_schema.py
第4组：工具描述 Schema 定义
提供统一的工具元数据模型，支持自动生成 JSON Schema，
并转换为 MCP / OpenAI / Anthropic 等格式。

设计参考：
  - MCP 协议 tools/list 规范 (inputSchema)
  - OpenAI Function Calling 格式 (function.parameters)
  - Anthropic Tool Use 格式 (input_schema)
  - LangChain pydantic 驱动的 schema 自动生成
"""

import inspect
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional


# JSON Schema 类型映射
PYTHON_TO_JSON_TYPE = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
    type(None): "null",
}


def _py_type_to_json(py_type) -> str:
    """将 Python 类型映射为 JSON Schema 类型字符串"""
    if py_type in PYTHON_TO_JSON_TYPE:
        return PYTHON_TO_JSON_TYPE[py_type]
    origin = getattr(py_type, "__origin__", None)
    if origin is list:
        return "array"
    if origin is dict:
        return "object"
    return "string"


def _has_default(param: inspect.Parameter) -> bool:
    return param.default != inspect.Parameter.empty


def _get_default(param: inspect.Parameter):
    return param.default if _has_default(param) else None


@dataclass
class ParameterDef:
    """单个参数的定义"""
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None

    def to_json_schema_prop(self) -> dict:
        """转换为 JSON Schema 属性定义"""
        schema = {"type": self.type}
        if self.description:
            schema["description"] = self.description
        if not self.required:
            schema["default"] = self.default
        return schema


@dataclass
class ToolSpec:
    """
    工具描述规范

    包含工具的名称、描述、参数定义、风险等级、类别等元信息。
    支持一键转换为 MCP / OpenAI / Anthropic 的 tool 格式。
    """
    name: str
    description: str = ""
    parameters: list = field(default_factory=list)  # list[ParameterDef]
    category: str = "通用"
    risk: str = "low"
    fn_signature: str = ""  # 人类可读的函数签名

    def to_mcp_format(self) -> dict:
        """转换为 MCP tools/list 格式"""
        properties = {}
        required = []
        for p in self.parameters:
            properties[p.name] = p.to_json_schema_prop()
            if p.required:
                required.append(p.name)

        result = {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
            },
        }
        if required:
            result["inputSchema"]["required"] = required
        return result

    def to_openai_format(self) -> dict:
        """转换为 OpenAI Function Calling 格式"""
        mcp = self.to_mcp_format()
        return {
            "type": "function",
            "function": {
                "name": mcp["name"],
                "description": mcp["description"],
                "parameters": mcp["inputSchema"],
            },
        }

    def to_anthropic_format(self) -> dict:
        """转换为 Anthropic Tool Use 格式"""
        mcp = self.to_mcp_format()
        return {
            "name": mcp["name"],
            "description": mcp["description"],
            "input_schema": mcp["inputSchema"],
        }

    def to_dict(self) -> dict:
        """序列化为普通字典"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type,
                    "description": p.description,
                    "required": p.required,
                    "default": p.default,
                }
                for p in self.parameters
            ],
            "category": self.category,
            "risk": self.risk,
            "fn_signature": self.fn_signature,
        }


def _parse_docstring_params(docstring: str) -> dict[str, str]:
    """
    从 docstring 中提取参数描述。

    支持两种格式：
    1. 参数：xxx — 描述
    2.  Args: 参数名: 描述
    """
    if not docstring:
        return {}
    param_descs = {}
    lines = docstring.split("\n")
    in_params = False
    for line in lines:
        stripped = line.strip()
        # 匹配 "参数：xxx" 或 "Args:" 标记
        if stripped.startswith("参数：") or stripped.startswith("参数:") or stripped.startswith("Args:"):
            in_params = True
            # 尝试从同一行提取
            rest = stripped.split("：", 1)[-1].split(":", 1)[-1].strip()
            if rest:
                continue
        elif stripped.startswith("返回") or stripped.startswith("Returns"):
            in_params = False
        elif in_params and stripped:
            # 尝试匹配 "name: description" 或 "name — description"
            for sep in ("—", "—", ":", " "):
                if sep in stripped:
                    parts = stripped.split(sep, 1)
                    pname = parts[0].strip()
                    pdesc = parts[1].strip()
                    if pname and pdesc:
                        param_descs[pname] = pdesc
                    break
    return param_descs


def generate_spec_from_function(name: str, func: Callable) -> ToolSpec:
    """
    从 Python 函数自动生成 ToolSpec。

    通过 inspect 获取函数签名、类型注解、docstring，
    自动提取参数名、类型、默认值、描述。
    """
    try:
        sig = inspect.signature(func)
    except (ValueError, TypeError):
        # 无法获取签名（如 C 扩展函数），返回最小 spec
        return ToolSpec(name=name, description=name)

    doc = inspect.getdoc(func) or ""
    param_descs = _parse_docstring_params(doc)

    # 提取第一行作为简短描述
    first_line = doc.split("\n\n")[0].strip() if doc else name
    description = first_line.split("\n")[0] if "\n" in first_line else first_line

    # 生成参数列表
    parameters = []
    for pname, param in sig.parameters.items():
        if pname == "self":
            continue

        # 类型
        py_type = str
        if param.annotation != inspect.Parameter.empty:
            ann = param.annotation
            if hasattr(ann, "__origin__") and ann.__origin__ is list:
                py_type = list
            elif hasattr(ann, "__origin__") and ann.__origin__ is dict:
                py_type = dict
            elif isinstance(ann, type):
                py_type = ann
            else:
                py_type = str

        json_type = _py_type_to_json(py_type)
        has_default = _has_default(param)
        default = _get_default(param)

        # 如果 default 是 False，仍视为有默认值但 required=True 更合理
        # 当 default 是 False/0/"" 时，表示参数可选但默认值为假
        # 我们保留 required 语义：有默认值就是可选
        desc = param_descs.get(pname, "")

        parameters.append(ParameterDef(
            name=pname,
            type=json_type,
            description=desc,
            required=not has_default,
            default=default if has_default else None,
        ))

    # 构建人类可读的函数签名
    sig_str = f"{name}("
    sig_parts = []
    for p in parameters:
        opt = "?" if not p.required else ""
        sig_parts.append(f"{p.name}: {p.type}{opt}")
    sig_str += ", ".join(sig_parts) + ")"

    return ToolSpec(
        name=name,
        description=description,
        parameters=parameters,
        fn_signature=sig_str,
    )


def merge_spec_with_safety(spec: ToolSpec, safety_meta: dict) -> ToolSpec:
    """
    将 safety.py 中的元数据（风险、类别、描述）合并到 ToolSpec 中。
    safety 的描述更精准，优先使用。
    """
    if safety_meta.get("description"):
        spec.description = safety_meta["description"]
    if safety_meta.get("category"):
        spec.category = safety_meta["category"]
    if safety_meta.get("risk"):
        spec.risk = safety_meta["risk"]
    return spec