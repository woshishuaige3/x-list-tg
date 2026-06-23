#!/usr/bin/env python3
"""逐个抓 X 博主推文：每抓 BATCH 个停顿一次防限流；只留最近 N 天、按时间排序。"""
import time
from datetime import datetime, timezone, timedelta
from Scweet import Scweet

ACCOUNTS = [
    "yiran2037840", "aleabitoreddit", "pcbanalysis", "jukan05",
    "shufen46250836", "alpha101xyz", "xiaomustock", "fi56622380",
    "ArtofSpecuycky", "qinbafrank", "iamai_omni", "Franktradinglog",
    "STANLEES4", "LinQingV", "fxtrader", "nft_hu",
    "MacroMargin", "ShanghaoJin", "BigbirdflyChan", "mingchikuo",
    "trendforce", "labubu_trader",
]

RECENT_DAYS = 1            # 只看最近 24 小时
PER_USER_LIMIT = 10        # 每个博主最多抓几条
BATCH = 8                  # 每抓几个博主停顿一次
BATCH_SLEEP = 5            # 每批之间停顿秒数（防限流）


def _parse_ts(ts):
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%a %b %d %H:%M:%S +0000 %Y"):
        try:
            return datetime.strptime(ts, fmt)
        except (ValueError, TypeError):
            continue
    return None


def fetch_x_items(auth_token, proxy=None, per_user_limit=None):
    if per_user_limit is None:
        per_user_limit = PER_USER_LIMIT
    kwargs = {"auth_token": auth_token, "manifest_scrape_on_init": True}
    if proxy:
        kwargs["proxy"] = proxy
    s = Scweet(**kwargs)

    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)
    items = []

    for i, acct in enumerate(ACCOUNTS):
        # 每抓 BATCH 个博主，停顿一下，避免被 X 限流
        if i > 0 and i % BATCH == 0:
            print(f"[info] 已抓 {i} 个，停顿 {BATCH_SLEEP}s 防限流…", flush=True)
            time.sleep(BATCH_SLEEP)
        try:
            raw = s.get_profile_tweets([acct], limit=per_user_limit)
        except Exception as e:
            print(f"[warn] 抓 {acct} 失败：{e}", flush=True)
            raw = []
        got = 0
        for t in raw:
            tid = str(t.get("tweet_id", "")).strip()
            if not tid:
                continue
            ts = t.get("timestamp", "")
            dt = _parse_ts(ts)
            if dt is None or dt < cutoff:
                continue
            user = t.get("user") or {}
            screen = user.get("screen_name", "") or acct
            text = (t.get("text") or "").strip()
            link = f"https://x.com/{screen}/status/{tid}" if screen else ""
            items.append({
                "id": tid,
                "title": text[:50],
                "link": link,
                "author": screen,
                "content": text,
                "published": ts,
                "_sort_dt": dt,
            })
            got += 1
        print(f"[info] {acct}: 最近{RECENT_DAYS}天 {got} 条", flush=True)

    items.sort(key=lambda it: it["_sort_dt"], reverse=True)
    for it in items:
        it.pop("_sort_dt", None)
    print(f"[info] 合计 {len(items)} 条", flush=True)
    return items
