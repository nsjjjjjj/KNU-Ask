#!/usr/bin/env python3
"""Git 추적 파일에서 커밋하면 안 되는 대표 비밀값 패턴을 검사한다."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    "OpenAI-style API key": re.compile(r"sk-(?:proj-)?[0-9A-Za-z_-]{20,}"),
    "GitHub token": re.compile(r"gh[pousr]_[0-9A-Za-z]{20,}"),
    "known insecure admin token": re.compile("local" + "-dev-admin"),
    "embedded default database password": re.compile(r"postgresql(?:\+\w+)?://[^\s:]+:knuask@"),
}


def tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"])
    paths = [Path(value.decode()) for value in output.split(b"\0") if value]
    script_path = Path(__file__).resolve().relative_to(Path.cwd().resolve())
    if script_path not in paths:
        paths.append(script_path)
    return paths


def main() -> int:
    findings: list[str] = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{path}: {label}")
    if findings:
        print("추적 파일에서 비밀값 후보를 발견했습니다:")
        print("\n".join(f"- {item}" for item in findings))
        return 1
    print("추적 파일에서 알려진 비밀값 패턴을 발견하지 못했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
