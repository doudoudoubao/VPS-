#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Mini App 后端 (X 实时热门趋势)

为 x_trending_bot 提供一个可在 Telegram 里直接打开的可视化界面:
  - 浏览任意地区的实时热门趋势榜 (实时调 RapidAPI)
  - 在线切换轮播地区 / 静音时段 / Top N / 最低讨论量 等设置
    (写入 .webapp_settings.json, cron / 常驻 bot 下次运行即生效)
  - 「立即推送」: 把当前地区榜单马上推到已配置的 Telegram 目标

仅依赖 Python 标准库 (+ 复用 x_trending_bot 里的 requests)。
Telegram WebApp 的 initData 会在服务端做 HMAC 校验, 防止伪造。

环境变量 (除复用 bot 的 RAPIDAPI_*/TELEGRAM_* 外):
  WEBAPP_HOST                监听地址, 默认 127.0.0.1 (建议放 nginx 后面)
  WEBAPP_PORT                监听端口, 默认 8088
  WEBAPP_AUTH               on(默认)/off; off 时跳过 initData 校验 (仅本地调试)
  WEBAPP_ALLOWED_USER_IDS   逗号分隔的 Telegram user id 白名单 (留空=不限制)
  WEBAPP_INITDATA_MAX_AGE   initData 有效期(秒), 默认 86400; 0=不校验时间
  WEBAPP_PUBLIC_URL         Mini App 的公网 https 地址 (用于 --set-menu)

用法:
  python webapp_server.py                 # 启动服务
  python webapp_server.py --set-menu      # 把 bot 菜单按钮设为打开本 Mini App
"""

import argparse
import hashlib
import hmac
import json
import logging
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖 requests, 请先执行: pip install -r requirements.txt")

import x_trending_bot as bot

log = logging.getLogger("webapp_server")

ROOT = Path(__file__).resolve().parent
WEBAPP_DIR = Path(os.environ.get("WEBAPP_DIR", str(ROOT / "webapp"))).resolve()

# 常见地区 WOEID -> 名称, 供前端做下拉预设 (用户也可手填任意 WOEID)
REGION_PRESETS = {
    "1": "全球",
    "23424977": "美国",
    "23424856": "日本",
    "23424975": "英国",
    "23424819": "韩国",
    "23424781": "印度",
    "23424775": "加拿大",
    "23424748": "澳大利亚",
    "23424954": "新加坡",
    "23424829": "德国",
    "615702": "法国",
}

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


# --------------------------------------------------------------------------- #
# Telegram initData 校验
# --------------------------------------------------------------------------- #
class AuthError(Exception):
    """initData 校验失败。"""


def _auth_enabled() -> bool:
    return os.environ.get("WEBAPP_AUTH", "on").strip().lower() not in ("off", "0", "false")


def _allowed_user_ids() -> set[str]:
    raw = os.environ.get("WEBAPP_ALLOWED_USER_IDS", "")
    return {x.strip() for x in raw.split(",") if x.strip()}


def verify_init_data(init_data: str, bot_token: str) -> dict:
    """
    校验 Telegram WebApp 的 initData, 返回解析后的字段 dict (含解析出的 user)。
    校验失败抛 AuthError。
    算法见 https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data:
        raise AuthError("缺少 initData")
    if not bot_token:
        raise AuthError("服务端未配置 TELEGRAM_BOT_TOKEN, 无法校验")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    recv_hash = pairs.pop("hash", None)
    if not recv_hash:
        raise AuthError("initData 缺少 hash")

    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc_hash, recv_hash):
        raise AuthError("initData 签名不匹配")

    # 时效性校验 (防重放)
    max_age = int(os.environ.get("WEBAPP_INITDATA_MAX_AGE", "86400") or "0")
    if max_age > 0:
        import time
        try:
            auth_date = int(pairs.get("auth_date", "0"))
        except ValueError:
            auth_date = 0
        if auth_date and time.time() - auth_date > max_age:
            raise AuthError("initData 已过期")

    user = {}
    if pairs.get("user"):
        try:
            user = json.loads(pairs["user"])
        except (ValueError, TypeError):
            user = {}
    pairs["user"] = user

    allowed = _allowed_user_ids()
    if allowed and str(user.get("id", "")) not in allowed:
        raise AuthError("该 Telegram 账号不在白名单内")

    return pairs


# --------------------------------------------------------------------------- #
# 业务逻辑 (复用 x_trending_bot)
# --------------------------------------------------------------------------- #
def current_settings() -> dict:
    cfg = bot.Config()
    return {
        "regions": [{"woeid": w, "label": label} for w, label in cfg.regions],
        "rotate": cfg.rotate,
        "top_n": cfg.top_n,
        "min_volume": cfg.min_volume,
        "quiet_hours": cfg.quiet_hours_raw,
        "telegram_ready": bool(cfg.tg_token and cfg.tg_chat_ids),
        "rapidapi_ready": bool(cfg.rapidapi_key),
        "target_count": len(cfg.tg_chat_ids),
        "presets": REGION_PRESETS,
    }


def fetch_region_trends(woeid: str) -> list[dict]:
    cfg = bot.Config()
    if not cfg.rapidapi_key:
        raise RuntimeError("未配置 RAPIDAPI_KEY")
    trends = bot.fetch_trends(cfg, woeid)
    if cfg.min_volume > 0:
        trends = [t for t in trends if (t["volume"] or 0) >= cfg.min_volume]
    return trends[: cfg.top_n]


def save_settings(payload: dict) -> dict:
    """校验并落盘 Mini App 设置覆盖, 返回新的有效设置。"""
    data = bot.load_webapp_settings()

    if "regions" in payload and isinstance(payload["regions"], list):
        woeids, labels = [], []
        for r in payload["regions"]:
            if not isinstance(r, dict):
                continue
            w = str(r.get("woeid", "")).strip()
            if not w:
                continue
            woeids.append(w)
            labels.append(str(r.get("label", "")).strip() or REGION_PRESETS.get(w, f"WOEID {w}"))
        if woeids:
            data["woeids"] = woeids
            data["labels"] = labels

    if "rotate" in payload:
        data["rotate"] = bool(payload["rotate"])
    if "top_n" in payload:
        data["top_n"] = max(1, min(50, int(payload["top_n"])))
    if "min_volume" in payload:
        data["min_volume"] = max(0, int(payload["min_volume"]))
    if "quiet_hours" in payload:
        qh = str(payload["quiet_hours"] or "").strip()
        # 校验格式: 空 或 HH:MM-HH:MM
        if qh and bot._parse_quiet_hours(qh) is None:
            raise ValueError("静音时段格式应为 HH:MM-HH:MM, 例如 23:00-07:00")
        data["quiet_hours"] = qh

    bot.save_webapp_settings(data)
    return current_settings()


def push_now(woeid: str, label: str) -> dict:
    cfg = bot.Config()
    if not (cfg.tg_token and cfg.tg_chat_ids):
        raise RuntimeError("未配置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
    trends = fetch_region_trends(woeid)
    if not trends:
        return {"ok": 0, "count": 0, "message": "该地区暂无可推送的趋势"}
    label = label or REGION_PRESETS.get(woeid, f"WOEID {woeid}")
    text = bot.build_message(label, trends)
    ok = bot.send_telegram(cfg, text)
    return {"ok": ok, "count": len(trends), "targets": len(cfg.tg_chat_ids)}


# --------------------------------------------------------------------------- #
# HTTP Handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "XTrendMiniApp/1.0"

    # ---- 工具 ----
    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _require_auth(self) -> dict:
        """返回校验通过的 initData 字段; 失败抛 AuthError。"""
        if not _auth_enabled():
            return {"user": {"id": "dev"}, "_dev": True}
        init_data = self.headers.get("X-Telegram-Init-Data", "")
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        return verify_init_data(init_data, token)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw or b"{}")
        except (ValueError, TypeError):
            return {}

    def log_message(self, fmt, *args):  # noqa: A003 - 静默默认 stderr 噪声
        log.info("%s - %s", self.address_string(), fmt % args)

    # ---- 路由 ----
    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._json({"status": "ok", "auth": _auth_enabled()})
        if path.startswith("/api/"):
            return self._handle_api_get(path)
        return self._serve_static(path)

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        try:
            self._require_auth()
        except AuthError as e:
            return self._json({"error": str(e)}, 401)

        body = self._read_body()
        try:
            if path == "/api/settings":
                return self._json(save_settings(body))
            if path == "/api/push":
                woeid = str(body.get("woeid", "")).strip()
                if not woeid:
                    return self._json({"error": "缺少 woeid"}, 400)
                return self._json(push_now(woeid, str(body.get("label", "")).strip()))
        except (ValueError, RuntimeError) as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            log.exception("POST %s 处理失败", path)
            return self._json({"error": f"服务端错误: {e}"}, 500)
        return self._json({"error": "not found"}, 404)

    def _handle_api_get(self, path: str):
        try:
            self._require_auth()
        except AuthError as e:
            return self._json({"error": str(e)}, 401)
        try:
            if path == "/api/config":
                return self._json(current_settings())
            if path == "/api/trends":
                qs = dict(parse_qsl(urlparse(self.path).query))
                woeid = (qs.get("woeid") or "1").strip()
                trends = fetch_region_trends(woeid)
                label = qs.get("label") or REGION_PRESETS.get(woeid, f"WOEID {woeid}")
                return self._json({"woeid": woeid, "label": label, "trends": trends})
        except (ValueError, RuntimeError) as e:
            return self._json({"error": str(e)}, 400)
        except requests.RequestException as e:
            return self._json({"error": f"数据源请求失败: {e}"}, 502)
        except Exception as e:  # noqa: BLE001
            log.exception("GET %s 处理失败", path)
            return self._json({"error": f"服务端错误: {e}"}, 500)
        return self._json({"error": "not found"}, 404)

    def _serve_static(self, path: str):
        if path in ("", "/"):
            path = "/index.html"
        target = (WEBAPP_DIR / path.lstrip("/")).resolve()
        # 防目录穿越
        if WEBAPP_DIR not in target.parents and target != WEBAPP_DIR:
            return self._json({"error": "forbidden"}, 403)
        if not target.is_file():
            return self._json({"error": "not found"}, 404)
        ctype = STATIC_TYPES.get(target.suffix, "application/octet-stream")
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


# --------------------------------------------------------------------------- #
# 菜单按钮 (可选)
# --------------------------------------------------------------------------- #
def set_menu_button() -> None:
    """把 bot 的默认菜单按钮设为打开本 Mini App (需要 WEBAPP_PUBLIC_URL)。"""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    url = os.environ.get("WEBAPP_PUBLIC_URL", "").strip()
    if not token or not url:
        sys.exit("需要同时设置 TELEGRAM_BOT_TOKEN 和 WEBAPP_PUBLIC_URL")
    if not url.startswith("https://"):
        sys.exit("WEBAPP_PUBLIC_URL 必须是 https 地址 (Telegram 要求)")
    api = f"https://api.telegram.org/bot{token}/setChatMenuButton"
    payload = {"menu_button": {"type": "web_app", "text": "趋势榜",
                               "web_app": {"url": url}}}
    resp = requests.post(api, json=payload, timeout=20)
    if resp.status_code == 200 and resp.json().get("ok"):
        print(f"✅ 已把菜单按钮设为打开: {url}")
    else:
        sys.exit(f"设置失败 [{resp.status_code}]: {resp.text}")


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="X 趋势 Telegram Mini App 后端")
    parser.add_argument("--set-menu", action="store_true",
                        help="把 bot 菜单按钮设为打开本 Mini App 后退出")
    parser.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.set_menu:
        set_menu_button()
        return

    host = os.environ.get("WEBAPP_HOST", "127.0.0.1").strip()
    port = int(os.environ.get("WEBAPP_PORT", "8088") or "8088")

    if not WEBAPP_DIR.is_dir():
        sys.exit(f"前端目录不存在: {WEBAPP_DIR}")
    if not _auth_enabled():
        log.warning("WEBAPP_AUTH=off: 已关闭 initData 校验, 仅用于本地调试!")

    httpd = ThreadingHTTPServer((host, port), Handler)
    log.info("Mini App 后端已启动: http://%s:%d  (静态目录: %s)", host, port, WEBAPP_DIR)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("收到中断, 退出。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
