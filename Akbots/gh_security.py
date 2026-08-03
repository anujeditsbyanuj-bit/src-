# Akbots
# Don't Remove Credit
# Telegram Channel @AkBots_Official
#
# Secret detection + redaction, ported near-verbatim from the "GitHub Auto
# Uploader Pro" CLI tool (github_auto_uploader/services/security.py). Used
# by Akbots/repo_upload.py's /uploadrepo command to refuse pushing a folder
# that still contains an obvious token/key, and to keep any such value out
# of error messages shown back to the user.

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
import re

MAX_SCAN_BYTES = 2 * 1024 * 1024

SECRET_PATTERNS = (
    (
        "GitHub token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    (
        "AWS access key",
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ),
    (
        "Private key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
)


@dataclass(frozen=True)
class SecretFinding:
    path: str
    line: int
    kind: str


def _gitignore_patterns(root):
    gitignore = Path(root) / ".gitignore"
    if not gitignore.exists():
        return []
    try:
        lines = gitignore.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    return [
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith(("#", "!"))
    ]


def _is_ignored(relative_path, patterns):
    normalized = relative_path.replace("\\", "/")
    parts = normalized.split("/")
    for pattern in patterns:
        normalized_pattern = pattern.replace("\\", "/").lstrip("/").rstrip("/")
        if not normalized_pattern:
            continue
        if fnmatch(normalized, normalized_pattern) or fnmatch(normalized, f"{normalized_pattern}/*"):
            return True
        if "/" not in normalized_pattern and any(fnmatch(part, normalized_pattern) for part in parts):
            return True
    return False


def is_path_ignored(root, relative_path):
    """Return whether an existing .gitignore rule covers a relative path."""
    return _is_ignored(str(relative_path), _gitignore_patterns(root))


def scan_for_secrets(root):
    """Scan non-ignored text files without returning or displaying secret values."""
    root = Path(root)
    ignore_patterns = _gitignore_patterns(root)
    findings = []

    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative_path = path.relative_to(root).as_posix()
        if _is_ignored(relative_path, ignore_patterns):
            continue
        try:
            if path.stat().st_size > MAX_SCAN_BYTES:
                continue
            content = path.read_bytes()
        except OSError:
            continue
        if b"\0" in content:
            continue
        text = content.decode("utf-8", errors="ignore")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for kind, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(SecretFinding(relative_path, line_number, kind))

    return findings


def redact_sensitive_text(value):
    """Remove credentials from logs and diagnostic command text."""
    redacted = str(value)
    redacted = re.sub(r"https://[^/@\s]+@github\.com/", "https://***@github.com/", redacted)
    for _kind, pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def extract_github_secret_paths(error_output):
    """Extract file paths from GitHub push-protection diagnostics."""
    paths = []
    for line in str(error_output).splitlines():
        match = re.search(r"\bpath:\s+(.+?)\s*$", line)
        if not match:
            continue
        path = match.group(1).strip()
        path = re.sub(r":\d+(?::\d+)?$", "", path)
        if path and path not in paths:
            paths.append(path)
    return paths
