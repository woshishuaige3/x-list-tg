#!/usr/bin/env python3
"""用 Scweet 抓取 X 博主推文：只保留最近 N 天、按时间从新到旧排序。"""
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

# 只保留最近几天的推文（你可以改这个数字：2 或 3）
RECENT_DAYS = 3


def _parse_ts(ts):
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%a %b %d %H:%M:%S +0000 %Y"):
        try:
            return datetime.strptime(ts, fmt)
        except (ValueError, TypeError):
            continue
    return None


def fetch_x_items(auth_token, proxy=None, per_user_limit=10):
    kwargs = {"auth_token": auth_token, "manifest_scrape_on_init": True}
    if proxy:
        kwargs["proxy"] = proxy
    s = Scweet(**kwargs)
    raw = s.get_profile_tweets(ACCOUNTS, limit=per_user_limit * len(ACCOUNTS))

    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)

    items = []
    for t in raw:
        tid = str(t.get("tweet_id", "")).strip()
        if not tid:
            continue
        ts = t.get("timestamp", "")
        dt = _parse_ts(ts)
        # 解析不出时间、或早于 N 天前的，直接跳过
        if dt is None or dt < cutoff:
            continue
        user = t.get("user") or {}
        screen = user.get("screen_name", "")
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

    items.sort(key=lambda it: it["_sort_dt"], reverse=True)
    for it in items:
        it.pop("_sort_dt", None)
    return items
