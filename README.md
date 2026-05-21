# Aquabridge BDI 一键抓取脚本

每天运行一次，自动从阿里云企业邮拉取 Baltic Exchange 报告 → 解析 → 写入 Excel/CSV + 输出 JSON。

## 一次性安装

```bash
cd ~/Downloads/bdi_pipeline
pip3 install pdfplumber
```

## 日常使用

```bash
cd ~/Downloads/bdi_pipeline
python3 run_bdi.py
```

默认会抓最近 7 天里"还没入库"的 BDI 邮件并写入。

## 常用参数

```bash
# 只处理指定日期
python3 run_bdi.py --date 2026-04-22

# 回溯 30 天（首次跑或补漏用）
python3 run_bdi.py --days 30

# 强制重新解析（覆盖已有日期）
python3 run_bdi.py --date 2026-04-22 --force
```

## 输出

```
bdi_pipeline/
├── baltic_exchange_index_history.csv   ← 主数据文件，每天追加 1 行
├── output/
│   ├── json/
│   │   ├── bdi_2026-04-22.json         ← 当日完整解析结果（供 Shane 接口 POST）
│   │   └── bdi_2026-04-23.json
│   └── attachments/
│       └── 2026-04-22/                 ← 当日 PDF 附件备份
│           ├── Baltic Dry Index.pdf
│           └── ...
```

## JSON 字段示例

```json
{
  "date": "2026-04-22",
  "BDI": 2675, "BCI": 4356, "BPI": 1971, "BSI": 1484, "BHSI": 781,
  "C5TC": 39505, "P5TC": 17744, "S11TC": 18760, "HS7TC": 14050,
  "C3": 33.268, "C5": 13.25, "C7": 18.875,
  "P1A_82": 14530, "P2A_82": 25837, "P3A_82": 18592, ...
  "S1B": 17775, "S1C": 23964, "S2": 18625, ...
  "HS1_38": 7914, "HS2_38": 10079, ...
  "updated_at": "2026-04-26 11:30:15"
}
```

## 自动化（可选）

如果想"每天早上 9:30 自动跑"，把以下加到 macOS 定时任务（终端执行）：

```bash
( crontab -l 2>/dev/null; echo "30 9 * * * cd ~/Downloads/bdi_pipeline && /usr/bin/python3 run_bdi.py >> run.log 2>&1" ) | crontab -
```

取消：`crontab -e` 删除那一行。

## 修改邮箱密码

打开 `run_bdi.py`，改 `IMAP_PWD = "..."` 那行即可。

## 同步到 navgreen 接口

解析出的 JSON 可原样推送到数据接口（请求体字段与 `bdi_<date>.json` 一一对应）：

```bash
python sync_bdi.py --date 2026-05-20      # 同步指定日期
python sync_bdi.py --last 3               # 同步最近 3 个「完整」JSON（默认）
python sync_bdi.py --all                  # 同步全部
python sync_bdi.py --last 5 --allow-incomplete   # 连未完成（BDI 为空）的也推
```

默认只推送 `BDI` 已到齐的「完整」记录 —— 最近一交易日的干散指数要等次日 BRS Daily
才齐，当天会被跳过，次日重跑自动补推。接口按 `date` upsert，重复推送幂等、安全。
接口地址可用环境变量 `SYNC_URL` 覆盖。

## 每日自动化（GitHub Actions）

`.github/workflows/daily-bdi.yml` 每天北京时间 23:00 自动执行：
抓取 BRS 邮件 → 解析 → `sync_bdi.py` 推送 → 把更新后的 CSV/JSON 提交回仓库。
也可在仓库 Actions 页 `Run workflow` 手动触发。

**一次性配置：**

1. 推送到私有 GitHub 仓库（IMAP 密码已在 git 历史中，务必用 **private**）。
2. 配置 Secrets（仓库 Settings → Secrets and variables → Actions）：
   - `IMAP_PWD` — 阿里云三方客户端专用密码（必需）
   - `SYNC_URL` — 同步接口地址（可选，不设则用代码默认值）
3. **接入自建 runner**：阿里云邮箱对境外 IP 的 IMAP 登录会风控，GitHub 托管 runner
   多在境外，故必须用国内常开机器做 self-hosted runner。
   在该机器上：仓库 Settings → Actions → Runners → New self-hosted runner，
   按页面指引 `./config.sh --url <repo> --token <token>` 后
   `./run.sh`（或 `./svc.sh install && ./svc.sh start` 装成后台服务）。
   ⚠️ 该机器须在每天 23:00（北京）保持开机在线，否则当天定时任务不执行。

凭据通过环境变量 `IMAP_PWD` / `IMAP_USER` / `IMAP_HOST` / `IMAP_PORT` 注入，
未设置时回退到 `run_bdi.py` 内的默认值（方便本地直接运行）。

## 故障排查

- **`socket.gaierror: Name or service not known`** → 没联网或 DNS 故障
- **`imaplib.IMAP4.error: Login failed`** → 应用专用密码错或被禁用
- **某些字段没解析出来** → Baltic Exchange 改了 PDF 模板，给 Claude 发新样本即可
- **Actions 里 IMAP 登录失败/超时** → runner 不在国内或被风控，确认用的是国内 self-hosted runner

