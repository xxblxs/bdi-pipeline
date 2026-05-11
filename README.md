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

## 故障排查

- **`socket.gaierror: Name or service not known`** → 没联网或 DNS 故障
- **`imaplib.IMAP4.error: Login failed`** → 应用专用密码错或被禁用
- **某些字段没解析出来** → Baltic Exchange 改了 PDF 模板，给 Claude 发新样本即可

