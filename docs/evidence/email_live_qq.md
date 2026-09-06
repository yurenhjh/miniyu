# 真机证据：邮件全链路 send_email —— QQ 发送 + IMAP 双端核验（2026-09-06）

> 对应能力：`core/email_client.py`（纯 stdlib SMTP/IMAP）+ Agent 白名单组合技能 `send_email`
> 运行脚本：`examples/email_demo.py`（默认直驱技能；`--agent` 走真实 LLM）
> 与 `docs/evidence/agent_qwen_live.md`（真实 LLM function-calling）同属"真机验证代替假设"系列。

## 一、演示参数（用户指定并确认后才执行）

| 项 | 值 |
|---|---|
| 发件账号 | QQ 主号 `3480870832@qq.com`（SMTP/IMAP 授权码） |
| SMTP | smtp.qq.com:465 (SSL) |
| IMAP | imap.qq.com:993 (SSL) |
| 收件人 | QQ 小号 `3837227570@qq.com`（同时作 verify_inbox 收件侧核验） |
| 主题 | 来自小余人的测试信 |
| 正文 | 你好呀，小余人 |

授权码只写进本地 `config.yaml` 的 `email` 段（skip-worktree 锁住、不入库），本文件与仓库不含任何密钥。

## 二、实跑结果（`python examples/email_demo.py --to 3837227570@qq.com --body "你好呀，小余人"`）

```
send_email —— 邮件发送预告
  发件账号 : 3480870832@qq.com
  SMTP     : smtp.qq.com:465
  收件人   : 3837227570@qq.com
  主题     : 来自小余人的测试信
  正文     : 你好呀，小余人
  确认门放行：send_email  (风险 high)

技能返回：
  success = True  skill = send_email
  smtp_accepted = True
  message_id = <178869916241.35044.12277291464732296101@miniyu.local>
```

**第一版回读未命中**（暴露问题，见下），随后按"只读实测落库"核实：小号收件箱 INBOX 最新一封正是
`来自小余人的测试信`、from=`miniyu <3480870832@qq.com>` —— **邮件确实送达**，属核验匹配缺陷而非发送失败。

## 三、实跑暴露的 3 个真实坑（已修，回归单测 2 条）

1. **QQ 投递改写 Message-ID**：发件时生成 `<…@miniyu.local>`，投递后被换成
   `<tencent_52059BAB…@qq.com>` → 按 Message-ID 匹配永远找不到。
   修复：`_folder_search_ids` 保留 Message-ID 优先，但以主题兜底。
2. **中文主题在存储头里是 RFC2047 编码**（`Subject: =?utf-8?B?…?=`），按中文子串比对原字节对不上。
   修复：逐封解析头字段 `Subject`（`email.parser` + `policy.default` 自动解码）后再子串比对。
3. **发件箱名带空格 + QQ 不留副本**：
   - QQ 的『已发送』文件夹在 IMAP 层叫 `Sent Messages`（带空格）。imaplib 不加引号直接
     `EXAMINE Sent Messages` 会报 `BAD EXAMINE parameters!`。修复：`_quote_mailbox` 按 RFC 3501 加引号。
   - 实测发现 **QQ 授权码 SMTP 发信不在发件箱『Sent Messages』留副本**（该文件夹停在 2026-06，
     本次发送的信不落其中）→ 『已发送』回读未命中 ≠ 没发。结论逻辑改为：配了 `verify_inbox`
     时以**收件箱到达核验为铁证**；发件箱命中当作额外强证据（163/Gmail 等通常留副本仍可查）。

## 四、修复后只读重核验（对已送达那封，未重复发信）

```
--- [已发送] 回读（QQ 不留副本，未命中=预期）---
   {'folder': '已发送', 'found': False, 'by': 'message-id'}
--- [到达] 收件箱核验（铁证）---
   {'folder': 'INBOX', 'found': True, 'poll_seconds': 1.2, 'by': 'subject'}
```

到达核验 1.2s 内命中 → 双端闭环成立：SMTP 接受 + 收件人收件箱真收到。

## 五、全量回归

修复前 368 全绿 → 补 2 条回归单测（`SELECT` 加引号、RFC2047 主题命中）→ **370 全绿**
（`tests/test_email.py` 25→27 例）。

## 六、运行前置（给答辩/复现的人）

1. `config.yaml` 的 `email` 段填发件账号 + SMTP/IMAP **授权码**（QQ 邮箱『设置→账户→开启
   SMTP/IMAP 服务』生成，不是登录密码；敏感，本地填、不入库）；
2. 想核验"确实到达收件人"再配 `email.verify_inbox`（收件侧邮箱的 IMAP）；
3. `python examples/email_demo.py --to 收件人 --subject 主题 --body 正文`
   （发信对外不可撤回，脚本被显式运行视为已确认参数内容）。
