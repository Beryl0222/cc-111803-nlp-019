import json
from pathlib import Path

import pytest

from nlp_travel_policy_interpreter.audit import (
    build_audit_report,
    build_ledger,
    identify_speaker,
    parse_itinerary,
    resolve_nights,
    review_change_fee,
    build_exception_policy,
)

FIXTURE = Path(__file__).parents[1] / 'fixtures'


@pytest.fixture()
def facts():
    return json.loads((FIXTURE / 'business.json').read_text(encoding='utf-8'))


@pytest.fixture()
def records():
    return json.loads((FIXTURE / 'records.json').read_text(encoding='utf-8'))


@pytest.fixture()
def case():
    return json.loads((FIXTURE / 'work.json').read_text(encoding='utf-8'))


def test_note_night_resolves_to_bill_night(records):
    # “三号那晚”“林青三号返程”都必须落到酒店账单的 05-03 房晚
    nights = ['05-02', '05-03']
    assert resolve_nights(records[0]['text'], nights) == ['05-03', '05-02']
    assert resolve_nights(records[1]['text'], nights) == ['05-03']


def test_speaker_identified_from_itinerary_and_hotel_bill(facts, records):
    speaker = identify_speaker(records, facts)
    assert speaker['speaker'] == '林青'
    assert speaker['confidence'] == '核对一致'
    # 证据保留原话与来源，可回查
    assert any(item['record_id'] == 'trip-6-update' for item in speaker['evidence'])


def test_itinerary_marks_only_the_early_returnee(facts, records):
    parsed = parse_itinerary(records, facts)
    statuses = parsed['statuses']
    assert statuses['林青']['05-03'] == '已返程'
    assert statuses['方珏']['05-03'] == '留宿'
    assert statuses['林青']['05-02'] == '未提及'


def test_ledger_reallocates_night_three_to_actual_occupant(facts, records):
    ledger = build_ledger(facts, records)
    night3 = {(row['applicant']): row for row in ledger if row['night'] == '05-03'}
    # 林青三号已返程：不参与 05-03 分摊
    assert night3['林青']['stayed'] is False
    assert night3['林青']['gross_share'] == 0
    assert night3['林青']['net_self_pay'] == 0
    # 方珏单独承担整房 68000 分，但主办方已付全额，自付为 0
    assert night3['方珏']['stayed'] is True
    assert night3['方珏']['gross_share'] == 68000
    assert night3['方珏']['host_share'] == 68000
    assert night3['方珏']['net_self_pay'] == 0
    assert any('已返程' in reason for reason in night3['林青']['review_reasons'])


def test_night_two_keeps_equal_split_and_posted_share(facts, records):
    ledger = build_ledger(facts, records)
    night2 = {row['applicant']: row for row in ledger if row['night'] == '05-02'}
    assert night2['林青']['gross_share'] == 34000
    assert night2['方珏']['gross_share'] == 34000
    # 已结报 34000 保留依据，余额为 0；方珏仍有 34000 自付余额
    assert night2['林青']['posted_cents'] == 34000
    assert night2['林青']['balance_cents'] == 0
    assert night2['方珏']['balance_cents'] == 34000
    assert night2['林青']['posted_refs'][0]['paid'] is True


def test_host_paid_share_can_never_be_claimed_again(facts, records):
    # 即使按更正前两人分摊，方珏 05-03 的申请也必须被拒绝
    report = build_audit_report(facts, records, claims=[
        {'id': 'TR-3', 'applicant': '方珏', 'receipt': 'H-91',
         'night': '05-03', 'cents': 34000},
    ])
    tr3 = report['claims'][0]
    assert tr3['state'] == '无足额自付款'
    assert tr3['host_share'] == 68000


def test_claim_review_preserves_posted_approval_and_flags_review(facts, records, case):
    report = build_audit_report(facts, records, claims=case['claims'])
    by_id = {item['id']: item for item in report['claims']}
    # 林青 05-02 已支付：批准不被顺手改动
    assert by_id['TR-1']['state'] == '已支付'
    assert by_id['TR-1']['approved_cents'] == 34000
    assert by_id['TR-1']['posted_cents'] == 34000
    # 方珏 05-02 不受影响，正常可报销
    assert by_id['TR-2']['state'] == '可报销'
    assert by_id['TR-2']['approved_cents'] == 34000
    # 方珏 05-03 无自付余额
    assert by_id['TR-3']['state'] == '无足额自付款'


def test_correction_flags_affected_application_without_touching_peer(facts, records, case):
    # 若林青在 05-03 曾有未支付申请，行程更正后必须被标复核/拒绝，
    # 而方珏既有的 05-02 批准范围不变
    claims = case['claims'] + [
        {'id': 'TR-4', 'applicant': '林青', 'receipt': 'H-91',
         'night': '05-03', 'cents': 34000},
    ]
    report = build_audit_report(facts, records, claims=claims)
    by_id = {item['id']: item for item in report['claims']}
    assert by_id['TR-4']['state'] == '无足额自付款'
    assert any('已返程' in reason for reason in by_id['TR-4']['review_reasons'])
    assert by_id['TR-1']['state'] == '已支付'
    assert by_id['TR-2']['state'] == '可报销'


def test_exception_approval_covers_only_event_and_listed_applicant(facts, records, case):
    policy = build_exception_policy(case['exception_events'])
    covered, excluded, no_receipt = [
        review_change_fee(fee, policy, records) for fee in case['change_fees']
    ]
    assert covered['state'] == '例外覆盖'
    assert covered['approved_cents'] == 46000
    # 方珏未改行程，不在例外名单
    assert excluded['state'] == '例外不覆盖'
    assert excluded['approved_cents'] == 0
    # 缺票据先待核，不放款
    assert no_receipt['state'] == '待核'
    assert no_receipt['approved_cents'] == 0


def test_exception_cap_limits_coverage(facts, records):
    policy = build_exception_policy([
        {'event': '会议提前结束紧急返程', 'applicants': ['林青'],
         'fee_types': ['机票改签'], 'cents_cap': 50000},
    ])
    result = review_change_fee(
        {'id': 'FEE-X', 'applicant': '林青', 'event': '会议提前结束紧急返程',
         'fee_type': '机票改签', 'receipt': 'A-99', 'cents': 72000},
        policy, records)
    assert result['state'] == '部分覆盖'
    assert result['approved_cents'] == 50000


def test_partial_confirmation_allows_unaffected_claims_and_quarantines_rest(facts, records):
    # 构造一份 05-03 存在自付余额的资料，再把 05-03 列入待核：
    # 05-02 不受影响照常办理，05-03 的自付余额全部进待核且可查回原话
    partial_facts = json.loads(json.dumps(facts))
    partial_facts['host_paid'] = []
    report = build_audit_report(
        partial_facts, records,
        claims=[
            {'id': 'TR-2', 'applicant': '方珏', 'receipt': 'H-91',
             'night': '05-02', 'cents': 34000},
            {'id': 'TR-3', 'applicant': '方珏', 'receipt': 'H-91',
             'night': '05-03', 'cents': 68000},
        ],
        unconfirmed_nights=[('H-91', '05-03')],
    )
    by_id = {item['id']: item for item in report['claims']}
    assert by_id['TR-2']['state'] == '可报销'
    assert by_id['TR-3']['state'] == '待核'
    pending = {(p['receipt'], p['night']): p for p in report['pending']}
    assert pending[('H-91', '05-03')]['pending_cents'] == 68000
    # 待核金额可查回原话与票据
    assert pending[('H-91', '05-03')]['evidence']


def test_every_money_row_links_back_to_source_text(facts, records):
    report = build_audit_report(facts, records)
    for row in report['ledger']:
        for note in row['evidence']:
            assert set(note) >= {'record_id', 'source', 'revision', 'quote'}
    # “三号这晚”的房晚索引直接引用备注原话
    ref3 = report['night_reference']['05-03']
    quotes = ''.join(item['quote'] for item in ref3)
    assert '三号那晚主办方承担' in quotes
    assert '林青三号返程' in quotes


def test_posted_claim_with_changed_share_is_flagged_not_altered(facts, records):
    # 林青 05-03 已获结报（结报总额不变），但行程更正后她该晚已返程、
    # 个人份额发生变化：批准金额保留，申请被明确标为需复核
    shifted = json.loads(json.dumps(facts))
    shifted['reimbursements'].append(
        {'applicant': '林青', 'receipt': 'H-91', 'night': '05-03',
         'cents': 34000, 'paid': True})
    shifted['host_paid'] = []
    report = build_audit_report(shifted, records, claims=[
        {'id': 'TR-4', 'applicant': '林青', 'receipt': 'H-91',
         'night': '05-03', 'cents': 34000},
        {'id': 'TR-2', 'applicant': '方珏', 'receipt': 'H-91',
         'night': '05-02', 'cents': 34000},
    ])
    by_id = {item['id']: item for item in report['claims']}
    assert by_id['TR-4']['state'] == '已支付'
    assert by_id['TR-4']['approved_cents'] == 34000
    assert by_id['TR-4']['share_changed'] is False  # 她已不住宿，不再参与分摊
    assert any('已返程' in reason for reason in by_id['TR-4']['review_reasons'])
    # 同行人的批准范围不被顺手改动
    assert by_id['TR-2']['state'] == '可报销'


def test_applicant_summary_shows_share_change_and_totals(facts, records):
    report = build_audit_report(facts, records)
    summary = report['applicant_summary']
    # 结报总额视角：林青只剩已支付的 34000，无新增余额
    assert summary['林青']['posted_total'] == 34000
    assert summary['林青']['balance_total'] == 0
    # 方珏 05-02 自付 34000；05-03 由主办方承担
    assert summary['方珏']['balance_total'] == 34000
    assert summary['方珏']['host_covered_total'] == 68000
    assert 'H-91/05-03' in summary['林青']['review_nights']
