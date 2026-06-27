#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X(Twitter) 实时热门趋势榜 -> Telegram 推送

数据源: RapidAPI 上的第三方 Twitter 聚合接口 (默认: twitter154)
功能:
  - 拉取一个或多个地区 (WOEID) 的实时热门趋势榜
  - 地区轮播: 多地区时每次轮流推一个 (或一次性全推)
  - 定时静音: 指定时段内不推送 (如夜间)
  - 多目标推送: chat_id 支持多个, 可同时推到私聊 / 群 / 频道(@频道名)
  - 去重: 同一地区榜单内容不重复推送
  - 单次执行 (--once, 适合 cron) 或常驻轮询 (--interval)

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


def _split(raw: str) -> list[str]:
    """按逗号拆分并去空白, 过滤空项。"""
    return [x.strip() for x in raw.split(",") if x.strip()]


def _parse_quiet_hours(raw: str):
    """
    解析 "23:00-07:00" 为 (start_min, end_min)。
    返回 None 表示未启用。支持跨午夜 (start > end)。
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        start_s, end_s = raw.split("-", 1)
        sh, sm = (int(x) for x in start_s.strip().split(":"))
        eh, em = (int(x) for x in end_s.strip().split(":"))
        return (sh * 60 + sm, eh * 60 + em)
    except (ValueError, IndexError):
        log.warning("QUIET_HOURS 格式无法解析 (%s), 已忽略。应形如 23:00-07:00", raw)
        return None


def _webapp_settings_path() -> str:
    """Mini App 写入的设置覆盖文件路径。"""
    return _env(
        "WEBAPP_SETTINGS_FILE",
        str(Path(__file__).resolve().parent / ".webapp_settings.json"),
    )


def load_webapp_settings() -> dict:
    """读取 Mini App 写入的设置覆盖 (不存在/损坏则返回空 dict)。"""
    try:
        with open(_webapp_settings_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_webapp_settings(data: dict) -> None:
    """持久化 Mini App 设置覆盖。"""
    with open(_webapp_settings_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class Config:
    """配置: 以环境变量为基线, 若存在 Mini App 设置文件则覆盖对应项。"""

    def __init__(self) -> None:
        # --- RapidAPI ---
        self.rapidapi_key = _env("RAPIDAPI_KEY")
        self.rapidapi_host = _env("RAPIDAPI_HOST", "twitter154.p.rapidapi.com")
        self.trends_path = _env("RAPIDAPI_TRENDS_PATH", "/trends/")
        self.woeid_param = _env("RAPIDAPI_WOEID_PARAM", "woeid")

        # --- Telegram (chat_id 支持多个: 私聊/群/@频道名) ---
        self.tg_token = _env("TELEGRAM_BOT_TOKEN")
        self.tg_chat_ids = _split(_env("TELEGRAM_CHAT_ID"))

        # --- 地区 / 推送行为 (env 为基线) ---
        woeids = _split(_env("TREND_WOEID", "1")) or ["1"]
        labels = _split(_env("TREND_REGION_LABEL", ""))
        rotate_raw = _env("TREND_ROTATE", "on")
        top_n = int(_env("TREND_TOP_N", "15") or "15")
        min_volume = int(_env("TREND_MIN_VOLUME", "0") or "0")
        quiet_raw = _env("QUIET_HOURS")

        # --- Mini App 设置文件覆盖 (存在则生效, 不影响其它项) ---
        ov = load_webapp_settings()
        if ov:
            ov_woeids = [str(w).strip() for w in (ov.get("woeids") or []) if str(w).strip()]
            if ov_woeids:
                woeids = ov_woeids
                labels = [str(x).strip() for x in (ov.get("labels") or [])]
            if "rotate" in ov:
                rotate_raw = "on" if ov.get("rotate") else "off"
            if ov.get("top_n") is not None:
                try:
                    top_n = int(ov["top_n"])
                except (TypeError, ValueError):
                    pass
            if ov.get("min_volume") is not None:
                try:
                    min_volume = int(ov["min_volume"])
                except (TypeError, ValueError):
                    pass
            if "quiet_hours" in ov:
                quiet_raw = str(ov.get("quiet_hours") or "")

        # 地区列表: [(woeid, label), ...], label 缺省时用 "WOEID xxx"
        self.regions = [
            (w, labels[i] if i < len(labels) else f"WOEID {w}")
            for i, w in enumerate(woeids)
        ]
        # 轮播模式: on=每次轮流推一个地区; off=每次把所有地区都推一遍
        self.rotate = rotate_raw.lower() not in ("off", "0", "false")
        self.top_n = top_n
        self.min_volume = min_volume
        self.quiet_hours_raw = quiet_raw
        self.quiet_hours = _parse_quiet_hours(quiet_raw)

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
        if not self.tg_chat_ids:
            missing.append("TELEGRAM_CHAT_ID")
        if missing:
            raise SystemExit(
                "缺少必要的环境变量: " + ", ".join(missing) +
                "\n请参考 config.example.env 进行配置。"
            )


log = logging.getLogger("x_trending_bot")


# --------------------------------------------------------------------------- #
# 定时静音
# --------------------------------------------------------------------------- #
def in_quiet_hours(cfg: Config, now=None) -> bool:
    """当前本地时间是否落在静音时段内。"""
    if not cfg.quiet_hours:
        return False
    lt = now or time.localtime()
    cur = lt.tm_hour * 60 + lt.tm_min
    start, end = cfg.quiet_hours
    if start == end:
        return False
    if start < end:                 # 同日, 如 09:00-18:00
        return start <= cur < end
    return cur >= start or cur < end  # 跨午夜, 如 23:00-07:00


# --------------------------------------------------------------------------- #
# 拉取趋势
# --------------------------------------------------------------------------- #
def fetch_trends(cfg: Config, woeid: str) -> list[dict]:
    """
    调用 RapidAPI 拉取指定地区趋势榜, 返回规整后的列表:
        [{"name": str, "url": str|None, "volume": int|None}, ...]
    对常见的几种返回结构做了兼容解析。
    """
    url = f"https://{cfg.rapidapi_host}{cfg.trends_path}"
    headers = {
        "x-rapidapi-key": cfg.rapidapi_key,
        "x-rapidapi-host": cfg.rapidapi_host,
    }
    params = {cfg.woeid_param: woeid}

    log.info("请求趋势接口: %s (%s=%s)", url, cfg.woeid_param, woeid)
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
            trends.append({"name": item, "url": _guess_url(item), "volume": None})
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
# 状态 (去重 hash + 轮播指针)
# --------------------------------------------------------------------------- #
def load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("hashes", {})
                data.setdefault("rotate_index", 0)
                return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"hashes": {}, "rotate_index": 0}


def save_state(path: str, state: dict) -> None:
    state["ts"] = int(time.time())
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
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


def build_message(label: str, trends: list[dict]) -> str:
    now = time.strftime("%Y-%m-%d %H:%M", time.localtime())
    lines = [f"🔥 <b>X 实时热门趋势 · {html.escape(label)}</b>",
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


def send_telegram(cfg: Config, text: str) -> int:
    """推送到所有配置的 chat_id, 返回成功数。单个失败不影响其它。"""
    api = f"https://api.telegram.org/bot{cfg.tg_token}/sendMessage"
    ok = 0
    for chat_id in cfg.tg_chat_ids:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(api, json=payload, timeout=cfg.http_timeout)
            if resp.status_code == 200:
                ok += 1
                log.info("已推送到 Telegram chat_id=%s", chat_id)
            else:
                log.error("Telegram 推送失败 chat_id=%s [%s]: %s",
                          chat_id, resp.status_code, resp.text[:300])
        except requests.RequestException as e:
            log.error("Telegram 请求异常 chat_id=%s: %s", chat_id, e)
    return ok


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def push_region(cfg: Config, woeid: str, label: str, state: dict,
                force: bool) -> bool:
    """抓取并推送单个地区。返回是否实际推送。"""
    trends = fetch_trends(cfg, woeid)
    if cfg.min_volume > 0:
        trends = [t for t in trends if (t["volume"] or 0) >= cfg.min_volume]
    trends = trends[: cfg.top_n]

    if not trends:
        log.warning("[%s] 没有可推送的趋势数据, 跳过。", label)
        return False

    new_hash = trends_hash(trends)
    if not force and state["hashes"].get(woeid) == new_hash:
        log.info("[%s] 趋势榜与上次相同, 跳过推送。", label)
        return False

    message = build_message(label, trends)
    if send_telegram(cfg, message) > 0:
        state["hashes"][woeid] = new_hash
        return True
    return False


def run_once(cfg: Config, force: bool = False) -> bool:
    """
    执行一次。地区轮播逻辑:
      - rotate=on : 只推当前指针指向的地区, 然后指针后移
      - rotate=off: 把所有地区都推一遍
    静音时段内 (且非 force) 直接跳过。
    """
    if not force and in_quiet_hours(cfg):
        log.info("当前处于静音时段 %s, 跳过推送。", cfg.quiet_hours_raw)
        return False

    state = load_state(cfg.state_file)
    pushed = False

    if cfg.rotate and len(cfg.regions) > 1:
        idx = state.get("rotate_index", 0) % len(cfg.regions)
        woeid, label = cfg.regions[idx]
        log.info("地区轮播: 第 %d/%d 个 -> %s", idx + 1, len(cfg.regions), label)
        pushed = push_region(cfg, woeid, label, state, force)
        state["rotate_index"] = (idx + 1) % len(cfg.regions)
    else:
        for woeid, label in cfg.regions:
            try:
                if push_region(cfg, woeid, label, state, force):
                    pushed = True
            except Exception as e:  # noqa: BLE001 - 单个地区失败不影响其它
                log.error("[%s] 抓取/推送出错: %s", label, e)

    save_state(cfg.state_file, state)
    return pushed


def main() -> None:
    parser = argparse.ArgumentParser(description="X 热门趋势 -> Telegram 推送")
    parser.add_argument("--once", action="store_true",
                        help="只执行一次 (适合配合 cron 使用)")
    parser.add_argument("--interval", type=int, default=0, metavar="SECONDS",
                        help="常驻轮询模式, 每 N 秒拉取一次 (如 3600)")
    parser.add_argument("--force", action="store_true",
                        help="忽略去重和静音时段, 强制推送")
    parser.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    cfg = Config()
    cfg.validate()
    log.info("地区: %s | 轮播: %s | 目标数: %d | 静音: %s",
             ", ".join(l for _, l in cfg.regions),
             "开" if cfg.rotate else "关",
             len(cfg.tg_chat_ids),
             cfg.quiet_hours_raw or "无")

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
