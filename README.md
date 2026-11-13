# GitHub 趣项目日报 ☁️

一个跑在 **GitHub Actions 云端** 的定时任务：每天早上 9 点（北京时间）
自动抓取近 7 天新崛起的热门开源项目，整理成中文日报发到你的邮箱。
**你的电脑关不关机完全不影响。**

## 它做什么

1. 调 GitHub 官方 Search API，找「7 天内创建、按 Star 排序」的新仓库（比 trending 榜更聚焦新项目）
2. 自动过滤 nodejs/node 这类老牌常青项目，选出前 8 个
3. 按关键词给每个项目打方向标签：`AI / Agent`、`开发工具 / 效率`、`好玩 / 有趣`
4. 把日报发到你的邮箱（同时归档到仓库 `digests/` 目录，随时回看）

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

## 常见问题

- **Actions 定时会有几分钟延迟**：GitHub 的 cron 不是秒级精确，晚 5~30 分钟属正常
- **收不到邮件**：先检查 Actions 运行日志里 SMTP 报错，多半是授权码错了或没开 SMTP 服务
- **想让某天多发一次**：仓库 Actions 页面手动点 Run workflow 即可
- **Public 仓库的 Actions 免费不限量**；Private 仓库每月有 2000 分钟免费额度，本项目每天跑一次远用不完

## 文件说明

| 文件 | 作用 |
|---|---|
| `daily_digest.py` | 核心脚本：抓数据 → 生成中文日报 → 发邮件 |
| `.github/workflows/daily.yml` | GitHub Actions 定时任务定义（UTC 1:00 = 北京 9:00） |
| `digests/` | 每天日报的 Markdown 归档（自动生成） |
