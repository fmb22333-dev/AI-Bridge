(() => {
  const byId = (id) => document.getElementById(id);

  function ensureCard() {
    if (byId("supabaseBackupCard")) return byId("supabaseBackupCard");
    const main = document.querySelector("main");
    if (!main) return null;
    const card = document.createElement("div");
    card.className = "card";
    card.id = "supabaseBackupCard";
    card.innerHTML = `
      <h2>4. Supabase Primary Bus</h2>
      <p>默认高速命令通道。GitHub Bus 作为备用命令通道并继续承担 Authority / 文档 / 版本职责；Bridge ID 自动继承 GitHub Authority。</p>
      <label>Project URL
        <input id="supabaseUrl" autocomplete="off" placeholder="https://xxxxxxxx.supabase.co">
      </label>
      <label>Secret Key
        <input id="supabaseSecret" type="password" autocomplete="new-password" placeholder="sb_secret_...">
      </label>
      <div class="split">
        <label>Poll Interval (seconds)
          <input id="supabasePoll" type="number" min="0.25" max="60" step="0.25" value="0.5">
        </label>
        <button id="connectSupabase" class="primary compact" type="button">连接并测试</button>
      </div>
      <div id="supabaseBridgeHint" class="hint muted"></div>
      <div id="supabaseOut" class="status muted">正在读取 Supabase 主通道状态…</div>
    `;
    const githubHeading = [...document.querySelectorAll("h2")].find((node) => node.textContent.trim() === "GitHub Bus");
    if (githubHeading) githubHeading.textContent = "GitHub Bus · Backup + Authority";
    main.appendChild(card);
    return card;
  }

  async function parse(response) {
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = {detail: text}; }
    if (!response.ok) throw new Error(data.detail || text || `HTTP ${response.status}`);
    return data;
  }

  function render(state) {
    const fallback = state.fallback || {};
    const url = byId("supabaseUrl");
    const secret = byId("supabaseSecret");
    const poll = byId("supabasePoll");
    const connect = byId("connectSupabase");
    const hint = byId("supabaseBridgeHint");
    const out = byId("supabaseOut");

    if (fallback.project_url && !url.value.trim()) url.value = fallback.project_url;
    if (fallback.poll_interval_seconds) poll.value = fallback.poll_interval_seconds;
    secret.placeholder = fallback.credential_saved
      ? "已由 Windows DPAPI 安全保存；留空继续使用现有 Key"
      : "sb_secret_...";
    hint.textContent = state.primary_configured
      ? `Bridge ID 自动继承：${state.primary_bridge_id || "当前 GitHub Bus"}`
      : "请先连接 GitHub Bus；Supabase Primary Bus 必须复用 GitHub Authority 的同一个 Bridge ID。";
    connect.disabled = !state.primary_configured;
    connect.title = state.primary_configured ? "" : "等待 Step 2 GitHub Bus 完成连接";

    if (!fallback.configured) {
      out.className = "status muted";
      out.textContent = fallback.status === "disabled" ? "Supabase 主通道已断开。" : "尚未配置 Supabase Primary Bus。";
      return;
    }
    const good = fallback.status === "connected";
    out.className = "status " + (good ? "ok" : "error");
    out.textContent = `${good ? "✓ 已连接" : fallback.status} · ${fallback.project_url || ""} · Poll ${fallback.poll_interval_seconds || 0.5}s${fallback.credential_saved ? " · Key 已安全保存" : ""}${fallback.detail ? " · " + fallback.detail : ""}`;
  }

  async function load() {
    ensureCard();
    const state = await parse(await fetch("/setup/supabase/state", {cache: "no-store"}));
    render(state);
  }

  async function connect() {
    const button = byId("connectSupabase");
    const out = byId("supabaseOut");
    button.disabled = true;
    out.className = "status muted";
    out.textContent = "正在验证 Supabase Data API、表权限并保存凭据…";
    try {
      const data = await parse(await fetch("/setup/supabase", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          project_url: byId("supabaseUrl").value.trim(),
          secret_key: byId("supabaseSecret").value.trim(),
          poll_interval_seconds: Number(byId("supabasePoll").value || 0.5),
          table: "ai_bridge_commands",
        }),
      }));
      byId("supabaseSecret").value = "";
      out.className = "status ok";
      out.textContent = `✓ Supabase Primary Bus 已连接。Bridge ID ${data.fallback.bridge_id} · Poll ${data.fallback.poll_interval_seconds}s`;
      await load();
    } catch (error) {
      out.className = "status error";
      out.textContent = "连接失败：" + error.message;
    } finally {
      button.disabled = false;
    }
  }

  ensureCard();
  byId("connectSupabase").onclick = connect;
  load().then(() => {
    if (location.hash === "#supabaseBackupCard") byId("supabaseBackupCard")?.scrollIntoView({behavior: "smooth"});
  }).catch((error) => {
    const out = byId("supabaseOut");
    if (out) {
      out.className = "status error";
      out.textContent = "读取失败：" + error.message;
    }
  });
  setInterval(() => { load().catch(() => {}); }, 1500);
})();
