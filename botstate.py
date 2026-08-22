"""
The bot's global ON/OFF switch — controlled only by the OWNER_ID via 
`/on` and `/off` commands[span_0](start_span)[span_0](end_span). When OFF, the bot does not respond to 
any message or button (only `/on` and `/off` continue to work)[span_1](start_span)[span_1](end_span).

Fast in-memory cache is used (so a DB call isn't needed on every message), 
but the state is also persisted in the DB (see db.py: set_bot_status / 
get_bot_status) — so it is remembered even after a restart[span_2](start_span)[span_2](end_span).
"""

_enabled = True


def is_enabled() -> bool:
    return _enabled


def set_enabled(value: bool):
    global _enabled
    _enabled = bool(value)
