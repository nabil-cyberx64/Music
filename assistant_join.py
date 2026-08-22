"""
Before playing music in any group, the assistant (userbot) account 
must be present there — the assistant joins the VC, not the bot.

This module checks whether the assistant is already in the group or not, and 
if not, attempts to make it join automatically:

  1. If the group is public (has a username) -> the assistant directly joins 
     using that username[span_1](start_span)[span_1](end_span).
  2. If the group is private -> the bot (which is already in the group) creates 
     an invite link and the assistant joins using it[span_2](start_span)[span_2](end_span).
  3. Last resort -> the bot tries to add the assistant as a member itself 
     (for this, the bot needs "invite users" admin rights)[span_3](start_span)[span_3](end_span).

If all three fail (e.g., the bot itself is not an admin, or the assistant's privacy 
settings are blocking it), the caller is informed to manually add/join 
@ASSISTANT_USERNAME to the group[span_4](start_span)[span_4](end_span).
"""

from pyrogram.errors import (
    UserNotParticipant,
    UserAlreadyParticipant,
    FloodWait,
    ChatAdminRequired,
    RPCError,
)

import config
from clients import bot, assistant, LOGGER


async def is_assistant_in_chat(chat_id: int) -> bool:
    """Directly checks whether the assistant is a member of this chat or not[span_5](start_span)[span_5](end_span)."""
    try:
        await assistant.get_chat_member(chat_id, "me")
        return True
    except UserNotParticipant:
        return False
    except Exception as e:
        LOGGER.warning(f"Assistant membership check fail: {e}")
        return False


async def ensure_assistant_in_chat(chat_id: int):
    """
    Confirms whether the assistant is in the chat, and if not, 
    makes every effort to get it to join[span_6](start_span)[span_6](end_span).

    Returns: (joined: bool, reason: str)
        joined=True  -> assistant is now in chat, VC can be joined[span_7](start_span)[span_7](end_span).
        joined=False -> could not join; `reason` explains why (caller can use 
                         this to build a user-facing message)[span_8](start_span)[span_8](end_span).
    """
    if await is_assistant_in_chat(chat_id):
        return True, ""

    try:
        chat = await bot.get_chat(chat_id)
    except Exception as e:
        LOGGER.warning(f"get_chat fail (before assistant join): {e}")
        chat = None

    # --- Try 1: public group -> join directly using username ---------------
    if chat and chat.username:
        try:
            await assistant.join_chat(chat.username)
            LOGGER.info(f"Assistant joined public group @{chat.username}.")
            return True, ""
        except UserAlreadyParticipant:
            return True, ""
        except FloodWait as e:
            LOGGER.warning(f"Assistant join FloodWait: {e.value}s")
            return False, "flood_wait"
        except RPCError as e:
            LOGGER.warning(f"Assistant username join fail: {e}")

    # --- Try 2: private group -> bot creates invite link, assistant joins it
    try:
        link = getattr(chat, "invite_link", None) if chat else None
        if not link:
            link = await bot.export_chat_invite_link(chat_id)
        if link:
            try:
                await assistant.join_chat(link)
                LOGGER.info(f"Assistant joined chat {chat_id} via invite link.")
                return True, ""
            except UserAlreadyParticipant:
                return True, ""
    except ChatAdminRequired:
        LOGGER.warning("Bot is not an admin — could not export invite link.")
    except FloodWait as e:
        LOGGER.warning(f"Assistant join FloodWait: {e.value}s")
        return False, "flood_wait"
    except Exception as e:
        LOGGER.warning(f"Assistant invite-link join fail: {e}")

    # --- Try 3: bot adds the assistant to the group itself (if rights permit)
    try:
        me_assistant = await assistant.get_me()
        await bot.add_chat_members(chat_id, me_assistant.id)
        # add_chat_members does not confirm instantly, so verify again
        if await is_assistant_in_chat(chat_id):
            LOGGER.info(f"Bot added assistant to chat {chat_id}.")
            return True, ""
    except Exception as e:
        LOGGER.warning(f"Bot add_chat_members(assistant) fail: {e}")

    return False, "manual_needed"