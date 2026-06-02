"""Shared security utilities."""
import re

_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"system\s*:\s*",
    r"you\s+are\s+now\s+",
    r"new\s+role\s*:\s*",
    r"<\|system\|>",
    r"<\|assistant\|>",
    r"<\|user\|>",
    r"\{\{.*\}\}",
    r"\[SYSTEM\s+",
    r" disregard ",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)
_MAX_PROMPT_LEN = 8000


def sanitize_input(text: str) -> str:
    """Strip control chars and flag obvious prompt-injection fragments."""
    if not isinstance(text, str):
        text = str(text)
    # Remove null bytes and most control chars (keep \n, \r, \t)
    text = "".join(
        ch
        for ch in text
        if ch == "\n"
        or ch == "\r"
        or ch == "\t"
        or (ord(ch) >= 32 and ord(ch) < 127)
        or ord(ch) > 127
    )
    # Truncate
    if len(text) > _MAX_PROMPT_LEN:
        text = text[:_MAX_PROMPT_LEN] + "\n...[truncated]"
    # Escape injection markers by breaking the pattern
    text = _INJECTION_RE.sub(lambda m: "🚫" + m.group(0)[1:], text)
    return text
