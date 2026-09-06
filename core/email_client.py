"""
email_client.py —— 邮件协议层（纯标准库，零第三方依赖）
================================================================

给 miniyu 提供两块原子能力，供上层（skill_library.send_email）编排：

  1. smtp_send(...)           —— SMTP 发信
  2. imap_verify_sent(...)    —— IMAP 回读发件箱的"已发送"，核验这封信确实被发出并落库
  3. imap_verify_arrival(...) —— 可选"双端闭环"：用收件侧邮箱的 IMAP 轮询其收件箱，
                                核验邮件确实"到达"了收件人，而不是只证明"发出去"

设计要点：
  - 全部用 Python 标准库 smtplib / imaplib / email / ssl，不引入第三方依赖；
  - 连接/搜索被拆成 _connect_smtp / _connect_imap / _pick_sent_folder 等内部函数，
    单元测试无需真联网，patch 这些缝隙即可；
  - 配置来自 config.yaml 顶层 email 段（_EMAIL_SECTION）。授权码与 llm.api_key 同级
    敏感：只本地填 / env 注入，入库前留空、填回后不要再 commit；
  - fail-closed：email 段没配好（缺 username / auth_code）时抛 MailError 并给出中文
    指引，绝不假装"已发送"。
"""

import imaplib
import smtplib
import time
from email import policy as _policy
from email.message import EmailMessage
from email.parser import BytesParser as _BytesParser
from email.utils import formataddr, make_msgid
from typing import Optional

from core.agent_config import load_config

_EMAIL_SECTION = "email"

# IMAP 里"已发送"文件夹的常见名字（QQ 中文文件夹在协议层是 modified-UTF-7 的 &XfJT0ZAB-）
_SENT_FOLDER_ALIASES = ("已发送", "Sent", "Sent Items", "&XfJT0ZAB-", "[Gmail]/Sent Mail")


class MailError(RuntimeError):
    """邮件能力不可用/失败：配置缺失或 SMTP/IMAP 出错。提示用户而不是假装成功。"""


_GUIDE = (
    "发邮件需要先在 config.yaml 配好 email 段（授权码是敏感字段：本地填、不要 commit）：\n"
    "  email:\n"
    "    smtp_host: \"smtp.qq.com\"\n"
    "    smtp_port: 465            # QQ 邮箱走 SSL 465\n"
    "    smtp_ssl: true\n"
    "    imap_host: \"imap.qq.com\"\n"
    "    imap_port: 993\n"
    "    imap_ssl: true\n"
    "    username: \"<你的邮箱全址>\"\n"
    "    auth_code: \"<SMTP/IMAP 授权码，不是登录密码；QQ 邮箱在『设置→账户→开启 SMTP/IMAP 服务』"
    "生成，开服务后 SMTP 和 IMAP 共用这一把>\"\n"
    "  不同邮箱服务商的 host/port/是否 SSL 不同，请按需改（163：smtp.163.com / imap.163.com）。\n"
    "  要核验『确实到达收件人』（双端闭环）：在 email 段下加 verify_inbox 子块，填收件侧邮箱的 IMAP。\n"
    "没配好时本功能会明确报错，不会假装已发送。"
)


def email_section(config: Optional[dict] = None) -> dict:
    """取 config.yaml 顶层的 email 段（默认 load_config()；config 为空 dict 时给空段）。"""
    cfg = config if config is not None else load_config()
    if not isinstance(cfg, dict):
        return {}
    return cfg.get(_EMAIL_SECTION, {}) or {}


def _require_creds(email_cfg: dict):
    """校验发件侧配置：缺 username/auth_code 直接抛指引（fail-closed，别等连上才报）。"""
    user = (email_cfg.get("username") or "").strip()
    code = (email_cfg.get("auth_code") or "").strip()
    if not user or not code:
        raise MailError(
            "email 段未配好：username / auth_code 为空。" + _GUIDE)
    return user, code


# ------------------------------------------------------------------
# SMTP 发送
# ------------------------------------------------------------------

def _connect_smtp(host: str, port: int, use_ssl: bool, timeout: float):
    """按配置建 SMTP 连接（SSL 直连，或普通连接后 STARTTLS），返回已连上的实例。"""
    if use_ssl:
        return smtplib.SMTP_SSL(host, port, timeout=timeout)
    conn = smtplib.SMTP(host, port, timeout=timeout)
    conn.starttls()
    return conn


def smtp_send(to: str, subject: str, body: str, cc: Optional[str] = None,
              msg_id: Optional[str] = None, email_cfg: Optional[dict] = None) -> dict:
    """
    SMTP 发送一封邮件（纯文本正文）。配好 email 段才能发；没配好抛 MailError，不发。

    参数：
        to:        收件人邮箱（可多个，逗号分隔）
        subject:   邮件主题
        body:      正文（纯文本）
        cc:        可选抄送（逗号分隔）
        msg_id:    可选自定义 Message-ID 头（默认自动生成唯一 id，回读核验用）
        email_cfg: email 段配置（默认 load_config() 读）

    返回：
        {"message_id", "to", "subject", "smtp_accepted": True}
    """
    email_cfg = email_cfg if email_cfg is not None else email_section()
    user, code = _require_creds(email_cfg)
    to = (to or "").strip()
    subject = (subject or "").strip()
    if not to or not subject:
        raise MailError("收件人和主题不能为空，无法发送邮件。")

    host = (email_cfg.get("smtp_host") or "smtp.qq.com").strip()
    port = int(email_cfg.get("smtp_port") or 465)
    use_ssl = bool(email_cfg.get("smtp_ssl", True))
    timeout = float(email_cfg.get("timeout") or 20)

    msg = EmailMessage()
    msg["From"] = formataddr(("miniyu", user))
    msg["To"] = to
    if cc and str(cc).strip():
        msg["Cc"] = str(cc).strip()
    msg["Subject"] = subject
    if not msg_id:
        msg_id = make_msgid(domain="miniyu.local")
    msg["Message-ID"] = msg_id
    msg.set_content(body)

    try:
        conn = _connect_smtp(host, port, use_ssl, timeout)
        try:
            conn.login(user, code)
            conn.send_message(msg)
        finally:
            try:
                conn.quit()
            except Exception:
                conn.close()
    except MailError:
        raise
    except Exception as e:  # SMTPAuthenticationError / SMTPRecipientsRefused / socket 等
        raise MailError(
            f"SMTP 发送失败（{host}:{port}，{type(e).__name__}）：{e}\n"
            "请检查 email 段的 smtp_host/port/ssl、username/auth_code 是否正确、"
            "账号是否已开启 SMTP 服务。详见：\n" + _GUIDE) from e

    return {
        "message_id": msg_id,
        "to": to,
        "cc": (str(cc).strip() if cc and str(cc).strip() else ""),
        "subject": subject,
        "smtp_accepted": True,
    }


# ------------------------------------------------------------------
# IMAP 回读核验
# ------------------------------------------------------------------

def _connect_imap(imap_cfg: dict):
    """按 imap 子配置建 IMAP(S) 连接并登录，返回 conn。imap_cfg 需含 username/auth_code。"""
    host = (imap_cfg.get("imap_host") or "imap.qq.com").strip()
    port = int(imap_cfg.get("imap_port") or 993)
    use_ssl = bool(imap_cfg.get("imap_ssl", True))
    user = (imap_cfg.get("username") or "").strip()
    code = (imap_cfg.get("auth_code") or "").strip()
    timeout = float(imap_cfg.get("timeout") or 20)
    if not user or not code:
        raise MailError("IMAP 回读需要 username / auth_code，当前为空。\n" + _GUIDE)
    if use_ssl:
        conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    else:
        conn = imaplib.IMAP4(host, port)
        conn.starttls()
    conn.login(user, code)
    return conn


def _pick_sent_folder(conn) -> str:
    """从服务器文件夹清单里挑'已发送'；找不到则抛 MailError。"""
    try:
        typ, data = conn.list()
    except Exception as e:
        raise MailError(f"无法列出 IMAP 文件夹：{e}") from e
    names = []
    for row in data or []:
        try:
            txt = row.decode("utf-8", "replace") if isinstance(row, bytes) else str(row)
        except Exception:
            continue
        # 形如 (\\HasNoChildren) "/" "已发送" / "&XfJT0ZAB-"
        m = None
        # 取最后一个带引号的名字
        parts = txt.split('"')
        cand = None
        for part in reversed(parts):
            part = part.strip()
            if part and '"' not in part:
                cand = part
                break
        if cand:
            names.append(cand)
    for name in names:
        for alias in _SENT_FOLDER_ALIASES:
            if name == alias or alias.lower() in name.lower() or name.lower() in alias.lower():
                return name
    if names:
        raise MailError(
            f"在 IMAP 文件夹里找不到『已发送』（见到：{', '.join(names[:8])}），无法回读核验。")
    raise MailError("IMAP 返回的文件夹清单为空，无法回读核验。")


def _quote_mailbox(name: str) -> str:
    """邮箱名含空格/双引号等时按 RFC 3501 加引号（imaplib 不会自动加，直接 EXAMINE 会 BAD）。

    QQ 发件箱叫 'Sent Messages'，不带引号 select 会报 'EXAMINE parameters!'。
    """
    name = str(name)
    if not name or " " in name or '"' in name:
        return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return name


def _header_subject(raw: bytes) -> Optional[str]:
    """从某封邮件的头字段字节里解出 Subject（自动解 RFC2047 编码中文）。失败返回 None。"""
    try:
        msg = _BytesParser(policy=_policy.default).parsebytes(bytes(raw))
        s = msg.get("Subject")
        return str(s) if s else None
    except Exception:
        return None


def _folder_search_ids(conn, folder: str, msg_id: str, subject: Optional[str],
                       lookback: int = 60) -> bool:
    """在 folder 里看最近 lookback 封，Message-ID 或主题 命中即 True。

    兼容两类真实情况：
      - 服务商改写 Message-ID（如 QQ 投递后换成 tencent_xxx@qq.com）→ 靠主题兜底；
      - 中文主题在存储头里可能是 RFC2047 编码 → 先解出纯文本再子串比对。
    文件夹名含空格会加引号再 SELECT。
    """
    try:
        typ, _sel = conn.select(_quote_mailbox(folder), readonly=True)
        if typ != "OK":
            return False
        typ, data = conn.search(None, "ALL")
        if typ != "OK":
            return False
        ids = (data or [b""])[0].split()
        ids = ids[-lookback:] if len(ids) > lookback else ids
        if not ids:
            return False
        # 按消息逐个取头字段（不取正文，避免整封拉取）
        needle = (subject or "").strip().lower()
        for i in ids:
            typ, msgs = conn.fetch(i, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT)])")
            if typ != "OK":
                continue
            for part in msgs or []:
                raw = part
                if isinstance(part, tuple):
                    raw = part[1] if len(part) > 1 else part[0]
                if raw is None:
                    continue
                if isinstance(raw, str):
                    raw = raw.encode("utf-8", "replace")
                text = bytes(raw).decode("utf-8", "replace").lower()
                if msg_id and msg_id.lower() in text:
                    return True
                if needle:
                    if needle in text:  # 主题是纯文本直接落在头里
                        return True
                    dec = _header_subject(raw)
                    if dec and needle in dec.lower():  # 主题被 RFC2047 编码
                        return True
    except Exception:
        return False
    return False


def _imap_poll_found(imap_cfg: dict, msg_id: str, subject: Optional[str],
                     sent_folder: bool, timeout_s: float) -> dict:
    """轮询直到在目标文件夹命中 msg_id/subject 或超时。返回 {"folder","found","poll_seconds","by"}。"""
    deadline = time.time() + timeout_s
    by = "message-id" if msg_id else "subject"
    while True:
        conn = None
        try:
            conn = _connect_imap(imap_cfg)
            folder = _pick_sent_folder(conn) if sent_folder else "INBOX"
            if _folder_search_ids(conn, folder, msg_id, subject):
                return {"folder": folder, "found": True,
                        "poll_seconds": round(timeout_s - max(0.0, deadline - time.time()), 1),
                        "by": by}
        except MailError:
            raise
        except Exception:
            pass
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass
        if time.time() >= deadline:
            return {"folder": "已发送" if sent_folder else "INBOX", "found": False,
                    "poll_seconds": timeout_s, "by": by}


def imap_verify_sent(msg_id: str, subject: Optional[str] = None,
                     email_cfg: Optional[dict] = None,
                     timeout_s: Optional[float] = None) -> dict:
    """
    IMAP 回读发件箱『已发送』，确认发出去的信确实落库。找不到则如实返回 found=False（不装成功）。

    参数：
        msg_id:    发信时返回的 Message-ID（精确匹配）
        subject:   可选的兜底主题（Message-ID 被服务商改写时用主题匹配）
        email_cfg: email 段配置（默认 load_config() 读）
        timeout_s: 轮询窗口（秒，默认 email.timeout / 20）

    返回：
        {"folder", "found", "poll_seconds", "by"}
    """
    email_cfg = email_cfg if email_cfg is not None else email_section()
    timeout_s = timeout_s if timeout_s is not None else float(email_cfg.get("timeout") or 20)
    return _imap_poll_found(email_cfg, msg_id, subject, sent_folder=True, timeout_s=timeout_s)


def imap_verify_arrival(msg_id: str, subject: Optional[str] = None,
                        verify_cfg: Optional[dict] = None,
                        timeout_s: Optional[float] = None) -> dict:
    """
    （可选双端闭环）用收件侧邮箱的 IMAP 轮询其收件箱，核验邮件确实到达。需配置
    email.verify_inbox 子块（收件侧账号的 imap_host/port/ssl + username/auth_code）。

    参数：
        msg_id:    发信时返回的 Message-ID
        subject:   兜底主题
        verify_cfg: email.verify_inbox 子块（默认从 email 段读取）
        timeout_s: 轮询窗口（秒，默认 20）

    返回：
        {"folder", "found", "poll_seconds", "by"}
    """
    verify_cfg = verify_cfg if verify_cfg is not None else \
        (email_section().get("verify_inbox") or {})
    if not (verify_cfg.get("username") or "").strip() or not (verify_cfg.get("auth_code") or "").strip():
        raise MailError(
            "要核验『确实到达收件人』需在 email 段下配 verify_inbox 子块"
            "（收件侧邮箱的 IMAP username/auth_code）。\n" + _GUIDE)
    timeout_s = timeout_s if timeout_s is not None else float(verify_cfg.get("timeout") or 20)
    return _imap_poll_found(verify_cfg, msg_id, subject, sent_folder=False, timeout_s=timeout_s)
