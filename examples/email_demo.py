"""
email_demo.py
第4组 · examples —— 邮件全链路演示（send_email：SMTP 发信 + IMAP 回读核验）

两种模式：
  默认（直驱技能）：
      python examples/email_demo.py \
          --to 3837227570@qq.com --subject "来自小余人的测试信" --body "你好呀，小余人"
      只依赖邮件授权码（config.yaml 的 email 段），不需要 LLM key。
  真实 LLM 驱动（--agent）：
      python examples/email_demo.py --agent --to ... --subject ... --body ...
      用 Agent(真实 qwen) 把一句中文指令转成一次 send_email function-call，
      需要 AGENT_LLM_API_KEY（或 config.yaml 的 llm.api_key）。

运行前置：
  1. config.yaml 的 email 段已填好发件账号与授权码（QQ 邮箱『设置→账户→开启 SMTP/IMAP
     服务』生成授权码；SMTP/IMAP 共用一把；入库前留空、填回后不要再 commit）。
  2. 收尾的"独立核验"默认用发件账号回读『已发送』；若还想核验『确实到达收件人』
     （双端闭环），在 email 段下再配 verify_inbox（收件侧邮箱的 IMAP）。

安全说明：
  - 发信是外发、不可撤回的高危动作。直驱模式走 run_skill_safely 的确认门，会先打印
    动作预告（收件人/主题/正文）再执行；本脚本被显式运行、参数就是你写好的内容，
    视为你已经确认 —— 请对 --to/--subject/--body 使用你真的想发的内容。
  - 密钥只从 config.yaml / 环境变量读，不打印授权码。
"""

import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent import Agent  # noqa: E402
from core.agent_config import load_config  # noqa: E402
from core.email_client import MailError, email_section, imap_verify_arrival, imap_verify_sent  # noqa: E402
from core.safety import ConfirmationDenied  # noqa: E402
from core.skill_library import SkillLibrary  # noqa: E402


def print_preview(sec, to, subject, body, cc=None):
    print()
    print("=" * 60)
    print("  send_email —— 邮件发送预告")
    print("=" * 60)
    print(f"  发件账号 : {sec.get('username') or '(未配)'}")
    print(f"  SMTP     : {sec.get('smtp_host')}:{sec.get('smtp_port')}")
    print(f"  收件人   : {to}")
    if cc:
        print(f"  抄送     : {cc}")
    print(f"  主题     : {subject}")
    print(f"  正文     : {body}")
    print(f"  发送后   : IMAP 回读核验（发件箱『已发送』"
          + (" + 收件人收件箱(双端)" if (sec.get("verify_inbox") or {}).get("username")
             else ""))
    print("=" * 60)


def confirm_handler(pv):
    """直驱模式确认门：打印预告即放行（脚本被显式运行 = 你已经确认了参数内容）"""
    print(f"  确认门放行：{pv.get('name')}  (风险 {pv.get('risk')})")
    return True


def _verify_and_report(sec, to, subject, msg_id=None, verify_inbox=True):
    """独立收尾核验：信技能/LLM 之前的结果，重新开 IMAP 连接回读一次，以实测为准。

    QQ 用授权码 SMTP 发信通常不会在发件箱『Sent Messages』留副本 → 『已发送』未命中
    不等于没发；真正能证明送达的是配了 verify_inbox 时的收件箱到达核验（双端闭环）。
    """
    print()
    print("----- 独立核验（重新连 IMAP 实测，不轻信上面的返回） -----")
    sent_ok = None
    # 1) 发件箱『已发送』回读（命中=强证据；QQ 等对授权码 SMTP 不留副本，可能未命中）
    try:
        sv = imap_verify_sent(msg_id=msg_id, subject=subject, email_cfg=sec,
                              timeout_s=30)
        sent_ok = bool(sv.get("found"))
        print(f"  [已发送] 在 {sv.get('folder')} "
              f"{'命中' if sent_ok else '未命中'}"
              f"（{sv.get('by')}，{sv.get('poll_seconds')}s）")
        if not sent_ok:
            print("          注：QQ 授权码 SMTP 常不留发件箱副本 → 未命中≠没发，"
                  "以收件箱到达核验为准")
    except MailError as e:
        print(f"  [已发送] 核验失败：{e}")

    # 2) 收件侧收件箱（双端闭环，可选）：配了 verify_inbox 才查"确实到达"——这才是铁证
    vb = (sec.get("verify_inbox") or {})
    arrival_ok = None
    if verify_inbox and (vb.get("username") or "").strip():
        try:
            av = imap_verify_arrival(msg_id=msg_id, subject=subject,
                                     verify_cfg=vb, timeout_s=40)
            arrival_ok = bool(av.get("found"))
            print(f"  [到达] 收件人收件箱 {vb.get('username')} "
                  f"{'命中' if arrival_ok else '未命中'}"
                  f"（{av.get('by')}，{av.get('poll_seconds')}s）")
        except MailError as e:
            print(f"  [到达] 核验失败：{e}")
    elif vb and not (vb.get("username") or "").strip():
        print("  [到达] 跳过：email.verify_inbox 未配收件侧账号")

    # 结论：双端闭环下以"确实到达收件箱"为准；没配 verify_inbox 才退回发件箱回读
    ok = arrival_ok if arrival_ok is not None else (sent_ok if sent_ok is not None else False)
    print("----- 独立核验结论：%s -----" % ("全部命中 ✓" if ok else "有未命中 ✗（如实报告）"))
    return ok


def run_direct(to, subject, body, cc, verify_inbox):
    """默认模式：直驱技能（只依赖邮件授权码）"""
    sec = email_section()
    if not (sec.get("username") or "").strip() or not (sec.get("auth_code") or "").strip():
        print("✗ config.yaml 的 email 段还没配好发件账号/授权码：")
        print("   email:\n"
              "     username:  <你的邮箱全址>\n"
              "     auth_code: <SMTP/IMAP 授权码，不是登录密码>\n"
              "   （QQ 邮箱『设置→账户→开启 SMTP/IMAP 服务』生成授权码）")
        return 2

    print_preview(sec, to, subject, body, cc=cc)
    skills = SkillLibrary()
    r = skills.run_skill_safely("send_email", {
        "to": to, "subject": subject, "body": body, "cc": cc or "", "verify": True,
    }, confirm_handler=confirm_handler)
    print()
    print("技能返回：")
    for k, v in r.items():
        if k == "result":
            for kk, vv in v.items():
                print(f"    {kk} = {vv}")
        else:
            print(f"  {k} = {v}")
    if not r.get("success"):
        print("✗ 发送未成功：", r.get("error"))
        return 1

    msg_id = (r.get("result") or {}).get("message_id")
    _verify_and_report(sec, to, subject, msg_id=msg_id, verify_inbox=verify_inbox)
    return 0


def run_agent(to, subject, body, cc):
    """--agent 模式：真实 LLM 把一句中文指令驱动成 send_email function-call"""
    instruction = (
        f"请调用 send_email 技能，给 {to} 发一封邮件："
        f"主题是『{subject}』，正文是『{body}』"
        + (f"，抄送 {cc}" if cc else "")
        + "。send_email 会自动回读核验，发完请用中文简短总结是否核验成功。"
    )
    print("指令（真实 LLM 驱动）：", instruction)
    print("----- Agent 回复 -----")
    config = load_config()
    agent = Agent(config=config, confirm_handler=lambda _pv: True)  # 演示：放行高危
    reply = agent.run(instruction)
    if isinstance(reply, dict):
        reply = reply.get("text") or reply.get("reply") or str(reply)
    print(reply)
    print("----- /Agent 回复 -----")
    # 独立收尾核验：按主题回读（msg_id 在 LLM 内部，外面拿不到 → 用主题兜底匹配）
    _verify_and_report(email_section(), None, subject, msg_id=None, verify_inbox=True)
    return 0


def main():
    ap = argparse.ArgumentParser(description="邮件全链路演示：send_email（SMTP+IMAP 回读核验）")
    ap.add_argument("--to", default="3837227570@qq.com",
                    help="收件人邮箱（默认：演示小号）")
    ap.add_argument("--subject", default="来自小余人的测试信", help="邮件主题")
    ap.add_argument("--body", default="你好呀，小余人", help="正文（纯文本）")
    ap.add_argument("--cc", default=None, help="可选抄送（逗号分隔）")
    ap.add_argument("--no-arrival-verify", action="store_true",
                    help="即使配了 verify_inbox 也不核验收件人收件箱")
    ap.add_argument("--agent", action="store_true",
                    help="用真实 LLM（Agent）驱动，而不是直驱技能（需要 LLM key）")
    args = ap.parse_args()

    if args.agent:
        return run_agent(args.to, args.subject, args.body, args.cc)
    return run_direct(args.to, args.subject, args.body, args.cc,
                      verify_inbox=not args.no_arrival_verify)


if __name__ == "__main__":
    raise SystemExit(main())
