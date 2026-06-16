#!/usr/bin/env python3
"""
x_list_to_tg.py
读取 RSS.app 为 X 列表生成的 RSS -> 筛出新推文 -> 用 Gemini 总结 -> 推送到 Telegram。
只依赖 Python 标准库，macOS 自带 python3 即可运行。

用法:
    python3 x_list_to_tg.py --config config.json

去重逻辑:
    每次成功推送后，把"已见过的推文 ID"写入 state.json。
    下次运行只处理 state.json 里没有的新推文，因此即使两次定时之间有很多推文也不会漏，
    且不会重复总结同一批内容。
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state(path):
    if not os.path.exists(path):
        return {"seen_ids": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "seen_ids" not in data:
                data["seen_ids"] = []
            return data
    except Exception:
        return {"seen_ids": []}


def save_state(path, state):
    # 只保留最近 2000 个 ID，防止文件无限膨胀
    state["seen_ids"] = state["seen_ids"][-2000:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def http_get(url, proxy=None, timeout=30):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (x-list-to-tg)"})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def http_post_json(url, payload, proxy=None, timeout=60, headers=None):
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return text.strip()


def parse_rss(xml_text):
    """解析 RSS 2.0 / Atom，返回 [{id, title, link, author, content, published}]"""
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log(f"RSS 解析失败: {e}")
        return items

    # RSS 2.0
    for it in root.iter("item"):
        def t(tag):
            el = it.find(tag)
            return el.text if el is not None and el.text else ""
        guid = t("guid") or t("link")
        items.append({
            "id": guid.strip(),
            "title": strip_html(t("title")),
            "link": t("link").strip(),
            "author": strip_html(t("{http://purl.org/dc/elements/1.1/}creator") or t("author")),
            "content": strip_html(t("description")),
            "published": t("pubDate").strip(),
        })

    # Atom (RSS.app 有时输出 atom)
    if not items:
        ns = "{http://www.w3.org/2005/Atom}"
        for it in root.iter(f"{ns}entry"):
            def a(tag):
                el = it.find(f"{ns}{tag}")
                return el.text if el is not None and el.text else ""
            link_el = it.find(f"{ns}link")
            link = link_el.get("href") if link_el is not None else ""
            author_el = it.find(f"{ns}author/{ns}name")
            author = author_el.text if author_el is not None and author_el.text else ""
            items.append({
                "id": (a("id") or link).strip(),
                "title": strip_html(a("title")),
                "link": link.strip(),
                "author": strip_html(author),
                "content": strip_html(a("summary") or a("content")),
                "published": a("published").strip(),
            })
    return items


def summarize_with_gemini(cfg, tweets, daily=False):
    lines = []
    for i, tw in enumerate(tweets, 1):
        author = tw.get("author") or tw.get("title") or "未知"
        body = tw.get("content") or tw.get("title") or ""
        lines.append(f"[{i}] 博主:{author} 内容:{body}")
    joined = "\n".join(lines)

    if daily:
        scope = (
            "下面是我关注的 X(Twitter) 列表里【今天一整天】的推文（投资、AI、美股、A股、加密等主题）。\n"
            "请帮我把今天的内容整理成一份当日总结。"
        )
    else:
        scope = (
            "下面是我关注的 X(Twitter) 列表里最近几小时的新推文（投资、AI、美股、A股、加密等主题）。\n"
            "请帮我整理成一份简报。"
        )

    prompt = (
        f"{scope}\n"
        "输出一份要在 Telegram 阅读的中文简报。需要加粗的地方，用 ⟪ 和 ⟫ 这对符号包起来，"
        "例如 ⟪这里会加粗⟫。除了这对符号，不要使用任何其它 Markdown 或 HTML 标记。\n"
        "严格按下面的格式输出（不要写最外层大标题，我会自己加）：\n"
        "\n"
        "开头部分：\n"
        "第一行：⟪📌 TLDR⟫\n"
        "第二行：用一到两句话概括这段时间最重要的事。\n"
        "然后空一行。\n"
        "\n"
        "正文按主题分组。主题小标题单独一行并加粗，前面带 emoji，例如 "
        "⟪🌏 宏观⟫、⟪🇺🇸 美股⟫、⟪🤖 AI⟫、⟪📈 A股⟫、⟪🪙 加密⟫、⟪🛢️ 能源⟫（只列实际出现的主题，主题标题本身不要编号）。\n"
        "\n"
        "每个主题小标题下面，放属于该主题的若干条帖子。每条帖子严格用【三行】，格式如下：\n"
        "  第一行：序号. ⟪一句话精华标题⟫     （序号全局连续，从 1 开始，跨主题继续累加；精华标题加粗，是这条帖子的核心看点）\n"
        "  第二行：用一两句话把帖子内容简练讲清楚（发生了什么/博主的观点），有标的、数据、价格一定保留（如 $AMKR、油价跌5%）。\n"
        "  第三行：— @博主 «N»   （N 必须是这条帖子在下方原始列表里对应的方括号编号，例如原文是 [3]，这里就写 «3»。这个 «N» 非常重要，绝对不能省略或写错，我会用它生成原帖链接。）\n"
        "  ⚠️ 每条帖子之间必须空一行；主题与主题之间也空一行。\n"
        "\n"
        "举例（假设原始列表里 [1][2] 是 fxtrader 的、[5] 是 jukan05 的，注意空行和 «编号»）：\n"
        "⟪🌏 宏观⟫\n"
        "\n"
        "1. ⟪特朗普宣布美伊达成协议⟫\n"
        "霍尔木兹海峡将重开，美军解除海上封锁，原油应声下跌。\n"
        "— @fxtrader «1»\n"
        "\n"
        "2. ⟪录音显示封锁仍在执行⟫\n"
        "与官方说法矛盾，市场对落地存疑。\n"
        "— @fxtrader «2»\n"
        "\n"
        "⟪🤖 AI⟫\n"
        "\n"
        "3. ⟪三星 4nm 为 Neuralink 造芯片⟫\n"
        "已试产，目标明年底量产。\n"
        "— @jukan05 «5»\n"
        "\n"
        "通用要求：\n"
        "- 只如实转述发生了什么 / 博主说了什么，不要加你自己的分析点评。\n"
        "- 大白话、简洁。\n"
        "- 跳过纯广告、生日祝福、无信息量的闲聊。\n"
        "- 务必严格保留每条帖子之间、主题之间的空行。\n"
        "- 每条第三行的 «N» 必须对应原始列表方括号里的编号，不能漏写。\n\n"
        f"以下是推文内容（每条最前面的 [N] 是它的编号）:\n{joined}"
    )

    url = cfg["gemini_base_url"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['gemini_api_key']}"}
    proxy = cfg.get("http_proxy") or None

    # 主模型 + 备用模型链：主模型高峰过载(503)时自动降级到更稳的模型，保证能出结果。
    primary = cfg["gemini_model"]
    fallbacks = cfg.get("fallback_models", ["gemini-3-flash-preview", "gemini-2.5-flash"])
    model_chain = [primary] + [m for m in fallbacks if m != primary]

    def try_model(model_name, waits):
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
        }
        for attempt, wait in enumerate(waits, 1):
            try:
                resp = http_post_json(url, payload, proxy=proxy, headers=headers, timeout=120)
                return resp["choices"][0]["message"]["content"].strip()
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 529) and attempt < len(waits):
                    log(f"[{model_name}] 暂不可用({e.code})，{wait}s 后重试（{attempt}/{len(waits)}）...")
                    time.sleep(wait)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt < len(waits):
                    log(f"[{model_name}] 网络超时/波动（{e}），{wait}s 后重试（{attempt}/{len(waits)}）...")
                    time.sleep(wait)
                    continue
                raise
        raise RuntimeError(f"{model_name} 重试用尽")

    # 主模型先在几十秒内快速重试；实在不行就降级到更稳的备用模型。
    primary_waits = [10, 20, 30]
    fallback_waits = [10, 20, 30]

    last_err = None
    for i, model_name in enumerate(model_chain):
        waits = primary_waits if i == 0 else fallback_waits
        try:
            return try_model(model_name, waits)
        except Exception as e:
            last_err = e
            log(f"模型 {model_name} 仍不可用，尝试下一个备用模型…（{e}）")
            continue
    raise last_err if last_err else RuntimeError("所有模型均失败")


def linkify_sources(text, tweets):
    """把模型输出里的『— @博主 «N»』替换成可点击链接（点击跳到第 N 条原推）。
    用控制字符做占位，等 to_telegram_html 转义后再换成 <a> 标签，避免与转义冲突。
    «N» 对应 tweets[N-1]["link"]。"""
    def repl(m):
        handle = m.group(1)
        try:
            idx = int(m.group(2))
        except ValueError:
            return f"— @{handle}"
        if 1 <= idx <= len(tweets):
            url = tweets[idx - 1].get("link", "")
        else:
            url = ""
        if url:
            # \x01 url \x02 @handle \x03  -> 之后转成 <a href="url">@handle</a>
            return f"— \x01{url}\x02@{handle}\x03"
        return f"— @{handle}"

    # 形如：— @handle «12»  （handle 允许字母数字下划线）
    text = re.sub(r"—\s*@([A-Za-z0-9_]+)\s*«\s*(\d+)\s*»", repl, text)
    # 兜底：模型若漏了 «N»，至少去掉残留的 «...»
    text = re.sub(r"\s*«\s*\d*\s*»", "", text)
    return text


def to_telegram_html(text):
    """转成 Telegram HTML：先转义 < > &，再把加粗标记 ⟪⟫ 换成 <b>，链接占位符换成 <a>。"""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("⟪", "<b>").replace("⟫", "</b>")
    # 链接占位符： \x01 url \x02 文字 \x03  ->  <a href="url">文字</a>
    def link_repl(m):
        url = m.group(1).replace("&amp;", "&").replace('"', "%22")
        label = m.group(2)
        return f'<a href="{url}">{label}</a>'
    text = re.sub("\x01(.*?)\x02(.*?)\x03", link_repl, text, flags=re.S)
    return text


def enforce_blank_lines(text):
    """安全网：确保每条帖子（以『数字.』开头的行）和每个主题标题前都有空行，
    即使模型偶尔忘了留空行也能保证排版清爽。"""
    lines = text.split("\n")
    out = []
    for line in lines:
        stripped = line.lstrip()
        is_item = bool(re.match(r"^\d+\.\s", stripped))
        is_topic = stripped.startswith("⟪") and "TLDR" not in stripped
        if (is_item or is_topic) and out and out[-1].strip() != "":
            out.append("")
        out.append(line)
    # 压掉连续多个空行为最多一个
    cleaned = []
    for ln in out:
        if ln.strip() == "" and cleaned and cleaned[-1].strip() == "":
            continue
        cleaned.append(ln)
    return "\n".join(cleaned)


def send_telegram(cfg, text):
    """Telegram 单条上限 4096 字符，超出自动分段。
    用 HTML 模式让加粗生效（加粗标记 ⟪⟫ 在发送前转成 <b>）。解析失败时退回纯文本。"""
    token = cfg["telegram_bot_token"]
    chat_id = cfg["telegram_chat_id"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    proxy = cfg.get("http_proxy") or None  # TG 在国内一般也需要代理，与 Gemini 共用

    chunks = []
    while text:
        # 尽量在换行处切，避免把一条消息从中间截断
        cut = 3800
        if len(text) > cut:
            nl = text.rfind("\n", 0, cut)
            if nl > cut * 0.6:
                cut = nl
        chunks.append(text[:cut])
        text = text[cut:]

    for chunk in chunks:
        payload = {
            "chat_id": chat_id,
            "text": to_telegram_html(chunk),
            "disable_web_page_preview": True,
            "parse_mode": "HTML",
        }
        try:
            http_post_json(url, payload, proxy=proxy)
        except urllib.error.HTTPError:
            # HTML 解析失败时退回纯文本重发（去掉加粗标记，不让符号露出来）
            payload["text"] = chunk.replace("⟪", "").replace("⟫", "")
            payload.pop("parse_mode", None)
            http_post_json(url, payload, proxy=proxy)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--state", default=os.path.join(HERE, "state.json"))
    ap.add_argument("--dry-run", action="store_true", help="只打印简报，不推送 TG")
    ap.add_argument("--daily", action="store_true",
                    help="全天日报模式：汇总当天所有推文（用于 23:00 那次），并忽略去重")
    ap.add_argument("--limit", type=int, default=0,
                    help="测试用：只取最近 N 条推文（配合 --dry-run，出得快、好看排版）")
    args = ap.parse_args()

    cfg = load_config(args.config)
    state = load_state(args.state)
    seen = set(state["seen_ids"])

    log("拉取 RSS...")
    xml_text = http_get(cfg["rss_url"], proxy=(cfg.get("http_proxy") or None))
    items = parse_rss(xml_text)
    log(f"RSS 共 {len(items)} 条")

    max_n = int(cfg.get("max_tweets_per_run", 80))

    if args.daily:
        # 全天日报：取"今天"的推文，不做去重、不动 state
        today = datetime.now().date()
        day_items = []
        for it in items:
            pub = it.get("published", "")
            try:
                dt = parsedate_to_datetime(pub)
                if dt is not None and dt.date() == today:
                    day_items.append(it)
            except Exception:
                continue
        # 如果时间解析不出来（feed 没给规范时间），退化为取最近 max_n 条
        target_items = day_items if day_items else items[:max_n]
        if not target_items:
            log("今天没有推文，日报跳过。")
            return
        log(f"全天日报：汇总 {len(target_items)} 条，开始总结...")
        summary = enforce_blank_lines(linkify_sources(summarize_with_gemini(cfg, target_items, daily=True), target_items))
        now = datetime.now().strftime("%m-%d")
        header = f"🌙 X 列表 · 今日日报 {now}\n（全天共 {len(target_items)} 条）\n\n"
        message = header + summary

        if args.dry_run:
            print("\n" + "=" * 40 + "\n" + message + "\n" + "=" * 40)
            log("dry-run 模式，未推送。")
            return
        log("推送到 Telegram...")
        send_telegram(cfg, message)
        log("日报完成。")
        return

    # 普通时段：只处理新推文并去重
    # dry-run 预览时忽略去重，方便随时看排版效果（不写 state，不影响正式去重）
    if args.dry_run:
        new_items = items[:max_n]
    else:
        new_items = [it for it in items if it["id"] and it["id"] not in seen]
        if len(new_items) > max_n:
            new_items = new_items[:max_n]

    # 测试用：只取最近 N 条
    if args.limit and len(new_items) > args.limit:
        new_items = new_items[:args.limit]

    if not new_items:
        log("没有新推文，本次不推送。")
        return

    log(f"发现 {len(new_items)} 条新推文，开始总结...")
    summary = enforce_blank_lines(linkify_sources(summarize_with_gemini(cfg, new_items), new_items))

    # 计算这批推文覆盖的时间窗口（取最早 -> 现在）
    pub_times = []
    for it in new_items:
        try:
            dt = parsedate_to_datetime(it.get("published", ""))
            if dt is not None:
                pub_times.append(dt)
        except Exception:
            continue
    now_dt = datetime.now()
    if pub_times:
        earliest = min(pub_times).astimezone()
        window = f"{earliest.strftime('%H:%M')} → {now_dt.strftime('%H:%M')}"
    else:
        window = now_dt.strftime('%H:%M')

    header = (
        f"⟪📰 X 列表要闻 · {now_dt.strftime('%m-%d')} {window}⟫\n"
        f"（本次 {len(new_items)} 条新推）\n\n"
    )
    message = header + summary

    if args.dry_run:
        print("\n" + "=" * 40 + "\n" + message + "\n" + "=" * 40)
        log("dry-run 模式，未推送。")
        return

    log("推送到 Telegram...")
    send_telegram(cfg, message)

    # 推送成功后再记录已见 ID，避免推送失败导致漏掉
    for it in new_items:
        seen.add(it["id"])
    state["seen_ids"] = list(seen)
    save_state(args.state, state)
    log("完成。")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        log(f"HTTP 错误: {e.code} {e.reason} - {e.read().decode('utf-8', 'replace')[:500]}")
        sys.exit(1)
    except Exception as e:
        log(f"运行出错: {e}")
        sys.exit(1)
