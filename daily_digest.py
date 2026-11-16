#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub 趣项目日报

设计要点：
1. GitHub Actions 每小时唤醒一次 —— 用来对抗 GitHub 定时任务偶尔丢失或大幅延迟的问题。
2. 真正决定"发不发"的两道闸门：
   - 时间闸门：北京时间 9 点之前一律不发；
   - 幂等闸门：当天已经发过（归档 + 发送标记都在）就跳过。
3. 项目介绍由 LLM 生成中文解读（默认走 GitHub Models，用 GITHUB_TOKEN 即可，不需要额外买 key）。
   没有可用 key 时降级为规则摘要，保证邮件仍然能发出去，但质量会差一些。
4. 全程只做推荐，绝不下载、克隆或运行任何项目。

用法：
    python daily_digest.py             # 生成 + 发送（本地调试用，等价于 --all）
    python daily_digest.py --prepare   # 只生成归档
    python daily_digest.py --send      # 只发已生成的归档
    python daily_digest.py --demo      # 用假数据渲染一遍，不联网、不发信
"""

import json
import os
import re
import smtplib
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ---------------------------------------------------------------- 配置
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.163.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
TO_EMAIL = os.environ.get("TO_EMAIL", "")

# 时间闸门：北京时间几点之后才允许发送
SEND_AFTER_HOUR = int(os.environ.get("SEND_AFTER_HOUR", "9"))
# 注意：环境变量拿到的是字符串，"false" 在 Python 里也是真值，必须显式解析
FORCE_SEND = os.environ.get("FORCE_SEND", "").strip().lower() in ("1", "true", "yes", "on")

# LLM：任意 OpenAI 兼容接口（DeepSeek / 硅基流动 / OpenAI / 自建网关均可）
# 注意：GitHub Models（models.github.ai）实测在 Actions 内只返回纯文本 "OK"，
# 无法真正生成内容，因此不再默认走它；没配 key 时自动降级为中文数据版日报。
LLM_BASE_URL = (os.environ.get("LLM_BASE_URL") or "").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL") or "deepseek-chat"
LLM_API_KEY = os.environ.get("LLM_API_KEY") or ""

MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "9"))
# star 超过这个数的当作「老牌巨无霸」，不再占日报篇幅
MAX_TOTAL_STARS = int(os.environ.get("MAX_TOTAL_STARS", "50000"))

BJT = timezone(timedelta(hours=8))
NOW = datetime.now(BJT)
TODAY = NOW.strftime("%Y-%m-%d")
DIGEST_DIR = "digests"
ARCHIVE_MD = f"{DIGEST_DIR}/{TODAY}.md"
ARCHIVE_HTML = f"{DIGEST_DIR}/{TODAY}.html"
SENT_FLAG = f"{DIGEST_DIR}/.sent-{TODAY}"

GROUP_ORDER = ["AI / Agent", "开发工具 / 效率", "好玩 / 有趣", "综合 / 其他"]
GROUP_TITLE = {
    "AI / Agent": "AI / Agent",
    "开发工具 / 效率": "开发工具 / 效率",
    "好玩 / 有趣": "好玩 / 有趣",
    "综合 / 其他": "其他",
}
CN_NUM = ["一", "二", "三", "四", "五", "六"]

# 老牌常青项目：上不上榜都不该占位置
EVERGREEN = {
    "nodejs/node", "nvm-sh/nvm", "python/cpython", "torvalds/linux",
    "microsoft/vscode", "facebook/react", "vuejs/vue", "vercel/next.js",
    "rust-lang/rust", "golang/go", "tensorflow/tensorflow", "pytorch/pytorch",
    "ohmyzsh/ohmyzsh", "jlevy/the-art-of-command-line", "github/gitignore",
    "louislam/uptime-kuma", "codecrafters-io/build-your-own-x",
    "sindresorhus/awesome", "public-apis/public-apis",
}

TAG_RULES = [
    ("AI / Agent", ["ai", "agent", "agents", "llm", "gpt", "model", "inference",
                    "rag", "assistant", "copilot", "chatbot", "prompt", "mcp", "skill",
                    "skills", "transformer", "embedding", "claude", "openai", "deepseek",
                    "diffusion", "multimodal", "vector"]),
    ("好玩 / 有趣", ["game", "games", "emulator", "retro", "remake", "recomp", "pixel",
                    "3d", "visual", "art", "music", "animation", "toy", "fun", "meme",
                    "playground", "shader", "webgl", "webgpu", "creative", "generative",
                    "simulation", "underwater"]),
    ("开发工具 / 效率", ["cli", "tool", "tools", "extension", "editor", "plugin", "sdk",
                       "productivity", "automation", "devtool", "terminal", "workflow",
                       "launcher", "manager", "monitor", "diagram", "review", "lint",
                       "debug", "deploy", "self-hosted", "serverless", "gui", "canvas",
                       "library", "api", "proxy", "scheduler"]),
]

TAG_HINT = {
    "AI / Agent": "属于 AI / Agent 方向，关注模型应用与智能体工具链的人可以跟进。",
    "开发工具 / 效率": "属于开发工具 / 效率方向，解决的是日常开发或运维里的具体摩擦。",
    "好玩 / 有趣": "属于好玩 / 有趣方向，偏创意、可视化或体验层面的项目。",
    "综合 / 其他": "暂未归入明确方向，可以按 README 自行判断是否符合你的口味。",
}


def _kw_hit(kw, text):
    """短英文关键词按词边界匹配，避免 "ai" 命中 available 这类误判。"""
    if re.fullmatch(r"[a-z0-9+#.\- ]+", kw):
        return re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", text) is not None
    return kw in text


# ---------------------------------------------------------------- 小工具
def fmt_stars(n):
    if not n:
        return "?"
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def norm(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def http_json(url, payload=None, headers=None, timeout=60):
    hdrs = {"User-Agent": "trending-daily-bot", "Accept": "application/vnd.github+json"}
    # 调 GitHub API 时带上 token，避免 60 次/小时的匿名限流（Actions 内是 5000 次/小时）
    gh_token = os.environ.get("GH_API_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if gh_token and "api.github.com" in url:
        hdrs["Authorization"] = f"Bearer {gh_token}"
    hdrs.update(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------- 抓数据
def fetch_trending():
    """解析 github.com/trending 日榜，顺带拿到「今日新增 star」。"""
    req = urllib.request.Request("https://github.com/trending?since=daily",
                                 headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            html = r.read().decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        print(f"trending 抓取失败：{e}", file=sys.stderr)
        return []

    repos = []
    for block in html.split("<article")[1:]:
        # GitHub 会给 <a> 加 data-hydro-click 之类的跟踪属性，href 不再紧跟 <a，
        # 所以先在 <h2> 里取仓库链接，别依赖属性顺序。
        h2 = re.search(r"<h2[^>]*>(.*?)</h2>", block, re.S)
        name = re.search(r'href="/([\w.\-]+/[\w.\-]+)"', h2.group(1)) if h2 else None
        if not name:
            continue
        full = name.group(1)
        if full.count("/") != 1:
            continue
        desc = re.search(r'<p[^>]*class="[^"]*col-9[^"]*"[^>]*>(.*?)</p>', block, re.S)
        lang = re.search(r'itemprop="programmingLanguage">([^<]+)<', block)
        star = re.search(rf'href="/{re.escape(full)}/stargazers"[^>]*>(.*?)</a>', block, re.S)
        stars = 0
        if star:
            digits = re.sub(r"[^0-9]", "", re.sub(r"<[^>]+>", "", star.group(1)))
            stars = int(digits) if digits else 0
        today = re.search(r"([\d,]+)\s+stars?\s+today", block)
        repos.append({
            "full_name": full,
            "description": norm(re.sub(r"<[^>]+>", "", desc.group(1))) if desc else "",
            "language": norm(lang.group(1)) if lang else "",
            "stars": stars,
            "stars_today": int(today.group(1).replace(",", "")) if today else 0,
            "url": f"https://github.com/{full}",
            "topics": [],
            "created": "",
        })
    if not repos:
        print("⚠️ trending 解析到 0 条，页面结构可能又变了", file=sys.stderr)
    return repos


def fetch_new_repos(days=7):
    """近 N 天新建、按 star 排序的仓库 —— 比 trending 更聚焦真正的新项目。"""
    since = (NOW - timedelta(days=days)).strftime("%Y-%m-%d")
    url = ("https://api.github.com/search/repositories"
           f"?q=created:%3E{since}&sort=stars&order=desc&per_page=40")
    try:
        data = http_json(url)
    except Exception as e:  # noqa: BLE001
        print(f"Search API 失败：{e}", file=sys.stderr)
        return []
    out = []
    for r in data.get("items", []):
        out.append({
            "full_name": r["full_name"],
            "description": norm(r.get("description")),
            "language": r.get("language") or "",
            "stars": r.get("stargazers_count") or 0,
            "stars_today": 0,
            "url": r["html_url"],
            "topics": r.get("topics") or [],
            "created": (r.get("created_at") or "")[:10],
        })
    return out


def recent_digest_names():
    """最近几天已经推荐过的项目，避免重复（归档 md + 手工历史清单）。"""
    seen = set()
    if not os.path.isdir(DIGEST_DIR):
        return seen
    files = [fn for fn in sorted(os.listdir(DIGEST_DIR))
             if (fn.endswith(".md") and fn != f"{TODAY}.md") or fn == "history.txt"]
    for fn in files:
        try:
            with open(os.path.join(DIGEST_DIR, fn), encoding="utf-8") as f:
                for line in f:
                    m = re.search(r"https://github\.com/([\w.\-]+/[\w.\-]+)", line) \
                        or re.match(r"\s*([\w.\-]+/[\w.\-]+)\s*$", line)
                    if m:
                        seen.add(m.group(1).lower())
        except OSError:
            continue
    return seen


def collect():
    seen = recent_digest_names()
    picked, used, used_desc = [], set(), set()

    def add(repo):
        key = repo["full_name"].lower()
        if key in used or key in seen or key in EVERGREEN:
            return
        if repo.get("stars", 0) >= MAX_TOTAL_STARS:
            return
        # 同日克隆去重：描述前缀相同的一律只留第一个（GitHub 上克隆潮很常见）
        desc_key = norm(repo.get("description")).lower()[:60]
        if desc_key and desc_key in used_desc:
            return
        used.add(key)
        used_desc.add(desc_key)
        picked.append(repo)

    # 今天的日榜优先（有「今日 +N」这个增长信号）
    for r in fetch_trending():
        add(r)
    # 再用新建高星仓库补足
    for r in fetch_new_repos():
        if r["stars"] >= 300:
            add(r)

    # 今日涨得猛的排前面，其余按总 star
    picked.sort(key=lambda r: (r["stars_today"], r["stars"]), reverse=True)
    picked = picked[:MAX_ITEMS]
    print(f"共筛出 {len(picked)} 个项目")
    return picked


# ---------------------------------------------------------------- 中文解读
def rule_tag(repo):
    """按 主题 / 仓库名 / 描述 三个权重打分，取最高分，避免关键词一撞就误判方向。"""
    name = repo.get("full_name", "").replace("/", " ").replace("-", " ").replace("_", " ").lower()
    desc = (repo.get("description") or "").lower()
    topics = " ".join(repo.get("topics") or []).lower()

    best_tag, best_score, tie = "综合 / 其他", 0, False
    for tag, kws in TAG_RULES:
        score = 0
        for k in kws:
            if topics and _kw_hit(k, topics):
                score += 3
            if _kw_hit(k, name):
                score += 2
            if _kw_hit(k, desc):
                score += 1
        if score > best_score:
            best_tag, best_score, tie = tag, score, False
        elif score == best_score and score > 0:
            tie = True
    # 分数太低或并列，就老实归到「其他」，不硬塞方向
    if best_score < 2 or tie:
        return "综合 / 其他"
    return best_tag


def fallback_notes(repo):
    """无 LLM 时的兜底：不编造功能，只用可核对的数据 + 中文方向提示。"""
    desc = norm(repo.get("description"))
    tag = rule_tag(repo)
    bits = [repo.get("language") or "语言未标注"]
    if repo.get("stars"):
        bits.append(f"累计 {fmt_stars(repo['stars'])} star")
    if repo.get("stars_today"):
        bits.append(f"今日 +{repo['stars_today']}")
    if repo.get("created"):
        bits.append(f"仓库建于 {repo['created'][:10]}")
    return {
        "tag": tag,
        "summary": desc or "仓库暂无描述",
        "why": " · ".join(bits) + "。" + TAG_HINT[tag],
    }


SYSTEM_PROMPT = (
    "你是中文技术媒体编辑，为《GitHub 趣项目日报》撰写项目介绍。"
    "文风：口语化、克制、有判断力，像一个懂行的朋友在跟你讲。"
    "禁止使用「赋能」「一站式」「革命性」「颠覆」这类营销词，不要堆砌感叹号。"
    "严禁编造仓库描述里没有的功能，不确定就只描述它是什么。"
)


def llm_notes(repos):
    """一次调用让模型给所有项目写中文解读；失败返回 None。"""
    if not (LLM_API_KEY and LLM_BASE_URL):
        print("未配置 LLM_API_KEY / LLM_BASE_URL，使用中文数据版兜底", file=sys.stderr)
        return None

    payload_repos = [{
        "full_name": r["full_name"],
        "description": r["description"],
        "language": r["language"],
        "stars": r["stars"],
        "stars_today": r["stars_today"],
        "topics": r["topics"][:8],
    } for r in repos]

    user_prompt = f"""下面是今天 GitHub 上冒头的 {len(repos)} 个项目（JSON）。请为每个项目写中文介绍。

要求：
1. summary：一句话说清它是什么，40 字以内，不要重复项目名。
2. why：2-3 句，说清"为什么有意思 / 解决了什么痒点"，可以是技术亮点、使用场景或社区现象，80-140 字。
3. tag：只能从 ["AI / Agent", "开发工具 / 效率", "好玩 / 有趣", "综合 / 其他"] 里选一个。
4. 不要写 star 数，不要写"开源"这种废话，不要编造功能。

严格只输出 JSON，不要 markdown 代码块：
{{"items":[{{"full_name":"owner/repo","tag":"...","summary":"...","why":"..."}}]}}

项目列表：
{json.dumps(payload_repos, ensure_ascii=False, indent=1)}
"""

    # 模型名不同网关可能不一样，按顺序退让，避免整封邮件降级
    candidates = []
    for m in (LLM_MODEL, "deepseek-chat", "gpt-4o-mini"):
        if m and m not in candidates:
            candidates.append(m)

    last_err = None
    for model in candidates:
        try:
            data = http_json(
                f"{LLM_BASE_URL}/chat/completions",
                payload={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.7,
                },
                headers={"Authorization": f"Bearer {LLM_API_KEY}"},
                timeout=180,
            )
            text = data["choices"][0]["message"]["content"]
            text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
            items = json.loads(text).get("items", [])
            out = {}
            for it in items:
                tag = it.get("tag") if it.get("tag") in GROUP_TITLE else "综合 / 其他"
                out[it["full_name"].lower()] = {
                    "tag": tag,
                    "summary": norm(it.get("summary")),
                    "why": norm(it.get("why")),
                }
            if out:
                print(f"LLM 生成了 {len(out)} 条中文解读（{model}）")
                return out
            last_err = "模型返回空结果"
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"模型 {model} 调用失败：{e}", file=sys.stderr)

    print(f"LLM 全部不可用，改用规则摘要：{last_err}", file=sys.stderr)
    return None


def annotate(repos):
    notes = llm_notes(repos) or {}
    ai_ok = bool(notes)
    result = {}
    for r in repos:
        n = notes.get(r["full_name"].lower())
        if not n or not n.get("summary"):
            n = fallback_notes(r)
        result[r["full_name"]] = n
    return result, ai_ok


# ---------------------------------------------------------------- 渲染
def group_items(repos, notes):
    buckets = {g: [] for g in GROUP_ORDER}
    for r in repos:
        buckets[notes[r["full_name"]]["tag"]].append(r)
    return [(g, buckets[g]) for g in GROUP_ORDER if buckets[g]]


def render_md(repos, notes, ai_ok):
    lines = [f"# GitHub 趣项目日报 · {TODAY}", ""]
    lines.append(f"> 今日共 {len(repos)} 个新上榜项目，已剔除老牌常青树与近期重复推荐。")
    lines.append("> 每个项目标注了所属方向；只做推荐，不下载、不运行。")
    if not ai_ok:
        lines.append("> 说明：本轮未配置 LLM key，未生成 AI 中文解读，"
                     "「一句话」沿用仓库原始英文描述，其余信息（方向、Star、语言）均由本地规则判定。")
    lines.append("")

    idx = 0
    for gi, (group, items) in enumerate(group_items(repos, notes)):
        num = CN_NUM[gi] if gi < len(CN_NUM) else str(gi + 1)
        lines.append(f"## {num}、{GROUP_TITLE[group]}")
        lines.append("")
        for r in items:
            idx += 1
            n = notes[r["full_name"]]
            data = f"⭐ {fmt_stars(r['stars'])}"
            if r["stars_today"]:
                data += f"（今日 +{r['stars_today']}）"
            if r["language"]:
                data += f"｜{r['language']}"
            lines += [
                f"### {idx}. {r['full_name']}  `{n['tag']}`",
                f"- **一句话**：{n['summary']}",
                f"- **为什么有意思**：{n['why']}",
                f"- **数据**：{data}",
                f"- 🔗 {r['url']}",
                "",
            ]

    lines.append("---")
    if not ai_ok:
        lines.append("> 注：本次 AI 解读未启用，项目简介为仓库原始描述。")
    lines.append("*本日报只做推荐，未下载、未克隆、未运行任何项目。*")
    lines.append("*对哪个感兴趣，回一句「帮我下载 / 研究一下 XX」即可。*")
    return "\n".join(lines)


CSS = """
  body{margin:0;padding:0;background:#f6f7f9;}
  .wrap{max-width:660px;margin:0 auto;padding:20px 16px 32px;
        font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;
        color:#1f2328;}
  .head{padding:4px 0 14px;border-bottom:2px solid #1f2328;}
  .head h1{margin:0 0 6px;font-size:21px;letter-spacing:.3px;}
  .head p{margin:0;font-size:12px;color:#6b7280;line-height:1.7;}
  .grp{margin:26px 0 10px;font-size:13px;font-weight:700;color:#0b5cad;letter-spacing:1px;}
  .notice{margin:14px 0 0;padding:10px 12px;background:#fff8e6;border:1px solid #f0e0b8;
          border-radius:8px;font-size:12px;line-height:1.7;color:#8a6d1f;}
  .card{background:#fff;border:1px solid #e6e8eb;border-radius:12px;
        padding:14px 16px;margin:0 0 12px;}
  .t{font-size:15px;font-weight:650;margin:0 0 8px;line-height:1.5;}
  .t a{color:#0b5cad;text-decoration:none;}
  .tag{display:inline-block;font-size:11px;color:#0b5cad;background:#e8f1fb;
       border-radius:20px;padding:2px 9px;margin-left:6px;vertical-align:middle;font-weight:600;}
  .sm{font-size:14px;line-height:1.75;color:#24292f;margin:0 0 6px;}
  .why{font-size:13px;line-height:1.8;color:#57606a;margin:0 0 9px;}
  .meta{font-size:12px;color:#8b949e;border-top:1px dashed #eaecef;padding-top:8px;}
  .meta a{color:#8b949e;text-decoration:none;}
  .foot{margin-top:26px;font-size:12px;color:#9aa0a6;line-height:1.9;
        border-top:1px solid #e6e8eb;padding-top:14px;}
"""


def render_html(repos, notes, ai_ok):
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>GitHub 趣项目日报 · {TODAY}</title><style>{CSS}</style></head><body>",
        "<div class='wrap'>",
        "<div class='head'>",
        f"<h1>GitHub 趣项目日报 · {TODAY}</h1>",
        f"<p>今日 {len(repos)} 个新上榜项目 · 已剔除老牌常青树与近期重复推荐</p>",
        "</div>",
    ]
    if not ai_ok:
        parts.append(
            "<div class='notice'>本轮未配置 LLM key，未生成 AI 中文解读："
            "「一句话」为仓库原始英文描述，方向、Star、语言由本地规则判定。</div>"
        )
    idx = 0
    for gi, (group, items) in enumerate(group_items(repos, notes)):
        num = CN_NUM[gi] if gi < len(CN_NUM) else str(gi + 1)
        parts.append(f"<div class='grp'>{num}、{GROUP_TITLE[group]}</div>")
        for r in items:
            idx += 1
            n = notes[r["full_name"]]
            meta = f"⭐ {fmt_stars(r['stars'])}"
            if r["stars_today"]:
                meta += f"（今日 +{r['stars_today']}）"
            if r["language"]:
                meta += f" · {r['language']}"
            parts += [
                "<div class='card'>",
                f"<div class='t'>{idx}. <a href='{r['url']}'>{r['full_name']}</a>"
                f"<span class='tag'>{n['tag']}</span></div>",
                f"<div class='sm'>{n['summary']}</div>",
                f"<div class='why'>{n['why']}</div>",
                f"<div class='meta'>{meta} · "
                f"<a href='{r['url']}'>github.com/{r['full_name']}</a></div>",
                "</div>",
            ]
    parts.append("<div class='foot'>")
    if not ai_ok:
        parts.append("本次 AI 解读未启用，简介为仓库原始描述。<br>")
    parts.append("只做推荐，未下载、未克隆、未运行任何项目。<br>"
                 "想深入研究某个项目，回一句「帮我下载 / 研究一下 XX」即可。</div>")
    parts += ["</div></body></html>"]
    return "".join(parts)


# ---------------------------------------------------------------- 发送
def send_email(text, html):
    if not (SMTP_USER and SMTP_PASS and TO_EMAIL):
        raise RuntimeError("缺少 SMTP_USER / SMTP_PASS / TO_EMAIL 环境变量")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(f"GitHub 趣项目日报 · {TODAY}", "utf-8")
    msg["From"] = SMTP_USER
    msg["To"] = TO_EMAIL
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    if SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=45,
                                  local_hostname="localhost")
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=45,
                              local_hostname="localhost")
        server.starttls()
    server.login(SMTP_USER, SMTP_PASS)
    server.sendmail(SMTP_USER, [TO_EMAIL], msg.as_string())
    server.quit()


# ---------------------------------------------------------------- 主流程
def time_gate():
    if FORCE_SEND:
        print("FORCE_SEND 已设置，跳过时间闸门")
        return True
    if NOW.hour < SEND_AFTER_HOUR:
        print(f"⏰ 现在北京时间 {NOW:%H:%M}，{SEND_AFTER_HOUR}:00 之前不发送，本次退出")
        return False
    return True


def stage_prepare():
    if os.path.exists(ARCHIVE_MD) and not FORCE_SEND:
        print(f"{ARCHIVE_MD} 已存在，跳过生成")
        return True
    repos = collect()
    if len(repos) < 3:
        print("抓到的项目太少，本次不生成日报")
        return False
    notes, ai_ok = annotate(repos)
    md = render_md(repos, notes, ai_ok)
    html = render_html(repos, notes, ai_ok)
    os.makedirs(DIGEST_DIR, exist_ok=True)
    with open(ARCHIVE_MD, "w", encoding="utf-8") as f:
        f.write(md)
    with open(ARCHIVE_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 已生成 {ARCHIVE_MD}（{len(repos)} 个项目）")
    return True


def stage_send():
    if os.path.exists(SENT_FLAG) and not FORCE_SEND:
        print(f"今天（{TODAY}）已经发过，跳过本次")
        return
    if not os.path.exists(ARCHIVE_MD):
        print("今天还没有归档，跳过发送")
        return
    with open(ARCHIVE_MD, encoding="utf-8") as f:
        text = f.read()
    if os.path.exists(ARCHIVE_HTML):
        with open(ARCHIVE_HTML, encoding="utf-8") as f:
            html = f.read()
    else:
        html = f"<pre>{text}</pre>"
    send_email(text, html)
    with open(SENT_FLAG, "w", encoding="utf-8") as f:
        f.write(f"sent at {NOW:%Y-%m-%d %H:%M:%S} (BJT)\n")
    print(f"📮 日报已发送至 {TO_EMAIL}")


DEMO_REPOS = [
    {"full_name": "demo/agent-board", "description": "Annotation workspace for coding agents",
     "language": "TypeScript", "stars": 11200, "stars_today": 408,
     "url": "https://github.com/demo/agent-board", "topics": ["agent"]},
    {"full_name": "demo/zelda-recomp", "description": "Static recompilation of a game to native PC",
     "language": "C", "stars": 4100, "stars_today": 0,
     "url": "https://github.com/demo/zelda-recomp", "topics": ["game"]},
    {"full_name": "demo/cli-helper", "description": "A small CLI tool for productivity",
     "language": "Go", "stars": 860, "stars_today": 120,
     "url": "https://github.com/demo/cli-helper", "topics": ["cli"]},
]


def main():
    mode = (sys.argv[1].lstrip("-") if len(sys.argv) > 1 else "all").lower()

    if mode == "demo":
        notes = {r["full_name"]: fallback_notes(r) for r in DEMO_REPOS}
        print(render_md(DEMO_REPOS, notes, False))
        html = render_html(DEMO_REPOS, notes, False)
        assert "<div class='card'>" in html and html.count("<div class='card'>") == 3
        print("\n[demo] 渲染通过，未联网、未发送。")
        return

    if not time_gate():
        return
    if mode in ("prepare", "all"):
        if not stage_prepare():
            return
    if mode in ("send", "all"):
        stage_send()


if __name__ == "__main__":
    main()
