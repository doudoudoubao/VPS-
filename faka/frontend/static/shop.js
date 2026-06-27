let CATS = [];
let CUR_CAT = null;     // null = 全部
let CUR_PRODUCT = null;
let CUR_ORDER = null;

async function loadCats() {
  CATS = await api("/api/categories");
  const box = document.getElementById("cats");
  const all = `<div class="chip ${CUR_CAT === null ? "active" : ""}" onclick="pickCat(null)">全部</div>`;
  box.innerHTML = all + CATS.map(c =>
    `<div class="chip ${CUR_CAT === c.id ? "active" : ""}" onclick="pickCat(${c.id})">${esc(c.name)}</div>`
  ).join("");
}

function pickCat(id) {
  CUR_CAT = id;
  loadCats();
  loadProducts();
}

async function loadProducts() {
  const q = CUR_CAT ? `?category_id=${CUR_CAT}` : "";
  const list = await api("/api/products" + q);
  const grid = document.getElementById("grid");
  if (!list.length) {
    grid.innerHTML = `<div class="empty">暂无商品</div>`;
    return;
  }
  grid.innerHTML = list.map(p => {
    const out = p.stock <= 0;
    return `<div class="product">
      <h3>${esc(p.name)}</h3>
      <div class="desc">${esc(p.description || "")}</div>
      <div class="row">
        <span class="price">${money(p.price)}<small> / 张</small></span>
        <span class="stock ${out ? "out" : ""}">库存 ${p.stock}</span>
      </div>
      <button ${out ? "disabled" : ""} onclick='openBuy(${JSON.stringify(p)})'>
        ${out ? "已售罄" : "购买"}
      </button>
    </div>`;
  }).join("");
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, m =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
}

function openBuy(p) {
  CUR_PRODUCT = p;
  document.getElementById("m-name").textContent = p.name;
  document.getElementById("m-desc").textContent = p.description || "";
  document.getElementById("m-price").textContent = money(p.price) + " / 张";
  document.getElementById("m-stock").textContent = "库存 " + p.stock;
  document.getElementById("m-qty").value = 1;
  document.getElementById("m-qty").max = p.stock;
  document.getElementById("m-contact").value = "";
  updateTotal();
  document.getElementById("buyModal").classList.add("show");
}
function closeBuy() { document.getElementById("buyModal").classList.remove("show"); }
function updateTotal() {
  const qty = Math.max(1, parseInt(document.getElementById("m-qty").value) || 1);
  document.getElementById("m-total").textContent = money(CUR_PRODUCT.price * qty);
}
document.getElementById("m-qty").addEventListener("input", updateTotal);

async function submitOrder() {
  const qty = parseInt(document.getElementById("m-qty").value) || 1;
  const contact = document.getElementById("m-contact").value.trim();
  const btn = document.getElementById("m-submit");
  btn.disabled = true;
  try {
    CUR_ORDER = await api("/api/orders", {
      method: "POST",
      body: { product_id: CUR_PRODUCT.id, quantity: qty, contact },
    });
    closeBuy();
    showPay();
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
  }
}

function showPay() {
  document.getElementById("p-no").textContent = CUR_ORDER.order_no;
  document.getElementById("p-amount").textContent = money(CUR_ORDER.amount);
  document.getElementById("p-pay").style.display = "";
  document.getElementById("p-done").style.display = "none";
  document.getElementById("p-title").textContent = "订单已创建,请支付";
  document.getElementById("payModal").classList.add("show");
}

async function doPay() {
  const btn = document.getElementById("p-paybtn");
  btn.disabled = true;
  try {
    await api(`/api/orders/${CUR_ORDER.order_no}/mock_pay`, { method: "POST" });
    const order = await api(`/api/orders/${CUR_ORDER.order_no}`);
    document.getElementById("p-title").textContent = "支付成功";
    document.getElementById("p-pay").style.display = "none";
    document.getElementById("p-done").style.display = "";
    document.getElementById("p-cards").textContent = order.cards.join("\n");
  } catch (e) {
    toast(e.message);
    btn.disabled = false;
  }
}

function copyCards() { copy(document.getElementById("p-cards").textContent); }

loadCats();
loadProducts();
