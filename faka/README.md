# ⚡ 极速发卡 — 自动发卡网(前端 + 后端)

一个开箱即用的**自动发卡平台**:买家下单付款后,系统自动从库存取出卡密发货,无需人工。

- **后端**:FastAPI + SQLite,零重型依赖,单文件数据库,适合直接丢到 VPS 上跑
- **前端**:原生 HTML / CSS / JS,无需构建,由后端直接托管
- **支付**:内置「模拟支付」演示完整下单→付款→自动发货闭环;接真实支付只需替换一个回调

## 功能

买家端
- 商品分类浏览、库存展示、售罄禁购
- 下单(可填联系邮箱)→ 支付 → **自动发货**,卡密即时展示
- 凭订单号查询订单状态、重新获取卡密

后台(`/admin`,需登录)
- 数据概览:已支付订单数 / 总收入 / 待支付 / 商品数 / 剩余库存
- 分类管理(增删改)
- 商品管理(增删改、上下架、排序)
- 卡密管理(批量导入、查看已售/未售、删除未售卡密)
- 订单列表

## 目录结构

```
faka/
├── backend/
│   ├── app.py          # FastAPI 路由(买家端 + 后台 + 静态托管)
│   ├── database.py     # SQLite 表结构与连接(含演示数据)
│   ├── auth.py         # 后台登录鉴权(token)
│   └── requirements.txt
└── frontend/
    ├── index.html      # 商城首页
    ├── order.html      # 订单查询
    ├── admin.html      # 后台管理
    └── static/         # style.css / common.js / shop.js / admin.js
```

## 快速开始

```bash
cd faka/backend
pip install -r requirements.txt
python app.py            # 或: uvicorn app:app --host 0.0.0.0 --port 8000
```

打开浏览器:

| 页面     | 地址                          |
|----------|-------------------------------|
| 商城     | http://localhost:8000/        |
| 订单查询 | http://localhost:8000/order   |
| 后台     | http://localhost:8000/admin   |

首次启动会自动建表并写入演示商品与卡密,可直接体验完整购买流程。

## 配置(环境变量)

| 变量              | 默认值       | 说明                 |
|-------------------|--------------|----------------------|
| `FAKA_ADMIN_USER` | `admin`      | 后台账号             |
| `FAKA_ADMIN_PASS` | `admin123`   | 后台密码(**务必修改**) |
| `FAKA_DB`         | `backend/faka.db` | SQLite 文件路径 |

```bash
export FAKA_ADMIN_PASS='your-strong-password'
python app.py
```

## 接入真实支付

当前下单后调用的是 `POST /api/orders/{order_no}/mock_pay`(模拟支付)。
接入支付宝 / 微信 / 易支付时:

1. 下单接口返回真实的支付二维码 / 跳转链接(替换 `create_order` 里的 `pay_url`)
2. 新增一个**支付平台异步回调**接口,验签通过后调用现有的发货逻辑:
   ```python
   deliver_order(conn, order)          # 原子扣减库存 + 绑定卡密
   # 然后把订单标记为 paid
   ```
   发货与库存逻辑无需任何改动,只是把「模拟支付」换成「验签后的真实回调」。

## 安全提示

- 上线前**必须**修改后台密码,并建议放到 HTTPS 后面(Nginx / Caddy 反代)。
- token 存在内存中,服务重启后失效;高可用场景可换成 JWT + Redis。
