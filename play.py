import random
import time

from pyrogram import filters, StopPropagation
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
)
from pytgcalls.types.input_stream import AudioPiped
from pytgcalls.exceptions import NoActiveGroupCall

import config
import db
import music_queue as q
import progress
import botstate
from clients import bot, assistant, call_py, LOGGER, START_TIME
from youtube import search_track, get_stream_url, get_related_track
from helpers import (
    smallcaps_title,
    random_processing_text,
    format_duration,
    fancy_italic,
    duration_to_seconds,
    format_uptime,
    expandable_quote,
    strip_quotes,
    smallcaps,
    DIVIDER,
    bullet_lines,
)

from nowplaying import generate_now_playing_card
from assistant_join import ensure_assistant_in_chat

OWNER_FILTER = filters.user(config.OWNER_ID) if config.OWNER_ID else filters.create(lambda _, __, ___: False)

ADMIN_STATUSES = (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)

# Wait for the owner's next image/video/gif after running /addvd (private start message)
_pending_addvd = set()

# Wait for the owner's next image/video/gif after running /addvd2 (GROUP start message)
_pending_addvd2 = set()

_SEND_MEDIA_MAP_NAME = {"photo": "send_photo", "video": "send_video", "animation": "send_animation"}


# ---------------------------------------------------------------------------
# Peer cache helper — To avoid the "Peer id invalid" error.
# When the assistant account does not receive direct updates in a chat 
# (only joins the VC), pyrogram cannot cache its peer/access_hash, causing 
# subsequent change_stream/leave_group_call calls to fail. 
# Therefore, on error, we refresh dialogs once and retry.
# ---------------------------------------------------------------------------
async def _refresh_assistant_peers():
    try:
        async for _ in assistant.get_dialogs():
            pass
    except Exception as e:
        LOGGER.warning(f"Peer refresh fail: {e}")


def _is_peer_error(e: Exception) -> bool:
    return isinstance(e, ValueError) and "Peer id invalid" in str(e)


# ---------------------------------------------------------------------------
# Admin / owner check — /skip /pause /resume /stop /reload are restricted to 
# group admins or the bot OWNER_ID. Normal users can only use /play.
# ---------------------------------------------------------------------------
async def _is_group_admin(client, chat_id: int, user_id: int) -> bool:
    if config.OWNER_ID and user_id == config.OWNER_ID:
        return True
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in ADMIN_STATUSES
    except Exception:
        return False


ADMIN_ONLY_TEXT = f"❌ {smallcaps_title('This only Owner and Admin')}."

NOT_YOUR_REQUEST_TEXT = (
    f"❌ {smallcaps_title('It’s Not Your Request ')}!\n"
    f"{smallcaps_title('Control only song requester And Owner/admin')}."
)


# ---------------------------------------------------------------------------
# Control-permission check — /skip /pause /resume /stop (and their inline buttons) 
# can only be used by 3 people: the user who requested the current track, 
# a group admin, or the bot owner. Other normal users get NOT_YOUR_REQUEST_TEXT.
# ---------------------------------------------------------------------------
async def _can_control(client, chat_id: int, user_id: int) -> bool:
    if await _is_group_admin(client, chat_id, user_id):
        return True
    track = q.get_now_playing(chat_id)
    return bool(track and track.get("requested_by_id") == user_id)


ASSISTANT_NOT_JOINED_TEXT = (
    f"❌ **{smallcaps_title('assistant account is not in group')}!**\n\n"
    f"{smallcaps_title('music assistant must be in group')}.\n"
    f"👉 @{config.ASSISTANT_USERNAME} {smallcaps_title('first add this assistant in group')}.\n\n"
    f"{smallcaps_title('And Again')} `/play` {smallcaps_title('use')}."
)

ASSISTANT_FLOOD_TEXT = (
    f"⏳ {smallcaps_title('telegram rate limit hit, please try again after some time')}."
)


# ---------------------------------------------------------------------------
# Owner: /on /off — Global switch to enable or disable the entire bot.
# When OFF, the bot does not respond to any messages or buttons, except /on and /off.
# Persisted in DB, so the status is remembered even after restarts.
# ---------------------------------------------------------------------------
@bot.on_message(filters.command("on") & OWNER_FILTER)
async def on_command(client, message: Message):
    botstate.set_enabled(True)
    await db.set_bot_status(True)
    await message.reply_text(f"✅ {smallcaps_title('bot has been turned on')}.")


@bot.on_message(filters.command("off") & OWNER_FILTER)
async def off_command(client, message: Message):
    botstate.set_enabled(False)
    await db.set_bot_status(False)
    await message.reply_text(
        f"🔴 {smallcaps_title('bot has been turned off')}.\n"
        f"{smallcaps_title('now only')} `/on` {smallcaps_title('will work')}."
    )


def _off_blocker(_, __, message: Message) -> bool:
    if botstate.is_enabled():
        return False
    text = message.text or message.caption or ""
    # /on and /off should always work, even if the bot is OFF
    return not text.startswith(("/on", "/off"))


def _off_blocker_cb(_, __, cq: CallbackQuery) -> bool:
    return not botstate.is_enabled()


# group=-1 -> This handler runs first; when OFF, messages/callbacks do not reach 
# any other handler (StopPropagation).
@bot.on_message(filters.create(_off_blocker), group=-1)
async def _blocked_while_off(client, message: Message):
    raise StopPropagation


@bot.on_callback_query(filters.create(_off_blocker_cb), group=-1)
async def _blocked_cb_while_off(client, cq: CallbackQuery):
    await cq.answer(smallcaps_title("bot is currently off"), show_alert=True)
    raise StopPropagation


def _btn(text: str, *, style: str = None, **kwargs) -> InlineKeyboardButton:
    """
    Creates an InlineKeyboardButton. Telegram Bot API colored buttons 
    (style="primary"/"success"/"danger") will only show up if your pyrogram/kurigram/pyrofork 
    library supports it — otherwise, it gracefully falls back to a normal button without errors.
    """
    if style:
        try:
            return InlineKeyboardButton(text, style=style, **kwargs)
        except TypeError:
            pass
    return InlineKeyboardButton(text, **kwargs)


SEEK_STEP = 10  # Seconds to skip forward/backward for ⏪ -10s / ⏩ +10s buttons


def _controls_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                _btn("▶️", callback_data="m_resume", style="success"),
                _btn("⏸", callback_data="m_pause", style="primary"),
                _btn("🔁", callback_data="m_replay", style="primary"),
                _btn("⏭", callback_data="m_skip", style="primary"),
                _btn("⏹", callback_data="m_stop", style="danger"),
            ],
            [
                _btn(f"⏪ -{SEEK_STEP}s", callback_data="m_back10", style="primary"),
                _btn(f"+{SEEK_STEP}s ⏩", callback_data="m_fwd10", style="primary"),
            ],
            [_btn(f"⚙️ {smallcaps_title('bot settings')}", callback_data="m_settings", style="primary")],
            [_btn(f"⊙ {smallcaps_title('close')} ⊙", callback_data="m_close", style="danger")],
        ]
    )


def _settings_keyboard(autoplay_on: bool):
    """Now Playing -> ⚙️ Bot Settings containing only 2 buttons: autoplay toggle + back."""
    state = smallcaps_title("on") if autoplay_on else smallcaps_title("off")
    return InlineKeyboardMarkup(
        [
            [
                _btn(
                    f"🔁 {smallcaps_title('autoplay')} : {state}",
                    callback_data="m_autoplay",
                    style="success" if autoplay_on else "danger",
                )
            ],
            [_btn(f"🔙 {smallcaps_title('back')}", callback_data="m_back", style="primary")],
        ]
    )


def _start_keyboard(bot_username: str):
    return InlineKeyboardMarkup(
        [
            [
                _btn(
                    f"➕ {smallcaps_title('add me to your group')}",
                    url=f"https://t.me/{bot_username}?startgroup=true",
                    style="success",
                )
            ],
            [
                _btn(f"👑 {smallcaps_title('owner')}", url=config.OWNER_URL, style="primary"),
                _btn(f"🛠 {smallcaps_title('support')}", url=config.SUPPORT_URL, style="primary"),
            ],
            [
                _btn(f"📢 {smallcaps_title('channel')}", url=config.CHANNEL_URL, style="primary"),
                _btn(f"❓ {smallcaps_title('help')}", callback_data="help_menu", style="primary"),
            ],
        ]
    )


def _help_keyboard():
    return InlineKeyboardMarkup(
        [[_btn(f"🔙 {smallcaps_title('back')}", callback_data="back_to_start", style="primary")]]
    )


async def _safe_quote_send(action, text: str):
    """
    Sends/edits a message with an expandable quote. If your pyrogram/kurigram build 
    does not support `<blockquote expandable>`, it safely falls back to plain text 
    without crashing while keeping the text style identical.
    """
    try:
        return await action(text)
    except Exception as e:
        LOGGER.warning(f"Quote send fail, plain fallback: {e}")
        return await action(strip_quotes(text))


async def _send_welcome(chat_id: int, text: str, reply_markup, media: dict = None):
    """Sends a welcome message with the given media (photo/video/gif) or text alone."""
    if media:
        send_func = getattr(bot, _SEND_MEDIA_MAP_NAME.get(media["media_type"], "send_photo"))
        try:
            return await _safe_quote_send(
                lambda t: send_func(chat_id, media["file_id"], caption=t, reply_markup=reply_markup),
                text,
            )
        except Exception as e:
            LOGGER.warning(f"Start media send fail, text fallback: {e}")
    return await _safe_quote_send(
        lambda t: bot.send_message(chat_id, t, reply_markup=reply_markup, disable_web_page_preview=True),
        text,
    )


async def _edit_body(cq_message, text: str, reply_markup):
    """Edits message on callback query — handles media captions or plain text."""
    if cq_message.photo or cq_message.video or cq_message.animation:
        await _safe_quote_send(lambda t: cq_message.edit_caption(t, reply_markup=reply_markup), text)
    else:
        await _safe_quote_send(
            lambda t: cq_message.edit_text(t, reply_markup=reply_markup, disable_web_page_preview=True),
            text,
        )


HELP_TEXT = (
    "🦋 **AVAILABLE COMMANDS**\n\n"
    "`/play <song>` — song play command\n"
    "`/skip` — skip song _(admin only)_\n"
    "`/pause` — pause song _(admin only)_\n"
    "`/resume` — resume song _(admin only)_\n"
    "`/stop` — stop song _(admin only)_\n"
    "`/reload` — bot refresh _(admin only)_\n"
    "⏪ -10s / +10s ⏩ — _(admin or requester)_\n"
    "⚙️ BOT SETTINGS — autoplay on/off_(admin or requester)_\n"
    "`/id` — check your group id"
)


def _welcome_text(user_name: str, user_id: int, bot_name: str, bot_username: str) -> str:
    user_tag = f"[{smallcaps_title(user_name)}](tg://user?id={user_id})"
    bot_tag = f"[{fancy_italic(bot_name)}](https://t.me/{bot_username})"
    body = (
        f"🦋 WELCOME TO {bot_tag}\n"
        f"PREMIUM  AD-FREE ✧ ULTRA SMOOTH\n\n"
        f"🦋 HIGH • QUALITY • MUSIC • BOT\n"
        f"FOR TELEGRAM GROUPS & CHANNELS\n\n"
        f"🦋 INSTANT STREAMING\n"
        f"🦋 ULTRA SMOOTH PLAYBACK\n"
        f"🦋 CRYSTAL CLEAR SOUND • NO LAG\n\n"
        f"🦋 TAP HELP TO VIEW ALL COMMANDS\n\n"
        f"🦋POWERED BY : [Aditya × APIs](https://t.me/AdityaXzexxyAPI)\n\n"
        f"╭─────────────  ─────────────╮\n"
        f" ENJOY THE MUSIC\n"
        f"╰─────────────  ─────────────╯"
    )
    return f"🦋 HEY {user_tag}..!!!\n\n" + expandable_quote(body)


def _group_start_text(bot_name: str) -> str:
    """Group start message with quick info regarding the music bot."""
    return f"✨ {fancy_italic(bot_name)} IS ONLINE AND READY ✨\n\n" + expandable_quote(
        "🎧 MUSIC PANEL\n"
        "➤ /play <SONG NAME> — play song\n"
        "➤ /skip • /pause • /resume • /stop\n"
        "➤ /queue — list of upcoming songs\n"
        "➤ /autoplayon • /autoplayoff\n\n"
        "⌾ CONTROL : ADMINS AND REQUESTER ONLY\n"
        "⌾ QUALITY : HIGH DEFINITION AUDIO\n\n"
        "•────────────────"
    )


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------
@bot.on_message(filters.command("start"))
async def start_cmd(client, message: Message):
    await db.add_user(message.from_user.id)
    me = await bot.get_me()

    if message.chat.type != "private":
        await db.add_chat(message.chat.id)
        media = await db.get_group_start_media()
        await _send_welcome(
            message.chat.id,
            _group_start_text(me.first_name),
            _start_keyboard(me.username),
            media,
        )
    else:
        media = await db.get_start_media()
        await _send_welcome(
            message.chat.id,
            _welcome_text(message.from_user.first_name, message.from_user.id, me.first_name, me.username),
            _start_keyboard(me.username),
            media,
        )

    if message.chat.type == "private" and config.OWNER_ID and message.from_user.id != config.OWNER_ID:
        try:
            await bot.send_message(
                config.OWNER_ID,
                f"👤 Bot user:\n"
                f"Name: {message.from_user.first_name}\n"
                f"Username: @{message.from_user.username}\n"
                f"ID: `{message.from_user.id}`",
            )
        except Exception as e:
            LOGGER.warning(f"Owner notify fail: {e}")


@bot.on_callback_query(filters.regex("^help_menu$"))
async def help_menu_cb(client, cq: CallbackQuery):
    await cq.answer()
    await _edit_body(cq.message, HELP_TEXT, _help_keyboard())


@bot.on_callback_query(filters.regex("^back_to_start$"))
async def back_to_start_cb(client, cq: CallbackQuery):
    await cq.answer()
    me = await bot.get_me()
    if cq.message.chat.type != "private":
        text = _group_start_text(me.first_name)
    else:
        text = _welcome_text(cq.from_user.first_name, cq.from_user.id, me.first_name, me.username)
    await _edit_body(cq.message, text, _start_keyboard(me.username))


# ---------------------------------------------------------------------------
# Adding bot to a group
# ---------------------------------------------------------------------------
@bot.on_message(filters.new_chat_members)
async def added_to_group(client, message: Message):
    me = await bot.get_me()
    if not any(u.id == me.id for u in message.new_chat_members):
        return

    await db.add_chat(message.chat.id)
    adder = message.from_user.first_name if message.from_user else "there"

    await message.reply_text(
        f"🎉 HEY **{adder}**!\n\n"
        f"THANK YOU FOR ADDING **[{me.first_name}](https://t.me/{me.username})** IN {message.chat.title}.\n\n"
        f"🎶 **{me.first_name}** IS NOW READY TO STREAM MUSIC, MANAGE CHATS AND DELIVER THE BEST EXPERIENCE.",
        reply_markup=_start_keyboard(me.username),
        disable_web_page_preview=True,
    )


# ---------------------------------------------------------------------------
# /play — open to everyone
# ---------------------------------------------------------------------------
@bot.on_message(filters.command("play") & filters.group)
async def play_command(client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"❌ {smallcaps_title('Enter Song name')}!\nExample: `/play star boy`"
        )

    query = message.text.split(None, 1)[1]
    chat_id = message.chat.id
    requester = message.from_user.mention if message.from_user else "Someone"
    requester_id = message.from_user.id if message.from_user else None

    # Confirm first whether the assistant account is in this group — otherwise, 
    # VC cannot be joined. Assistant joining attempt happens here.
    joined, reason = await ensure_assistant_in_chat(chat_id)
    if not joined:
        if reason == "flood_wait":
            return await message.reply_text(ASSISTANT_FLOOD_TEXT)
        return await message.reply_text(ASSISTANT_NOT_JOINED_TEXT)

    status = await message.reply_text(random_processing_text())

    track = await search_track(query)
    if not track:
        return await status.edit_text(f"❌ {smallcaps_title('nothing searched Try another name')}.")

    # New API downloads the song — takes some time, so we wait until it's ready (max 3 minutes).
    try:
        await status.edit_text(f"⏳ {smallcaps_title('SONG DOWNLOADING')}...")
    except Exception:
        pass

    try:
        stream_url = await get_stream_url(track["id"])
    except Exception as e:
        LOGGER.error(f"Stream URL error: {e}")
        return await status.edit_text(
            f"❌ {smallcaps_title('download error')} — "
            f"{smallcaps_title('Song Loading Error 🔴')}."
        )

    track["stream_url"] = stream_url
    track["requested_by"] = requester
    track["requested_by_id"] = requester_id

    # If something is already playing -> add to queue
    if q.is_playing(chat_id):
        position = q.push(chat_id, track)
        await status.delete()
        await message.reply_text(
            f"🎵 {smallcaps_title('added to queue at')} #{position}\n"
            f"📝 {smallcaps_title('title')} : {track['title']}\n"
            f"🕐 {smallcaps_title('duration')} : {track['duration']} MINUTES\n"
            f"👤 {smallcaps_title('requested')} : {requester}"
        )
        return

    await status.delete()
    await _start_playing(chat_id, track, message)


async def _start_playing(chat_id: int, track: dict, message: Message):
    """Joins/changes VC stream to play track and sends the Now Playing card."""
    try:
        try:
            await call_py.join_group_call(chat_id, AudioPiped(track["stream_url"]))
        except NoActiveGroupCall:
            return await message.reply_text(
                f"❌ **{smallcaps_title('voice chat is not active')}!**\n\n"
                f"{smallcaps_title('first start a voice chat on group')}:\n"
                "Group Settings → Voice Chat → Start Voice Chat\n\n"
                f"{smallcaps_title('than')} `/play` {smallcaps_title('send Again')}."
            )
        except Exception as e:
            if _is_peer_error(e):
                await _refresh_assistant_peers()
            try:
                await call_py.change_stream(chat_id, AudioPiped(track["stream_url"]))
            except Exception as e2:
                LOGGER.error(f"Play error: {e2}")
                return await message.reply_text(
                    f"❌ **{smallcaps_title('play error')}**\n\n"
                    f"{smallcaps_title('voice chat active or not check Please')}."
                )

        q.set_now_playing(chat_id, track)
        await _send_now_playing(chat_id, track, message)

    except Exception as e:
        LOGGER.error(f"_start_playing fatal error: {e}")
        await message.reply_text(f"❌ {smallcaps_title('Something Wrong')}.")


def _now_playing_caption(track: dict) -> str:
    artists = [a.strip() for a in str(track.get("channel") or "").replace("-", ",").split(",") if a.strip()]
    if not artists:
        artists = [track.get("requested_by", "Unknown")]

    body = (
        f" {smallcaps_title(track['title'])}\n"
        f"{bullet_lines(smallcaps_title(a) for a in artists)}\n\n"
        f"◽ {smallcaps_title('duration')} : {track['duration']}\n"
        f"◽ {smallcaps_title('by')} : {track.get('requested_by', 'Unknown')}\n\n"
        f"{DIVIDER}"
    )
    return f"🎧 {smallcaps_title('NOW PLAYING')}..!!! \n\n" + expandable_quote(body)


async def _send_now_playing(chat_id: int, track: dict, message: Message = None, edit_message: Message = None):
    """
    Sends the Now Playing card. If `edit_message` is provided (like from skip button), 
    it updates the message in place instead of deleting and sending a new one.
    """
    caption = _now_playing_caption(track)
    card = await generate_now_playing_card(track.get("thumbnail"), track["title"], track["duration"])
    markup = _controls_keyboard()
    media = card or track.get("thumbnail")

    sent = None

    if edit_message is not None:
        try:
            if media:
                sent = await edit_message.edit_media(InputMediaPhoto(media, caption=caption), reply_markup=markup)
            else:
                sent = await edit_message.edit_text(caption, reply_markup=markup, disable_web_page_preview=True)
        except Exception as e:
            LOGGER.warning(f"Now playing in-place edit fail, new message sending: {e}")

    if sent is None:
        try:
            if media:
                sent = await _safe_quote_send(
                    lambda t: bot.send_photo(chat_id, media, caption=t, reply_markup=markup), caption
                )
            elif message is not None:
                sent = await _safe_quote_send(
                    lambda t: message.reply_text(t, reply_markup=markup, disable_web_page_preview=True), caption
                )
            else:
                sent = await _safe_quote_send(
                    lambda t: bot.send_message(chat_id, t, reply_markup=markup, disable_web_page_preview=True),
                    caption,
                )
        except Exception as e:
            LOGGER.warning(f"Now playing card send fail: {e}")
            plain = strip_quotes(caption)
            if message is not None:
                sent = await message.reply_text(plain, reply_markup=markup, disable_web_page_preview=True)
            else:
                sent = await bot.send_message(chat_id, plain, reply_markup=markup, disable_web_page_preview=True)

    # Live progress bar starts — updates forward automatically from 00:00 to duration.
    if sent is not None:
        total_sec = duration_to_seconds(track.get("duration"))
        progress.start(chat_id, track["id"])
        progress.start_updater(
            chat_id, sent,
            lambda: _now_playing_caption(track),
            _controls_keyboard,
            track["id"], total_sec,
        )

    return sent


# ---------------------------------------------------------------------------
# Autoplay — When the queue ends and autoplay is ON, the bot 
# automatically finds and plays a related track (similar to YouTube autoplay).
# Toggle: Now Playing -> ⚙️ Bot Settings.
# ---------------------------------------------------------------------------
async def _autoplay_next_track(chat_id: int):
    if not await db.get_autoplay(chat_id):
        return None

    current = q.get_now_playing(chat_id)
    if not current:
        return None

    try:
        track = await get_related_track(current.get("title", ""), exclude_id=current.get("id"))
        if not track:
            return None
        track["stream_url"] = await get_stream_url(track["id"])

    except Exception as e:
        LOGGER.warning(f"Autoplay next track fail: {e}")
        return None

    track["requested_by"] = f"🔁 {smallcaps_title('autoplay')}"
    track["requested_by_id"] = current.get("requested_by_id")
    return track


# ---------------------------------------------------------------------------
# Play next track from queue when stream ends
# ---------------------------------------------------------------------------
@call_py.on_stream_end()
async def on_stream_end(client, update):
    chat_id = update.chat_id
    next_track = q.pop_next(chat_id)

    if not next_track:
        # Queue empty — autoplay ON, so play a related track automatically
        next_track = await _autoplay_next_track(chat_id)

    if not next_track:
        q.set_now_playing(chat_id, None)
        progress.clear(chat_id)
        try:
            await call_py.leave_group_call(chat_id)
        except Exception as e:
            LOGGER.warning(f"Auto leave fail: {e}")
        return

    try:
        try:
            await call_py.change_stream(chat_id, AudioPiped(next_track["stream_url"]))
        except Exception as e:
            if _is_peer_error(e):
                await _refresh_assistant_peers()
            await call_py.join_group_call(chat_id, AudioPiped(next_track["stream_url"]))

        q.set_now_playing(chat_id, next_track)
        await _send_now_playing(chat_id, next_track)
    except Exception as e:
        LOGGER.error(f"Auto-play next error: {e}")


# ---------------------------------------------------------------------------
# /autoplayon /autoplayoff — Toggle group autoplay (admin/owner or requester only)
# ---------------------------------------------------------------------------
async def _set_autoplay_cmd(client, message: Message, value: bool):
    if not await _can_control(client, message.chat.id, message.from_user.id):
        return await message.reply_text(NOT_YOUR_REQUEST_TEXT)
    await db.set_autoplay(message.chat.id, value)
    state = smallcaps_title("on") if value else smallcaps_title("off")
    await message.reply_text(f"🔁 {smallcaps_title('autoplay')} : {state}")


@bot.on_message(filters.command(["autoplayon", "autuolayon"]) & filters.group)
async def autoplay_on_command(client, message: Message):
    await _set_autoplay_cmd(client, message, True)


@bot.on_message(filters.command(["autoplayoff", "autuolayoff"]) & filters.group)
async def autoplay_off_command(client, message: Message):
    await _set_autoplay_cmd(client, message, False)


# ---------------------------------------------------------------------------
# /skip /pause /resume /stop — Group admin/owner or track requester only
# ---------------------------------------------------------------------------
@bot.on_message(filters.command("skip") & filters.group)
async def skip_command(client, message: Message):
    if not await _can_control(client, message.chat.id, message.from_user.id):
        return await message.reply_text(NOT_YOUR_REQUEST_TEXT)

    chat_id = message.chat.id
    next_track = q.pop_next(chat_id)
    if not next_track:
        q.set_now_playing(chat_id, None)
        progress.clear(chat_id)
        try:
            await call_py.leave_group_call(chat_id)
        except Exception:
            pass
        return await message.reply_text(f"⏭ {smallcaps_title('queue is empty, left voice chat')}.")

    try:
        await call_py.change_stream(chat_id, AudioPiped(next_track["stream_url"]))
    except Exception as e:
        if _is_peer_error(e):
            await _refresh_assistant_peers()
        try:
            await call_py.join_group_call(chat_id, AudioPiped(next_track["stream_url"]))
        except Exception as e2:
            LOGGER.error(f"Skip error: {e2}")
            return await message.reply_text(f"❌ {smallcaps_title('skip error try Again')}.")

    q.set_now_playing(chat_id, next_track)
    await _send_now_playing(chat_id, next_track, message)


@bot.on_message(filters.command("pause") & filters.group)
async def pause_command(client, message: Message):
    if not await _can_control(client, message.chat.id, message.from_user.id):
        return await message.reply_text(NOT_YOUR_REQUEST_TEXT)
    try:
        await call_py.pause_stream(message.chat.id)
        q.set_state(message.chat.id, "paused")
        progress.pause(message.chat.id)
        await message.reply_text(f"⏸ {smallcaps_title('paused')}.")
    except Exception as e:
        await message.reply_text(f"❌ {e}")


@bot.on_message(filters.command("resume") & filters.group)
async def resume_command(client, message: Message):
    if not await _can_control(client, message.chat.id, message.from_user.id):
        return await message.reply_text(NOT_YOUR_REQUEST_TEXT)
    try:
        await call_py.resume_stream(message.chat.id)
        q.set_state(message.chat.id, "playing")
        progress.resume(message.chat.id)
        await message.reply_text(f"▶️ {smallcaps_title('resumed')}.")
    except Exception as e:
        await message.reply_text(f"❌ {e}")


@bot.on_message(filters.command(["stop", "end"]) & filters.group)
async def stop_command(client, message: Message):
    if not await _can_control(client, message.chat.id, message.from_user.id):
        return await message.reply_text(NOT_YOUR_REQUEST_TEXT)
    try:
        await call_py.leave_group_call(message.chat.id)
    except Exception:
        pass
    q.clear(message.chat.id)
    progress.clear(message.chat.id)
    await message.reply_text(f"⏹️ {smallcaps_title('voice chat stopped')}.")


# ---------------------------------------------------------------------------
# /reload — Group admin/owner only. Checks if bot itself is an admin.
# ---------------------------------------------------------------------------
@bot.on_message(filters.command("reload") & filters.group)
async def reload_command(client, message: Message):
    if not await _is_group_admin(client, message.chat.id, message.from_user.id):
        return await message.reply_text(ADMIN_ONLY_TEXT)

    me = await bot.get_me()
    try:
        bot_member = await client.get_chat_member(message.chat.id, me.id)
        is_bot_admin = bot_member.status in ADMIN_STATUSES
    except Exception as e:
        LOGGER.warning(f"Reload admin-check fail: {e}")
        is_bot_admin = False

    if is_bot_admin:
        await message.reply_text(f"✅ {smallcaps_title('reloaded successfully')}.")
    else:
        await message.reply_text(
            f"❌ {smallcaps_title('first add me group admin then')} `/reload` {smallcaps_title('use')}."
        )


# ---------------------------------------------------------------------------
# ⏪ -10s / ⏩ +10s — Restarts stream from position using ffmpeg `-ss`, 
# and updates the progress bar position accordingly.
# ---------------------------------------------------------------------------
async def _seek_stream(chat_id: int, delta: int) -> "int | None":
    track = q.get_now_playing(chat_id)
    if not track:
        return None

    total = duration_to_seconds(track.get("duration"))
    position = int(progress.elapsed(chat_id)) + delta
    position = max(0, position)
    if total and position >= total - 1:
        position = max(0, total - 2)

    try:
        stream = AudioPiped(track["stream_url"], additional_ffmpeg_parameters=f"-ss {position}")
    except TypeError:
        LOGGER.warning("AudioPiped additional_ffmpeg_parameters not supported — skipping seek")
        return None

    try:
        await call_py.change_stream(chat_id, stream)
    except Exception as e:
        if _is_peer_error(e):
            await _refresh_assistant_peers()
            await call_py.change_stream(chat_id, stream)
        else:
            raise

    progress.seek(chat_id, position)
    if q.get_state(chat_id) == "paused":
        q.set_state(chat_id, "playing")
    return position


# ---------------------------------------------------------------------------
# Inline buttons (below Now Playing card)
# ---------------------------------------------------------------------------
@bot.on_callback_query(filters.regex("^m_"))
async def controls_callback(client, cq: CallbackQuery):
    chat_id = cq.message.chat.id
    action = cq.data

    if action in ("m_resume", "m_pause", "m_skip", "m_stop", "m_back10", "m_fwd10", "m_autoplay"):
        if not await _can_control(client, chat_id, cq.from_user.id):
            return await cq.answer(NOT_YOUR_REQUEST_TEXT, show_alert=True)

    try:
        if action == "m_resume":
            await call_py.resume_stream(chat_id)
            q.set_state(chat_id, "playing")
            progress.resume(chat_id)
            await cq.answer("▶️ Resumed")

        elif action == "m_pause":
            await call_py.pause_stream(chat_id)
            q.set_state(chat_id, "paused")
            progress.pause(chat_id)
            await cq.answer("⏸ Paused")

        elif action == "m_replay":
            track = q.get_now_playing(chat_id)
            if track:
                await call_py.change_stream(chat_id, AudioPiped(track["stream_url"]))
                progress.replay(chat_id)
                await cq.answer("🔁 Replaying")
            else:
                await cq.answer(smallcaps_title("nothing is playing"), show_alert=True)

        elif action == "m_skip":
            await cq.answer("⏭ Skipping")
            next_track = q.pop_next(chat_id)
            if not next_track:
                q.set_now_playing(chat_id, None)
                progress.clear(chat_id)
                await call_py.leave_group_call(chat_id)
                try:
                    await cq.message.edit_reply_markup(None)
                except Exception:
                    pass
                await cq.message.reply_text(f"⏭ {smallcaps_title('queue is empty, left voice chat')}.")
            else:
                await call_py.change_stream(chat_id, AudioPiped(next_track["stream_url"]))
                q.set_now_playing(chat_id, next_track)
                await _send_now_playing(chat_id, next_track, edit_message=cq.message)

        elif action == "m_stop":
            await call_py.leave_group_call(chat_id)
            q.clear(chat_id)
            progress.clear(chat_id)
            await cq.answer("⏹ Stopped")
            try:
                await cq.message.edit_reply_markup(None)
            except Exception:
                pass
            await cq.message.reply_text(f"⏹️ {smallcaps_title('voice chat stopped')}.")

        elif action in ("m_back10", "m_fwd10"):
            delta = SEEK_STEP if action == "m_fwd10" else -SEEK_STEP
            position = await _seek_stream(chat_id, delta)
            if position is None:
                await cq.answer(smallcaps_title("nothing is playing"), show_alert=True)
            else:
                arrow = "⏩" if delta > 0 else "⏪"
                await cq.answer(f"{arrow} {format_duration(position)}")

        elif action == "m_settings":
            await cq.answer()
            autoplay_on = await db.get_autoplay(chat_id)
            await cq.message.edit_reply_markup(_settings_keyboard(autoplay_on))

        elif action == "m_autoplay":
            new_value = not await db.get_autoplay(chat_id)
            await db.set_autoplay(chat_id, new_value)
            state = smallcaps_title("on") if new_value else smallcaps_title("off")
            await cq.answer(f"🔁 {smallcaps_title('autoplay')} : {state}")
            await cq.message.edit_reply_markup(_settings_keyboard(new_value))

        elif action == "m_back":
            await cq.answer()
            await cq.message.edit_reply_markup(_controls_keyboard())

        elif action == "m_close":
            await cq.answer()
            progress.cancel_task(chat_id)
            await cq.message.delete()

    except Exception as e:
        LOGGER.warning(f"Callback error ({action}): {e}")
        await cq.answer(f"❌ {e}", show_alert=True)


# ---------------------------------------------------------------------------
# Owner: /addvd /delvd — Image/video/gif sent with the private start message
# ---------------------------------------------------------------------------
@bot.on_message(filters.command("addvd") & OWNER_FILTER)
async def addvd_command(client, message: Message):
    _pending_addvd.add(message.from_user.id)
    await message.reply_text(
        f"🖼 {smallcaps_title('now send an image, video or gif — it will be sent with the PRIVATE start message from now on')}."
    )


@bot.on_message(filters.command("delvd") & OWNER_FILTER)
async def delvd_command(client, message: Message):
    await db.delete_start_media()
    _pending_addvd.discard(message.from_user.id)
    await message.reply_text(f"🗑 {smallcaps_title('private start message media has been removed')}.")


@bot.on_message(filters.command("addvd2") & OWNER_FILTER)
async def addvd2_command(client, message: Message):
    _pending_addvd2.add(message.from_user.id)
    await message.reply_text(
        f"🖼 {smallcaps_title('now send an image, video or gif — it will be sent with the GROUP start message from now on')}."
    )


@bot.on_message(filters.command("delvd2") & OWNER_FILTER)
async def delvd2_command(client, message: Message):
    await db.delete_group_start_media()
    _pending_addvd2.discard(message.from_user.id)
    await message.reply_text(f"🗑 {smallcaps_title('group start message media has been removed')}.")


@bot.on_message(
    (filters.photo | filters.video | filters.animation)
    & OWNER_FILTER
    & filters.create(
        lambda _, __, m: bool(m.from_user)
        and (m.from_user.id in _pending_addvd or m.from_user.id in _pending_addvd2)
    )
)
async def addvd_receive(client, message: Message):
    is_group_variant = message.from_user.id in _pending_addvd2
    _pending_addvd.discard(message.from_user.id)
    _pending_addvd2.discard(message.from_user.id)

    if message.photo:
        file_id, media_type = message.photo.file_id, "photo"
    elif message.video:
        file_id, media_type = message.video.file_id, "video"
    elif message.animation:
        file_id, media_type = message.animation.file_id, "animation"
    else:
        return

    if is_group_variant:
        await db.set_group_start_media(file_id, media_type)
        await message.reply_text(f"✅ {smallcaps_title('group start message media set successfully')}.")
    else:
        await db.set_start_media(file_id, media_type)
        await message.reply_text(f"✅ {smallcaps_title('private start message media set successfully')}.")


# ---------------------------------------------------------------------------
# Owner: /broadcast
# ---------------------------------------------------------------------------
@bot.on_message(filters.command("broadcast") & OWNER_FILTER)
async def broadcast_command(client, message: Message):
    if len(message.command) < 2 and not message.reply_to_message:
        return await message.reply_text(
            f"❌ {smallcaps_title('provide message for broadcast')}!\nExample: `/broadcast Hello everyone`"
        )

    text = message.text.split(None, 1)[1] if len(message.command) > 1 else None
    users = await db.get_all_users()
    status = await message.reply_text(f"📢 {smallcaps_title('broadcasting to')} {len(users)} {smallcaps_title('users')}...")

    sent, failed = 0, 0
    for uid in users:
        try:
            if message.reply_to_message:
                await message.reply_to_message.copy(uid)
            else:
                await bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1

    await status.edit_text(
        f"✅ {smallcaps_title('broadcast done')}.\n{smallcaps_title('sent')}: {sent}\n{smallcaps_title('failed')}: {failed}"
    )


# ---------------------------------------------------------------------------
# /id — Show user and chat id
# ---------------------------------------------------------------------------
@bot.on_message(filters.command("id"))
async def id_command(client, message: Message):
    user_id = message.from_user.id if message.from_user else "Unknown"
    lines = [f"👤 **{smallcaps_title('your id')}:** `{user_id}`"]
    if message.chat.type != "private":
        lines.append(f"👥 **{smallcaps_title('chat id')}:** `{message.chat.id}`")
    if message.reply_to_message and message.reply_to_message.from_user:
        lines.append(f"↩️ **{smallcaps_title('replied user id')}:** `{message.reply_to_message.reply_to_message.from_user.id if hasattr(message.reply_to_message, 'from_user') else message.reply_to_message.from_user.id}`")
    await message.reply_text("\n".join(lines))
