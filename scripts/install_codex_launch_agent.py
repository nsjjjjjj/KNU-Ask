#!/usr/bin/env python3
"""Codex 후처리 LaunchAgent를 비밀값이 plist에 남지 않게 설치한다."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_SUPPORT = Path.home() / "Library" / "Application Support" / "KNU-Ask"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
LABEL = "com.knuask.codex-enrichment"


def env_value(name: str) -> str | None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return None


def main() -> None:
    token = env_value("ADMIN_API_TOKEN")
    if not token or len(token) < 32:
        raise SystemExit("프로젝트 .env에 32자 이상의 ADMIN_API_TOKEN이 필요합니다.")

    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROJECT_ROOT / "scripts" / "codex_ingestion_worker.py", APP_SUPPORT)

    secret_path = APP_SUPPORT / "worker.env"
    secret_path.write_text(f"ADMIN_API_TOKEN={token}\n", encoding="utf-8")
    secret_path.chmod(0o600)

    with (PROJECT_ROOT / "scripts" / f"{LABEL}.plist").open("rb") as stream:
        launch_agent = plistlib.load(stream)
    launch_agent["ProgramArguments"][1] = str(APP_SUPPORT / "codex_ingestion_worker.py")
    launch_agent["WorkingDirectory"] = str(APP_SUPPORT)
    launch_agent["EnvironmentVariables"]["KNUASK_ENV_FILE"] = str(secret_path)
    launch_agent["StandardOutPath"] = str(Path.home() / "Library" / "Logs" / "KNU-Ask-codex-worker.log")
    launch_agent["StandardErrorPath"] = str(Path.home() / "Library" / "Logs" / "KNU-Ask-codex-worker-error.log")

    target = LAUNCH_AGENTS / f"{LABEL}.plist"
    with target.open("wb") as stream:
        plistlib.dump(launch_agent, stream, sort_keys=False)
    target.chmod(0o600)

    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(target)], check=False)
    subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
    subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"], check=True)
    print("Codex enrichment LaunchAgent installed (token stored only in chmod 600 worker.env).")


if __name__ == "__main__":
    main()
