# 数据质量门禁

## 规则与发布

采集默认写入 data/live.candidate.json，任何模块异常均退出1并保留候选 errors。校验返回0才通过 os.replace 原子替换正式文件（两者须在同一文件系统）。Actions 仅在定时和手动事件更新；PR/push只运行离线测试。并发更新串行执行，推送冲突时失败，不强制覆盖远端。

|范围|阻断 ERROR|告警 WARNING|
|---|---|---|
|整体|坏JSON、根对象错误；模块缺失/null或采集errors；上海时间updated_at无效、超过24小时或未来超过5分钟|无|
|treasury|日期无效/未来/超过14自然日；收益率非有限数或超出[-5,30]%；利差与cn10-cn2差值超过0.0002个百分点；历史为空、乱序/重复、数值异常、末条日期或数值不一致|日期超过3自然日|
|indices|来源不在白名单；指定五个指数缺失/重复；价格非正有限数；涨跌幅非有限数或超过±100%|新浪财经回退；报价时间不可验证|
|industries|来源不在白名单；up不是8条/down不是5条；组内名称或代码缺失/重复；涨跌幅异常；up非降序/down非升序|同花顺回退；报价时间不可验证|

来源白名单是东财/新浪财经、东财/同花顺。代码不接受空白或字符串nan/none，必须来自真实提供方。当前同花顺分支固定输出空代码，会被阻断；不能根据名称虚构代码。up/down表示排名两端，不强制涨跌符号。

国债采用自然日阈值，没有交易日历；3天告警、14天阻断是操作规则，不等于精确交易日新鲜度。updated_at仅代表采集时间，指数与行业没有上游报价时间，因此始终记录QUOTE_TIME_UNVERIFIED。门禁不证明第三方行情真实正确。

## 测试入口与依赖

Python >=3.11,<3.14，Actions使用3.13。直接采集依赖为akshare>=1.17,<2、pandas>=2.2,<3；不是全依赖锁定，每次Actions用pip freeze记录实际解析版本。校验和测试只用标准库。

测试：python -m unittest discover -s tests -v

安装：python -m pip install -r requirements.txt

采集：python update_data.py --output data/live.candidate.json

校验发布：python quality_gate.py data/live.candidate.json --publish data/live.json

存量审计：python quality_gate.py data/live.json --now 2026-10-10T12:00:00+08:00

本次离线6个测试方法通过，包括11种字段异常、逐模块抓取失败、坏JSON、警告放行、失败保留正式文件与成功原子发布。未联网执行AkShare采集或安装采集依赖，实时接口可用性未验证。

## 当前快照

updated_at=2026-10-10 11:39:09，读取时blob SHA为ff33a65764bcf9998b178ab3d1ac6cc627d2c50f。固定审计时点为2026-10-10 12:00:00 +08:00。treasury.date=2026-10-09通过。SOURCE_FALLBACK告警2项、QUOTE_TIME_UNVERIFIED告警2项；up的8条及down的5条均触发INDUSTRY_CODE。合计13 ERROR、4 WARNING，退出1。正式数据保留原样。结论仅对应上述快照和时点，实际运行重新计算时间规则。

## 日志与验收

quality_rule字段：event、checked_at（含时区）、candidate、level、rule、section、field、value、message。

quality_summary字段：event、checked_at、errors、warnings、status（passed/blocked）。

fetch_error字段：event、section、level、exception_type、message；候选errors保存逐模块失败原因。

Actions保留quality.jsonl、失败候选和dependency-versions.txt，期限30天。采集失败时校验不执行，以采集日志和候选errors作为证据，不能将缺少校验报告解释为通过。

验收：离线测试全通过；任何模块抓取异常退出非零；空代码、缺指数、无效数值、坏JSON均阻断；失败时正式文件逐字节不变；无阻断的告警允许发布；成功发布与候选内容一致且候选被移动；失败步骤无法进入commit/push；PR测试不抓取网络行情、不提交数据；Actions保存失败证据及实际依赖版本。
