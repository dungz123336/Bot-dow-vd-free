# Telegram Media Downloader Bot + Web Dashboard

Bot Telegram chuyên nghiệp: **tải video/ảnh từ mọi link**, **cắt video theo số giây**, **gửi lại trong chat**. Kèm **web dashboard** theo dõi thống kê.

## Tính năng

- Tải video/ảnh từ YouTube, TikTok, Instagram, Facebook, Twitter/X, và hầu hết site mà `yt-dlp` hỗ trợ
- Bot **hỏi số giây** muốn cắt (nút 15/30/60/90/120 hoặc nhập tay)
- Cắt video bằng **FFmpeg**, gửi từng phần về Telegram
- **Album / nhiều media**: tải hết và gửi lần lượt
- Ảnh gửi nguyên (không cắt)
- Tự nén nếu file vượt giới hạn ~50MB của Bot API
- Web dashboard realtime: job, user, lỗi
- Admin ID bảo vệ lệnh `/status`

## Yêu cầu

- Python 3.10+
- FFmpeg (đã có trên máy bạn)
- Token bot từ [@BotFather](https://t.me/BotFather)

## Cài đặt

```powershell
cd C:\Users\ADMIN\telegram-media-bot
py -m pip install -r requirements.txt
```

## Cấu hình

Sửa file `.env`:

```env
BOT_TOKEN=123456789:AAHxxxxxxxxxxxxxxxx  # token từ BotFather
ADMIN_ID=5949258698
WEB_PORT=8080
ADMIN_ONLY=false
DEFAULT_SPLIT_SECONDS=30
```

> Bạn đã set `ADMIN_ID=5949258698`. Chỉ còn thiếu **BOT_TOKEN**.

## Chạy local

```powershell
cd C:\Users\ADMIN\telegram-media-bot
py main.py
```

- Bot Telegram: long polling
- Dashboard: http://127.0.0.1:8080

## Deploy 24/7 (tắt máy vẫn chạy)

Xem chi tiết **[DEPLOY.md](DEPLOY.md)** — khuyên dùng **Railway** + Docker:

```
BOT_TOKEN=...
ADMIN_ID=5949258698
```

## Điều khiển khi đang tải

| Nút / lệnh | Ý nghĩa |
|------------|---------|
| ⏸ / `/pause` | Tạm dừng |
| ▶️ / `/resume` | Tiếp tục |
| ⏹ / `/stop` | Dừng hẳn + dọn file |

## Cách dùng trên Telegram

1. Mở bot → `/start`
2. Gửi link video/ảnh/album
3. Chọn thời lượng cắt hoặc gõ số giây (`0` = không cắt)
4. Chờ bot tải, cắt và gửi file về chat

### Lệnh

| Lệnh | Mô tả |
|------|--------|
| `/start` | Giới thiệu |
| `/help` | Hướng dẫn |
| `/cancel` | Hủy thao tác đang chờ |
| `/status` | Thống kê (chỉ Admin) |

## Cấu trúc thư mục

```
telegram-media-bot/
├── main.py              # Chạy bot + web
├── bot/
│   ├── config.py
│   ├── handlers.py      # Telegram handlers
│   ├── downloader.py    # yt-dlp
│   ├── splitter.py      # FFmpeg split/compress
│   └── stats.py
├── web/                 # FastAPI dashboard
├── downloads/           # File tạm
├── data/stats.json
├── .env
└── requirements.txt
```

## Lưu ý

- Bot API Telegram giới hạn ~**50MB**/file — bot tự cắt/nén
- Một số site (Instagram private, login wall) có thể không tải được
- Tôn trọng bản quyền nội dung khi sử dụng
