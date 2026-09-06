"""
Mock 模块初始化文件（第4组）
用于提供 ToolRegistry / SkillLibrary 的模拟实现
支持跨组联调（第1~3组）
"""

from .mock_tools import MockToolRegistry
from .mock_skills import MockSkillLibrary


# =========================
# 对外统一Mock入口
# =========================
class MockOSServiceAPI:
    """
    Mock版 OS 服务接口
    用于系统联调阶段（不依赖真实系统）
    """

    def __init__(self):
        self.registry = MockToolRegistry()
        self.skills = MockSkillLibrary()

    # -------------------------
    # 工具调用 Mock
    # -------------------------
    def execute_tool(self, tool_name: str, params: dict = None):
        params = params or {}
        return self.registry.call(tool_name, params)

    # -------------------------
    # 技能调用 Mock
    # -------------------------
    def run_skill(self, skill_name: str, params: dict = None):
        params = params or {}

        if not hasattr(self.skills, skill_name):
            return {
                "success": False,
                "error": f"Skill不存在: {skill_name}"
            }

        func = getattr(self.skills, skill_name)
        result = func(**params)

        return {
            "success": True,
            "skill": skill_name,
            "result": result
        }

    # -------------------------
    # Agent 白名单技能（对齐正式版 OSServiceAPI 接口）
    # -------------------------
    def openai_skill_names(self):
        return self.skills.openai_skill_names()

    def is_agent_skill(self, name):
        return self.skills.is_agent_skill(name)

    def list_skills_openai(self):
        return self.skills.list_openai_tools()


# =========================
# 默认导出（方便import）
# =========================
__all__ = [
    "MockToolRegistry",
    "MockSkillLibrary",
    "MockOSServiceAPI"
]
