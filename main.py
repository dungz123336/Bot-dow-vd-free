"""
Telegram Media Bot + Web Dashboard
Railway-ready: web on PORT + long polling + keep-alive.
"""

from __future__ import annotations

import asyncio
import logging
import os
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


def keep_alive_loop() -> None:
    """
    Self-ping public URL mỗi 4 phút — giảm khả năng host free sleep.
    Railway inject: RAILWAY_PUBLIC_DOMAIN
    """
    import httpx

    domain = (
        os.getenv("RAILWAY_PUBLIC_DOMAIN")
        or os.getenv("RAILWAY_STATIC_URL")
        or os.getenv("KEEP_ALIVE_URL")
        or ""
    ).strip()
    if not domain:
        logger.info("Keep-alive: no public domain (OK if process stays always-on)")
        return
    if not domain.startswith("http"):
        domain = "https://" + domain
    url = domain.rstrip("/") + "/api/health"
    logger.info("Keep-alive ping → %s", url)
    while True:
        try:
            httpx.get(url, timeout=15)
        except Exception as e:
            logger.warning("Keep-alive ping failed: %s", e)
        time.sleep(240)


async def run_bot() -> None:
    token_ok = bool(BOT_TOKEN and ":" in BOT_TOKEN and "YOUR_BOT" not in BOT_TOKEN)
    if not token_ok:
        logger.error(
            "❌ BOT_TOKEN THIẾU trên Railway!\n"
            "Vào service → Variables → Add:\n"
            "  BOT_TOKEN = token từ @BotFather\n"
            "  ADMIN_ID  = 5949258698\n"
            "Rồi Redeploy. Web 'Online' KHÔNG có nghĩa bot đang nhận tin."
        )
        stats.set_bot_online(False)
        stats.set_last_error("BOT_TOKEN missing on Railway Variables")
        while True:
            await asyncio.sleep(3600)
        return

    # Che token in logs
    masked = BOT_TOKEN[:8] + "…" + BOT_TOKEN[-4:]
    logger.info("BOT_TOKEN loaded: %s", masked)

    while True:
        app = None
        try:
            app = build_application(BOT_TOKEN)
            logger.info("Initializing Telegram application...")
            await app.initialize()

            me = await app.bot.get_me()
            logger.info("✅ Bot online: @%s (id=%s)", me.username, me.id)
            stats.set_bot_online(True)
            stats.set_last_error(None)

            # Xóa webhook nếu có — polling mới nhận tin
            await app.bot.delete_webhook(drop_pending_updates=False)

            await app.start()
            await app.updater.start_polling(
                drop_pending_updates=False,  # nhận tin đang treo
                allowed_updates=["message", "callback_query"],
                poll_interval=1.0,
                timeout=30,
            )
            logger.info("Polling started — waiting for messages...")

            # Block forever while polling
            stop_event = asyncio.Event()
            await stop_event.wait()

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Bot loop error: %s — restart after 15s", e)
            stats.set_bot_online(False)
            stats.set_last_error(str(e))
            await asyncio.sleep(15)
        finally:
            if app is not None:
                try:
                    if app.updater and app.updater.running:
                        await app.updater.stop()
                    if app.running:
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

    web_thread = threading.Thread(target=start_web_server, name="web", daemon=True)
    web_thread.start()
    time.sleep(2.0)

    ka = threading.Thread(target=keep_alive_loop, name="keepalive", daemon=True)
    ka.start()

    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
        stats.set_bot_online(False)
    except Exception as e:
        logger.exception("Fatal: %s", e)
        stats.set_bot_online(False)
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
