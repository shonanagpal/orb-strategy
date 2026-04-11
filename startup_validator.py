#!/usr/bin/env python3
# startup_validator.py
#
# Runs at 09:20 via cron (waits 60s internally, checks at ~09:21).
# Validates all pipeline components are healthy after market open.
# Sends email alert if any checks fail.
#
# Required env vars (add to .env.kite):
#   SMTP_USER      — Gmail address used to send alerts
#   SMTP_PASS      — Gmail App Password (not your login password)
#   ALERT_EMAIL    — address to receive alerts

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import time


# ---------- EMAIL CONFIG ----------
ALERT_EMAIL = os.getenv("ALERT_EMAIL")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT   = 587
SMTP_USER   = os.getenv("SMTP_USER")
SMTP_PASS   = os.getenv("SMTP_PASS")


def send_alert(subject, body):
    if not all([SMTP_USER, SMTP_PASS, ALERT_EMAIL]):
        print("⚠️  Email credentials not set — skipping alert email")
        return

    try:
        msg = MIMEMultipart()
        msg['From']    = SMTP_USER
        msg['To']      = ALERT_EMAIL
        msg['Subject'] = f"🚨 Trading Alert: {subject}"
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()

        print(f"✅ Alert email sent to {ALERT_EMAIL}")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")


def validate_pipeline():
    errors = []
    today = datetime.now().strftime("%Y-%m-%d")

    # 1. Collector: by 09:21 the first candle (09:15) should have closed
    #    for most symbols — expect 400+ candle files
    live_dir = f"live_candles/{today}"
    if not os.path.isdir(live_dir):
        errors.append(f"❌ COLLECTOR: No directory {live_dir} — not receiving ticks")
    else:
        file_count = len([f for f in os.listdir(live_dir) if f.endswith('.parquet')])
        if file_count < 400:
            errors.append(
                f"❌ COLLECTOR: Only {file_count} candle files — expected 400+ by 09:21"
            )

    # 2. Assembler: most assembled parquets should have been updated
    #    in the last 2 minutes after the first candle close
    assembled_dir = "assembled_candles"
    if os.path.isdir(assembled_dir):
        cutoff = time.time() - 120
        recent = [
            f for f in os.listdir(assembled_dir)
            if f.endswith('.parquet')
            and os.path.getmtime(os.path.join(assembled_dir, f)) > cutoff
        ]
        if len(recent) < 400:
            errors.append(
                f"❌ ASSEMBLER: Only {len(recent)} parquets updated in last 2 min — expected 400+"
            )

    # 3. Kite token
    if not os.getenv("KITE_ACCESS_TOKEN"):
        errors.append("❌ KITE_ACCESS_TOKEN not set")

    # 4. tmux sessions
    for session in ['candle', 'assembler', 'signal', 'executor']:
        result = os.system(f"tmux has-session -t {session} 2>/dev/null")
        if result != 0:
            errors.append(f"❌ tmux session '{session}' not running")

    return errors


if __name__ == "__main__":
    # Cron fires at 09:20 — wait 60s so first candle has time to close and assemble
    print(f"🔍 Startup validator waiting 60s before checking [{datetime.now().strftime('%H:%M:%S')}]...")
    time.sleep(60)
    print(f"🔍 Validating pipeline [{datetime.now().strftime('%H:%M:%S')}]...")

    errors = validate_pipeline()

    if not errors:
        print("✅ All systems operational")
    else:
        print("\n🚨 STARTUP FAILURES DETECTED:")
        for err in errors:
            print(f"  {err}")

        subject = f"Startup Failure - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        body = "Trading pipeline startup validation FAILED:\n\n"
        body += "\n".join(errors)
        body += "\n\nPlease check the system immediately."

        send_alert(subject, body)

        with open("startup_failures.log", "a") as f:
            f.write(f"\n{'='*50}\n")
            f.write(f"{datetime.now()}\n")
            f.write("\n".join(errors))
            f.write("\n")
