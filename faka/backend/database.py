"""SQLite 数据访问层。

故意只用标准库 sqlite3,零额外 ORM 依赖,方便在任意 VPS 上直接跑起来。
"""
import os
import sqlite3
import threading
from contextlib import contextmanager

DB_PATH = os.environ.get("FAKA_DB", os.path.join(os.path.dirname(__file__), "faka.db"))

# sqlite3 连接默认不是线程安全的;用线程本地存储为每个线程维护一条连接。
_local = threading.local()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


@contextmanager
def transaction():
    """在一个事务里执行写操作,异常时回滚。"""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    sort        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id  INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    price        REAL NOT NULL DEFAULT 0,          -- 单价(元)
    enabled      INTEGER NOT NULL DEFAULT 1,
    sort         INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 卡密库存:一条记录 = 一张待售卡。售出后绑定到订单。
CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    secret      TEXT NOT NULL,                      -- 卡密内容
    status      TEXT NOT NULL DEFAULT 'unsold',     -- unsold | sold
    order_id    INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no     TEXT NOT NULL UNIQUE,
    product_id   INTEGER NOT NULL REFERENCES products(id),
    product_name TEXT NOT NULL,                     -- 下单时快照
    unit_price   REAL NOT NULL,
    quantity     INTEGER NOT NULL,
    amount       REAL NOT NULL,
    contact      TEXT NOT NULL DEFAULT '',          -- 买家邮箱/联系方式,用于找回
    status       TEXT NOT NULL DEFAULT 'pending',   -- pending | paid | cancelled
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    paid_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_cards_product ON cards(product_id, status);
CREATE INDEX IF NOT EXISTS idx_orders_no ON orders(order_no);
CREATE INDEX IF NOT EXISTS idx_products_cat ON products(category_id);
"""


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    _seed_demo(conn)


def _seed_demo(conn: sqlite3.Connection):
    """首次运行时塞一点演示数据,方便立刻看到效果。"""
    n = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
    if n:
        return
    cur = conn.cursor()
    cur.execute("INSERT INTO categories(name, sort) VALUES (?, ?)", ("游戏点卡", 1))
    cat_game = cur.lastrowid
    cur.execute("INSERT INTO categories(name, sort) VALUES (?, ?)", ("会员充值", 2))
    cat_vip = cur.lastrowid

    cur.execute(
        "INSERT INTO products(category_id, name, description, price, sort) VALUES (?,?,?,?,?)",
        (cat_game, "Steam 充值卡 100 元", "全国通用,自动发货,秒到账。", 95.0, 1),
    )
    p1 = cur.lastrowid
    cur.execute(
        "INSERT INTO products(category_id, name, description, price, sort) VALUES (?,?,?,?,?)",
        (cat_vip, "某视频 VIP 月卡", "官方直充,下单后自动发卡。", 15.0, 1),
    )
    p2 = cur.lastrowid

    demo_cards = [(p1, f"STEAM-DEMO-{i:04d}-XXXX") for i in range(1, 11)]
    demo_cards += [(p2, f"VIP-DEMO-{i:04d}-YYYY") for i in range(1, 21)]
    cur.executemany("INSERT INTO cards(product_id, secret) VALUES (?,?)", demo_cards)
    conn.commit()
