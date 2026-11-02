"""Send the report PDF by SMTP (stdlib smtplib, run in a worker thread)."""

import asyncio
import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from xml.sax.saxutils import escape

from app.config import Settings
from app.report.pdf import OFFERING_URL

log = logging.getLogger(__name__)

EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


class EmailError(RuntimeError):
    """Sending failed; the message is safe to show to the user."""


def valid_email(address: str) -> bool:
    return len(address) <= 254 and bool(EMAIL_PATTERN.match(address)) and ".." not in address


def _safe_filename(brand: str) -> str:
    cleaned = re.sub(r"[^\w一-鿿-]+", "-", brand).strip("-")[:40]
    return f"GEO诊断报告-{cleaned or 'report'}.pdf"


def build_message(settings: Settings, to: str, brand: str, url: str, geo_score: float, pdf: bytes) -> EmailMessage:
    brand_line = re.sub(r"[\r\n\t]+", " ", brand)[:60]  # crawled text: never let it break header lines
    message = EmailMessage()
    message["Subject"] = f"您的GEO全面诊断报告：{brand_line}"
    message["From"] = formataddr((settings.smtp_from_name.strip(), settings.smtp_from.strip()))
    message["To"] = to
    if settings.smtp_reply_to.strip():
        message["Reply-To"] = settings.smtp_reply_to.strip()
    message["Message-ID"] = make_msgid(domain=settings.smtp_from.strip().split("@")[-1])
    text = (
        f"您好！\n\n附件是 {brand_line}（{url}）的GEO全面诊断报告（PDF），GEO综合得分 {geo_score:.0f}/100。\n"
        "报告涵盖AI内容可引用性、品牌 AI 引用权威度、内容质量E-E-A-T、技术GEO基础、结构化数据与AI平台适配度六大维度，"
        "并附有优先行动计划。\n\n"
        f"欲了解罗德出海GEO顾问服务，请访问：{OFFERING_URL}\n\n罗德出海GEO准备度评估\n"
    )
    message.set_content(text)
    message.add_alternative(
        "<div style=\"font-family:Arial,'Microsoft YaHei',sans-serif;font-size:14px;line-height:1.7;color:#1a1a1a\">"
        f"<p>您好！</p><p>附件是 <b>{escape(brand_line)}</b>（{escape(url)}）的GEO全面诊断报告（PDF），"
        f"GEO综合得分 <b>{geo_score:.0f}/100</b>。</p>"
        "<p>报告涵盖AI内容可引用性、品牌 AI 引用权威度、内容质量E-E-A-T、技术GEO基础、结构化数据与AI平台适配度六大维度，并附有优先行动计划。</p>"
        f"<p><a href=\"{OFFERING_URL}\" style=\"color:#1b7531\">欲了解更多，请点击这里</a></p>"
        "<p style=\"color:#727272\">罗德出海GEO准备度评估</p></div>",
        subtype="html",
    )
    message.add_attachment(pdf, maintype="application", subtype="pdf", filename=_safe_filename(brand_line))
    return message


def _bcc(settings: Settings) -> list[str]:
    return [a.strip() for a in settings.report_bcc.split(",") if valid_email(a.strip())]


def _security(settings: Settings) -> str:
    """Implicit TLS on port 465 regardless of the configured mode: STARTTLS there would hang or fail."""
    mode = settings.smtp_security.strip().lower()
    return "ssl" if settings.smtp_port == 465 else mode if mode in ("starttls", "ssl", "none") else "starttls"


def _send(settings: Settings, message: EmailMessage) -> dict:
    """Send and return any recipients the server refused while still accepting others (e.g. a bad BCC)."""
    recipients = [message["To"], *_bcc(settings)]
    context = ssl.create_default_context()
    host = settings.smtp_host.strip()
    security = _security(settings)
    if security == "ssl":
        server = smtplib.SMTP_SSL(host, settings.smtp_port, timeout=settings.smtp_timeout_seconds, context=context)
    else:
        server = smtplib.SMTP(host, settings.smtp_port, timeout=settings.smtp_timeout_seconds)
    with server:
        if security == "starttls":
            server.starttls(context=context)
        if settings.smtp_username.strip():
            server.login(settings.smtp_username.strip(), settings.smtp_password)
        return server.send_message(message, from_addr=settings.smtp_from.strip(), to_addrs=recipients)


def _mask(address: str) -> str:
    return "***@" + address.rsplit("@", 1)[-1]


def _replies(refused: dict) -> dict[str, tuple[int, str]]:
    return {_mask(addr): (code, msg.decode(errors="replace") if isinstance(msg, bytes) else str(msg))
            for addr, (code, msg) in refused.items()}


BAD_MAILBOX = re.compile(r"5\.1\.[0-3]|user unknown|unknown user|does not exist|no such (user|mailbox)|"
                         r"mailbox (unavailable|not found)|recipient not found|invalid (recipient|address)", re.I)


def refusal_message(refused: dict, authenticated: bool) -> str:
    """A user-facing message for a refused recipient, from the server's reply codes.

    Only a "no such mailbox" reply is the visitor's mistake. Relay/permission refusals (5.7.x, e.g. an
    unauthenticated or direct-send connection asked to deliver to an outside domain) are a sender
    configuration problem, and 4xx replies are temporary.
    """
    replies = _replies(refused).values()
    if replies and all(400 <= code < 500 for code, _ in replies):
        return "邮件服务器暂时无法投递，请稍后重试。"
    if any(BAD_MAILBOX.search(text) for _, text in replies):
        return "邮件服务器拒绝了该收件地址，请检查邮箱是否正确。"
    if not authenticated:
        return "邮件服务器拒绝转发该邮件（发件账号未登录验证），请联系罗德业务经理。"
    return "邮件服务器拒绝投递该邮件（发件服务器配置或权限问题），请联系罗德业务经理。"


async def send_report(settings: Settings, message: EmailMessage) -> None:
    try:
        partial = await asyncio.to_thread(_send, settings, message)
    except smtplib.SMTPRecipientsRefused as exc:
        log.error("SMTP %s:%s refused all recipients: %s", settings.smtp_host.strip(), settings.smtp_port,
                  _replies(exc.recipients))
        raise EmailError(refusal_message(exc.recipients, bool(settings.smtp_username.strip()))) from exc
    except smtplib.SMTPSenderRefused as exc:
        log.error("SMTP refused sender %s: %s %s", settings.smtp_from.strip(), exc.smtp_code, exc.smtp_error)
        raise EmailError("邮件服务器拒绝了发件地址（SMTP_FROM 与发件账号不匹配或未授权），请联系罗德业务经理。") from exc
    except smtplib.SMTPAuthenticationError as exc:
        log.error("SMTP authentication failed: %s %s", exc.smtp_code, exc.smtp_error)
        raise EmailError("邮件服务器登录验证失败，请联系罗德业务经理。") from exc
    except (smtplib.SMTPException, OSError) as exc:
        log.error("SMTP send failed: %s: %s", type(exc).__name__, exc)
        raise EmailError("邮件发送失败，请稍后重试。") from exc
    if partial:
        log.warning("SMTP accepted the report but refused some recipients: %s", _replies(partial))
