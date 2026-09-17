(() => {
  function fallbackRemoteCard(remote) {
    if (!remote || !remote.configured) {
      const status = remote?.status === "disabled" ? "Disabled" : "未配置";
      const detail = remote?.status === "disabled" ? "备用通道已断开。" : "Supabase Backup Bus 尚未连接。";
      return `<div class="item remoteState offline"><div class="sessionTitle"><span class="dot"></span><b>${esc(status)}</b><span class="stateText">supabase_fallback</span></div><small>${esc(detail)}</small></div>`;
    }
    const good = remote.status === "connected";
    return `<div class="item remoteState ${good ? "online" : "offline"}"><div class="sessionTitle"><span class="dot"></span><b>${good ? "Connected" : esc(remote.status)}</b><span class="stateText">supabase_fallback</span></div><small>${esc(remote.project_url || "")}</small><small>Bridge ID: <b>${esc(remote.bridge_id || "")}</b> · Poll ${esc(remote.poll_interval_seconds ?? 0.5)}s</small>${remote.credential_saved ? "<small>凭据：已由 Windows DPAPI 安全保存</small>" : ""}${remote.detail ? `<small>${esc(remote.detail)}</small>` : ""}</div>`;
  }

  function ensureFallbackCard() {
    if ($("#fallbackRemote")) return;
    const githubRemote = $("#remote");
    const githubCard = githubRemote?.closest("article.card");
    if (!githubCard) return;
    const article = document.createElement("article");
    article.className = "card";
    article.innerHTML = `
      <h2>Supabase Backup Bus</h2>
      <div id="fallbackRemote"><p class="muted">正在读取备用通道…</p></div>
      <div class="actions">
        <button id="editFallbackRemote">修改配置</button>
        <button id="testFallbackRemote">测试连接</button>
        <button id="disconnectFallbackRemote" class="danger">断开并清除凭据</button>
      </div>
      <div id="fallbackRemoteError" class="errorText"></div>
      <small>与 GitHub Bus 并行监听；故障切换时继续使用同一个 command_id。</small>
    `;
    githubCard.insertAdjacentElement("afterend", article);

    $("#editFallbackRemote").onclick = () => { window.location.href = "/setup#supabaseBackupCard"; };
    $("#testFallbackRemote").onclick = async () => {
      const target = $("#fallbackRemoteError");
      target.textContent = "正在测试 Supabase Data API…";
      try {
        const result = await api("/control/fallback/test", {method: "POST"});
        target.textContent = `✓ 连接测试成功：${result.detail || "ok"}`;
        await refreshFallbackRemote();
      } catch (error) {
        target.textContent = "测试失败：" + error.message;
      }
    };
    $("#disconnectFallbackRemote").onclick = async () => {
      if (!confirm("断开 Supabase Backup Bus 并从本机删除保存的 Secret Key？")) return;
      const target = $("#fallbackRemoteError");
      try {
        await api("/control/fallback", {method: "DELETE"});
        target.textContent = "备用通道已断开，Secret Key 已从本机安全存储中删除。";
        await refreshFallbackRemote();
      } catch (error) {
        target.textContent = "断开失败：" + error.message;
      }
    };
  }

  async function refreshFallbackRemote() {
    ensureFallbackCard();
    try {
      const remote = await api("/control/fallback/state");
      $("#fallbackRemote").innerHTML = fallbackRemoteCard(remote);
      $("#disconnectFallbackRemote").style.display = remote.configured ? "inline-block" : "none";
      $("#testFallbackRemote").disabled = !remote.configured;
    } catch (error) {
      if ($("#fallbackRemote")) $("#fallbackRemote").innerHTML = `<div class="item remoteState offline"><small>${esc(error.message)}</small></div>`;
    }
  }

  ensureFallbackCard();
  refreshFallbackRemote();
  setInterval(refreshFallbackRemote, 2000);
})();
