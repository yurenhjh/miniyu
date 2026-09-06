"""
os_service_api.py
第4组：对外统一服务接口（OSServiceAPI）
为上层 Agent（第3组）提供统一的 Tool 和 Skill 调用入口
"""

from core.tool_registry import ToolRegistry
from core.skill_library import SkillLibrary
from core.executor import ExecutionEngine
from core import classifier


class OSServiceAPI:
    """
    第4组对外统一接口

    使用方式：
        api = OSServiceAPI()
        api.execute_tool("copy_file", {"src": "a.txt", "dest": "b.txt"})
        api.run_skill("system_info")
    """

    def __init__(self):
        self.registry = ToolRegistry()
        self.skills = SkillLibrary()
        self._executor = None

    # =====================================================
    # Tool 接口
    # =====================================================

    def execute_tool(self, tool_name, params=None):
        """
        调用系统工具

        参数：
            tool_name: 工具名称
            params: 参数字典

        返回：
            {"success": True, "tool": name, "result": ...}
            或 {"success": False, "tool": name, "error": "..."}
        """
        return self.registry.call(tool_name, params or {})

    def list_available_tools(self):
        """列出所有可用工具"""
        return self.registry.list_tools()

    def get_tool_stats(self):
        """获取工具调用统计"""
        return self.registry.get_stats()

    # =====================================================
    # Tool Schema / MCP 兼容接口
    # =====================================================

    def get_tool_spec(self, name: str) -> dict:
        """获取指定工具的完整描述规范"""
        return self.registry.get_tool_spec(name)

    def list_tools_specs(self) -> list:
        """列出所有工具的描述规范（含参数/类别/风险）"""
        return self.registry.list_tools_specs()

    def list_tools_mcp(self) -> list:
        """
        按 MCP tools/list 格式返回工具列表。
        上层 Agent 可直接将返回值传递给 LLM 的 tool calling 能力。
        """
        return self.registry.list_tools_mcp()

    def list_tools_openai(self) -> list:
        """按 OpenAI Function Calling 格式返回工具列表"""
        return self.registry.list_tools_openai()

    def list_tools_anthropic(self) -> list:
        """按 Anthropic Tool Use 格式返回工具列表"""
        return self.registry.list_tools_anthropic()

    # =====================================================
    # 工具签名
    # =====================================================

    def get_tool_signature(self, name: str) -> dict:
        """获取工具的签名信息"""
        return self.registry.get_tool_signature(name)

    def list_tool_signatures(self) -> list:
        """列出所有工具的签名"""
        return self.registry.list_tool_signatures()

    def verify_tool_integrity(self, name: str) -> dict:
        """验证工具的完整性（签名是否匹配）"""
        return self.registry.verify_tool_integrity(name)

    # =====================================================
    # Skill 接口
    # =====================================================

    def run_skill(self, skill_name, params=None):
        """
        调用系统技能

        参数：
            skill_name: 技能名称
            params: 参数字典

        返回：
            {"success": True, "skill": name, "result": ...}
            或 {"success": False, "skill": name, "error": "..."}
        """
        return self.skills.call(skill_name, params or {})

    def list_available_skills(self):
        """列出所有可用技能"""
        return self.skills.list_skills()

    def openai_skill_names(self):
        """白名单内、可被 Agent/大模型直接调用的组合技能名（叠加在 registry 工具之上）"""
        return self.skills.openai_skill_names()

    def is_agent_skill(self, name):
        """name 是否是 Agent 可调的白名单组合技能"""
        return self.skills.is_agent_skill(name)

    def list_skills_openai(self):
        """白名单组合技能 → OpenAI Function Calling 格式（供 Agent 传给 LLM）"""
        return self.skills.list_openai_tools()

    # =====================================================
    # 安全 / 确认 / 计划 / 分类
    # =====================================================

    def set_confirm_handler(self, fn):
        """统一注入确认处理器（作用于工具与技能）"""
        self.registry.set_confirm_handler(fn)
        self.skills.set_confirm_handler(fn)
        return self

    def get_tool_meta(self, name):
        return self.registry.get_tool_meta(name)

    def list_tools_meta(self):
        return self.registry.list_tools_meta()

    def get_skill_meta(self, name):
        return self.skills.get_skill_meta(name)

    def list_skills_meta(self):
        return self.skills.list_skills_meta()

    def call_tool_safely(self, tool_name, params=None, confirm_handler=None):
        """带确认门的工具调用（不可逆动作执行前征求许可）"""
        return self.registry.call_safely(tool_name, params, confirm_handler)

    def run_skill_safely(self, skill_name, params=None, confirm_handler=None):
        """带确认门的技能调用（不可逆动作执行前征求许可）"""
        return self.skills.run_skill_safely(skill_name, params, confirm_handler)

    def execute_plan(self, plan, confirm_handler=None, warning=None):
        """执行多步计划；可选低置信度提醒 + 每步确认"""
        if self._executor is None:
            self._executor = ExecutionEngine(self)
        return self._executor.execute(
            plan, confirm_handler=confirm_handler, warning=warning)

    def classify_task(self, text):
        """对自然语言任务分类：auto / confirm / warn"""
        return classifier.classify(text)
