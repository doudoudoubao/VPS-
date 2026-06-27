# X 热门趋势 → Telegram 推送

把 **X(Twitter) 实时热门趋势榜** 定时抓取并推送到你的 **Telegram Bot**。

- 数据源：RapidAPI 上的第三方 Twitter 聚合接口（默认 `twitter154`，有免费额度）
- 推送内容：指定地区（全球 / 美国 / 日本 …）的实时 Trending Topics 榜单
- **地区轮播**：配多个地区时每次轮流推一个（也可一次性全推）
- **定时静音**：指定时段不推送（如夜间 23:00–07:00）
- **多目标推送**：chat_id 支持多个，可同时推到私聊 / 群 / **频道**（`@频道名`）
- 其它：榜单去重（不重复刷屏）、讨论量过滤、HTML 富文本带链接
- 运行方式：cron 单次 / 常驻轮询 / systemd / Docker 均可

---

## 1. 准备

### RapidAPI Key
1. 注册 [rapidapi.com](https://rapidapi.com)
2. 订阅一个带 `trends` 接口的 Twitter API，默认用
   [twitter154](https://rapidapi.com/omarmhaimdat/api/twitter154)（免费档即可）
3. 在 API 页面 **Endpoints** 里复制你的 `x-rapidapi-key`

> 换别的提供方也行，改 `config.env` 里的 `RAPIDAPI_HOST` / `RAPIDAPI_TRENDS_PATH`
> 即可，脚本对常见返回结构做了兼容解析。

### Telegram Bot
1. 在 Telegram 找 [@BotFather](https://t.me/BotFather) → `/newbot` 拿到 **token**
2. 获取推送目标 `chat_id`（可配多个，逗号分隔，同时推送）：
   - **私聊/群**：给 bot 发条消息（群里把 bot 拉进去），用
     [@userinfobot](https://t.me/userinfobot) 取 chat_id（群是负数）
   - **频道**：把 bot 设为频道**管理员**，`chat_id` 直接填 `@频道用户名`
     （或 `-100` 开头的数字 id）

---

## 2. 配置

```bash
git clone <this-repo> && cd VPS-
pip install -r requirements.txt

cp config.example.env config.env
# 编辑 config.env, 填入 RAPIDAPI_KEY / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
```

加载环境变量并测试推送一次：

```bash
export $(grep -v '^#' config.env | xargs)
python x_trending_bot.py --once --force
```

看到 Telegram 收到一条「🔥 X 实时热门趋势」即为成功。

---

## 3. 运行方式

### A. cron（推荐，最省资源）
每小时整点跑一次，自动去重：

```cron
0 * * * * cd /opt/VPS- && /usr/bin/env $(grep -v '^#' config.env | xargs) python3 x_trending_bot.py --once >> /var/log/x-trend.log 2>&1
```

### B. 常驻轮询
```bash
python x_trending_bot.py --interval 3600   # 每 3600 秒拉一次
```

### C. systemd
```bash
sudo cp -r . /opt/VPS- && cd /opt/VPS-
sudo cp deploy/x-trending-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now x-trending-bot
journalctl -u x-trending-bot -f
```

### D. Docker
```bash
docker build -t x-trending-bot .
docker run -d --name x-trend --restart unless-stopped \
  --env-file config.env x-trending-bot --interval 3600
```

---

## 4. 命令行参数

| 参数 | 说明 |
|------|------|
| `--once` | 只执行一次（配合 cron） |
| `--interval N` | 常驻轮询，每 N 秒一次 |
| `--force` | 忽略去重**和静音时段**，强制推送（测试用） |
| `-v` | 调试日志 |

## 5. 主要配置项（config.env）

| 变量 | 默认 | 说明 |
|------|------|------|
| `RAPIDAPI_KEY` | — | **必填**，RapidAPI 密钥 |
| `TELEGRAM_BOT_TOKEN` | — | **必填**，Bot token |
| `TELEGRAM_CHAT_ID` | — | **必填**，推送目标，多个用逗号分隔（私聊/群/`@频道名`） |
| `TREND_WOEID` | `1` | 地区，多个用逗号分隔实现轮播。1=全球 23424977=美国 23424856=日本 |
| `TREND_REGION_LABEL` | — | 地区显示名，与 `TREND_WOEID` 一一对应 |
| `TREND_ROTATE` | `on` | `on`=每次轮流推一个地区；`off`=每次全推 |
| `QUIET_HOURS` | — | 静音时段（本地时间），如 `23:00-07:00`，支持跨午夜；留空=全天推 |
| `TREND_TOP_N` | `15` | 最多推送前 N 条 |
| `TREND_MIN_VOLUME` | `0` | 只推讨论量≥该值的趋势 |

### 地区轮播说明
配 `TREND_WOEID=1,23424977,23424856`、`TREND_ROTATE=on` 后：
- **cron / 轮询每次只推一个地区**，按顺序轮换（全球→美国→日本→全球…），
  轮换指针记录在状态文件里。想每小时换一个地区，配合 `0 * * * *` 即可。
- 设 `TREND_ROTATE=off` 则每次把所有地区一次性全推。

> ⚠️ `config.env` 含密钥，已在 `.gitignore` 中，**不要提交到仓库**。
