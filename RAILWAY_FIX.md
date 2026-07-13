# Sửa lỗi deploy Railway — làm theo từng bước

## A. Chuẩn bị code trên GitHub

Trong PowerShell:

```powershell
cd C:\Users\ADMIN\telegram-media-bot

# 1) Commit bản fix deploy
git add .
git commit -m "fix: railway nixpacks deploy without healthcheck fail"

# 2) Tạo repo trên github.com (New repository, để trống, không README)
# 3) Thay YOUR_USER và YOUR_REPO:

git branch -M main
git remote remove origin 2>$null
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

Đăng nhập GitHub khi được hỏi. Nếu báo `rejected`, dùng:

```powershell
git push -u origin main --force
```

---

## B. Deploy trên Railway (đúng cách)

1. Vào https://railway.app → Login bằng **GitHub**
2. **New Project** → **Deploy from GitHub repo**
3. Chọn repo vừa push → **Deploy**
4. Vào service → tab **Variables** → **Add**:

| Name | Value |
|------|--------|
| `BOT_TOKEN` | `8798614238:AAE...` (token BotFather) |
| `ADMIN_ID` | `5949258698` |
| `ADMIN_ONLY` | `false` |

5. **Settings**:
   - Start Command: để trống (dùng `nixpacks.toml`) hoặc `python main.py`
   - **Tắt** Healthcheck nếu có (Healthcheck Path để trống)
6. Tab **Deployments** → **Redeploy**

---

## C. Lỗi thường gặp & cách xử lý

### 1) `Build failed`
- Xem **Build Logs** (dòng đỏ cuối)
- Đảm bảo repo có: `main.py`, `requirements.txt`, `nixpacks.toml`
- Nếu chọn Docker mà fail: Settings → Builder = **Nixpacks**

### 2) `Healthcheck failed` / `service unavailable`
- Xóa Healthcheck Path trong Settings
- File `railway.toml` đã bỏ healthcheck

### 3) Deploy xong nhưng bot không trả lời
- **Variables thiếu `BOT_TOKEN`** → xem Deploy Logs có chữ `CHƯA CÓ BOT_TOKEN`
- **Máy local vẫn chạy bot** → Conflict  
  → Tắt bot trên máy (`Ctrl+C` / đóng cửa sổ `main.py`) rồi Redeploy Railway

### 4) `Conflict: terminated by other getUpdates`
- Chỉ được **1** nơi chạy bot: Railway **hoặc** máy, không cả hai
- Tắt local, Redeploy cloud

### 5) Push GitHub lỗi `Permission denied` / `Authentication failed`
- Dùng GitHub Desktop, hoặc Personal Access Token thay password
- Hoặc kéo thả file lên github.com (Upload files)

### 6) `ModuleNotFoundError: bot`
- Root Directory trên Railway phải là thư mục chứa `main.py` (để `/`)

---

## D. Kiểm tra đã chạy chưa

1. Railway → **Deployments** → status **Success** / **Active**
2. **Deploy Logs** thấy:
   ```
   Bot online: @bottaivideofree_bot
   ```
3. Telegram → gửi `/start` cho bot
4. **Tắt máy tính** — bot vẫn trả lời = OK

---

## E. Gửi mình log nếu vẫn lỗi

Copy **toàn bộ đoạn đỏ** từ:
- Build Logs, hoặc  
- Deploy Logs  

Dán vào chat để mình chỉ đúng chỗ hỏng.
