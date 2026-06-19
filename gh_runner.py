#!/usr/bin/env python3
"""
gh_runner.py —— 在 GitHub Actions 上运行的统一入口（轮询模式）。
防重复：定点窗口收窄到 1 分钟 + 先记账后干活 + 超时补漏；不再靠杀任务。
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

TZ = timezone(timedelta(hours=8))

PUSH_TIMES = [(7, 30), (12, 0), (14, 0), (23, 0)]

PUSH_WINDOW_MIN = 1
CATCHUP_MINUTES = 10


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
        "http_proxy": "",
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
    now_dt = datetime.now(TZ)
    tag = "（手动查询）" if manual else ""
    header = (f"⟪📰 X 列表要闻{tag} · {now_dt.strftime('%m-%d')} 截至 {now_dt.strftime('%H:%M')}⟫\n"
              f"（本次 {len(new_items)} 条新推）\n\n")
    return header + summary


def fetch_new_items(cfg, seen):
    xml_text = http_get(cfg["rss_url"], proxy=None)
    items = parse_rss(xml_text)
    new_items = [it for it in items if it["id"] and it["id"] not in seen]
    max_n = cfg["max_tweets_per_run"]
    return new_items[:max_n]


def handle_check_commands(cfg, state, seen):
    offset = load_offset()
    try:
        resp = tg_api(cfg, "getUpdates", {"offset": offset + 1, "timeout": 0,
                                          "allowed_updates": ["message"]})
    except Exception as e:
        log(f"getUpdates 失败：{e}")
        return

    updates = resp.get("result", [])
    if not updates:
        return

    wants_check = False
    for upd in updates:
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

    try:
        tg_api(cfg, "getUpdates", {"offset": offset + 1, "timeout": 0,
                                   "allowed_updates": ["message"]})
    except Exception as e:
        log(f"确认 offset 失败（不致命）：{e}")
    save_offset(offset)

    if wants_check:
        send_text(cfg, cfg["telegram_chat_id"], "🔍 正在查看最新动态，请稍候…")
        new_items = fetch_new_items(cfg, seen)
        if not new_items:
            send_text(cfg, cfg["telegram_chat_id"], "暂时没有新推文 ✅")
            return
        for it in new_items:
            seen.add(it["id"])
        state["seen_ids"] = list(seen)
        save_state(STATE_PATH, state)
        message = build_briefing(cfg, new_items, manual=True)
        send_telegram(cfg, message)


def maybe_scheduled_push(cfg, state, seen, poll_minutes):
    now = datetime.now(TZ)
    today_key = now.strftime("%Y-%m-%d")

    claimed = set(state.get("pushed_windows", []))
    done = set(state.get("done_windows", []))

    win_key = None
    is_catchup = False
    for h, m in PUSH_TIMES:
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        diff = (now - target).total_seconds() / 60.0
        key = f"{today_key} {h:02d}:{m:02d}"
        if 0 <= diff < PUSH_WINDOW_MIN:
            win_key = key
            is_catchup = False
            break
        if PUSH_WINDOW_MIN <= diff < CATCHUP_MINUTES and key in claimed and key not in done:
            win_key = key
            is_catchup = True
            break
    if win_key is None:
        return

    if not is_catchup:
        if win_key in claimed:
            return
        claimed.add(win_key)
        state["pushed_windows"] = list(claimed)[-30:]
        save_state(STATE_PATH, state)

    new_items = fetch_new_items(cfg, seen)
    if not new_items:
        now_str = now.strftime("%m-%d %H:%M")
        send_text(cfg, cfg["telegram_chat_id"], f"📭 {now_str} 定点播报：暂无新内容")
        log(f"[{win_key}] 没有新推文，已发空提示。")
    else:
        message = build_briefing(cfg, new_items, manual=False)
        send_telegram(cfg, message)
        for it in new_items:
            seen.add(it["id"])
        state["seen_ids"] = list(seen)

    done.add(win_key)
    state["done_windows"] = list(done)[-30:]
    save_state(STATE_PATH, state)


def main():
    cfg = cfg_from_env()
    poll_minutes = int(os.environ.get("POLL_MINUTES", "5"))
    state = load_state(STATE_PATH)
    seen = set(state["seen_ids"])

    handle_check_commands(cfg, state, seen)
    maybe_scheduled_push(cfg, state, seen, poll_minutes)


if __name__ == "__main__":
    main()
