function formatUptime(sec) {
  sec = Math.max(0, Number(sec) || 0);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function formatTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleString("vi-VN", {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "2-digit",
  });
}

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function loadStats() {
  try {
    const res = await fetch("/api/stats", { cache: "no-store" });
    const data = await res.json();

    document.getElementById("statTotal").textContent = data.total_jobs ?? 0;
    document.getElementById("statOk").textContent = data.success_jobs ?? 0;
    document.getElementById("statFail").textContent = data.failed_jobs ?? 0;
    document.getElementById("statVideos").textContent = data.videos_sent ?? 0;
    document.getElementById("statImages").textContent = data.images_sent ?? 0;
    document.getElementById("statUsers").textContent = data.users_count ?? 0;
    document.getElementById("uptime").textContent = formatUptime(data.uptime_sec);

    const online = !!data.bot_online;
    const dot = document.getElementById("statusDot");
    const text = document.getElementById("statusText");
    dot.className = "dot " + (online ? "on" : "off");
    text.textContent = online ? "Bot online" : "Bot offline";

    const tbody = document.getElementById("recentBody");
    const recent = data.recent || [];
    if (!recent.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="muted">Chưa có job nào</td></tr>';
    } else {
      tbody.innerHTML = recent
        .slice(0, 20)
        .map((r) => {
          const user = r.username ? `@${escapeHtml(r.username)}` : escapeHtml(r.user_id);
          const badge = r.success
            ? '<span class="badge ok">OK</span>'
            : '<span class="badge fail">FAIL</span>';
          return `<tr>
            <td>${formatTime(r.ts)}</td>
            <td>${user}</td>
            <td>${escapeHtml(r.media_type || "—")}</td>
            <td>${r.parts ?? 0}</td>
            <td>${badge}</td>
          </tr>`;
        })
        .join("");
    }

    const errBox = document.getElementById("lastError");
    if (data.last_error) {
      errBox.classList.remove("hidden");
      errBox.textContent = "Lỗi gần nhất: " + data.last_error;
    } else {
      errBox.classList.add("hidden");
      errBox.textContent = "";
    }
  } catch (e) {
    console.error(e);
    document.getElementById("statusDot").className = "dot off";
    document.getElementById("statusText").textContent = "API error";
  }
}

document.getElementById("btnRefresh").addEventListener("click", loadStats);
loadStats();
setInterval(loadStats, 8000);
