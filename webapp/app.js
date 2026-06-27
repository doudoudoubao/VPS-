/* X 实时热门趋势 — Telegram Mini App 前端 */
(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.setHeaderColor) {
      try { tg.setHeaderColor("secondary_bg_color"); } catch (e) { /* older clients */ }
    }
  }

  const initData = tg ? tg.initData : "";
  let config = null;        // 当前有效设置 (来自 /api/config)
  let editRegions = [];     // 设置页正在编辑的地区列表 [{woeid,label}]

  // ---------- 工具 ----------
  function $(id) { return document.getElementById(id); }

  async function api(path, opts = {}) {
    const headers = Object.assign(
      { "Content-Type": "application/json", "X-Telegram-Init-Data": initData },
      opts.headers || {}
    );
    const res = await fetch(path, Object.assign({}, opts, { headers }));
    let data = {};
    try { data = await res.json(); } catch (e) { /* 非 JSON */ }
    if (!res.ok) throw new Error(data.error || ("请求失败 " + res.status));
    return data;
  }

  function toast(msg) {
    if (tg && tg.showPopup) {
      try { tg.HapticFeedback && tg.HapticFeedback.notificationOccurred("success"); } catch (e) {}
    }
    const el = $("toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), 2600);
  }

  function fmtVolume(v) {
    if (!v) return "";
    if (v >= 10000) return (v / 10000).toFixed(1) + " 万讨论";
    return v + " 讨论";
  }

  // ---------- Tab ----------
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $("tab-" + btn.dataset.tab).classList.add("active");
    });
  });

  // ---------- 趋势榜 ----------
  function currentRegion() {
    const sel = $("regionSelect");
    const opt = sel.options[sel.selectedIndex];
    return opt ? { woeid: opt.value, label: opt.dataset.label || opt.textContent } : null;
  }

  async function loadTrends() {
    const region = currentRegion();
    if (!region) return;
    const list = $("trendList");
    const empty = $("trendEmpty");
    const meta = $("trendMeta");
    list.innerHTML = "";
    empty.classList.add("hidden");
    meta.textContent = "加载中…";
    $("pushBtn").disabled = true;
    try {
      const data = await api("/api/trends?woeid=" + encodeURIComponent(region.woeid) +
        "&label=" + encodeURIComponent(region.label));
      const trends = data.trends || [];
      meta.textContent = data.label + " · 共 " + trends.length + " 条 · " +
        new Date().toLocaleTimeString();
      if (!trends.length) { empty.classList.remove("hidden"); return; }
      trends.forEach((t, i) => {
        const li = document.createElement("li");
        const rank = document.createElement("span");
        rank.className = "rank";
        rank.textContent = i + 1;
        const body = document.createElement("div");
        body.className = "body";
        const name = document.createElement(t.url ? "a" : "span");
        name.className = "name";
        name.textContent = t.name;
        if (t.url) { name.href = t.url; name.target = "_blank"; name.rel = "noopener"; }
        body.appendChild(name);
        if (t.volume) {
          const vol = document.createElement("div");
          vol.className = "vol";
          vol.textContent = fmtVolume(t.volume);
          body.appendChild(vol);
        }
        li.appendChild(rank);
        li.appendChild(body);
        list.appendChild(li);
      });
      $("pushBtn").disabled = !(config && config.telegram_ready);
    } catch (e) {
      meta.textContent = "";
      empty.textContent = "加载失败: " + e.message;
      empty.classList.remove("hidden");
    }
  }

  $("refreshBtn").addEventListener("click", loadTrends);
  $("regionSelect").addEventListener("change", loadTrends);

  $("pushBtn").addEventListener("click", async () => {
    const region = currentRegion();
    if (!region) return;
    const btn = $("pushBtn");
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = "推送中…";
    try {
      const r = await api("/api/push", {
        method: "POST",
        body: JSON.stringify({ woeid: region.woeid, label: region.label }),
      });
      if (r.ok > 0) toast("已推送到 " + r.ok + " 个目标 (" + r.count + " 条)");
      else toast(r.message || "推送失败");
    } catch (e) {
      toast("推送失败: " + e.message);
    } finally {
      btn.textContent = old;
      btn.disabled = false;
    }
  });

  // ---------- 设置 ----------
  function renderRegionSelect() {
    const sel = $("regionSelect");
    const prev = sel.value;
    sel.innerHTML = "";
    (config.regions || []).forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r.woeid;
      opt.dataset.label = r.label;
      opt.textContent = r.label + " (WOEID " + r.woeid + ")";
      sel.appendChild(opt);
    });
    if (prev) sel.value = prev;
  }

  function renderPresetSelect() {
    const sel = $("presetSelect");
    sel.innerHTML = "";
    const presets = config.presets || {};
    Object.keys(presets).forEach((woeid) => {
      const opt = document.createElement("option");
      opt.value = woeid;
      opt.textContent = presets[woeid] + " (" + woeid + ")";
      sel.appendChild(opt);
    });
  }

  function renderRegionEditor() {
    const ul = $("regionEditor");
    ul.innerHTML = "";
    editRegions.forEach((r, idx) => {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.className = "r-label";
      label.textContent = r.label;
      const woeid = document.createElement("span");
      woeid.className = "r-woeid";
      woeid.textContent = "WOEID " + r.woeid;
      const del = document.createElement("button");
      del.className = "del";
      del.textContent = "✕";
      del.title = "删除";
      del.addEventListener("click", () => {
        editRegions.splice(idx, 1);
        renderRegionEditor();
      });
      li.appendChild(label);
      li.appendChild(woeid);
      li.appendChild(del);
      ul.appendChild(li);
    });
  }

  $("addRegionBtn").addEventListener("click", () => {
    const sel = $("presetSelect");
    const woeid = sel.value;
    const label = (config.presets || {})[woeid] || ("WOEID " + woeid);
    if (editRegions.some((r) => r.woeid === woeid)) { toast("该地区已在列表中"); return; }
    editRegions.push({ woeid, label });
    renderRegionEditor();
  });

  $("saveBtn").addEventListener("click", async () => {
    if (!editRegions.length) { toast("至少保留一个地区"); return; }
    const btn = $("saveBtn");
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = "保存中…";
    try {
      config = await api("/api/settings", {
        method: "POST",
        body: JSON.stringify({
          regions: editRegions,
          rotate: $("rotateToggle").checked,
          top_n: parseInt($("topN").value, 10) || 15,
          min_volume: parseInt($("minVolume").value, 10) || 0,
          quiet_hours: $("quietHours").value.trim(),
        }),
      });
      applyConfigToUI();
      toast("设置已保存, 下次自动推送生效");
    } catch (e) {
      toast("保存失败: " + e.message);
    } finally {
      btn.textContent = old;
      btn.disabled = false;
    }
  });

  function applyConfigToUI() {
    renderRegionSelect();
    renderPresetSelect();
    editRegions = (config.regions || []).map((r) => ({ woeid: r.woeid, label: r.label }));
    renderRegionEditor();
    $("rotateToggle").checked = !!config.rotate;
    $("topN").value = config.top_n;
    $("minVolume").value = config.min_volume;
    $("quietHours").value = config.quiet_hours || "";

    const rapid = $("stRapid");
    rapid.textContent = config.rapidapi_ready ? "已配置" : "未配置";
    rapid.className = config.rapidapi_ready ? "status-ok" : "status-bad";
    const tgEl = $("stTg");
    tgEl.textContent = config.telegram_ready ? (config.target_count + " 个目标") : "未配置";
    tgEl.className = config.telegram_ready ? "status-ok" : "status-bad";
  }

  // ---------- 启动 ----------
  (async function init() {
    try {
      config = await api("/api/config");
      applyConfigToUI();
      await loadTrends();
    } catch (e) {
      $("trendMeta").textContent = "";
      const empty = $("trendEmpty");
      empty.textContent = "初始化失败: " + e.message;
      empty.classList.remove("hidden");
    }
  })();
})();
