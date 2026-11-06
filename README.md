# IC Roll Tracker · IC贴水与移仓监测

可在WorkBuddy、Codex或其他支持SKILL.md的Agent中使用的技能，面向长期持有中证500股指期货多头、希望方便比较移仓价格的用户。

## 功能

- 每交易日北京时间10:30、14:00追踪实际IC合约和中证500指数。
- 记录价格、贴水点数、贴水率、年化贴水率，生成本地HTML曲线。
- 按“现持买一−远月卖一”比较移仓价差，成本未知不冒充净收益。
- 使用官方kdocs技能/CLI同步《IC贴水数据监测》在线表格及曲线。
- 重跑去重；休市、旧行情、不同步行情不进入正式历史；迁移Agent保留云端历史。

## 安装

让Agent从本GitHub仓库安装技能，或下载ZIP导入。仓库根目录SKILL.md是入口。Python 3.10+，采集部分没有第三方Python依赖；金山同步另需官方kdocs技能、CLI和当前用户登录。

可发送给Agent：

> 安装并使用本仓库的ic-roll-tracker技能。先读取SKILL.md，把数据存到我指定的仓库外目录，配置我确认的持仓。使用免费行情，每交易日北京时间10:30、14:00采集；通过官方kdocs技能初始化或绑定《IC贴水数据监测》，采集后同步。先验证接口和文档读写，再配置当前Agent的定时任务。不要重复创建已有文档或调度。

详见[安装与迁移](references/setup.md)、[金山文档同步](references/kdocs.md)。技能不后台常驻，调度由Agent或系统负责。

## 命令

```text
python "<技能目录>/scripts/track.py" probe --data-dir "<数据目录>"
python "<技能目录>/scripts/sync_kdocs.py" init --data-dir "<数据目录>"
python "<技能目录>/scripts/track.py" collect --slot 10:30 --data-dir "<数据目录>"
python "<技能目录>/scripts/track.py" collect --slot 14:00 --data-dir "<数据目录>"
python "<技能目录>/scripts/sync_kdocs.py" sync --data-dir "<数据目录>"
python -m unittest discover -s "<技能目录>/tests" -v
```

`<技能目录>`是本次安装中 `SKILL.md` 所在的文件夹；`<数据目录>`是用户在当前设备上选定的仓库外绝对路径。两者都不是仓库内的固定位置，也无需在技能升级时移动数据。首次probe建立运行config.json，holding默认留空，填写实际持仓后才进行移仓比较。有kdocs.json绑定后collect自动同步，无绑定仍可独立本地使用。个人配置、数据、文档标识与凭据不得上传GitHub。

## 口径与边界

正数表示贴水；年化=`(指数−期货)/指数×365/剩余自然日`。未核实到期日时按第三个周五估算，明确标记，可能有节假日调整。区间年化移仓价差仅描述期限价差，不是保证收益；多头仍有指数涨跌风险。

新浪公开免费行情可能延迟或改变，不保证长期可用；行情免费不代表Agent运行积分免费。没有经过回测的最佳移仓阈值，见[回测要求](references/research.md)。不连接交易账户，不下单。

曲线从有效采样开始积累。周末初始化只展示明确标记的参考行情，不造历史。换Agent时停用旧调度，重用数据目录或绑定原云文档。
