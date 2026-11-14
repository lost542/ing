#!/usr/bin/env python3
"""
GitHub Trending 每日日报
每天抓取 GitHub 上新崛起的热门项目，整理成中文日报并通过邮件发送。
设计为在 GitHub Actions 上运行（云端执行，不依赖本地电脑开机）。
"""
import os
import re
import sys
import json
import smtplib
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header

# ---------------------------------------------------------------- 配置
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.qq.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")   # 发件邮箱
SMTP_PASS = os.environ.get("SMTP_PASS", "")   # SMTP 授权码（不是登录密码）
TO_EMAIL = os.environ.get("TO_EMAIL", "")     # 收件邮箱

BJT = timezone(timedelta(hours=8))
TODAY = datetime.now(BJT).strftime("%Y-%m-%d")

# 方向关键词标签（按仓库描述 / topics 匹配）
TAG_RULES = [
    ("AI / Agent", ["ai", "agent", "llm", "gpt", "model", "inference", "rag",
                    "assistant", "copilot", "chatbot", "prompt", "mcp",
                    "transformer", "embedding", "claude", "openai"]),
    ("好玩 / 有趣", ["game", "emulator", "retro", "remake", "pixel", "3d",
                    "visual", "art", "music", "animation", "toy", "fun",
                    "hack", "hardware", "raspberry", "recreation"]),
    ("开发工具 / 效率", ["cli", "tool", "extension", "editor", "plugin",
                       "productivity", "automation", "devtool", "terminal",
                       "workflow", "launcher", "manager", "monitor"]),
]


def tag_repo(repo: dict) -> str:
    text = ((repo.get("description") or "") + " " + " ".join(repo.get("topics") or [])).lower()
    for tag, kws in TAG_RULES:
        if any(k in text for k in kws):
            return tag
    return "综合 / 其他"


def fmt_stars(n) -> str:
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


# ---------------------------------------------------------------- 抓取数据
def fetch_json(url: str):
    req = urllib.request.Request(url, headers={
        "User-Agent": "trending-daily-bot",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def fetch_trending_html() -> list:
    """官方 API 的备用方案：解析 github.com/trending 页面。"""
    req = urllib.request.Request("https://github.com/trending",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8")
    repos = []
    for m in re.finditer(r'<h2[^>]*>.*?href="/([^"]+/[^"]+)"', html, re.S):
        repos.append({"full_name": m.group(1), "description": "",
                      "stargazers_count": None, "html_url":
                      f"https://github.com/{m.group(1)}", "language": ""})
    return repos


def collect_repos() -> list:
    """
    主方案：GitHub 官方 Search API —— 找 7 天内创建、按 star 排序的仓库，
    这比 trending 页面更聚焦"新出炉的好项目"。
    """
    since = (datetime.now(BJT) - timedelta(days=7)).strftime("%Y-%m-%d")
    url = ("https://api.github.com/search/repositories"
           f"?q=created:%3E{since}&sort=stars&order=desc&per_page=30")
    try:
        data = fetch_json(url)
        repos = [
            {
                "full_name": r["full_name"],
                "description": (r.get("description") or "")[:120],
                "stars": r["stargazers_count"],
                "language": r.get("language") or "—",
                "url": r["html_url"],
                "topics": r.get("topics") or [],
            }
            for r in data.get("items", [])
        ]
        if repos:
            return repos
    except (urllib.error.URLError, KeyError, ValueError) as e:
        print(f"Search API 失败: {e}，回退到 trending 页面", file=sys.stderr)
    # 回退方案
    return [
        {"full_name": r["full_name"], "description": r["description"],
         "stars": r["stargazers_count"] or 0, "language": r["language"] or "—",
         "url": r["html_url"], "topics": []}
        for r in fetch_trending_html()[:20]
    ]


# ---------------------------------------------------------------- 生成日报
def build_digest(repos: list) -> tuple[str, str]:
    """返回 (纯文本日报, HTML 日报)"""
    repos = [r for r in repos if r["full_name"].lower() not in (
        "nodejs/node", "nvm-sh/nvm", "python/cpython")][:8]

    lines = [f"# GitHub 趣项目日报 · {TODAY}", ""]
    for i, r in enumerate(repos, 1):
        tag = tag_repo(r)
        desc = r["description"] or "（暂无描述）"
        stars = fmt_stars(r["stars"]) if r["stars"] else "?"
        lines += [
            f"## {i}. {r['full_name']}  `{tag}`",
            f"- 简介：{desc}",
            f"- 语言：{r['language']}　⭐ {stars}",
            f"- 链接：{r['url']}",
            "",
        ]
    lines.append("---")
    lines.append("本日报由云端机器人每日自动生成，只推荐不下载。")
    lines.append("想深入了解某个项目，打开 WorkBuddy 说一句即可。")
    text = "\n".join(lines)

    # HTML 版（邮件正文更好看）
    rows = []
    for i, r in enumerate(repos, 1):
        tag = tag_repo(r)
        stars = fmt_stars(r["stars"]) if r["stars"] else "?"
        rows.append(f"""
        <div style="margin:0 0 18px;padding:14px 16px;border:1px solid #e5e7eb;
                    border-radius:10px;background:#fafafa;">
          <div style="font-size:16px;font-weight:600;margin-bottom:6px;">
            {i}. <a href="{r['url']}" style="color:#0969da;text-decoration:none;">
                {r['full_name']}</a>
            <span style="font-size:12px;background:#ddf4ff;color:#0969da;
                         padding:2px 8px;border-radius:10px;margin-left:8px;">{tag}</span>
          </div>
          <div style="color:#333;font-size:14px;line-height:1.6;">{r['description'] or '（暂无描述）'}</div>
          <div style="color:#888;font-size:12px;margin-top:6px;">
            语言 {r['language']} · ⭐ {stars}
          </div>
        </div>""")
    html = f"""<html><body style="font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;
        max-width:640px;margin:0 auto;padding:16px;color:#1f2328;">
      <h1 style="font-size:22px;">📬 GitHub 趣项目日报 · {TODAY}</h1>
      <p style="color:#666;font-size:13px;">近 7 天新崛起的热门开源项目，按 Star 数排序，已过滤老牌常青项目</p>
      {''.join(rows)}
      <hr style="border:none;border-top:1px solid #e5e7eb;">
      <p style="color:#999;font-size:12px;">云端机器人自动生成 · 只推荐不下载 · 想研究哪个项目请打开 WorkBuddy 告诉我</p>
    </body></html>"""
    return text, html


# ---------------------------------------------------------------- 发送邮件
def send_email(text: str, html: str):
    if not (SMTP_USER and SMTP_PASS and TO_EMAIL):
        raise RuntimeError("缺少 SMTP_USER / SMTP_PASS / TO_EMAIL 环境变量")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(f"GitHub 趣项目日报 · {TODAY}", "utf-8")
    msg["From"] = SMTP_USER
    msg["To"] = TO_EMAIL
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    # local_hostname 固定为 ASCII 域名，避免中文主机名导致 EHLO 编码报错
    if SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30,
                                  local_hostname="localhost")
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30,
                              local_hostname="localhost")
        server.starttls()
    server.login(SMTP_USER, SMTP_PASS)
    server.sendmail(SMTP_USER, [TO_EMAIL], msg.as_string())
    server.quit()


def main():
    # 幂等保护：workflow 每天会触发多次（防 GitHub 丢运行），当天已发过就不再重复发
    archive = f"digests/{TODAY}.md"
    if os.path.exists(archive) and not os.environ.get("FORCE_SEND"):
        print(f"今天（{TODAY}）的日报已发送过，跳过本次。")
        return

    repos = collect_repos()
    if not repos:
        print("今天没有抓到项目，跳过发送")
        return
    text, html = build_digest(repos)
    send_email(text, html)
    # 发送成功后再写归档文件，确保归档 = 已发送
    os.makedirs("digests", exist_ok=True)
    with open(archive, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"✅ 日报已发送至 {TO_EMAIL}，共 {len(repos)} 个项目")


if __name__ == "__main__":
    main()
