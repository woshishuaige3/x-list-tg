#!/usr/bin/env python3
"""双 Cookie 抓 X 博主推文：两个小号各抓一半，避开单 Cookie 限流上限；合并去重、只留最近 N 天、按时间排序。"""
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
PER_USER_LIMIT = 10


def _parse_ts(ts):
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%a %b %d %H:%M:%S +0000 %Y"):
        try:
            return datetime.strptime(ts, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _fetch_with_token(auth_token, accounts, cutoff, per_user_limit, proxy, tag):
    """用一个 Cookie 抓指定的一批博主。"""
    kwargs = {"auth_token": auth_token, "manifest_scrape_on_init": True}
    if proxy:
        kwargs["proxy"] = proxy
    s = Scweet(**kwargs)

    items = []
    for acct in accounts:
        try:
            raw = s.get_profile_tweets([acct], limit=per_user_limit)
        except Exception as e:
            print(f"[warn][{tag}] 抓 {acct} 失败：{e}", flush=True)
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
                "id": tid, "title": text[:50], "link": link,
                "author": screen, "content": text,
                "published": ts, "_sort_dt": dt,
            })
            got += 1
        print(f"[info][{tag}] {acct}: 最近{RECENT_DAYS}天 {got} 条", flush=True)
    return items


def fetch_x_items(auth_token, auth_token_2=None, proxy=None, per_user_limit=None):
    if per_user_limit is None:
        per_user_limit = PER_USER_LIMIT
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)

    if auth_token_2:
        group_a = ACCOUNTS[0::2]
        group_b = ACCOUNTS[1::2]
        print(f"[info] 双 Cookie：A 抓 {len(group_a)} 个，B 抓 {len(group_b)} 个", flush=True)
        items = _fetch_with_token(auth_token, group_a, cutoff, per_user_limit, proxy, "A")
        items += _fetch_with_token(auth_token_2, group_b, cutoff, per_user_limit, proxy, "B")
    else:
        print("[info] 单 Cookie：全部博主一次抓", flush=True)
        items = _fetch_with_token(auth_token, ACCOUNTS, cutoff, per_user_limit, proxy, "A")

    seen = set()
    deduped = []
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        deduped.append(it)

    deduped.sort(key=lambda x: x["_sort_dt"], reverse=True)
    for it in deduped:
        it.pop("_sort_dt", None)
    print(f"[info] 合计 {len(deduped)} 条（去重后）", flush=True)
    return deduped
