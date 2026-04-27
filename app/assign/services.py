from __future__ import annotations

import smtplib
from email.message import EmailMessage


def send_assign_notification_email(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    smtp_use_tls: bool,
    smtp_use_ssl: bool,
    from_email: str,
    cc_email: str,
    reply_to: str,
    to_email: str,
    subject: str,
    body: str,
    attachment_filename: str,
    attachment_bytes: bytes,
) -> tuple[bool, str]:
    host = str(smtp_host or "").strip()
    username = str(smtp_username or "").strip()
    password = str(smtp_password or "").strip()
    from_addr = str(from_email or username).strip()
    recipient = str(to_email or "").strip()
    if not host or not from_addr or not recipient:
        return False, "smtp configuration is incomplete"

    msg = EmailMessage()
    msg["Subject"] = str(subject or "").strip() or "Order Sheet Update"
    msg["From"] = from_addr
    msg["To"] = recipient
    cc = str(cc_email or "").strip()
    if cc:
        msg["Cc"] = cc
    if reply_to:
        msg["Reply-To"] = str(reply_to).strip()
    msg.set_content(str(body or "").strip())
    msg.add_attachment(
        attachment_bytes,
        maintype="application",
        subtype="pdf",
        filename=str(attachment_filename or "order-sheet.pdf"),
    )

    try:
        if bool(smtp_use_ssl):
            with smtplib.SMTP_SSL(host, int(smtp_port or 465), timeout=30) as server:
                if username:
                    server.login(username, password)
                server.send_message(msg)
            return True, "sent"

        with smtplib.SMTP(host, int(smtp_port or 587), timeout=30) as server:
            if bool(smtp_use_tls):
                server.starttls()
            if username:
                server.login(username, password)
            server.send_message(msg)
        return True, "sent"
    except Exception as exc:  # pragma: no cover - transport/network dependent
        return False, f"{exc.__class__.__name__}: {exc}"
