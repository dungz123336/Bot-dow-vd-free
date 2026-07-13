# Deploy bot 24/7 (miễn phí)

Bot cần **chạy liên tục** + **FFmpeg** + vài trăm MB disk. Free tier tốt nhất hiện nay:

| Nền tảng | Free? | Ghi chú |
|----------|-------|---------|
| **Railway** | Có credit free / tháng | **Khuyên dùng** — Docker, dễ |
| **Koyeb** | Free instance | Docker OK |
| **Render** | Free worker | Có thể chậm / giới hạn |
| **Fly.io** | Free allowance | Cần cài `flyctl` |
| Oracle Cloud Always Free | VPS free | Mạnh nhưng setup lâu |

> ⚠️ Render/Railway free **web service** có thể sleep — bot Telegram **cần process luôn sống**. Dùng **Worker** hoặc container không sleep.

---

## Cách 1 — Railway (khuyên dùng)

1. Tạo tài khoản: https://railway.app  
2. **New Project** → **Deploy from GitHub repo** (push code lên GitHub trước)  
3. Railway nhận `Dockerfile` tự build  
4. **Variables** thêm:

```
BOT_TOKEN=8798614238:AAE...của_bạn
ADMIN_ID=5949258698
ADMIN_ONLY=false
DEFAULT_SPLIT_SECONDS=30
```

5. Deploy → bot online 24/7 kể cả khi tắt máy

### Push code lên GitHub

```powershell
cd C:\Users\ADMIN\telegram-media-bot
git init
git add .
git commit -m "Telegram media bot with pause/resume"
# Tạo repo trống trên github.com rồi:
git branch -M main
git remote add origin https://github.com/USER/telegram-media-bot.git
git push -u origin main
```

**Không** commit file `.env` (đã có trong `.gitignore`).

---

## Cách 2 — Koyeb free

1. https://app.koyeb.com → New App → GitHub  
2. Chọn Dockerfile  
3. Env: `BOT_TOKEN`, `ADMIN_ID`  
4. Instance type: Free  

---

## Cách 3 — Máy VPS free (Oracle Cloud)

1. Tạo VM Ubuntu Always Free  
2. Cài Docker:

```bash
sudo apt update && sudo apt install -y docker.io
sudo docker run -d --restart=always --name media-bot \
  -e BOT_TOKEN="YOUR_TOKEN" \
  -e ADMIN_ID="5949258698" \
  -p 8080:8080 \
  your-dockerhub-user/telegram-media-bot:latest
```

---

## Chạy local (máy bạn)

```powershell
cd C:\Users\ADMIN\telegram-media-bot
py main.py
```

Dashboard: http://127.0.0.1:8080  

Khi tắt máy → bot local tắt. Cần deploy cloud để 24/7.

---

## Lệnh bot khi đang tải

| Nút / lệnh | Tác dụng |
|------------|----------|
| ⏸ Tạm dừng / `/pause` | Dừng tạm, giữ tiến độ |
| ▶️ Tiếp tục / `/resume` | Tiếp tục tải |
| ⏹ Dừng / `/stop` | Hủy job, dọn file tạm |

---

## Checklist sau deploy

- [ ] Chỉ **1** instance bot (tránh lỗi Conflict getUpdates)  
- [ ] `BOT_TOKEN` đúng  
- [ ] Gửi `/start` trên Telegram  
- [ ] Test 1 link ngắn  
- [ ] Tắt máy tính → bot vẫn trả lời  
