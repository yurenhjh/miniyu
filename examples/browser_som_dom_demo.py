"""真实 Chrome 端到端冒烟：验证改后助手浏览器定位链路（Level1 find/ref → SoM）。
不依赖 LLM，直接走 ToolRegistry 工具层（Agent 实际调用的同一条工具面）。
"""
import json
import traceback
import sys
from core.tool_registry import ToolRegistry

URL = "https://www.bing.com"


def log(title, obj):
    print(f"\n=== {title} ===")
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str)[:2000])


def main():
    r = ToolRegistry()
    browser = None
    try:
        log("1.launch", r.register_calls_map if hasattr(r, "register_calls_map") else "start")
        url = r.browser_launch(headless=True, port=9222)
        print("launched ws:", url)

        nav = r.browser_navigate(URL)
        log("2.navigate", nav)

        # 等搜索框就绪
        try:
            w = r.browser_wait(selector="#sb_form_q", timeout=10)
            log("2b.wait", w)
        except Exception as e:
            print("wait warn:", e)

        # Level1：browser_find 按角色找输入框 → 拿稳定 ref
        hits = r.browser_find(text="", role="textbox", max_results=8)
        log("3.find_textbox", hits)
        # 优先 id 含 sb_form_q 或视口内的输入框
        tb_ref = next((h["ref"] for h in hits if "sb_form_q" in h.get("ref", "")), None) \
            or next((h["ref"] for h in hits if h.get("in_viewport")), None) \
            or (hits[0]["ref"] if hits else None)
        print("textbox ref:", tb_ref)
        if not tb_ref:
            raise SystemError("未找到搜索输入框")

        # 用 ref 输入中文查询
        typed = r.browser_type("湖南大学", target=tb_ref, press_enter=True)
        log("4.type", typed)

        # 等结果出现
        r.browser_wait(text="湖南大学", timeout=10)

        # Level1：browser_find 找"搜索"按钮，按 ref 点击（若页面还保留）
        find_btn = r.browser_find(text="搜索", max_results=8)
        log("5.find_btn", find_btn)
        if find_btn:
            click1 = r.browser_click(target=find_btn[0]["ref"])
            log("5b.click_btn_by_ref", click1)

        # Level3 兜底演示——SoM：browser_inspect 截带编号图
        insp = r.browser_inspect(max_elements=30)
        log("6.inspect", {k: v for k, v in insp.items() if k != "elements"})
        print("  elements:", json.dumps(insp["elements"][:6], ensure_ascii=False))
        print("  image:", insp["image_path"])

        # 用 som:1 点击验证 num→ref→真实鼠标
        if insp["elements"]:
            som_num = insp["elements"][0]["num"]
            som_ref = insp["elements"][0]["ref"]
            log("7.som_first", {"num": som_num, "ref": som_ref})
            try:
                r2 = r.browser_click(target=f"som:{som_num}")
                log("7b.som_click", r2)
            except Exception as e:
                print("som click warn:", type(e).__name__, e)

        # 状态仍在 → 确认 SoM 会话仍有效
        print("\nsom_session_valid:", r.browser.som_session_valid())
        return 0
    except Exception as e:
        traceback.print_exc()
        return 1
    finally:
        try:
            r.browser_close()
            print("\nbrowser closed")
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())