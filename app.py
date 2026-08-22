import asyncio
import threading

import aiohttp
import uvicorn
from fastapi import FastAPI
from pyrogram import idle
from pyrogram.types import BotCommand

import config
import db
import botstate
from clients import bot, assistant, call_py, LOGGER

# Import is necessary to register handlers
import play  # noqa: F401

web = FastAPI()


@web.get("/")
async def root():
    return {"status": "running"}


def run_web():
    uvicorn.run(web, host="0.0.0.0", port=config.PORT, log_level="warning")


async def keep_alive():
    """
    Render puts free web services to sleep after 15 minutes of inactivity.
    Therefore, the bot periodically pings its own URL so that the process 
    never goes offline. Render provides RENDER_EXTERNAL_URL automatically 
    during deployment, there is no need to set it manually.
    """
    if not config.RENDER_EXTERNAL_URL:
        LOGGER.info("RENDER_EXTERNAL_URL is not set — keep-alive ping is being skipped.")
        return

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(config.RENDER_EXTERNAL_URL, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    LOGGER.info(f"Keep-alive ping: {resp.status}")
            except Exception as e:
                LOGGER.warning(f"Keep-alive ping fail: {e}")
            # First ping immediately, followed by every PING_INTERVAL seconds — 
            # Render free tier puts services to sleep after ~15 min inactivity, 
            # so an immediate ping on startup is necessary, not just after sleep.
            await asyncio.sleep(config.PING_INTERVAL)


async def register_bot_commands():
    """Sets bot commands so that the menu appears when typing '/' in a group."""
    try:
        await bot.set_bot_commands(
            [
                BotCommand("start", "Start the bot"),
                BotCommand("play", "Play a song"),
                BotCommand("skip", "Skip to next song"),
                BotCommand("pause", "Pause the song"),
                BotCommand("resume", "Resume the song"),
                BotCommand("stop", "Stop the song"),
                BotCommand("reload", "Refresh the bot (admin only)"),
                BotCommand("id", "Check your/group ID"),
            ]
        )
    except Exception as e:
        LOGGER.warning(f"Could not set bot commands: {e}")


async def _run_once():
    await bot.start()
    LOGGER.info("✅ Bot started")

    await assistant.start()
    LOGGER.info("✅ Assistant started")

    # IMPORTANT: Fetch assistant dialogs once so that pyrogram caches the peer + access_hash 
    # for every group/channel. Without this, "ValueError: Peer id invalid: ..." occurs later 
    # when the bot tries change_stream/leave_group_call on a chat whose peer is not 
    # in the assistant's local cache.
    try:
        async for _ in assistant.get_dialogs():
            pass
        LOGGER.info("✅ Assistant peers cached")
    except Exception as e:
        LOGGER.warning(f"Error caching dialogs: {e}")

    await call_py.start()
    LOGGER.info("✅ PyTgCalls started — the bot is now ready to play music")

    # Load the status previously set by the owner's /on /off commands
    try:
        botstate.set_enabled(await db.get_bot_status())
        LOGGER.info(f"✅ Bot status loaded: {'ON' if botstate.is_enabled() else 'OFF'}")
    except Exception as e:
        LOGGER.warning(f"Could not load bot status, keeping default ON: {e}")

    await register_bot_commands()

    if config.LOG_GROUP_ID:
        try:
            await bot.send_message(config.LOG_GROUP_ID, "✅ Bot has restarted and is now online.")
        except Exception as e:
            LOGGER.warning(f"Could not send message to log group: {e}")

    keep_alive_task = asyncio.create_task(keep_alive())

    try:
        await idle()
    finally:
        keep_alive_task.cancel()
        try:
            await bot.stop()
        except Exception:
            pass
        try:
            await assistant.stop()
        except Exception:
            pass
        LOGGER.info("🛑 Bot stopped")


async def main():
    """
    Wraps _run_once() so that any unexpected crash (network drop, connection reset, etc.) 
    doesn't permanently take the bot offline — the process restarts itself after a short delay 
    unless Render kills the process itself.
    """
    while True:
        try:
            await _run_once()
            break  # idle() only returns when the process is normally stopped
        except Exception as e:
            LOGGER.error(f"Bot crashed, restarting in 10 seconds: {e}")
            await asyncio.sleep(10)


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    asyncio.get_event_loop().run_until_complete(main())