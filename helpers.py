import random

# Processing / "searching" random emoji (emoji only, no text)
PROCESSING_EMOJIS = ["🦋", "🔍"]


def smallcaps(text: str) -> str:
    """Returns the text as-is without any special styling."""
    return str(text)


def smallcaps_title(text: str) -> str:
    """Returns the text as-is without any special styling."""
    return str(text)


def fancy_italic(text: str) -> str:
    """Returns the text as-is without any special styling."""
    return str(text)


def random_processing_text() -> str:
    """Returns only a random emoji (out of 2) — no text."""
    return random.choice(PROCESSING_EMOJIS)


def format_duration(seconds) -> str:
    """Converts seconds to MM:SS or H:MM:SS format. Returns as-is if already a string."""
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return str(seconds) if seconds else "??:??"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def duration_to_seconds(duration_str: str) -> int:
    """Converts a string like '4:38' or '1:04:38' into seconds."""
    if not duration_str:
        return 0
    parts = str(duration_str).split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return 0
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


def format_uptime(seconds) -> str:
    """Creates an uptime string like 'HH:MM:ss' from seconds (like '12H:4M:10s')."""
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = 0
    h, rem = divmod(max(seconds, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{h}H:{m}M:{s}s"


# ---------------------------------------------------------------------------
# Expandable quote effect: message body comes inside a quote block 
# which can be clicked to expand.
# ---------------------------------------------------------------------------
def expandable_quote(text: str) -> str:
    return f"<blockquote expandable>{text}</blockquote>"


def quote(text: str) -> str:
    return f"<blockquote>{text}</blockquote>"


def strip_quotes(text: str) -> str:
    """Fallback if the library doesn't support expandable blockquotes."""
    return (
        text.replace("<blockquote expandable>", "")
        .replace("<blockquote>", "")
        .replace("</blockquote>", "")
    )


# ---------------------------------------------------------------------------
# Decorative divider
# ---------------------------------------------------------------------------
DIVIDER = "•────────────────"


def bullet_lines(items) -> str:
    """Puts each item on a separate line with '➤ '."""
    return "\n".join(f"➤ {i}" for i in items if i)
