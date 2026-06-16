# 部署到 GitHub Actions（免费、免信用卡、电脑可关机）

把整套 X 列表简报跑在 GitHub 上，每 5 分钟自动轮询：
- 处理你发的 `/check`（最多等约 5 分钟响应）
- 到 7:30 / 12:00 / 14:00 / 23:00 自动推送当时段新推

> 完全免费，不需要信用卡，不需要服务器，电脑关机也照常跑。

---

## 一、需要上传到仓库的文件

把 `x_list_to_tg` 文件夹里这些文件放进一个 GitHub 仓库（结构保持不变）：

```
x_list_to_tg.py        # 核心逻辑
gh_runner.py           # GitHub Actions 入口
state.json             # 去重状态（已建好初始空文件）
tg_offset.json         # Telegram 指令游标（已建好）
.github/workflows/x-list-to-tg.yml   # 定时任务配置
```

> ⚠️ **不要**上传 `config.json`（里面有密钥）。GitHub 上密钥用 Secrets 存（见第三步）。
> `run.sh` / `listen.sh` / `*.plist` 是本地 Mac 用的，GitHub 上用不到，可以不传。

---

## 二、建仓库

1. 登录 https://github.com → 右上角 **+** → **New repository**。
2. 仓库名随便，比如 `x-list-tg`。
3. **建议选 Private（私有）**。
   - 私有仓库每月有 2000 分钟免费额度。我们每 5 分钟跑一次、每次几十秒，**一个月约用 1500 分钟左右，刚好够**。
   - 如果担心超额度，把 workflow 里的 `*/5` 改成 `*/10`（每 10 分钟），`/check` 最多等 10 分钟，额度就很宽裕。
4. 建好后，把第一步那些文件传上去：
   - 网页操作：仓库页 **Add file → Upload files**，把文件拖进去（注意 `.github/workflows/` 这个层级要保留）。
   - 或用 git 命令推上去（你会的话）。

---

## 三、设置密钥（Secrets）

仓库页 → **Settings → Secrets and variables → Actions → New repository secret**，依次添加这几个：

| Name（名字，照抄） | Value（值，填你的） |
|---|---|
| `RSS_URL` | 你的 RSS.app feed 链接 |
| `GEMINI_API_KEY` | 你的 Gemini key |
| `GEMINI_MODEL` | `gemini-3.5-flash` |
| `TELEGRAM_BOT_TOKEN` | 你的 Bot token |
| `TELEGRAM_CHAT_ID` | 你的 chat_id（数字） |

> 这些值就是你本地 `config.json` 里那几个。Secrets 是加密的，别人看不到，比明文放仓库安全。

---

## 四、开启并测试

1. 仓库页 → **Actions** 标签。第一次进可能要点 **"I understand my workflows, enable them"**。
2. 左侧选 **x-list-to-tg** → 右侧 **Run workflow**（手动触发一次测试，不用等整点）。
3. 点进这次运行，看日志：
   - 绿色 ✅ = 成功跑完。
   - 如果当前不在推送窗口、也没 `/check`，它会正常跑完但什么都不发（正常）。
4. **测 `/check`**：在 TG 给 bot 发 `/check`，然后等下一次轮询（最多 5 分钟），或再手动 **Run workflow** 一次，应该收到简报。

---

## 五、验证去重持久化

GitHub Actions 每次跑完是"用完即弃"的，所以脚本会把 `state.json` / `tg_offset.json` **提交回仓库**来记住"看过哪些推"。
- 跑过几次后，去仓库看 `state.json`，里面 `seen_ids` 应该有内容了 → 说明去重在持久化，不会重复推。
- 如果看到仓库有 "update state [skip ci]" 的自动提交，那是正常的（脚本在存状态）。

---

## 六、日常管理

- **改推送时间**：编辑 `gh_runner.py` 里的 `PUSH_TIMES`（北京时间的时:分），提交即可。
- **改轮询频率 / `/check` 响应速度**：编辑 workflow 里的 `cron: "*/5 ..."`（5 分钟）。越小越快但越费额度。
- **暂停**：Actions 页 → 右上 **... → Disable workflow**。
- **看历史**：Actions 页能看到每次运行的日志，排查很方便。

---

## 注意事项

- **额度**：私有仓库每月 2000 分钟。每 5 分钟跑一次大约够用；不放心就改每 10 分钟，或用公开仓库（无限额度，但仓库代码公开——**密钥在 Secrets 里不会泄露**，只是代码可见）。
- **定时不绝对准点**：GitHub 的 cron 在高峰期可能延迟几分钟才触发，所以 7:30 的推送可能 7:32、7:35 才到。对"定点简报"无所谓。
- **`/check` 不是秒回**：最多等一个轮询周期（5 或 10 分钟）。这是免费方案的代价。

---

*Drafted with Dia*
