let TOKEN = localStorage.getItem("faka_token") || "";
let CUR_TAB = "orders";
let CATS_CACHE = [];

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, m =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
}
function req(path, opts = {}) { return api(path, { ...opts, token: TOKEN }); }

// ---------------- 登录 ----------------
async function login() {
  const username = document.getElementById("l-user").value.trim();
  const password = document.getElementById("l-pass").value;
  try {
    const r = await api("/api/admin/login", { method: "POST", body: { username, password } });
    TOKEN = r.token;
    localStorage.setItem("faka_token", TOKEN);
    enterAdmin();
  } catch (e) { toast(e.message); }
}
function logout() {
  req("/api/admin/logout", { method: "POST" }).catch(() => {});
  TOKEN = "";
  localStorage.removeItem("faka_token");
  location.reload();
}

async function enterAdmin() {
  document.getElementById("loginView").style.display = "none";
  document.getElementById("adminView").style.display = "";
  document.getElementById("logoutBtn").style.display = "";
  await loadStats();
  tab(CUR_TAB);
}

async function loadStats() {
  try {
    const s = await req("/api/admin/stats");
    document.getElementById("stats").innerHTML = `
      <div class="stat"><div class="n">${s.paid_orders}</div><div class="l">已支付订单</div></div>
      <div class="stat"><div class="n">${money(s.revenue)}</div><div class="l">总收入</div></div>
      <div class="stat"><div class="n">${s.pending_orders}</div><div class="l">待支付</div></div>
      <div class="stat"><div class="n">${s.products}</div><div class="l">商品数</div></div>
      <div class="stat"><div class="n">${s.stock}</div><div class="l">剩余库存</div></div>`;
  } catch (e) {
    if (String(e.message).includes("登录")) { logout(); }
  }
}

function tab(name) {
  CUR_TAB = name;
  ["orders", "products", "cats"].forEach(t => {
    document.getElementById("t-" + t).className = t === name ? "" : "btn-ghost";
  });
  if (name === "orders") renderOrders();
  if (name === "products") renderProducts();
  if (name === "cats") renderCats();
}

// ---------------- 订单 ----------------
async function renderOrders() {
  const list = await req("/api/admin/orders");
  const panel = document.getElementById("panel");
  if (!list.length) { panel.innerHTML = `<div class="empty">暂无订单</div>`; return; }
  panel.innerHTML = `<table><thead><tr>
    <th>订单号</th><th>商品</th><th>数量</th><th>金额</th><th>联系方式</th><th>状态</th><th>时间</th>
    </tr></thead><tbody>` +
    list.map(o => `<tr>
      <td>${o.order_no}</td>
      <td>${esc(o.product_name)}</td>
      <td>${o.quantity}</td>
      <td>${money(o.amount)}</td>
      <td>${esc(o.contact || "-")}</td>
      <td><span class="badge ${o.status}">${statusText[o.status] || o.status}</span></td>
      <td class="muted">${o.created_at}</td>
    </tr>`).join("") + `</tbody></table>`;
}

// ---------------- 商品 ----------------
async function renderProducts() {
  CATS_CACHE = await req("/api/admin/categories");
  const list = await req("/api/admin/products");
  const panel = document.getElementById("panel");
  const catName = id => (CATS_CACHE.find(c => c.id === id) || {}).name || "-";
  panel.innerHTML = `<button style="margin-bottom:14px" onclick="editProduct()">+ 新增商品</button>
    <table><thead><tr>
    <th>名称</th><th>分类</th><th>单价</th><th>库存</th><th>状态</th><th>操作</th>
    </tr></thead><tbody>` +
    list.map(p => `<tr>
      <td>${esc(p.name)}</td>
      <td>${esc(catName(p.category_id))}</td>
      <td>${money(p.price)}</td>
      <td>${p.stock}</td>
      <td>${p.enabled ? "<span class='badge paid'>上架</span>" : "<span class='badge cancelled'>下架</span>"}</td>
      <td>
        <button class="btn-sm" onclick='editProduct(${JSON.stringify(p)})'>编辑</button>
        <button class="btn-sm" onclick="manageCards(${p.id}, '${esc(p.name)}')">卡密</button>
        <button class="btn-sm btn-danger" onclick="delProduct(${p.id})">删</button>
      </td>
    </tr>`).join("") + `</tbody></table>`;
}

function editProduct(p) {
  const opts = `<option value="">未分类</option>` + CATS_CACHE.map(c =>
    `<option value="${c.id}" ${p && p.category_id === c.id ? "selected" : ""}>${esc(c.name)}</option>`).join("");
  openModal(`
    <h2>${p ? "编辑商品" : "新增商品"}</h2>
    <label>名称</label><input id="f-name" value="${p ? esc(p.name) : ""}" />
    <label>分类</label><select id="f-cat">${opts}</select>
    <label>描述</label><textarea id="f-desc" rows="2">${p ? esc(p.description) : ""}</textarea>
    <label>单价(元)</label><input id="f-price" type="number" step="0.01" value="${p ? p.price : 0}" />
    <label>排序(小在前)</label><input id="f-sort" type="number" value="${p ? p.sort : 0}" />
    <label><input type="checkbox" id="f-enabled" ${!p || p.enabled ? "checked" : ""} style="width:auto"> 上架销售</label>
    <div class="actions">
      <button class="btn-ghost" onclick="closeModal()">取消</button>
      <button onclick="saveProduct(${p ? p.id : 0})">保存</button>
    </div>`);
}

async function saveProduct(id) {
  const body = {
    name: document.getElementById("f-name").value.trim(),
    category_id: document.getElementById("f-cat").value ? Number(document.getElementById("f-cat").value) : null,
    description: document.getElementById("f-desc").value.trim(),
    price: Number(document.getElementById("f-price").value) || 0,
    sort: Number(document.getElementById("f-sort").value) || 0,
    enabled: document.getElementById("f-enabled").checked,
  };
  if (!body.name) { toast("请填写名称"); return; }
  try {
    if (id) await req(`/api/admin/products/${id}`, { method: "PUT", body });
    else await req("/api/admin/products", { method: "POST", body });
    closeModal(); toast("已保存"); renderProducts(); loadStats();
  } catch (e) { toast(e.message); }
}

async function delProduct(id) {
  if (!confirm("删除商品会同时删除其卡密,确认?")) return;
  try { await req(`/api/admin/products/${id}`, { method: "DELETE" }); toast("已删除"); renderProducts(); loadStats(); }
  catch (e) { toast(e.message); }
}

// ---------------- 卡密 ----------------
async function manageCards(pid, name) {
  const cards = await req(`/api/admin/products/${pid}/cards`);
  const unsold = cards.filter(c => c.status === "unsold").length;
  openModal(`
    <h2>卡密管理</h2>
    <p class="sub">${esc(name)} · 未售 ${unsold} / 共 ${cards.length}</p>
    <label>批量导入(一行一张)</label>
    <textarea id="c-input" rows="5" placeholder="CARD-AAAA-BBBB&#10;CARD-CCCC-DDDD"></textarea>
    <button style="margin-top:10px" onclick="addCards(${pid})">导入卡密</button>
    <div style="max-height:240px;overflow:auto;margin-top:16px">
      <table><thead><tr><th>卡密</th><th>状态</th><th></th></tr></thead><tbody>
      ${cards.map(c => `<tr>
        <td style="font-family:monospace">${esc(c.secret)}</td>
        <td>${c.status === "sold" ? "<span class='badge paid'>已售</span>" : "<span class='badge pending'>未售</span>"}</td>
        <td>${c.status === "unsold" ? `<button class="btn-sm btn-danger" onclick="delCard(${c.id}, ${pid}, '${esc(name)}')">删</button>` : ""}</td>
      </tr>`).join("")}
      </tbody></table>
    </div>
    <div class="actions"><button class="btn-ghost" onclick="closeModal()">关闭</button></div>`);
}

async function addCards(pid) {
  const text = document.getElementById("c-input").value;
  const secrets = text.split("\n").map(s => s.trim()).filter(Boolean);
  if (!secrets.length) { toast("请输入卡密"); return; }
  try {
    const r = await req(`/api/admin/products/${pid}/cards`, { method: "POST", body: { secrets } });
    toast(`成功导入 ${r.added} 张`);
    renderProducts(); loadStats();
    // 重新打开刷新列表
    const p = (await req("/api/admin/products")).find(x => x.id === pid);
    manageCards(pid, p.name);
  } catch (e) { toast(e.message); }
}

async function delCard(id, pid, name) {
  try { await req(`/api/admin/cards/${id}`, { method: "DELETE" }); toast("已删除"); manageCards(pid, name); renderProducts(); loadStats(); }
  catch (e) { toast(e.message); }
}

// ---------------- 分类 ----------------
async function renderCats() {
  const list = await req("/api/admin/categories");
  const panel = document.getElementById("panel");
  panel.innerHTML = `<button style="margin-bottom:14px" onclick="editCat()">+ 新增分类</button>
    <table><thead><tr><th>名称</th><th>排序</th><th>操作</th></tr></thead><tbody>` +
    list.map(c => `<tr>
      <td>${esc(c.name)}</td><td>${c.sort}</td>
      <td>
        <button class="btn-sm" onclick='editCat(${JSON.stringify(c)})'>编辑</button>
        <button class="btn-sm btn-danger" onclick="delCat(${c.id})">删</button>
      </td>
    </tr>`).join("") + `</tbody></table>`;
}

function editCat(c) {
  openModal(`
    <h2>${c ? "编辑分类" : "新增分类"}</h2>
    <label>名称</label><input id="cat-name" value="${c ? esc(c.name) : ""}" />
    <label>排序(小在前)</label><input id="cat-sort" type="number" value="${c ? c.sort : 0}" />
    <div class="actions">
      <button class="btn-ghost" onclick="closeModal()">取消</button>
      <button onclick="saveCat(${c ? c.id : 0})">保存</button>
    </div>`);
}

async function saveCat(id) {
  const body = {
    name: document.getElementById("cat-name").value.trim(),
    sort: Number(document.getElementById("cat-sort").value) || 0,
  };
  if (!body.name) { toast("请填写名称"); return; }
  try {
    if (id) await req(`/api/admin/categories/${id}`, { method: "PUT", body });
    else await req("/api/admin/categories", { method: "POST", body });
    closeModal(); toast("已保存"); renderCats();
  } catch (e) { toast(e.message); }
}

async function delCat(id) {
  if (!confirm("确认删除该分类?")) return;
  try { await req(`/api/admin/categories/${id}`, { method: "DELETE" }); toast("已删除"); renderCats(); }
  catch (e) { toast(e.message); }
}

// ---------------- 弹窗 ----------------
function openModal(html) {
  document.getElementById("modalBody").innerHTML = html;
  document.getElementById("modal").classList.add("show");
}
function closeModal() { document.getElementById("modal").classList.remove("show"); }

// 启动:已有 token 直接进入
if (TOKEN) {
  loadStats().then(() => { if (TOKEN) enterAdmin(); });
}
