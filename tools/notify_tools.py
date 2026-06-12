import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import NOTIFY_EMAIL_ENABLED, NOTIFY_EMAIL, NOTIFY_EMAIL_PASSWORD, NOTIFY_EMAIL_TO

_COOLDOWN_SECONDS = 600  # 10分
_last_sent: dict[str, float] = {}  # subject → 最終送信時刻


def send_email_notification(subject: str, body: str) -> bool:
    """Gmail でメール通知を送信する。同じ件名は10分以内に再送しない。失敗しても例外を上げず False を返す。"""
    if not NOTIFY_EMAIL_ENABLED:
        return False
    if not NOTIFY_EMAIL or not NOTIFY_EMAIL_PASSWORD:
        return False

    now = time.monotonic()
    if now - _last_sent.get(subject, 0) < _COOLDOWN_SECONDS:
        return False
    _last_sent[subject] = now

    to_addr = NOTIFY_EMAIL_TO or NOTIFY_EMAIL
    try:
        msg = MIMEMultipart()
        msg["From"] = NOTIFY_EMAIL
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as smtp:
            smtp.login(NOTIFY_EMAIL, NOTIFY_EMAIL_PASSWORD)
            smtp.sendmail(NOTIFY_EMAIL, to_addr, msg.as_string())
        return True
    except Exception as e:
        print(f"[notify] メール送信失敗: {e}")
        return False


def send_email(subject: str, body: str, to: str | None = None) -> dict:
    """
    エージェントが明示的に呼ぶメール送信ツール。

    send_email_notification と違い「クールダウンなし・成否を辞書で返す」。
    条件分岐（例: アクセス不可のときだけ通知）で使うことを想定し、
    送れなかった場合は理由を返してエージェントが気付けるようにする。
    """
    if not NOTIFY_EMAIL_ENABLED:
        return {"ok": False, "error": "メール通知が無効です（.env の NOTIFY_EMAIL_ENABLED=true が必要）"}
    if not NOTIFY_EMAIL or not NOTIFY_EMAIL_PASSWORD:
        return {"ok": False, "error": "送信元が未設定です（.env の NOTIFY_EMAIL / NOTIFY_EMAIL_PASSWORD）"}

    to_addr = (to or NOTIFY_EMAIL_TO or NOTIFY_EMAIL).strip()
    if not to_addr:
        return {"ok": False, "error": "宛先が未設定です（.env の NOTIFY_EMAIL_TO か to 引数）"}
    try:
        msg = MIMEMultipart()
        msg["From"] = NOTIFY_EMAIL
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as smtp:
            smtp.login(NOTIFY_EMAIL, NOTIFY_EMAIL_PASSWORD)
            smtp.sendmail(NOTIFY_EMAIL, to_addr, msg.as_string())
        return {"ok": True, "to": to_addr, "subject": subject}
    except Exception as e:
        print(f"[notify] メール送信失敗: {e}")
        return {"ok": False, "error": f"送信失敗: {e}"}
