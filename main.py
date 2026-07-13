"""
Telegram Media Bot + Web Dashboard
Chạy đồng thời bot polling + FastAPI (PORT do Railway inject).
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from pathlib import Path

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
    """HTTP server — Railway/Web health (nếu bật) + dashboard."""
    import uvicorn
    from web.app import app

    logger.info("Starting web on %s:%s", WEB_HOST, WEB_PORT)
    config = uvicorn.Config(
        app,
        host=WEB_HOST,
        port=WEB_PORT,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    asyncio.run(server.serve())


async def run_bot() -> None:
    token_ok = bool(BOT_TOKEN and ":" in BOT_TOKEN and "YOUR_BOT" not in BOT_TOKEN)
    if not token_ok:
        logger.error(
            "❌ CHƯA CÓ BOT_TOKEN! Railway → Variables → BOT_TOKEN=... rồi Redeploy"
        )
        stats.set_bot_online(False)
        stats.set_last_error("BOT_TOKEN missing")
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
        logger.error("getMe failed: %s — retry 30s", e)
        stats.set_bot_online(False)
        stats.set_last_error(str(e))
        await asyncio.sleep(30)
        return await run_bot()

    await app.start()
    await app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )

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
    logger.info(
        "BOT_TOKEN set: %s",
        bool(BOT_TOKEN and ":" in BOT_TOKEN and "YOUR_BOT" not in BOT_TOKEN),
    )

    # Web TRƯỚC — bind PORT ngay (tránh healthcheck fail)
    web_thread = threading.Thread(target=start_web_server, name="web", daemon=True)
    web_thread.start()
    time.sleep(2.0)

    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
        stats.set_bot_online(False)
    except Exception as e:
        logger.exception("Fatal: %s", e)
        stats.set_bot_online(False)
        stats.set_last_error(str(e))
        # Giữ process sống để web + Railway không crash loop ngay
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
