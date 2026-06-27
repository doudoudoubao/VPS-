#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X(Twitter) 实时热门趋势榜 -> Telegram 推送

数据源: RapidAPI 上的第三方 Twitter 聚合接口 (默认: twitter154)
功能:
  - 拉取指定地区 (WOEID) 的实时热门趋势榜
  - 去重 (同一榜单内容不重复推送)
  - 格式化后推送到 Telegram Bot
  - 支持单次执行 (--once, 适合 cron) 或常驻轮询 (--interval)

所有配置通过环境变量提供, 见 config.example.env。
"""

import argparse
import hashlib
import html
import json
import logging
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖 requests, 请先执行: pip install -r requirements.txt")


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class Config:
    """从环境变量读取配置。"""

    def __init__(self) -> None:
        # --- RapidAPI ---
        self.rapidapi_key = _env("RAPIDAPI_KEY")
        self.rapidapi_host = _env("RAPIDAPI_HOST", "twitter154.p.rapidapi.com")
        # 趋势接口路径, 不同 RapidAPI 提供方略有差异, 可覆盖
        self.trends_path = _env("RAPIDAPI_TRENDS_PATH", "/trends/")
        # WOEID: 1=全球, 23424977=美国, 23424856=日本, 23424781=中国(台湾另算)
        self.woeid = _env("TREND_WOEID", "1")
        self.woeid_param = _env("RAPIDAPI_WOEID_PARAM", "woeid")

        # --- Telegram ---
        self.tg_token = _env("TELEGRAM_BOT_TOKEN")
        self.tg_chat_id = _env("TELEGRAM_CHAT_ID")

        # --- 推送行为 ---
        self.top_n = int(_env("TREND_TOP_N", "15") or "15")
        # 只推送有讨论量(tweet_volume)的趋势, 过滤掉冷门
        self.min_volume = int(_env("TREND_MIN_VOLUME", "0") or "0")
        self.region_label = _env("TREND_REGION_LABEL", "全球")
        # 状态文件, 用于去重 (避免重复推送同一榜单)
        self.state_file = _env(
            "STATE_FILE",
            str(Path(__file__).resolve().parent / ".trend_state.json"),
        )
        self.http_timeout = int(_env("HTTP_TIMEOUT", "20") or "20")

    def validate(self) -> None:
        missing = []
        if not self.rapidapi_key:
            missing.append("RAPIDAPI_KEY")
        if not self.tg_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.tg_chat_id:
            missing.append("TELEGRAM_CHAT_ID")
        if missing:
            raise SystemExit(
                "缺少必要的环境变量: " + ", ".join(missing) +
                "\n请参考 config.example.env 进行配置。"
            )


log = logging.getLogger("x_trending_bot")


# --------------------------------------------------------------------------- #
# 拉取趋势
# --------------------------------------------------------------------------- #
def fetch_trends(cfg: Config) -> list[dict]:
    """
    调用 RapidAPI 拉取趋势榜, 返回规整后的 trend 列表:
        [{"name": str, "url": str|None, "volume": int|None}, ...]

    对常见的几种返回结构做了兼容解析。
    """
    url = f"https://{cfg.rapidapi_host}{cfg.trends_path}"
    headers = {
        "x-rapidapi-key": cfg.rapidapi_key,
        "x-rapidapi-host": cfg.rapidapi_host,
    }
    params = {cfg.woeid_param: cfg.woeid}

    log.info("请求趋势接口: %s (%s=%s)", url, cfg.woeid_param, cfg.woeid)
    resp = requests.get(url, headers=headers, params=params, timeout=cfg.http_timeout)
    resp.raise_for_status()
    data = resp.json()

    raw_trends = _extract_trend_array(data)
    if not raw_trends:
        log.warning("接口返回中未解析到趋势数据, 原始响应: %s",
                    json.dumps(data, ensure_ascii=False)[:500])
        return []

    trends: list[dict] = []
    for item in raw_trends:
        if isinstance(item, str):
            trends.append({"name": item, "url": None, "volume": None})
            continue
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("trend") or item.get("title")
        if not name:
            continue
        volume = (
            item.get("tweet_volume")
            or item.get("volume")
            or item.get("tweetVolume")
        )
        try:
            volume = int(volume) if volume is not None else None
        except (TypeError, ValueError):
            volume = None
        trends.append({
            "name": str(name).strip(),
            "url": item.get("url") or _guess_url(name),
            "volume": volume,
        })
    return trends


def _extract_trend_array(data) -> list:
    """从多种可能的响应结构中提取趋势数组。"""
    # 形如 [{"trends": [...], "locations": [...]}]  (标准 Twitter trends/place 格式)
    if isinstance(data, list) and data and isinstance(data[0], dict) and "trends" in data[0]:
        return data[0]["trends"] or []
    # 形如 {"trends": [...]}
    if isinstance(data, dict) and isinstance(data.get("trends"), list):
        return data["trends"]
    # 形如 {"data": [...]} 或 {"results": [...]}
    for key in ("data", "results", "trending", "items"):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    # 直接就是一个数组
    if isinstance(data, list):
        return data
    return []


def _guess_url(name: str) -> str:
    """没有 url 时, 根据话题名拼一个 X 搜索链接。"""
    from urllib.parse import quote
    return f"https://x.com/search?q={quote(name)}"


# --------------------------------------------------------------------------- #
# 去重状态
# --------------------------------------------------------------------------- #
def load_last_hash(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("last_hash", "")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ""


def save_last_hash(path: str, value: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"last_hash": value, "ts": int(time.time())}, f)
    except OSError as e:
        log.warning("写入状态文件失败 (%s): %s", path, e)


def trends_hash(trends: list[dict]) -> str:
    key = "|".join(t["name"] for t in trends)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 格式化 & Telegram 推送
# --------------------------------------------------------------------------- #
def _fmt_volume(v) -> str:
    if not v:
        return ""
    if v >= 10000:
        return f" · {v / 10000:.1f}万讨论"
    return f" · {v}讨论"


def build_message(cfg: Config, trends: list[dict]) -> str:
    now = time.strftime("%Y-%m-%d %H:%M", time.localtime())
    lines = [f"🔥 <b>X 实时热门趋势 · {html.escape(cfg.region_label)}</b>",
             f"<i>{now}</i>", ""]
    for i, t in enumerate(trends, 1):
        name = html.escape(t["name"])
        vol = _fmt_volume(t["volume"])
        url = t["url"]
        if url:
            lines.append(f"{i}. <a href=\"{html.escape(url)}\">{name}</a>{vol}")
        else:
            lines.append(f"{i}. {name}{vol}")
    return "\n".join(lines)


def send_telegram(cfg: Config, text: str) -> None:
    api = f"https://api.telegram.org/bot{cfg.tg_token}/sendMessage"
    payload = {
        "chat_id": cfg.tg_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    resp = requests.post(api, json=payload, timeout=cfg.http_timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"Telegram 推送失败 [{resp.status_code}]: {resp.text}")
    log.info("已推送到 Telegram chat_id=%s", cfg.tg_chat_id)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run_once(cfg: Config, force: bool = False) -> bool:
    """执行一次抓取+推送。返回是否实际推送。"""
    trends = fetch_trends(cfg)
    if cfg.min_volume > 0:
        trends = [t for t in trends if (t["volume"] or 0) >= cfg.min_volume]
    trends = trends[: cfg.top_n]

    if not trends:
        log.warning("没有可推送的趋势数据, 跳过。")
        return False

    new_hash = trends_hash(trends)
    if not force and new_hash == load_last_hash(cfg.state_file):
        log.info("趋势榜与上次相同, 跳过推送。")
        return False

    message = build_message(cfg, trends)
    send_telegram(cfg, message)
    save_last_hash(cfg.state_file, new_hash)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="X 热门趋势 -> Telegram 推送")
    parser.add_argument("--once", action="store_true",
                        help="只执行一次 (适合配合 cron 使用)")
    parser.add_argument("--interval", type=int, default=0, metavar="SECONDS",
                        help="常驻轮询模式, 每 N 秒拉取一次 (如 3600)")
    parser.add_argument("--force", action="store_true",
                        help="忽略去重, 强制推送")
    parser.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    cfg = Config()
    cfg.validate()

    if args.interval and not args.once:
        log.info("进入常驻轮询模式, 间隔 %s 秒。", args.interval)
        while True:
            try:
                run_once(cfg, force=args.force)
            except Exception as e:  # noqa: BLE001 - 守护进程不应因单次异常退出
                log.error("本轮执行出错: %s", e)
            time.sleep(args.interval)
    else:
        try:
            run_once(cfg, force=args.force)
        except Exception as e:  # noqa: BLE001
            log.error("执行失败: %s", e)
            sys.exit(1)


if __name__ == "__main__":
    main()
