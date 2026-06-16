#!/usr/bin/env python3
"""
gh_runner.py —— 在 GitHub Actions 上运行的统一入口（轮询模式）。

每次被 Actions 唤醒（每 N 分钟一次）时，做两件事：
  1) 检查 Telegram 有没有新的 /check 指令，有就立即拉新推 -> 总结 -> 回复。
  2) 判断当前是否落在某个定时推送窗口（7:30 / 12:00 / 14:00 / 23:00 附近），是则推送当时段新推。

配置全部来自环境变量（GitHub Secrets），不读 config.json。
状态文件 state.json / tg_offset.json 由 workflow 提交回仓库来持久化。
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from x_list_to_tg import (
    load_state, save_state, http_get, parse_rss,
    summarize_with_gemini, enforce_blank_lines, linkify_sources,
    send_telegram, log,
)
from email.utils import parsedate_to_datetime
import urllib.request
import urllib.error

STATE_PATH = os.path.join(HERE, "state.json")
OFFSET_PATH = os.path.join(HERE, "tg_offset.json")

# 北京时间
TZ = timezone(timedelta(hours=8))

# 定时推送窗口（北京时间，时:分）。轮询间隔内只要落在窗口里就触发一次。
PUSH_TIMES = [(7, 30), (12, 0), (14, 0), (23, 0)]


def cfg_from_env():
    return {
        "rss_url": os.environ["RSS_URL"],
        "gemini_api_key": os.environ["GEMINI_API_KEY"],
        "gemini_base_url": os.environ.get("GEMINI_BASE_URL",
                                          "https://generativelanguage.googleapis.com/v1beta/openai/"),
        "gemini_model": os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
        "fallback_models": ["gemini-3-flash-preview", "gemini-2.5-flash"],
        "telegram_bot_token": os.environ["TELEGRAM_BOT_TOKEN"],
        "telegram_chat_id": os.environ["TELEGRAM_CHAT_ID"],
        "max_tweets_per_run": int(os.environ.get("MAX_TWEETS_PER_RUN", "80")),
        "http_proxy": "",  # GitHub 海外，直连，不用代理
    }


def load_offset():
    if os.path.exists(OFFSET_PATH):
        try:
            with open(OFFSET_PATH) as f:
                return json.load(f).get("offset", 0)
        except Exception:
            return 0
    return 0


def save_offset(offset):
    with open(OFFSET_PATH, "w") as f:
        json.dump({"offset": offset}, f)


def tg_api(cfg, method, params, timeout=30):
    url = f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/{method}"
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def send_text(cfg, chat_id, text):
    tg_api(cfg, "sendMessage", {"chat_id": chat_id, "text": text,
                                "disable_web_page_preview": True})


def build_briefing(cfg, new_items, manual=False):
    summary = enforce_blank_lines(linkify_sources(summarize_with_gemini(cfg, new_items), new_items))
    pub_times = []
    for it in new_items:
        try:
            dt = parsedate_to_datetime(it.get("published", ""))
            if dt:
                pub_times.append(dt)
        except Exception:
            pass
    now_dt = datetime.now(TZ)
    if pub_times:
        earliest = min(pub_times).astimezone(TZ)
        window = f"{earliest.strftime('%H:%M')} → {now_dt.strftime('%H:%M')}"
    else:
        window = now_dt.strftime("%H:%M")
    tag = "（手动查询）" if manual else ""
    header = (f"⟪📰 X 列表要闻{tag} · {now_dt.strftime('%m-%d')} {window}⟫\n"
              f"（本次 {len(new_items)} 条新推）\n\n")
    return header + summary


def fetch_new_items(cfg, seen):
    xml_text = http_get(cfg["rss_url"], proxy=None)
    items = parse_rss(xml_text)
    new_items = [it for it in items if it["id"] and it["id"] not in seen]
    max_n = cfg["max_tweets_per_run"]
    return new_items[:max_n]


def handle_check_commands(cfg, state, seen):
    """处理自上次以来的 /check 指令。返回是否消耗了新推（消耗了就更新 seen）。"""
    offset = load_offset()
    try:
        resp = tg_api(cfg, "getUpdates", {"offset": offset + 1, "timeout": 0,
                                          "allowed_updates": ["message"]})
    except Exception as e:
        log(f"getUpdates 失败：{e}")
        return

    wants_check = False
    for upd in resp.get("result", []):
        offset = max(offset, upd["update_id"])
        msg = upd.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        text = (msg.get("text") or "").strip()
        if str(chat_id) != str(cfg["telegram_chat_id"]):
            continue
        cmd = text.split()[0].lower().split("@")[0] if text else ""
        if cmd == "/check":
            wants_check = True
        elif cmd in ("/start", "/help"):
            send_text(cfg, chat_id,
                      "我会在每天 7:30 / 12:00 / 14:00 / 23:00 自动推送 X 列表简报。\n\n"
                      "发送 /check 可随时查看最新动态（最多等几分钟响应）。")
    # 关键：先存 offset（认领这批消息），再去慢慢总结。
    # 这样即使下一次轮询提前启动，它的 getUpdates 也读不到这批 /check，避免重复响应。
    save_offset(offset)

    if wants_check:
        send_text(cfg, cfg["telegram_chat_id"], "🔍 正在查看最新动态，请稍候…")
        new_items = fetch_new_items(cfg, seen)
        if not new_items:
            send_text(cfg, cfg["telegram_chat_id"], "暂时没有新推文 ✅")
            return
        # 先把这批标记为已读并落盘，再总结发送，进一步降低并发重复
        for it in new_items:
            seen.add(it["id"])
        state["seen_ids"] = list(seen)
        save_state(STATE_PATH, state)
        message = build_briefing(cfg, new_items, manual=True)
        send_telegram(cfg, message)


def maybe_scheduled_push(cfg, state, seen, poll_minutes):
    """如果当前时间落在某个推送窗口内（窗口宽度=轮询间隔），就推送。"""
    now = datetime.now(TZ)
    in_window = False
    for h, m in PUSH_TIMES:
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        diff = (now - target).total_seconds() / 60.0
        # 当前时间在窗口 [target, target+poll_minutes) 内
        if 0 <= diff < poll_minutes:
            in_window = True
            break
    if not in_window:
        return

    # 防重复：记录今天已推过的窗口
    today_key = now.strftime("%Y-%m-%d")
    win_key = f"{today_key} {h:02d}:{m:02d}"
    pushed = set(state.get("pushed_windows", []))
    if win_key in pushed:
        return

    new_items = fetch_new_items(cfg, seen)
    if not new_items:
        log(f"[{win_key}] 没有新推文，跳过。")
    else:
        message = build_briefing(cfg, new_items, manual=False)
        send_telegram(cfg, message)
        for it in new_items:
            seen.add(it["id"])
        state["seen_ids"] = list(seen)

    pushed.add(win_key)
    # 只保留最近 30 条窗口记录
    state["pushed_windows"] = list(pushed)[-30:]
    save_state(STATE_PATH, state)


def main():
    cfg = cfg_from_env()
    poll_minutes = int(os.environ.get("POLL_MINUTES", "5"))
    state = load_state(STATE_PATH)
    seen = set(state["seen_ids"])

    # 1) 先处理手动 /check
    handle_check_commands(cfg, state, seen)
    # 2) 再判断定时推送
    maybe_scheduled_push(cfg, state, seen, poll_minutes)


if __name__ == "__main__":
    main()
