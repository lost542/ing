# GitHub 趣项目日报 ☁️

一个跑在 **GitHub Actions 云端** 的定时任务：每天早上 9 点（北京时间）之后
自动抓取新崛起的热门开源项目，用 AI 写成中文解读，整理成日报发到你的邮箱。
**你的电脑关不关机完全不影响。**

## 它做什么

1. 抓 `github.com/trending` 日榜（带「今日 +N star」这个增长信号）+ GitHub Search API 近 7 天新建的高星仓库
2. 自动过滤 nodejs/node 这类老牌常青项目，并跳过最近几天已经推荐过的仓库
3. 调 **GitHub Models**（用仓库内置的 `GITHUB_TOKEN`，不需要另外买 API key）给每个项目写中文解读：
   一句话说清它是什么，再补 2-3 句「为什么有意思」
4. 按 `AI / Agent`、`开发工具 / 效率`、`好玩 / 有趣` 三个方向分组排版
5. 把日报发到你的邮箱，同时归档到仓库 `digests/`（`.md` 正文 + `.html` 排版版）


## 搭建步骤（只需做一次）

### 1. 在 GitHub 上创建仓库

- 打开 https://github.com/new
- 仓库名建议：`github-trending-daily`，选 **Public** 或 **Private** 都可以
- 不要勾选 "Add a README"，创建后按页面提示把本地这个文件夹推上去：

```bash
cd github-trending-daily
git init
git add .
git commit -m "init: github trending daily digest"
git branch -M main
git remote add origin https://github.com/<你的用户名>/github-trending-daily.git
git push -u origin main
```

### 2. 开通 163 邮箱的 SMTP 服务，拿到授权码

1. 网页登录 https://mail.163.com → 顶部 **设置** → **POP3/SMTP/IMAP**
2. 开启 **IMAP/SMTP 服务**（发送邮件用这个）
3. 按提示用手机发短信验证，然后会给你一个 **16 位授权码**（一串字母）
   ⚠️ 这是"客户端授权密码"，**不是**你的 163 登录密码；只显示一次，记下来

### 3. 配置仓库 Secrets（5 个）

打开你的仓库页面 → **Settings → Secrets and variables → Actions → New repository secret**，依次添加（名字必须完全一致，全大写）：

| Secret 名 | 值 |
|---|---|
| `SMTP_HOST` | `smtp.163.com` |
| `SMTP_PORT` | `465` |
| `SMTP_USER` | 你的 163 邮箱地址，如 `xxxx@163.com` |
| `SMTP_PASS` | 刚拿到的 16 位授权码 |
| `TO_EMAIL` | 收件邮箱（填你常用的邮箱，可以是同一个 163，也可以是 QQ 等其他邮箱） |

### 4. 启用 Actions 并手动测试

- 仓库 → **Actions** 标签页 → 如果提示 enable workflows，点启用
- 左侧选 "GitHub Trending Daily Digest" → **Run workflow** → 手动跑一次
- 绿色 ✅ 且邮箱收到日报 = 搭建成功，之后每天早上 9 点自动送达

## 发送时机（重要）

- cron 是 `0 * * * *`（每小时整点），**0-8 点这几轮会空跑**，因为脚本里有硬闸门：
  北京时间 9 点之前一律不发。
- 9 点之后第一轮真正跑成功的，就是当天那一封；之后每小时都会因为检测到
  `digests/.sent-YYYY-MM-DD` 而跳过，**当天不会重复发**。
- 顺序是「先归档并推送到仓库 → 再发信」。归档推不上去就不发，避免出现
  「信发了但仓库没记录，下一小时又发一遍」的情况。
- 所以实际到达时间大约在 **9:00-9:55** 之间，取决于 GitHub 定时器的排队情况。

## 常见问题

- **9 点前收不到**：正常，这是有意设计的，别改 `SEND_AFTER_HOUR` 就行
- **Actions 定时会有延迟**：GitHub 的 cron 不是秒级精确，晚十几分钟很常见
- **收不到邮件**：先看 Actions 日志。若是 `LLM 全部不可用` 不影响发信（会降级成仓库原始描述）；
  若是 SMTP 报错，多半是授权码错了或没开 SMTP 服务
- **想现在就要一封**：仓库 Actions → Run workflow，`force` 默认勾选，会忽略时间闸门立即发
- **换了邮箱**：改 `TO_EMAIL` 这个 Secret 即可，不用改代码
- **Public 仓库的 Actions 免费不限量**；Private 仓库每月有 2000 分钟免费额度，本项目远用不完


## 文件说明

| 文件 | 作用 |
|---|---|
| `daily_digest.py` | 核心脚本：抓数据 → AI 写中文解读 → 渲染日报 → 发邮件 |
| `.github/workflows/daily.yml` | 定时任务定义（每小时唤醒 + 脚本内 9 点闸门） |
| `digests/YYYY-MM-DD.md` | 每天日报的 Markdown 归档（自动生成） |
| `digests/YYYY-MM-DD.html` | 同一份日报的邮件排版版，可直接用浏览器打开 |
| `digests/.sent-YYYY-MM-DD` | 当天已发送标记，用来防止重复发信 |

本地调试（不联网、不发信，只验证排版）：

```bash
python daily_digest.py --demo
```
