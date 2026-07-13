"""Telegram bot handlers: download, split, album, pause/resume/stop."""

from __future__ import annotations

import asyncio
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot import stats
from bot.config import ADMIN_ID, ADMIN_ONLY, DEFAULT_SPLIT_SECONDS, URL_REGEX
from bot.downloader import cleanup_dir, download_media
from bot.jobs import JobCancelled, JobStatus, jobs
from bot.splitter import compress_if_needed_async, get_duration, split_video_async

logger = logging.getLogger(__name__)

WAITING_SECONDS = 1

KEY_URL = "pending_url"
KEY_STATUS_MSG = "status_msg_id"
KEY_ACTIVE_JOB = "active_job_id"


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and int(user_id) == int(ADMIN_ID)


def _allowed(user_id: int | None) -> bool:
    if not ADMIN_ONLY:
        return True
    return _is_admin(user_id)


def _extract_url(text: str | None) -> str | None:
    if not text:
        return None
    m = re.search(URL_REGEX, text)
    return m.group(0).rstrip(").,]\"'") if m else None


def split_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("15 giây", callback_data="split:15"),
                InlineKeyboardButton("30 giây", callback_data="split:30"),
                InlineKeyboardButton("60 giây", callback_data="split:60"),
            ],
            [
                InlineKeyboardButton("90 giây", callback_data="split:90"),
                InlineKeyboardButton("120 giây", callback_data="split:120"),
                InlineKeyboardButton("Không cắt", callback_data="split:0"),
            ],
            [InlineKeyboardButton("✏️ Nhập số giây khác", callback_data="split:custom")],
        ]
    )


def control_keyboard(job_id: str, *, paused: bool = False) -> InlineKeyboardMarkup:
    if paused:
        row = [
            InlineKeyboardButton("▶️ Tiếp tục", callback_data=f"job:resume:{job_id}"),
            InlineKeyboardButton("⏹ Dừng hẳn", callback_data=f"job:stop:{job_id}"),
        ]
    else:
        row = [
            InlineKeyboardButton("⏸ Tạm dừng", callback_data=f"job:pause:{job_id}"),
            InlineKeyboardButton("⏹ Dừng tải", callback_data=f"job:stop:{job_id}"),
        ]
    return InlineKeyboardMarkup([row])


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _allowed(user.id if user else None):
        await update.message.reply_text("⛔ Bot chỉ dành cho Admin.")
        return

    name = user.first_name if user else "bạn"
    text = (
        f"👋 Xin chào <b>{name}</b>!\n\n"
        "🤖 <b>Media Downloader Bot</b> — tải video/ảnh từ mọi nền tảng\n"
        "(YouTube, TikTok, Instagram, Facebook, Yandex Disk, ...)\n\n"
        "📌 <b>Cách dùng:</b>\n"
        "1️⃣ Gửi <b>link</b> video/ảnh/album\n"
        "2️⃣ Chọn thời lượng cắt (giây)\n"
        "3️⃣ Bot tải → cắt → gửi lại trong chat\n\n"
        "🎛 Khi đang tải: <b>Tạm dừng / Tiếp tục / Dừng</b>\n"
        "🖼 Album: tải toàn bộ (có thể pause giữa các file)\n\n"
        "Lệnh: /help · /pause · /resume · /stop · /status"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 <b>Hướng dẫn</b>\n\n"
        "• Gửi link → chọn số giây cắt\n"
        "• Trong lúc tải dùng nút:\n"
        "  ⏸ <b>Tạm dừng</b> — dừng tạm, giữ tiến độ\n"
        "  ▶️ <b>Tiếp tục</b> — resume tải\n"
        "  ⏹ <b>Dừng</b> — hủy job, dọn file tạm\n"
        "• Lệnh: /pause · /resume · /stop · /cancel\n"
        "• Album/playlist: tải lần lượt, pause được giữa file\n"
        "• /status — thống kê (Admin)",
        parse_mode=ParseMode.HTML,
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_admin(user.id if user else None):
        await update.message.reply_text("⛔ Chỉ Admin xem được.")
        return
    s = stats.load_stats()
    active = jobs.get_user_job(user.id) if user else None
    active_line = "không"
    if active and active.status in (JobStatus.RUNNING, JobStatus.PAUSED):
        active_line = f"{active.status.value} · {active.current}/{active.total}"
    text = (
        "📊 <b>Thống kê bot</b>\n\n"
        f"• Tổng job: <b>{s.get('total_jobs', 0)}</b>\n"
        f"• Thành công: <b>{s.get('success_jobs', 0)}</b>\n"
        f"• Thất bại: <b>{s.get('failed_jobs', 0)}</b>\n"
        f"• Video đã gửi: <b>{s.get('videos_sent', 0)}</b>\n"
        f"• Ảnh đã gửi: <b>{s.get('images_sent', 0)}</b>\n"
        f"• User: <b>{len(s.get('users') or {})}</b>\n"
        f"• Online: <b>{'✅' if s.get('bot_online') else '❌'}</b>\n"
        f"• Job của bạn: <b>{active_line}</b>\n"
    )
    if s.get("last_error"):
        text += f"\n⚠️ Lỗi gần nhất:\n<code>{s['last_error'][:300]}</code>"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if user:
        j = jobs.get_user_job(user.id)
        if j and j.status in (JobStatus.RUNNING, JobStatus.PAUSED):
            j.cancel()
    context.user_data.pop(KEY_URL, None)
    await update.message.reply_text("✅ Đã hủy. Gửi link mới khi sẵn sàng.")
    return ConversationHandler.END


async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    j = jobs.get_user_job(user.id)
    if not j or j.status not in (JobStatus.RUNNING, JobStatus.PAUSED):
        await update.message.reply_text("ℹ️ Không có job đang chạy.")
        return
    if j.pause():
        await update.message.reply_text(
            f"⏸ Đã tạm dừng job <code>{j.job_id}</code>.\n"
            f"Tiến độ: {j.current}/{j.total or '?'}\n"
            "Dùng /resume hoặc nút ▶️ để tiếp tục.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text("⚠️ Không thể tạm dừng lúc này.")


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    j = jobs.get_user_job(user.id)
    if not j or j.status != JobStatus.PAUSED:
        await update.message.reply_text("ℹ️ Không có job đang tạm dừng.")
        return
    if j.resume():
        await update.message.reply_text(
            f"▶️ Tiếp tục job <code>{j.job_id}</code>...",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text("⚠️ Không thể tiếp tục.")


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    j = jobs.get_user_job(user.id)
    if not j or j.status not in (JobStatus.RUNNING, JobStatus.PAUSED):
        await update.message.reply_text("ℹ️ Không có job đang chạy.")
        return
    j.cancel()
    await update.message.reply_text(
        f"⏹ Đã dừng job <code>{j.job_id}</code>. File tạm sẽ được dọn.",
        parse_mode=ParseMode.HTML,
    )


async def on_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not _allowed(user.id if user else None):
        await update.message.reply_text("⛔ Bot chỉ dành cho Admin.")
        return ConversationHandler.END

    url = _extract_url(update.message.text if update.message else None)
    if not url:
        await update.message.reply_text(
            "❓ Không thấy link hợp lệ.\nGửi URL bắt đầu bằng http:// hoặc https://"
        )
        return ConversationHandler.END

    context.user_data[KEY_URL] = url
    msg = await update.message.reply_text(
        f"🔗 <b>Đã nhận link</b>\n<code>{url[:180]}</code>\n\n"
        "✂️ Bạn muốn <b>cắt video thành bao nhiêu giây</b> mỗi phần?\n"
        f"(Mặc định: {DEFAULT_SPLIT_SECONDS}s — ảnh gửi nguyên)",
        parse_mode=ParseMode.HTML,
        reply_markup=split_keyboard(),
        disable_web_page_preview=True,
    )
    context.user_data[KEY_STATUS_MSG] = msg.message_id
    return WAITING_SECONDS


async def on_split_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = (query.data or "").strip()
    if not data.startswith("split:"):
        return WAITING_SECONDS

    choice = data.split(":", 1)[1]
    url = context.user_data.get(KEY_URL)
    if not url:
        await query.edit_message_text("⚠️ Không còn link. Hãy gửi lại link.")
        return ConversationHandler.END

    if choice == "custom":
        await query.edit_message_text(
            "✏️ Gõ số giây (vd: <code>25</code> hoặc <code>0</code> = không cắt).\n/cancel để hủy.",
            parse_mode=ParseMode.HTML,
        )
        return WAITING_SECONDS

    try:
        seconds = int(choice)
    except ValueError:
        await query.edit_message_text("⚠️ Lựa chọn không hợp lệ.")
        return WAITING_SECONDS

    await _start_job_from_message(update, context, url, seconds, status_message=query.message)
    context.user_data.pop(KEY_URL, None)
    return ConversationHandler.END


async def on_seconds_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = context.user_data.get(KEY_URL)
    if not url:
        return ConversationHandler.END

    text = (update.message.text or "").strip().lower()
    if text in {"không", "khong", "full", "nguyên", "nguyen", "0s"}:
        seconds = 0
    else:
        m = re.search(r"\d+", text)
        if not m:
            await update.message.reply_text(
                "⚠️ Gửi <b>số giây</b> (vd: 30) hoặc chọn nút.\n/cancel để hủy.",
                parse_mode=ParseMode.HTML,
                reply_markup=split_keyboard(),
            )
            return WAITING_SECONDS
        seconds = int(m.group(0))
        if seconds < 0 or seconds > 3600:
            await update.message.reply_text("⚠️ Số giây phải từ 0 đến 3600.")
            return WAITING_SECONDS

    status = await update.message.reply_text(
        f"⏳ Chuẩn bị tải · cắt <b>{seconds}s</b>...",
        parse_mode=ParseMode.HTML,
    )
    await _start_job_from_message(update, context, url, seconds, status_message=status)
    context.user_data.pop(KEY_URL, None)
    return ConversationHandler.END


async def on_job_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = (query.data or "").strip()
    # job:pause:ID | job:resume:ID | job:stop:ID
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "job":
        await query.answer()
        return

    action, job_id = parts[1], parts[2]
    job = jobs.get(job_id)
    user = update.effective_user
    if not job or not user or job.user_id != user.id:
        await query.answer("Job không tồn tại hoặc không phải của bạn.", show_alert=True)
        return

    if action == "pause":
        if job.pause():
            await query.answer("⏸ Đã tạm dừng")
            try:
                await query.edit_message_reply_markup(reply_markup=control_keyboard(job_id, paused=True))
            except TelegramError:
                pass
        else:
            await query.answer("Không thể tạm dừng", show_alert=True)
    elif action == "resume":
        if job.resume():
            await query.answer("▶️ Tiếp tục")
            try:
                await query.edit_message_reply_markup(reply_markup=control_keyboard(job_id, paused=False))
            except TelegramError:
                pass
        else:
            await query.answer("Không thể tiếp tục", show_alert=True)
    elif action == "stop":
        job.cancel()
        await query.answer("⏹ Đã dừng")
        try:
            await query.edit_message_text(
                f"⏹ <b>Đã dừng tải</b>\nJob <code>{job_id}</code>\n"
                f"Đã gửi: 🎬 {job.videos_sent} · 🖼 {job.images_sent}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError:
            pass
    else:
        await query.answer()


async def _safe_edit(message, text: str, reply_markup=None) -> None:
    try:
        await message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
    except TelegramError:
        pass


async def _start_job_from_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    segment_seconds: int,
    status_message,
) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    job = jobs.create(
        user_id=user.id,
        chat_id=chat.id,
        url=url,
        segment_seconds=segment_seconds,
    )
    context.user_data[KEY_ACTIVE_JOB] = job.job_id

    await _safe_edit(
        status_message,
        f"⏳ <b>Bắt đầu tải</b>\n"
        f"Job <code>{job.job_id}</code> · cắt <b>{segment_seconds}s</b>\n"
        f"🔗 <code>{url[:140]}</code>\n\n"
        f"Dùng nút bên dưới để ⏸ tạm dừng / ⏹ dừng",
        reply_markup=control_keyboard(job.job_id, paused=False),
    )
    # Run in background so conversation can end / buttons work
    asyncio.create_task(
        _process_job(context, job, status_message, user.username if user else None)
    )


async def _process_job(
    context: ContextTypes.DEFAULT_TYPE,
    job,
    status_message,
    username: str | None,
) -> None:
    bot = context.bot
    chat_id = job.chat_id
    url = job.url
    segment_seconds = job.segment_seconds
    user_id = job.user_id
    work_dir = None
    videos_sent = 0
    images_sent = 0

    last_edit = 0.0
    loop = asyncio.get_running_loop()

    async def _progress_async(msg: str) -> None:
        nonlocal last_edit
        now = loop.time()
        # throttle edits
        if now - last_edit < 1.2 and "Hoàn tất" not in msg and "dừng" not in msg.lower():
            job.message = msg
            return
        last_edit = now
        job.message = msg
        paused = job.is_paused()
        prefix = "⏸ <b>ĐANG TẠM DỪNG</b>\n" if paused else ""
        await _safe_edit(
            status_message,
            f"{prefix}{msg}\n🔗 <code>{url[:110]}</code>\n"
            f"Job <code>{job.job_id}</code> · {job.current}/{job.total or '?'}",
            reply_markup=control_keyboard(job.job_id, paused=paused),
        )

    def progress(msg: str) -> None:
        """Thread-safe progress (yt-dlp / yandex run in worker threads)."""
        try:
            fut = asyncio.run_coroutine_threadsafe(_progress_async(msg), loop)
            fut.result(timeout=10)
        except Exception:
            job.message = msg

    try:
        await bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
        await _progress_async("🔍 Đang phân tích link...")
        await job.checkpoint()

        result = await download_media(url, progress=progress, job=job)
        work_dir = result.work_dir

        if result.error == "cancelled" or job.is_cancelled():
            await _safe_edit(
                status_message,
                f"⏹ <b>Đã dừng</b>\n🎬 {videos_sent} · 🖼 {images_sent}\nJob <code>{job.job_id}</code>",
            )
            stats.record_job(
                user_id=user_id,
                username=username,
                url=url,
                success=False,
                media_type="unknown",
                error="cancelled",
            )
            return

        if not result.ok or not result.items:
            err = result.error or "Không tải được media."
            job.mark_failed()
            await _safe_edit(status_message, f"❌ {err}")
            stats.record_job(
                user_id=user_id,
                username=username,
                url=url,
                success=False,
                media_type="unknown",
                error=err,
            )
            return

        total = len(result.items)
        job.set_progress(0, total, "Đang gửi media...")
        await _progress_async(f"✅ Tải xong <b>{total}</b> file · «{result.title[:50]}»\n⚙️ Cắt & gửi...")

        for idx, item in enumerate(result.items, start=1):
            await job.checkpoint()
            job.set_progress(idx, total)
            await bot.send_chat_action(
                chat_id,
                ChatAction.UPLOAD_VIDEO if item.media_type == "video" else ChatAction.UPLOAD_PHOTO,
            )

            if item.media_type == "image":
                try:
                    with open(item.path, "rb") as f:
                        await bot.send_photo(
                            chat_id,
                            photo=f,
                            caption=f"🖼 [{idx}/{total}] {item.title[:80]}",
                        )
                    images_sent += 1
                    job.images_sent = images_sent
                except Exception:
                    try:
                        with open(item.path, "rb") as f:
                            await bot.send_document(
                                chat_id,
                                document=f,
                                caption=f"🖼 [{idx}/{total}] {item.title[:80]}",
                                filename=item.path.name,
                            )
                        images_sent += 1
                        job.images_sent = images_sent
                    except Exception as e2:
                        await bot.send_message(chat_id, f"⚠️ Không gửi được ảnh {idx}: {e2}")
                continue

            await _progress_async(
                f"✂️ Video {idx}/{total}: cắt ({segment_seconds or 'không'}s) · gửi..."
            )
            try:
                await job.checkpoint()
                parts = await split_video_async(item.path, segment_seconds)
            except JobCancelled:
                raise
            except Exception as e:
                logger.exception("split failed")
                await bot.send_message(chat_id, f"⚠️ Lỗi cắt video {idx}: {e}")
                parts = [item.path]

            n_parts = len(parts)
            for p_i, part in enumerate(parts, start=1):
                await job.checkpoint()
                try:
                    ready = await compress_if_needed_async(part)
                    size_mb = ready.stat().st_size / (1024 * 1024)
                    if ready.stat().st_size > 49 * 1024 * 1024:
                        await bot.send_message(
                            chat_id,
                            f"⚠️ Part {p_i}/{n_parts} quá lớn ({size_mb:.1f}MB). Thử cắt 15s.",
                        )
                        continue

                    dur = get_duration(ready)
                    caption = (
                        f"🎬 [{idx}/{total}] {item.title[:60]}\n"
                        f"Part {p_i}/{n_parts}"
                        + (f" · ~{int(dur)}s" if dur else "")
                        + f" · {size_mb:.1f}MB"
                    )
                    await bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
                    with open(ready, "rb") as f:
                        await bot.send_video(
                            chat_id,
                            video=f,
                            caption=caption,
                            supports_streaming=True,
                            filename=ready.name,
                            read_timeout=300,
                            write_timeout=300,
                            connect_timeout=60,
                        )
                    videos_sent += 1
                    job.videos_sent = videos_sent
                    await asyncio.sleep(0.35)
                except JobCancelled:
                    raise
                except Exception as e:
                    logger.exception("send video part failed")
                    try:
                        with open(part, "rb") as f:
                            await bot.send_document(
                                chat_id,
                                document=f,
                                caption=f"🎬 Part {p_i}/{n_parts}",
                                filename=part.name,
                                read_timeout=300,
                                write_timeout=300,
                            )
                        videos_sent += 1
                        job.videos_sent = videos_sent
                    except Exception as e2:
                        await bot.send_message(chat_id, f"❌ Không gửi được phần {p_i}: {e2}")

        if job.is_cancelled():
            summary = (
                f"⏹ <b>Đã dừng</b> — «{result.title[:50]}»\n"
                f"🎬 {videos_sent} · 🖼 {images_sent}"
            )
        elif segment_seconds == 0:
            summary = (
                f"✅ <b>Hoàn tất</b> — «{result.title[:50]}»\n"
                f"🎬 Video: <b>{videos_sent}</b> · 🖼 Ảnh: <b>{images_sent}</b>\n"
                f"✂️ Không cắt"
            )
        else:
            summary = (
                f"✅ <b>Hoàn tất</b> — «{result.title[:50]}»\n"
                f"🎬 Parts: <b>{videos_sent}</b> · 🖼 Ảnh: <b>{images_sent}</b>\n"
                f"✂️ Cắt: <b>{segment_seconds}s</b>/phần"
            )

        job.mark_completed()
        await _safe_edit(status_message, summary)
        stats.record_job(
            user_id=user_id,
            username=username,
            url=url,
            success=videos_sent + images_sent > 0,
            media_type="video" if videos_sent else "image",
            parts=videos_sent + images_sent,
            error=None if videos_sent + images_sent > 0 else "Không gửi được file nào",
        )

    except JobCancelled:
        job.cancel()
        await _safe_edit(
            status_message,
            f"⏹ <b>Đã dừng tải</b>\n"
            f"🎬 {videos_sent} · 🖼 {images_sent}\n"
            f"Job <code>{job.job_id}</code>",
        )
        stats.record_job(
            user_id=user_id,
            username=username,
            url=url,
            success=videos_sent + images_sent > 0,
            media_type="video" if videos_sent else "image",
            parts=videos_sent + images_sent,
            error="cancelled",
        )
    except Exception as e:
        logger.exception("job failed")
        job.mark_failed()
        await _safe_edit(status_message, f"❌ Lỗi: {e}")
        stats.record_job(
            user_id=user_id,
            username=username,
            url=url,
            success=False,
            media_type="unknown",
            error=str(e),
        )
    finally:
        cleanup_dir(work_dir)
        jobs.cleanup_old()


async def on_text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _allowed(user.id if user else None):
        return
    text = update.message.text or ""
    url = _extract_url(text)
    if url:
        context.user_data[KEY_URL] = url
        await update.message.reply_text(
            f"🔗 <b>Đã nhận link</b>\n<code>{url[:180]}</code>\n\n"
            "✂️ Bạn muốn <b>cắt video thành bao nhiêu giây</b> mỗi phần?",
            parse_mode=ParseMode.HTML,
            reply_markup=split_keyboard(),
            disable_web_page_preview=True,
        )
        return

    lower = text.lower()
    if any(k in lower for k in ("help", "hướng dẫn", "huong dan", "cách dùng", "cach dung")):
        await help_cmd(update, context)
        return
    await update.message.reply_text(
        "💡 Gửi <b>link</b> để tải.\n"
        "Khi đang tải: /pause · /resume · /stop\n"
        "/help để xem hướng dẫn.",
        parse_mode=ParseMode.HTML,
    )


def build_application(token: str) -> Application:
    app = (
        Application.builder()
        .token(token)
        .concurrent_updates(True)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(120)
        .build()
    )

    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & filters.Regex(URL_REGEX) & ~filters.COMMAND, on_link),
        ],
        states={
            WAITING_SECONDS: [
                CallbackQueryHandler(on_split_callback, pattern=r"^split:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_seconds_text),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_cmd),
            CommandHandler("start", start_cmd),
        ],
        allow_reentry=True,
        name="download_conv",
        persistent=False,
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CallbackQueryHandler(on_job_callback, pattern=r"^job:"))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_fallback))

    return app
