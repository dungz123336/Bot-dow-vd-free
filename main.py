"""
Telegram Media Bot + Web Dashboard
----------------------------------
Chạy đồng thời:
  - Bot Telegram (long polling)
  - Web dashboard FastAPI
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from pathlib import Path

# Ensure project root on path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.config import BOT_TOKEN, WEB_HOST, WEB_PORT
from bot.handlers import build_application
from bot import stats

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("main")


def start_web_server() -> None:
    import uvicorn
    from web.app import app

    config = uvicorn.Config(
        app,
        host=WEB_HOST,
        port=WEB_PORT,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    # Chạy trong thread với event loop riêng
    asyncio.run(server.serve())


async def run_bot() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE" or ":" not in BOT_TOKEN:
        logger.error(
            "Chưa cấu hình BOT_TOKEN!\n"
            "1. Mở Telegram, chat với @BotFather\n"
            "2. /newbot hoặc /token để lấy token\n"
            "3. Dán vào file .env: BOT_TOKEN=123456:ABC...\n"
            "4. Chạy lại: py main.py"
        )
        stats.set_bot_online(False)
        stats.set_last_error("BOT_TOKEN chưa được cấu hình trong .env")
        # Vẫn giữ process sống để web dashboard chạy
        while True:
            await asyncio.sleep(3600)
        return

    app = build_application(BOT_TOKEN)

    logger.info("Starting Telegram bot polling...")
    await app.initialize()
    try:
        me = await app.bot.get_me()
        logger.info("Bot online: @%s (id=%s)", me.username, me.id)
        stats.set_bot_online(True)
        stats.set_last_error(None)
    except Exception as e:
        logger.error("Không kết nối được bot: %s", e)
        stats.set_bot_online(False)
        stats.set_last_error(str(e))
        raise

    await app.start()
    await app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )

    # Keep alive until cancelled
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        stats.set_bot_online(False)
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main() -> None:
    logger.info("Dashboard: http://127.0.0.1:%s", WEB_PORT)
    logger.info("Admin ID: xem file .env")

    web_thread = threading.Thread(target=start_web_server, name="web", daemon=True)
    web_thread.start()

    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
        stats.set_bot_online(False)


if __name__ == "__main__":
    main()
