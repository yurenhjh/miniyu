"""
test_email.py
第4组：邮件全链路 send_email —— 协议层(email_client) + 技能编排 + Agent 分发

覆盖（本文件不真联网、不真发信）：
  1) 协议层 fail-closed：email 段没配好 / 收件人主题为空 → MailError 指引，绝不连网发送；
  2) SMTP 发送：_connect_smtp 缝隙注入假连接，断言 login/头字段(Message-ID/To/Subject)/
     quit 调用与返回结构；网络错误包成 MailError；
  3) IMAP 回读逻辑：_pick_sent_folder 认 QQ modified-UTF-7『&XfJT0ZAB-』/中文『已发送』/
     'Sent'，找不到明确报错；_folder_search_ids 按 Message-ID(大小写不敏感)/主题命中，
     未命中返回 False；
  4) 技能编排：发送成功→IMAP 回读『已发送』→sent_verified 如实 True/False；
     回读异常如实记录 verify_error 不假装成功；配了 verify_inbox 才额外核验到达；
     没配好授权码在『发之前』就报错（fail-closed）；
  5) Agent 分发：send_email 走技能库、HIGH 风险走确认门（拒绝绝不调用）；
  6) Mock 对等：mock_skills 含 send_email 且形状一致。
"""

import tempfile
import unittest
from unittest.mock import patch

from core import email_client as ec
from core.agent import Agent
from core.os_service_api import OSServiceAPI
from core.safety import ConfirmationDenied, HIGH
from core.skill_library import SkillLibrary

# 一套配好了发件侧凭据的 email 段（不含 verify_inbox）
CFG_CREDS = {
    "smtp_host": "smtp.qq.com",
    "smtp_port": 465,
    "smtp_ssl": True,
    "imap_host": "imap.qq.com",
    "imap_port": 993,
    "imap_ssl": True,
    "username": "me@qq.com",
    "auth_code": "secret-code",
    "timeout": 20,
}
# 配了收件侧 verify_inbox 的 email 段（双端闭环）
CFG_CREDS_VERIFY = dict(CFG_CREDS)
CFG_CREDS_VERIFY["verify_inbox"] = {
    "imap_host": "imap.qq.com",
    "imap_port": 993,
    "imap_ssl": True,
    "username": "small@qq.com",
    "auth_code": "secret-2",
    "timeout": 20,
}


def _cfg(tmp, confirm=True):
    return {
        "llm": {"provider": "deterministic"},
        "agent": {"max_steps": 15, "confirm_high_risk": confirm, "history_window": 20},
        "memory": {"storage_dir": tmp},
    }


# ------------------------------------------------------------------
# 假连接（协议层测试用，绝不真联网）
# ------------------------------------------------------------------

class _FakeSmtp:
    def __init__(self):
        self.logged_in = (None, None)
        self.sent = None
        self.quited = False

    def login(self, user, code):
        self.logged_in = (user, code)

    def send_message(self, msg):
        self.sent = msg

    def quit(self):
        self.quited = True

    def close(self):
        pass


class _FakeListImap:
    """list() 返回给定文件夹清单，供 _pick_sent_folder 解析"""

    def __init__(self, rows):
        self._rows = rows

    def list(self):
        return ("OK", self._rows)


def _list_row(folder_name):
    """IMAP LIST 行（bytes）。中文文件夹名必须 encode 成 utf-8，不能写进 bytes 字面量。"""
    return ('(\\HasNoChildren) "/" "%s"' % folder_name).encode("utf-8")


class _HeaderImap:
    """select/search/fetch 返回固定头部，供 _folder_search_ids 解析"""

    def __init__(self, headers):
        # headers: {id_bytes: "Message-ID: ...\r\nSubject: ...\r\n\r\n"}
        self.headers = headers
        self.selected = None

    def select(self, folder, readonly=False):
        self.selected = folder
        return ("OK", [b"1"])

    def search(self, charset, criterion):
        ids = b" ".join(self.headers.keys())
        return ("OK", [ids])

    def fetch(self, i, fields):
        hdr = self.headers.get(i, b"")
        if isinstance(hdr, str):
            hdr = hdr.encode("utf-8")
        return ("OK", [(i, hdr)])


class _CaptureAPI(OSServiceAPI):
    """捕获 run_skill / execute_tool，绝不真执行（防误发真实邮件）"""

    def __init__(self):
        super().__init__()
        self.skill_calls = []
        self.tool_calls = []

    def run_skill(self, name, params=None):
        self.skill_calls.append((name, params or {}))
        return {"success": True, "skill": name, "result": {"ok": 1}}

    def execute_tool(self, name, params=None):
        self.tool_calls.append((name, params or {}))
        return {"success": True, "tool": name, "result": {"ok": 1}}


# ------------------------------------------------------------------
# 协议层：fail-closed + SMTP
# ------------------------------------------------------------------

class TestEmailProtocolFailClosed(unittest.TestCase):
    """没配好(email 段空 / 缺收件人主题) → MailError 指引，绝不在没配好时报成功或连网"""

    def test_smtp_send_missing_creds_fails_closed(self):
        with patch.object(ec, "_connect_smtp") as connect:
            with self.assertRaises(ec.MailError) as ctx:
                ec.smtp_send("to@qq.com", "主题", "正文", email_cfg={})
            connect.assert_not_called()          # 发之前就拦下，不碰网络
            self.assertIn("未配好", str(ctx.exception))
            self.assertIn("auth_code", str(ctx.exception))

    def test_smtp_send_empty_to_or_subject_fails(self):
        with self.assertRaises(ec.MailError):
            ec.smtp_send("", "主题", "正文", email_cfg=CFG_CREDS)
        with self.assertRaises(ec.MailError):
            ec.smtp_send("to@qq.com", "  ", "正文", email_cfg=CFG_CREDS)

    def test_imap_verify_sent_missing_creds_fails_closed(self):
        with self.assertRaises(ec.MailError) as ctx:
            ec.imap_verify_sent("<x@miniyu.local>", subject="主题", email_cfg={})
        self.assertIn("IMAP 回读需要 username", str(ctx.exception))

    def test_imap_verify_arrival_requires_verify_inbox(self):
        with self.assertRaises(ec.MailError) as ctx:
            ec.imap_verify_arrival("<x@miniyu.local>", subject="主题", verify_cfg={})
        self.assertIn("verify_inbox", str(ctx.exception))


class TestEmailProtocolSmtp(unittest.TestCase):
    """SMTP 发送成功路径：头字段注入 + 调用顺序 + 返回结构"""

    def test_smtp_send_ok_injects_headers_and_logs_in(self):
        fake = _FakeSmtp()
        with patch.object(ec, "_connect_smtp", return_value=fake) as connect:
            r = ec.smtp_send(
                "to@qq.com", "Hello", "正文 hi", cc="cc@qq.com",
                msg_id="<mine-1@miniyu.local>", email_cfg=CFG_CREDS)
        connect.assert_called_once_with("smtp.qq.com", 465, True, 20.0)
        self.assertEqual(fake.logged_in, ("me@qq.com", "secret-code"))
        self.assertTrue(fake.quited)
        msg = fake.sent
        self.assertEqual(msg["Message-ID"], "<mine-1@miniyu.local>")
        self.assertEqual(msg["To"], "to@qq.com")
        self.assertEqual(msg["Cc"], "cc@qq.com")
        self.assertEqual(msg["Subject"], "Hello")
        self.assertIn("me@qq.com", str(msg["From"]))
        self.assertEqual(r["message_id"], "<mine-1@miniyu.local>")
        self.assertTrue(r["smtp_accepted"])

    def test_smtp_send_generates_message_id_when_omitted(self):
        fake = _FakeSmtp()
        with patch.object(ec, "_connect_smtp", return_value=fake):
            r = ec.smtp_send("to@qq.com", "主题", "正文", email_cfg=CFG_CREDS)
        self.assertIn("@", r["message_id"])
        self.assertEqual(fake.sent["Message-ID"], r["message_id"])

    def test_smtp_network_error_wrapped_as_mail_error(self):
        with patch.object(ec, "_connect_smtp", side_effect=OSError("conn refused")):
            with self.assertRaises(ec.MailError) as ctx:
                ec.smtp_send("to@qq.com", "主题", "正文", email_cfg=CFG_CREDS)
        self.assertIn("SMTP 发送失败", str(ctx.exception))
        self.assertIn("smtp.qq.com", str(ctx.exception))


# ------------------------------------------------------------------
# 协议层：IMAP 回读逻辑（文件夹挑选 + 头部命中，不轮询不联网）
# ------------------------------------------------------------------

class TestEmailProtocolImap(unittest.TestCase):
    def test_pick_sent_folder_qq_modified_utf7(self):
        conn = _FakeListImap([
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "&XfJT0ZAB-"',     # QQ 的『已发送』
        ])
        self.assertEqual(ec._pick_sent_folder(conn), "&XfJT0ZAB-")

    def test_pick_sent_folder_chinese_or_sent(self):
        conn = _FakeListImap([
            _list_row("收件箱"),
            _list_row("已发送"),
        ])
        self.assertEqual(ec._pick_sent_folder(conn), "已发送")
        conn2 = _FakeListImap([_list_row("Sent")])
        self.assertEqual(ec._pick_sent_folder(conn2), "Sent")

    def test_pick_sent_folder_missing_raises(self):
        conn = _FakeListImap([
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "Drafts"',
        ])
        with self.assertRaises(ec.MailError) as ctx:
            ec._pick_sent_folder(conn)
        self.assertIn("已发送", str(ctx.exception))

    def test_folder_search_message_id_case_insensitive(self):
        conn = _HeaderImap({
            b"1": "Message-ID: <ABC-1@miniyu.local>\r\nSubject: Quarterly\r\n\r\n",
            b"2": "Message-ID: <other-2@miniyu.local>\r\nSubject: None\r\n\r\n",
        })
        self.assertTrue(ec._folder_search_ids(
            conn, "INBOX", "<abc-1@miniyu.local>", None))

    def test_folder_search_subject_fallback_when_msgid_rewritten(self):
        conn = _HeaderImap({
            b"1": "Message-ID: <rewritten@server.local>\r\nSubject: Quarterly report 2026\r\n\r\n",
        })
        self.assertTrue(ec._folder_search_ids(
            conn, "INBOX", "<orig@miniyu.local>", "Quarterly report"))

    def test_folder_search_not_found(self):
        conn = _HeaderImap({
            b"1": "Message-ID: <a@miniyu.local>\r\nSubject: Alpha\r\n\r\n",
        })
        self.assertFalse(ec._folder_search_ids(
            conn, "INBOX", "<never@miniyu.local>", "Zzz"))

    def test_folder_search_select_quotes_spaced_folder(self):
        # QQ 发件箱叫 'Sent Messages'：不带引号 EXAMINE 会 BAD，必须按 RFC3501 加引号
        conn = _HeaderImap({
            b"1": "Message-ID: <a@miniyu.local>\r\nSubject: Alpha\r\n\r\n",
        })
        self.assertTrue(ec._folder_search_ids(
            conn, "Sent Messages", "<a@miniyu.local>", None))
        self.assertEqual(conn.selected, '"Sent Messages"')

    def test_folder_search_rfc2047_encoded_subject_matches(self):
        # 服务商改写 Message-ID（如 QQ→tencent_xxx@qq.com）+ 中文主题存为 RFC2047 B 编码
        import base64
        raw_subject = base64.b64encode("来自小余人的测试信".encode("utf-8")).decode("ascii")
        conn = _HeaderImap({
            b"1": ("Message-ID: <tencent_52059BAB1781178CAA63FA588370C8BD930A@qq.com>\r\n"
                   "Subject: =?utf-8?B?%s?=\r\n\r\n" % raw_subject).encode("utf-8"),
        })
        self.assertTrue(ec._folder_search_ids(
            conn, "INBOX", "<178869916241.35044@miniyu.local>", "来自小余人的测试信"))


# ------------------------------------------------------------------
# 技能层：编排（发 → 回读 → 如实上报；fail-closed）
# ------------------------------------------------------------------

class TestEmailSkillOrchestration(unittest.TestCase):
    """patch core.email_client 的模块级 seam，不真联网"""

    def setUp(self):
        self.skills = SkillLibrary()
        self.params = {"to": "to@qq.com", "subject": "你好", "body": "小余人"}

    def test_send_then_verify_sent_ok(self):
        sent = {"message_id": "<s1@miniyu.local>", "to": "to@qq.com", "cc": "",
                "subject": "你好", "smtp_accepted": True}
        with patch.object(ec, "email_section", return_value=dict(CFG_CREDS)), \
                patch.object(ec, "smtp_send", return_value=sent) as sm, \
                patch.object(ec, "imap_verify_sent",
                             return_value={"folder": "已发送", "found": True,
                                          "poll_seconds": 0.2, "by": "message-id"}) as vf:
            r = self.skills.call("send_email", dict(self.params))
        self.assertTrue(r["success"])
        res = r["result"]
        sm.assert_called_once()
        vf.assert_called_once()
        self.assertEqual(res["message_id"], "<s1@miniyu.local>")
        self.assertIs(res["sent_verified"], True)
        self.assertEqual(res["sent_verified_folder"], "已发送")
        self.assertIsNone(res["delivery_verified"])     # 没配 verify_inbox → 不核验到达

    def test_verify_not_found_reports_false_not_success(self):
        sent = {"message_id": "<s2@miniyu.local>", "to": "to@qq.com", "cc": "",
                "subject": "你好", "smtp_accepted": True}
        with patch.object(ec, "email_section", return_value=dict(CFG_CREDS)), \
                patch.object(ec, "smtp_send", return_value=sent), \
                patch.object(ec, "imap_verify_sent",
                             return_value={"folder": "已发送", "found": False,
                                          "poll_seconds": 20.0, "by": "message-id"}):
            r = self.skills.call("send_email", dict(self.params))
        self.assertTrue(r["success"])                    # 发成功算成功
        self.assertIs(r["result"]["sent_verified"], False)  # 但回读没验到 → 如实 False
        self.assertNotIn("verify_error", r["result"])    # 不是异常，只是没找到

    def test_imap_verify_exception_recorded_not_silent(self):
        sent = {"message_id": "<s3@miniyu.local>", "to": "to@qq.com", "cc": "",
                "subject": "你好", "smtp_accepted": True}
        with patch.object(ec, "email_section", return_value=dict(CFG_CREDS)), \
                patch.object(ec, "smtp_send", return_value=sent), \
                patch.object(ec, "imap_verify_sent",
                             side_effect=ec.MailError("IMAP 连接超时")):
            r = self.skills.call("send_email", dict(self.params))
        self.assertTrue(r["success"])
        self.assertIs(r["result"]["sent_verified"], False)
        self.assertIn("回读", r["result"]["verify_error"])

    def test_delivery_verified_when_verify_inbox_configured(self):
        sent = {"message_id": "<s4@miniyu.local>", "to": "to@qq.com", "cc": "",
                "subject": "你好", "smtp_accepted": True}
        with patch.object(ec, "email_section",
                          return_value=dict(CFG_CREDS_VERIFY)), \
                patch.object(ec, "smtp_send", return_value=sent), \
                patch.object(ec, "imap_verify_sent",
                             return_value={"folder": "已发送", "found": True,
                                          "poll_seconds": 0.2, "by": "message-id"}), \
                patch.object(ec, "imap_verify_arrival",
                             return_value={"folder": "INBOX", "found": True,
                                          "poll_seconds": 1.0, "by": "message-id"}) as av:
            r = self.skills.call("send_email", dict(self.params))
        self.assertTrue(r["success"])
        self.assertIs(r["result"]["sent_verified"], True)
        self.assertIs(r["result"]["delivery_verified"], True)
        av.assert_called_once()                          # 双端闭环确实额外核验了收件箱

    def test_no_creds_fails_closed_before_send(self):
        """email 段空 → 发之前抛指引（走真实 smtp_send，靠 _require_creds 拦下）"""
        with patch.object(ec, "email_section", return_value={}), \
                patch.object(ec, "_connect_smtp") as connect:
            r = self.skills.call("send_email", dict(self.params))
        connect.assert_not_called()                      # 绝没去连 SMTP
        self.assertFalse(r["success"])
        self.assertEqual(r["error_code"], "SKILL_ERROR")
        self.assertIn("email 段未配好", r["error"])


# ------------------------------------------------------------------
# Agent 分发：白名单技能 + HIGH 确认门
# ------------------------------------------------------------------

class TestEmailSkillAgentDispatch(unittest.TestCase):
    def _agent(self, tmp, api, confirm):
        return Agent(config=_cfg(tmp, confirm=confirm), api=api)

    def test_skill_goes_run_skill(self):
        with tempfile.TemporaryDirectory(prefix="mini_email_") as tmp:
            api = _CaptureAPI()
            agent = self._agent(tmp, api, confirm=False)
            r = agent._execute_one(
                "send_email", {"to": "to@qq.com", "subject": "你好", "body": "小余人"})
        self.assertTrue(r["success"])
        self.assertEqual(api.skill_calls[0][0], "send_email")
        self.assertEqual(api.skill_calls[0][1]["to"], "to@qq.com")

    def test_email_meta_is_high_risk(self):
        self.assertEqual(SkillLibrary().get_skill_meta("send_email")["risk"], HIGH)

    def test_high_risk_email_denied(self):
        def deny(_pv):
            raise ConfirmationDenied("send_email")

        with tempfile.TemporaryDirectory(prefix="mini_email_") as tmp:
            api = _CaptureAPI()
            agent = self._agent(tmp, api, confirm=True)
            agent.confirm_handler = deny
            r = agent._execute_one(
                "send_email", {"to": "to@qq.com", "subject": "你好", "body": "小余人"})
        self.assertFalse(r["success"])
        self.assertIn("拒绝", r["error"])
        self.assertEqual(api.skill_calls, [])            # 确认被拒 → 绝不发

    def test_high_risk_email_allowed_runs(self):
        with tempfile.TemporaryDirectory(prefix="mini_email_") as tmp:
            api = _CaptureAPI()
            agent = self._agent(tmp, api, confirm=True)
            agent.confirm_handler = lambda _pv: True      # 用户放行
            r = agent._execute_one(
                "send_email", {"to": "to@qq.com", "subject": "你好", "body": "小余人"})
        self.assertTrue(r["success"])
        self.assertEqual(api.skill_calls[0][0], "send_email")


# ------------------------------------------------------------------
# Mock 对等
# ------------------------------------------------------------------

class TestMockEmailParity(unittest.TestCase):
    def test_mock_has_send_email(self):
        from mock.mock_skills import MockSkillLibrary
        m = MockSkillLibrary()
        self.assertIn("send_email", m.list_skills())
        names = m.openai_skill_names()
        self.assertEqual(names, ["app_send_message", "send_email"])

    def test_mock_send_email_result_shape(self):
        from mock.mock_skills import MockSkillLibrary
        m = MockSkillLibrary()
        r = m.send_email("to@qq.com", "主题", "正文")
        self.assertTrue(r["smtp_accepted"])
        self.assertIs(r["sent_verified"], True)
        self.assertEqual(r["to"], "to@qq.com")

    def test_mock_openai_tools_include_send_email(self):
        from mock.mock_skills import MockSkillLibrary
        tools = MockSkillLibrary().list_openai_tools()
        by_name = {t["function"]["name"] for t in tools}
        self.assertIn("send_email", by_name)
        fn = [t["function"] for t in tools
              if t["function"]["name"] == "send_email"][0]
        self.assertEqual(fn["parameters"]["required"], ["to", "subject", "body"])


if __name__ == "__main__":
    unittest.main()
