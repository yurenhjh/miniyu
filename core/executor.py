"""
executor.py
第4组：多步执行引擎（计划 + 确认门 + 状态传递）

把“一句话任务”拆成的计划（Plan）顺序执行：
    - 每步之间用 $ref 引用上一步结果，实现跨步骤状态传递；
    - 命中 high 风险、或步骤显式标记 confirm=True 时，先预览并征求用户许可；
    - 任一一步失败即停在半路，返回已执行轨迹，不盲目继续。
"""

from core import safety


class PlanStep:
    """计划中的一步"""

    def __init__(self, step_id, kind, name, params=None, describe="",
                 depends_on=(), confirm=False):
        if kind not in ("tool", "skill"):
            raise ValueError(f"kind 必须是 tool 或 skill，得到 {kind}")
        self.id = step_id
        self.kind = kind
        self.name = name
        self.params = params or {}
        self.describe = describe
        self.depends_on = list(depends_on)
        self.confirm = confirm


class Plan:
    """一个多步执行计划（有序步骤列表）"""

    def __init__(self, steps=None):
        self.steps = steps or []

    def add(self, step_id, kind, name, params=None, describe="",
            depends_on=(), confirm=False):
        self.steps.append(PlanStep(
            step_id=step_id, kind=kind, name=name, params=params,
            describe=describe, depends_on=depends_on, confirm=confirm,
        ))
        return self

    def to_dict(self):
        return [
            {
                "id": s.id, "kind": s.kind, "name": s.name,
                "params": s.params, "describe": s.describe,
                "depends_on": s.depends_on, "confirm": s.confirm,
            }
            for s in self.steps
        ]


class ExecutionEngine:
    """按计划执行工具/技能，内置确认门与状态传递"""

    def __init__(self, api):
        # api 为 OSServiceAPI（或任意带 execute_tool / run_skill 的对象）
        self.api = api
        self._confirm_handler = None

    def set_confirm_handler(self, fn):
        self._confirm_handler = fn

    def execute(self, plan, confirm_handler=None, warning=None):
        """
        执行计划。

        参数：
            plan:            Plan 对象
            confirm_handler: 确认回调 fn(preview) -> bool；拒绝时抛 ConfirmationDenied
            warning:         可选的低置信度提醒文案；存在时先征求一次总许可再执行

        返回：
            {"success", "results", "context"}
            失败时额外携带 error / error_code / failed_step
        """
        handler = confirm_handler or self._confirm_handler
        context = {}
        trace = []

        # 低置信度任务：先提醒，取得许可后才继续（仍尽力执行）
        if warning:
            gate = {
                "kind": "plan", "name": "整体计划", "risk": safety.MEDIUM,
                "category": "低置信度", "description": warning,
                "requires_confirmation": True, "params": {}, "describe": warning,
            }
            if not self._ask(handler, gate, allow_missing=False):
                return {
                    "success": False,
                    "error": "用户因低置信度提醒而婉拒执行",
                    "error_code": "CONFIRMATION_DENIED",
                    "results": trace,
                }

        for step in plan.steps:
            params = self._resolve(step.params, context)
            meta = safety.get_meta(step.kind, step.name)

            need_confirm = step.confirm or safety.requires_confirmation(meta["risk"])
            if need_confirm:
                pv = safety.preview(step.kind, step.name, params, meta)
                pv["describe"] = step.describe or pv["description"]
                # 需确认的步骤在未配置确认处理器时默认拒绝（fail-safe）
                if not self._ask(handler, pv, allow_missing=False):
                    trace.append(self._denied(step))
                    return {
                        "success": False,
                        "error": f"用户拒绝执行步骤「{step.describe or step.name}」",
                        "error_code": "CONFIRMATION_DENIED",
                        "failed_step": step.id,
                        "results": trace,
                    }

            if step.kind == "tool":
                result = self.api.execute_tool(step.name, params)
            else:
                result = self.api.run_skill(step.name, params)

            context[step.id] = result
            trace.append({
                "step": step.id,
                "describe": step.describe,
                "confirmed": need_confirm,
                "success": result.get("success", False),
                "result": result.get("result"),
                "error": result.get("error"),
                "error_code": result.get("error_code"),
            })

            if not result.get("success"):
                return {
                    "success": False,
                    "error": result.get("error"),
                    "error_code": result.get("error_code", "EXEC_ERROR"),
                    "failed_step": step.id,
                    "results": trace,
                }

        return {"success": True, "results": trace, "context": context}

    # -----------------------------------------------------
    # 私有
    # -----------------------------------------------------

    @staticmethod
    def _denied(step):
        return {
            "step": step.id, "describe": step.describe,
            "confirmed": True, "success": False,
            "error": "用户拒绝执行", "error_code": "CONFIRMATION_DENIED",
        }

    @staticmethod
    def _ask(handler, pv, allow_missing):
        """执行确认门，返回是否放行"""
        if handler is None:
            # 未配置确认处理器：放行（由上层在真实运行时注入处理器）
            return allow_missing
        approved = handler(pv)
        return bool(approved)

    def _resolve(self, params, context):
        """解析参数，支持 $ref:step_id.key.subkey 引用前序结果"""
        if not isinstance(params, dict):
            return params
        resolved = {}
        for key, value in params.items():
            resolved[key] = self._resolve_value(value, context)
        return resolved

    def _resolve_value(self, value, context):
        if isinstance(value, dict):
            return self._resolve(value, context)
        if isinstance(value, list):
            return [self._resolve_value(v, context) for v in value]
        if isinstance(value, str) and value.startswith("$ref:"):
            return self._lookup(value[len("$ref:"):].strip(), context)
        return value

    @staticmethod
    def _lookup(ref, context):
        cur = context
        for key in ref.split("."):
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                raise KeyError(f"无法解析引用 $ref:{ref}")
        return cur