"""发卡网后端 —— FastAPI。

接口分两组:
  /api/...        面向买家(浏览商品、下单、支付、查单)
  /api/admin/...  面向后台(需登录 token,管理分类/商品/卡密/订单)

支付:内置「模拟支付」用于演示与自建场景;真实接入支付宝/微信/易支付时,
把 mock_pay 换成验签后的回调即可,发货逻辑(deliver_order)无需改动。
"""
import os
import random
import string
import time

from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import auth
import database as db

app = FastAPI(title="发卡网 API", version="1.0")

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")


@app.on_event("startup")
def _startup():
    db.init_db()


# ----------------------------- 工具函数 -----------------------------
def gen_order_no() -> str:
    ts = time.strftime("%Y%m%d%H%M%S")
    rnd = "".join(random.choices(string.digits, k=6))
    return f"{ts}{rnd}"


def row_to_dict(row) -> dict:
    return dict(row) if row is not None else None


def stock_of(conn, product_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS c FROM cards WHERE product_id=? AND status='unsold'",
        (product_id,),
    ).fetchone()["c"]


def deliver_order(conn, order: dict) -> list[str]:
    """支付成功后调用:原子地从库存取卡、绑定订单、标记已售。返回卡密列表。

    用事务 + 行内更新保证不会把同一张卡卖两次。
    """
    rows = conn.execute(
        "SELECT id, secret FROM cards WHERE product_id=? AND status='unsold' "
        "ORDER BY id LIMIT ?",
        (order["product_id"], order["quantity"]),
    ).fetchall()
    if len(rows) < order["quantity"]:
        raise HTTPException(status_code=409, detail="库存不足,无法发货,请联系客服退款")
    secrets_list = []
    for r in rows:
        conn.execute(
            "UPDATE cards SET status='sold', order_id=? WHERE id=? AND status='unsold'",
            (order["id"], r["id"]),
        )
        secrets_list.append(r["secret"])
    return secrets_list


# ----------------------------- 鉴权依赖 -----------------------------
def require_admin(authorization: str | None = Header(default=None)):
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    if not auth.check_token(token):
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return token


# =========================================================================
#  买家端接口
# =========================================================================
@app.get("/api/categories")
def list_categories():
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM categories ORDER BY sort, id").fetchall()
    return [row_to_dict(r) for r in rows]


@app.get("/api/products")
def list_products(category_id: int | None = None):
    conn = db.get_conn()
    sql = "SELECT * FROM products WHERE enabled=1"
    args: list = []
    if category_id:
        sql += " AND category_id=?"
        args.append(category_id)
    sql += " ORDER BY sort, id"
    rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = row_to_dict(r)
        d["stock"] = stock_of(conn, r["id"])
        out.append(d)
    return out


@app.get("/api/products/{product_id}")
def get_product(product_id: int):
    conn = db.get_conn()
    r = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not r:
        raise HTTPException(404, "商品不存在")
    d = row_to_dict(r)
    d["stock"] = stock_of(conn, product_id)
    return d


class CreateOrder(BaseModel):
    product_id: int
    quantity: int = Field(ge=1, le=100)
    contact: str = ""


@app.post("/api/orders")
def create_order(body: CreateOrder):
    with db.transaction() as conn:
        p = conn.execute(
            "SELECT * FROM products WHERE id=? AND enabled=1", (body.product_id,)
        ).fetchone()
        if not p:
            raise HTTPException(404, "商品不存在或已下架")
        if stock_of(conn, body.product_id) < body.quantity:
            raise HTTPException(409, "库存不足")
        amount = round(p["price"] * body.quantity, 2)
        order_no = gen_order_no()
        cur = conn.execute(
            "INSERT INTO orders(order_no, product_id, product_name, unit_price, "
            "quantity, amount, contact) VALUES (?,?,?,?,?,?,?)",
            (order_no, p["id"], p["name"], p["price"], body.quantity, amount, body.contact),
        )
        oid = cur.lastrowid
    return {
        "order_no": order_no,
        "product_name": p["name"],
        "quantity": body.quantity,
        "amount": amount,
        "status": "pending",
        # 真实环境这里返回支付二维码/跳转链接
        "pay_url": f"/api/orders/{order_no}/mock_pay",
    }


@app.post("/api/orders/{order_no}/mock_pay")
def mock_pay(order_no: str):
    """模拟支付成功 -> 自动发货。生产环境替换为支付平台异步回调。"""
    with db.transaction() as conn:
        o = conn.execute(
            "SELECT * FROM orders WHERE order_no=?", (order_no,)
        ).fetchone()
        if not o:
            raise HTTPException(404, "订单不存在")
        order = row_to_dict(o)
        if order["status"] == "paid":
            return {"order_no": order_no, "status": "paid", "message": "订单已支付"}
        if order["status"] == "cancelled":
            raise HTTPException(409, "订单已取消")
        deliver_order(conn, order)  # 库存不足会抛 409 并回滚
        conn.execute(
            "UPDATE orders SET status='paid', paid_at=datetime('now') WHERE id=?",
            (order["id"],),
        )
    return {"order_no": order_no, "status": "paid", "message": "支付成功,已自动发货"}


@app.get("/api/orders/{order_no}")
def query_order(order_no: str):
    conn = db.get_conn()
    o = conn.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if not o:
        raise HTTPException(404, "订单不存在")
    d = row_to_dict(o)
    cards = []
    if d["status"] == "paid":
        rows = conn.execute(
            "SELECT secret FROM cards WHERE order_id=? ORDER BY id", (d["id"],)
        ).fetchall()
        cards = [r["secret"] for r in rows]
    d["cards"] = cards
    d.pop("id", None)
    return d


# =========================================================================
#  后台接口
# =========================================================================
class Login(BaseModel):
    username: str
    password: str


@app.post("/api/admin/login")
def admin_login(body: Login):
    if not auth.verify_login(body.username, body.password):
        raise HTTPException(401, "账号或密码错误")
    return {"token": auth.issue_token()}


@app.post("/api/admin/logout")
def admin_logout(token: str = Depends(require_admin)):
    auth.revoke_token(token)
    return {"ok": True}


@app.get("/api/admin/stats")
def admin_stats(_=Depends(require_admin)):
    conn = db.get_conn()
    paid = conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(amount),0) s FROM orders WHERE status='paid'"
    ).fetchone()
    pending = conn.execute(
        "SELECT COUNT(*) c FROM orders WHERE status='pending'"
    ).fetchone()["c"]
    products = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
    stock = conn.execute(
        "SELECT COUNT(*) c FROM cards WHERE status='unsold'"
    ).fetchone()["c"]
    return {
        "paid_orders": paid["c"],
        "revenue": round(paid["s"], 2),
        "pending_orders": pending,
        "products": products,
        "stock": stock,
    }


# --------- 分类管理 ---------
class CategoryIn(BaseModel):
    name: str
    sort: int = 0


@app.get("/api/admin/categories")
def admin_categories(_=Depends(require_admin)):
    return list_categories()


@app.post("/api/admin/categories")
def admin_add_category(body: CategoryIn, _=Depends(require_admin)):
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO categories(name, sort) VALUES (?,?)", (body.name, body.sort)
        )
    return {"id": cur.lastrowid}


@app.put("/api/admin/categories/{cid}")
def admin_update_category(cid: int, body: CategoryIn, _=Depends(require_admin)):
    with db.transaction() as conn:
        conn.execute(
            "UPDATE categories SET name=?, sort=? WHERE id=?", (body.name, body.sort, cid)
        )
    return {"ok": True}


@app.delete("/api/admin/categories/{cid}")
def admin_delete_category(cid: int, _=Depends(require_admin)):
    with db.transaction() as conn:
        conn.execute("DELETE FROM categories WHERE id=?", (cid,))
    return {"ok": True}


# --------- 商品管理 ---------
class ProductIn(BaseModel):
    category_id: int | None = None
    name: str
    description: str = ""
    price: float = Field(ge=0)
    enabled: bool = True
    sort: int = 0


@app.get("/api/admin/products")
def admin_products(_=Depends(require_admin)):
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM products ORDER BY sort, id").fetchall()
    out = []
    for r in rows:
        d = row_to_dict(r)
        d["stock"] = stock_of(conn, r["id"])
        out.append(d)
    return out


@app.post("/api/admin/products")
def admin_add_product(body: ProductIn, _=Depends(require_admin)):
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO products(category_id, name, description, price, enabled, sort) "
            "VALUES (?,?,?,?,?,?)",
            (body.category_id, body.name, body.description, body.price,
             int(body.enabled), body.sort),
        )
    return {"id": cur.lastrowid}


@app.put("/api/admin/products/{pid}")
def admin_update_product(pid: int, body: ProductIn, _=Depends(require_admin)):
    with db.transaction() as conn:
        conn.execute(
            "UPDATE products SET category_id=?, name=?, description=?, price=?, "
            "enabled=?, sort=? WHERE id=?",
            (body.category_id, body.name, body.description, body.price,
             int(body.enabled), body.sort, pid),
        )
    return {"ok": True}


@app.delete("/api/admin/products/{pid}")
def admin_delete_product(pid: int, _=Depends(require_admin)):
    with db.transaction() as conn:
        conn.execute("DELETE FROM products WHERE id=?", (pid,))
    return {"ok": True}


# --------- 卡密管理 ---------
class CardsIn(BaseModel):
    # 一行一张卡密
    secrets: list[str]


@app.post("/api/admin/products/{pid}/cards")
def admin_add_cards(pid: int, body: CardsIn, _=Depends(require_admin)):
    items = [s.strip() for s in body.secrets if s.strip()]
    if not items:
        raise HTTPException(400, "没有有效卡密")
    with db.transaction() as conn:
        if not conn.execute("SELECT 1 FROM products WHERE id=?", (pid,)).fetchone():
            raise HTTPException(404, "商品不存在")
        conn.executemany(
            "INSERT INTO cards(product_id, secret) VALUES (?,?)",
            [(pid, s) for s in items],
        )
    return {"added": len(items)}


@app.get("/api/admin/products/{pid}/cards")
def admin_list_cards(pid: int, status: str | None = None, _=Depends(require_admin)):
    conn = db.get_conn()
    sql = "SELECT id, secret, status, order_id, created_at FROM cards WHERE product_id=?"
    args: list = [pid]
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY id DESC LIMIT 500"
    return [row_to_dict(r) for r in conn.execute(sql, args).fetchall()]


@app.delete("/api/admin/cards/{card_id}")
def admin_delete_card(card_id: int, _=Depends(require_admin)):
    """只允许删除未售出的卡密。"""
    with db.transaction() as conn:
        r = conn.execute("SELECT status FROM cards WHERE id=?", (card_id,)).fetchone()
        if not r:
            raise HTTPException(404, "卡密不存在")
        if r["status"] == "sold":
            raise HTTPException(409, "已售出的卡密不可删除")
        conn.execute("DELETE FROM cards WHERE id=?", (card_id,))
    return {"ok": True}


# --------- 订单管理 ---------
@app.get("/api/admin/orders")
def admin_orders(status: str | None = None, _=Depends(require_admin)):
    conn = db.get_conn()
    sql = "SELECT * FROM orders"
    args: list = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    sql += " ORDER BY id DESC LIMIT 500"
    return [row_to_dict(r) for r in conn.execute(sql, args).fetchall()]


# =========================================================================
#  前端静态页面
# =========================================================================
app.mount("/static", StaticFiles(directory=os.path.join(FRONTEND_DIR, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/order")
def order_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "order.html"))


@app.get("/admin")
def admin_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "admin.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
