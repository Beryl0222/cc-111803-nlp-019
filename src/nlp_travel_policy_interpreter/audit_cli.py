import argparse
import json
from pathlib import Path

from .audit import build_audit_report


def _load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='按人及房晚核对自付款，关联备注、酒店明细与同行行程')
    parser.add_argument('--facts', type=Path, default=Path('fixtures/business.json'),
                        help='酒店明细、主办方支付与已结报资料')
    parser.add_argument('--records', type=Path, default=Path('fixtures/records.json'),
                        help='中文备注与行程更新原始记录')
    parser.add_argument('--case', type=Path, default=Path('fixtures/work.json'),
                        help='待审报销申请（claims）与可选改签费用/例外事件')
    parser.add_argument('--unconfirmed', action='append', default=[],
                        help='待核房晚，格式 票据:房晚，如 H-91:05-03；可重复')
    args = parser.parse_args(argv)

    facts = _load_json(args.facts)
    records = _load_json(args.records)
    case = _load_json(args.case) if args.case.exists() else {}
    unconfirmed = [tuple(item.split(':', 1)) for item in args.unconfirmed]
    report = build_audit_report(
        facts,
        records,
        claims=case.get('claims', []),
        change_fees=case.get('change_fees', []),
        exception_events=case.get('exception_events', []),
        unconfirmed_nights=unconfirmed,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
