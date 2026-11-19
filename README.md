# 差旅申请中的规则例外与凭据回查

本地文本工作区用于保存费用和行程事实抽取相关的合成记录，并提供基础规范化和词项查看能力。

## 环境

需要 Python 3.11 或更新版本。运行程序仅使用 Python 标准库。测试依赖固定为 pytest 8.3.4，可执行 `python -m pip install -r requirements-dev.txt` 安装。

## 运行

运行 `PYTHONPATH=src python -m nlp_travel_policy_interpreter.desk_cli` 查看两位同行人的房费申请及已经支付的份额。金额为分。先扣除主办方支付额，已结报的份额仍占用相应房晚的自付款余额。

从项目根目录执行 `PYTHONPATH=src python -m nlp_travel_policy_interpreter.cli ingest fixtures/records.json` 导入样例，随后执行 `PYTHONPATH=src python -m nlp_travel_policy_interpreter.cli list` 查看记录。工作数据写入 `var/records.json`。业务查询使用 fixtures/business.json 中的初始资料，约定见 fixtures/business-contract.md。执行 PYTHONPATH=src python -m nlp_travel_policy_interpreter.cli business 可查看当前业务目录结果。

## 按人及房晚核对

执行 `PYTHONPATH=src python -m nlp_travel_policy_interpreter.audit_cli` 生成完整审核报告，把中文备注（如“三号那晚主办方承担”）解析到酒店账单的具体房晚，结合行程更新重算每人每房晚的实际自付款：

- `night_reference`：房晚与备注原话的对应关系，可从金额查回原话与来源；
- `itinerary`：每位申请人在每个房晚的状态（留宿/已返程/未提及）；
- `ledger`：按 申请人 × 票据 × 房晚 列出原始分摊、主办方承担、已结报（保留原始审批依据）、实际自付、可申报余额与待核金额；
- `claims`：逐条申请结论。已支付申请不被同行人资料更正顺手改动；份额受影响的申请给出 `review_reasons` 提示复核；主办方已付或已结报份额不可能再次通过；
- `change_fee_reviews`：紧急改签等例外审批，只覆盖 `exception_events` 中列明的事件、申请人、费用类型与金额上限，缺票据进入待核；
- `pending`：材料只足以确认部分房晚时，用 `--unconfirmed 票据:房晚`（可重复）把相应余额隔离为待核，不受影响的费用照常办理。

改签事件、票据与费用上限在 fixtures/work.json 的 `exception_events` 与 `change_fees` 中维护。

样例为本地合成文本。记录包含来源、修订号和原文，当前工作区对空白做统一处理，并提供字符及英文词项切分。

## 测试

执行 `python -m pytest -q` 检查规范化、记录持久化和来源筛选。
