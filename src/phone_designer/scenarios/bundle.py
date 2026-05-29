"""Zip + SMTP mail — Phase 0 stub.

zip 번들은 동작. 메일 전송은 환경변수 SMTP 자격증명이 있으면 동작, 없으면 안내.
keyring 통합은 Phase 0 작업.
"""
from __future__ import annotations

import os
import smtplib
import zipfile
from email.message import EmailMessage
from pathlib import Path
from typing import Any


def _zip_run_dir(run_dir: Path) -> Path:
    out = run_dir.with_suffix(".zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in run_dir.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(run_dir.parent))
    return out


def bundle_and_mail(run_dir: Path, meta: dict[str, Any]) -> None:
    zip_path = _zip_run_dir(run_dir)
    print(f">>> bundle:    {zip_path}  ({zip_path.stat().st_size // 1024} KB)")

    # 환경변수 기반 (Phase 0 stub — keyring 은 추후)
    host = os.environ.get("PHONE_DESIGNER_SMTP_HOST")
    port = int(os.environ.get("PHONE_DESIGNER_SMTP_PORT", "587"))
    user = os.environ.get("PHONE_DESIGNER_SMTP_USER")
    pw   = os.environ.get("PHONE_DESIGNER_SMTP_PASS")
    to   = os.environ.get("PHONE_DESIGNER_MAIL_TO")

    if not all([host, user, pw, to]):
        print("[warn] SMTP 환경변수 미설정 — zip 만 생성하고 메일 skip.")
        print("       lat.md/setup.md 의 메일 환경변수 또는 'phone-designer config mail' 참조.")
        return

    msg = EmailMessage()
    msg["Subject"] = f"[PhoneDesigner] {meta['scenario']} — {meta['outcome']}"
    msg["From"] = user
    msg["To"] = to
    msg.set_content(
        f"Scenario: {meta['scenario']}\n"
        f"Outcome:  {meta['outcome']}\n"
        f"Started:  {meta['started_at']}\n"
        f"Duration: {meta['duration_s']:.2f}s\n"
        f"Host:     {meta['hostname']}\n"
        f"Run dir:  {meta['run_dir']}\n\n"
        f"See attached zip for full log + screenshots + meta.\n"
    )
    with zip_path.open("rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application", subtype="zip",
            filename=zip_path.name,
        )

    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(msg)
    print(f">>> mailed to: {to}")
