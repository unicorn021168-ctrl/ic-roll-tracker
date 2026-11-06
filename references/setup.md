# 安装、调度和迁移

支持SKILL.md、Python 3.10+、文件读写与联网的Agent即可调用。采集只依赖Python标准库；金山同步另需官方kdocs技能、CLI及当前用户登录。

从GitHub导入仓库，或下载ZIP导入技能。WorkBuddy使用“添加技能”的GitHub/上传入口（以当前版本为准）；其他Agent将仓库置于其技能目录。也可直接运行脚本。

数据存于仓库外的固定绝对目录。仓库根config.json只是无持仓信息的默认模板。不要提交运行配置、kdocs.json、历史数据或凭据。

## 初始化

`python "<技能目录>/scripts/track.py" probe --data-dir "<数据目录>"`

`<技能目录>`取当前安装的 `SKILL.md` 所在文件夹；`<数据目录>`取用户选定的仓库外绝对路径。probe初始化配置并检查接口，不写入历史。填写数据目录config.json中的实际持仓，未知手续费保持null。金山文档按[kdocs.md](kdocs.md)初始化或绑定。

## 每次运行

```text
python "<技能目录>/scripts/track.py" collect --slot 10:30 --data-dir "<数据目录>"
python "<技能目录>/scripts/track.py" collect --slot 14:00 --data-dir "<数据目录>"
```

分别由当前Agent/系统调度器于北京时间周一至周五对应时点执行。先核对中金所休市安排；脚本另检查周末、时段和报价新鲜度。只启用一个调度器，迁移时停用旧任务。用户指定WorkBuddy时，不擅自用其他产品调度替代。

本地任务需要电脑、客户端和网络可用。云端须先验证Python、行情、CLI密钥链和持久存储，不可直接引用本地路径。

10:30和14:00是启动时间。执行稍晚时可采集运行时的有效新鲜报价，并保留实际采样时间；收市或行情过期时跳过。同一天同一计划时点首笔有效记录优先，重跑不重复入库。0退出码可能是跳过，status.json的recorded才表示写入；有金山绑定时collect自动同步，结果另见kdocs-status.json。云端失败保留本地样本，可单独sync补传。

## 配置

- holding：实际持仓，留空只追踪全体合约。
- round_trip_cost_yuan_per_lot：一手移仓两腿总手续费与额外滑点，单位元/手，null表示未知。
- max_quote_age_seconds：默认180秒。
- max_quote_skew_seconds：默认60秒，包含期货各腿与指数。
- expiry_overrides：真实合约为键，值含核实后的date（YYYY-MM-DD）及source来源链接；未核实时第三个周五仅作估算。

## 迁移与维护

同机换Agent：重用原有数据目录与配置，在新Agent中重新定位技能目录。换机器：迁移整个数据目录，并在新机重新登录kdocs-cli，不迁移明文凭据。云文档绑定在kdocs.json。即使新机本地库为空，也不清空云端历史；本地HTML只含本地拥有的数据。

report.html为本地报告，history.sqlite为正式历史，raw/保留来源。last_fetch.json可能只是probe，不能直接追加到正式历史。用户确认实际换月后才更新holding；保留过去样本的当时持仓。
