import logging
import time
from pyrogram import Client
from pytgcalls import PyTgCalls
from motor.motor_asyncio import AsyncIOMotorClient

import config

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] - [%(levelname)s] - %(name)s - %(message)s",
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("pytgcalls").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Compatibility shim: raw-update handler for py-tgcalls==0.9.7 (very old)
# assumes that the UpdateGroupCall object has a direct `.chat_id` 
# attribute (data2[update.chat_id]). In newer kurigram/pyrogram schemas, 
# this field was removed — now only `.peer` (PeerChat/PeerChannel) 
# is available. Because of this, the following error used to occur:
#   AttributeError: 'UpdateGroupCall' object has no attribute 'chat_id'
# This patch makes `.chat_id` a computed property that extracts it 
# from `.peer` to provide the exact same old behavior — without 
# modifying py-tgcalls or kurigram version.
# ---------------------------------------------------------------------------
try:
    from pyrogram.raw.types import UpdateGroupCall, PeerChat, PeerChannel

    if "chat_id" not in UpdateGroupCall.__dict__:
        def _shim_chat_id(self):
            peer = getattr(self, "peer", None)
            if isinstance(peer, PeerChat):
                return peer.chat_id
            if isinstance(peer, PeerChannel):
                return peer.channel_id
            raise AttributeError("chat_id")

        UpdateGroupCall.chat_id = property(_shim_chat_id)
        logging.getLogger("MusicBot").info(
            "✅ UpdateGroupCall.chat_id compatibility shim applied"
        )
except Exception as _shim_err:  # Bot should not crash if patch ever fails
    logging.getLogger("MusicBot").warning(
        f"⚠️ UpdateGroupCall compatibility shim skipped: {_shim_err}"
    )

LOGGER = logging.getLogger("MusicBot")

# Process start time — to display uptime in the start message
START_TIME = time.monotonic()

bot = Client(
    name="MusicBot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    in_memory=True,
)

assistant = Client(
    name="Assistant",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    session_string=config.STRING_SESSION,
    in_memory=True,
)

call_py = PyTgCalls(assistant)

mongo_client = AsyncIOMotorClient(config.MONGO_DB_URI)
db = mongo_client["MusicBotDB"]
