import os

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8937773941:AAF2ztacLB07q3ag957Pj5ivt_w5nQGah-Q")
STRING_SESSION = os.getenv("STRING_SESSION", "BQHDLbkACv2RVuiL89iWyGsF60f1WYT8SLuaRLBh9hOAHw9PtXJ1T5VMue2K-QcpCmG6vE4S2wtD-VhfI-9j6xbbMiE1B78qhHRLigZx3kv1m-LuFRp6fzFiNIu7pO6l10RBfKZYPiSZ0BVPX559rpt87lgw3aM_7oNN8UELYj_YliV2JFfU9-Im8yyTp7EA-omBHk239_5Ml8vTTaN7fnJSCHnHXg6K2TP5Kl-GtwZwh5MbADVy9HnjT3a7r4jpeSMedO2oAopXAHloMfgi0BJcj5bqOr24eT5MWWMwoxfEcsn7C_j9Ee3B6zntV2iNFTvKM7XyPdle0ZZcUfiSrfE-IvzzFgAAAAGloo-pAA")
LOG_GROUP_ID = int(os.getenv("LOG_GROUP_ID", "-1004485534183"))
OWNER_ID = int(os.getenv("OWNER_ID", "8220803062"))
MONGO_DB_URI = os.getenv("MONGO_DB_URI", "mrshuvo739_db_user")
PORT = int(os.getenv("PORT", "10000"))

# Start message ke buttons ke links (support group / update channel / owner)
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/NBxCHATx")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/NBxCODEX")
OWNER_URL = os.getenv("OWNER_URL", "t.me/NabilWave")

# Render apne aap yeh env variable deta hai (e.g. https://your-app.onrender.com)
# Isse bot khud ko periodically ping karke sleep hone se bachata hai.
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")
PING_INTERVAL = int(os.getenv("PING_INTERVAL", "600"))  # seconds (10 min default)

# Assistant (userbot) ka username — /play chalne par bot check karta hai ki
# yeh account group mein hai ya nahi, aur agar nahi hai to isi username se
# (ya invite link se) join karwane ki koshish karta hai.
ASSISTANT_USERNAME = os.getenv("ASSISTANT_USERNAME", "@NABILxMUSIC_bot")