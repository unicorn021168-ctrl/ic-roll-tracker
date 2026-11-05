# 金山文档同步

仅在用户要求云端写入时初始化；不改变分享权限，不将文档设为公开。

先读取已安装的官方 kdocs SKILL.md。首次使用检查 kdocs-cli version；需要认证时运行 kdocs-cli auth login，凭据存入系统密钥链。不要复制Token到脚本、配置、日志或GitHub。CLI路径由PATH解析，不依赖特定Agent。

## 创建或绑定

新建：`python scripts/sync_kdocs.py init --data-dir "DATA_DIR"`。

查重后创建《IC贴水数据监测》xlsx，立即把 file_id、link_url、enabled 保存到 DATA_DIR/kdocs.json，后续失败不重复新建。已有同名文件但无本地绑定时，先用官方kdocs工具确认类型、归属和结构，再写入本地绑定，不猜ID。

同步：`python scripts/sync_kdocs.py sync --data-dir "DATA_DIR"`。

成功后展示 kdocs-status.json 的 link_url；若响应缺少链接，按官方kdocs参考调用get_file_link获取并保存。绑定后track.py collect自动同步。

## 表格

- 监测总览：持仓、采样状态、计算口径与最后接口参考报价；参考报价不进入曲线。
- 合约数据：每个交易日/时点/真实合约一条，含指数、盘口、源时间、到期口径。
- 移仓数据：每个交易日/时点/合约对一条。
- 价格曲线、贴水曲线、年化贴水曲线、移仓价差曲线：按真实合约/合约对绘图；无有效历史时只显示等待表头，第一笔样本后创建图表，不填模拟值。

百分比列用数值百分数，例如10表示10%，不要另套百分比格式显示成1000%。

图表 sourceAddress 必须包含对应工作表名称（如 `'贴水曲线'!A1:E5`），不能只传 A1:E5，以免被解析为当前活动表。每次同步后回读 seriesDetails，逐项核对系列名称、横轴和实际数值；只找到同名标题不能判定图表同步成功。

只同步本地正式SQLite样本，旧probe不进入历史。按记录键去重；同键不同值停止并保留云端原值，不覆盖抹平冲突。迁移后保留云端历史，不以空本地库清空云端。只写变化单元格，并回读核对。个人备注请放另建工作表，不修改技能管理区域表头。只启用一个采集调度器。

## 异常与维护

本地成功、云端失败时保留本地库；修复后运行sync补传，不用当前采样冒充原时点。权限不足立即停止，不绕过；429按返回恢复时间停止所有调用；脚本不自动重试。未知命令按官方技能更新CLI。满足官方技能“问题反馈”条件时执行kdocs-cli feedback并展示真实反馈链接。

脚本通过CLI stdin传完整UTF-8 JSON，不使用Token参数、不直连私有接口。修改接口前按官方技能索引读取 drive/search.md、drive/create_and_upload.md、sheet/worksheet.md、sheet/data.md，对照真实返回。
