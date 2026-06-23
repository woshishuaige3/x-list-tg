#!/usr/bin/env python3
"""用 Scweet 抓取 X 博主推文，按发布时间从新到旧排序，输出格式与原 parse_rss 一致。"""
from datetime import datetime, timezone
from Scweet import Scweet

ACCOUNTS = [
    "yiran2037840", "aleabitoreddit", "pcbanalysis", "jukan05",
    "shufen46250836", "alpha101xyz", "xiaomustock", "fi56622380",
    "ArtofSpecuycky", "qinbafrank", "iamai_omni", "Franktradinglog",
    "STANLEES4", "LinQingV", "fxtrader", "nft_hu",
    "MacroMargin", "ShanghaoJin", "BigbirdflyChan", "mingchikuo",
    "trendforce", "labubu_trader",
]


def _parse_ts(ts):
    """把 Twitter 时间戳解析成可比较的 datetime；失败则返回最旧时间。"""
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%a %b %d %H:%M:%S +0000 %Y"):
        try:
            return datetime.strptime(ts, fmt)
        except (ValueError, TypeError):
            continue
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def fetch_x_items(auth_token, proxy=None, per_user_limit=10):
    kwargs = {"auth_token": auth_token, "manifest_scrape_on_init": True}
    if proxy:
        kwargs["proxy"] = proxy
    s = Scweet(**kwargs)
    raw = s.get_profile_tweets(ACCOUNTS, limit=per_user_limit * len(ACCOUNTS))

    items = []
    for t in raw:
        tid = str(t.get("tweet_id", "")).strip()
        if not tid:
            continue
        user = t.get("user") or {}
        screen = user.get("screen_name", "")
        text = (t.get("text") or "").strip()
        link = f"https://x.com/{screen}/status/{tid}" if screen else ""
        ts = t.get("timestamp", "")
        items.append({
            "id": tid,
            "title": text[:50],
            "link": link,
            "author": screen,
            "content": text,
            "published": ts,
            "_sort_dt": _parse_ts(ts),
        })

    # 按发布时间从新到旧排序
    items.sort(key=lambda it: it["_sort_dt"], reverse=True)
    for it in items:
        it.pop("_sort_dt", None)
    return items
