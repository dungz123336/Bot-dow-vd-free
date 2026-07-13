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
            "❌ CHƯA CÓ BOT_TOKEN trên Railway!\n"
            "Vào Project → Variables → thêm:\n"
            "  BOT_TOKEN = token từ @BotFather\n"
            "  ADMIN_ID  = 5949258698\n"
            "Rồi Redeploy."
        )
        stats.set_bot_online(False)
        stats.set_last_error("BOT_TOKEN chưa được cấu hình (Railway Variables)")
        # Giữ process sống (tránh crash loop) + web health vẫn chạy
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
        # Không crash process — log lỗi (token sai / mạng) và retry
        logger.error("Không kết nối được bot: %s — retry sau 30s", e)
        stats.set_bot_online(False)
        stats.set_last_error(str(e))
        await asyncio.sleep(30)
        return await run_bot()

    await app.start()
    try:
        await app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
    except Exception as e:
        logger.error("start_polling failed: %s", e)
        stats.set_last_error(str(e))
        raise

    # Keep alive until cancelled
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        stats.set_bot_online(False)
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception:
            pass


def main() -> None:
    logger.info("=== Telegram Media Bot starting ===")
    logger.info("WEB host=%s port=%s", WEB_HOST, WEB_PORT)
    logger.info("BOT_TOKEN set: %s", bool(BOT_TOKEN and ":" in BOT_TOKEN and "YOUR_BOT" not in BOT_TOKEN))

    web_thread = threading.Thread(target=start_web_server, name="web", daemon=True)
    web_thread.start()
    # Cho web bind PORT trước (Railway health / proxy)
    import time

    time.sleep(1.5)

    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
        stats.set_bot_online(False)
    except Exception as e:
        logger.exception("Fatal: %s", e)
        stats.set_bot_online(False)
        stats.set_last_error(str(e))
        # Exit non-zero so Railway restarts
        raise


if __name__ == "__main__":
    main()
