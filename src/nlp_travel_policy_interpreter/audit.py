"""差旅费用核对：把中文备注、酒店明细与同行行程关联到每人每房晚。

设计原则（对应业务约定 fixtures/business-contract.md 与审核口径）：

* 每条金额结论都带 source 引用，可从余额/待核金额查回备注原话、票据号与例外理由；
* 主办方支付额先按实际住宿人分配，任何个人不得再次申报该份额；
* 已结报份额保留原始审批依据，更正行程只产生“需复核”标记，不改动既批结果；
* 资料只足以确认部分房晚时，未确认部分进入待核，已确认部分照常办理；
* 紧急改签属例外审批，批准理由只覆盖同一事件关联的费用。

全部使用标准库；金额单位为分。
"""
from collections import OrderedDict, defaultdict

# 中文数字（备注只出现“一、两、二、三”等小额数字）
_CN_DIGITS = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
              '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}


def _raw_note(record):
    """保留备注原文（业务约定要求查询结果不得替代原始来源）。"""
    return {
        'record_id': record['record_id'],
        'source': record['source'],
        'revision': record['revision'],
        'quote': record['text'],
    }


def resolve_nights(text, hotel_nights):
    """把备注里的日期/“两晚”表述解析为酒店明细中的房晚键（如 '05-03'）。

    酒店明细按 (receipt, night) 给定时，传入的 night 形如 'MM-DD'。
    '三号/三日/3号' 匹配同一日；'两晚' 等全量表述覆盖全部房晚。
    """
    nights = list(OrderedDict((night, None) for night in hotel_nights).keys())
    matched = []
    for raw_day, unit in _scan_day_mentions(text):
        day = _CN_DIGITS.get(raw_day)
        if day is None and str(raw_day).isdigit():
            day = int(raw_day)
        if day is None:
            continue
        suffix = f'-{day:02d}'
        for night in nights:
            if night.endswith(suffix) and night not in matched:
                matched.append(night)
    # “合住两晚/住三晚”等总数表述：数字等于房晚总数时覆盖全部房晚
    if '晚' in text:
        for mark in _CN_DIGITS:
            if f'{mark}晚' in text and _CN_DIGITS[mark] == len(nights):
                for night in nights:
                    if night not in matched:
                        matched.append(night)
    return matched


def _scan_day_mentions(text):
    mentions = []
    for index, char in enumerate(text):
        day = None
        if char in _CN_DIGITS:
            day = char
        elif char.isdigit():
            start = index
            while start > 0 and text[start - 1].isdigit():
                start -= 1
            day = text[start:index + 1]
        if day is not None:
            next_char = text[index + 1] if index + 1 < len(text) else ''
            if next_char in ('号', '日'):
                mentions.append((day, next_char))
    return mentions


def identify_speaker(records, facts):
    """确认留言人身份。

    旅行申请以第一人称书写（“我和方珏合住”），而行程更新写明“林青三号返程”，
    酒店票据又按整房开给林青；两方证据指向同一人时给出“核对一致”，
    否则只能标“需人工确认”，由审核员回查原话。
    """
    applicants = set(facts['applicants'])
    evidence = []
    payers = {row['paid_by'] for row in facts['hotel']}
    returnees, hosts = set(), []
    for record in records:
        text = record['text']
        if record['source'] == 'itinerary-update':
            for applicant in applicants:
                if applicant in text and '返程' in text.split(applicant, 1)[1][:6]:
                    returnees.add(applicant)
                    evidence.append(_raw_note(record))
        if '主办方承担' in text:
            note = _raw_note(record)
            if note not in hosts:
                hosts.append(note)
        if record['source'] == 'receipt-note':
            evidence.append(_raw_note(record))
    candidates = (payers | returnees) & applicants
    if len(candidates) == 1:
        speaker = next(iter(candidates))
        confidence = '核对一致'
    else:
        speaker = None
        confidence = '需人工确认'
    return {
        'speaker': speaker,
        'confidence': confidence,
        'evidence': evidence,
        'hotel_payers': sorted(payers),
        'early_returnees': sorted(returnees),
        'host_payment_notes': hosts,
    }


def parse_itinerary(records, facts):
    """从行程更新中解析每位申请人在每个房晚的实际状态。

    返回 status[applicant][night] ∈ {'已返程', '留宿', '未提及'}，
    以及支持该结论的备注引用；房晚键通过 resolve_nights 与酒店明细对齐。
    """
    hotel_nights = sorted({row['night'] for row in facts['hotel']})
    statuses = {name: {night: '未提及' for night in hotel_nights}
                for name in facts['applicants']}
    evidence = []
    for record in records:
        if record['source'] != 'itinerary-update':
            continue
        text = record['text']
        note_nights = resolve_nights(text, hotel_nights)
        evidence.append(_raw_note(record))
        for clause in _split_clauses(text):
            clause_nights = resolve_nights(clause, hotel_nights) or note_nights
            for applicant in facts['applicants']:
                if applicant not in clause:
                    continue
                tail = clause.split(applicant, 1)[1]
                if '返程' in tail:
                    for night in clause_nights:
                        statuses[applicant][night] = '已返程'
                elif '留宿' in tail or '仍留' in tail:
                    for night in clause_nights:
                        statuses[applicant][night] = '留宿'
    return {'statuses': statuses, 'evidence': evidence}


def _split_clauses(text):
    for separator in '，,；;。':
        text = text.replace(separator, '|')
    return [part.strip() for part in text.split('|') if part.strip()]


def note_covers(record, night, hotel_nights):
    """判断一条备注是否关联某个房晚（用于证据回链）。"""
    return night in resolve_nights(record['text'], hotel_nights)


# ---------------------------------------------------------------------------
# 每人每房晚核对台账
# ---------------------------------------------------------------------------

def build_ledger(facts, records, unconfirmed_nights=None, itinerary=None):
    """按 申请人 × 票据 × 房晚 整理费用范围与实际自付款。

    unconfirmed_nights: 资料只足以确认部分房晚时，显式列入待核的 (receipt, night)。
    返回的每行都含 gross_share（原始分摊）、host_share（主办方承担）、
    posted（已结报，保留原始依据）、net_self_pay（实际自付）、
    balance（可再申报余额）、pending（待核金额）与证据/复核理由。
    """
    unconfirmed_nights = set(unconfirmed_nights or [])
    hotel_nights = sorted({row['night'] for row in facts['hotel']})
    itinerary = itinerary or parse_itinerary(records, facts)
    statuses = itinerary['statuses']

    host_by_night = defaultdict(int)
    for item in facts['host_paid']:
        host_by_night[(item['receipt'], item['night'])] += item['cents']
    posted_by_person = defaultdict(int)
    posted_refs = defaultdict(list)
    for item in facts['reimbursements']:
        if item.get('paid'):
            key = (item['applicant'], item['receipt'], item['night'])
            posted_by_person[key] += item['cents']
            posted_refs[key].append(item)

    ledger = []
    for room in facts['hotel']:
        key = (room['receipt'], room['night'])
        night = room['night']
        # 经核对的实际住宿人：行程更新确认已返程者不再占用该房晚
        effective = [name for name in room['occupants']
                     if statuses.get(name, {}).get(night) != '已返程']
        if not effective:
            effective = list(room['occupants'])
        original_share = room['cents'] // len(room['occupants'])
        gross = room['cents'] // len(effective)
        host_total = host_by_night.get(key, 0)
        host_per_person = host_total // len(effective) if effective else 0

        row_notes = [_raw_note(r) for r in records
                     if note_covers(r, night, hotel_nights)]
        for applicant in facts['applicants']:
            person_key = (applicant, *key)
            posted = posted_by_person.get(person_key, 0)
            status = statuses.get(applicant, {}).get(night, '未提及')
            stayed = applicant in effective
            share_changed = stayed and gross != original_share
            person_host = host_per_person if stayed else 0
            gross_person = gross if stayed else 0
            net = max(0, gross_person - person_host)
            pending = 0
            review_reasons = []
            if not stayed:
                review_reasons.append('行程更新显示该申请人此房晚已返程，不参与分摊')
            if share_changed:
                review_reasons.append(
                    f'实际住宿人变化，个人分摊由 {original_share} 分更正为 {gross} 分')
            if host_total and stayed:
                review_reasons.append('该房晚由主办方支付，主办方份额不得再申报')
            if key in unconfirmed_nights:
                pending = max(0, net - posted)
                review_reasons.append('现有材料不足以确认该房晚分摊，列入待核')
            balance = 0 if key in unconfirmed_nights else max(0, net - posted)
            ledger.append({
                'applicant': applicant,
                'receipt': room['receipt'],
                'night': night,
                'stayed': stayed,
                'occupancy_status': status,
                'room_cents': room['cents'],
                'original_occupants': list(room['occupants']),
                'effective_occupants': list(effective),
                'gross_share': gross_person,
                'host_share': person_host,
                'posted_cents': posted,
                'posted_refs': posted_refs.get(person_key, []),
                'net_self_pay': net,
                'balance_cents': balance,
                'pending_cents': pending,
                'share_changed': share_changed,
                'review_reasons': review_reasons,
                'evidence': row_notes,
            })
    return ledger


def find_ledger_row(ledger, applicant, receipt, night):
    for row in ledger:
        if (row['applicant'], row['receipt'], row['night']) == (applicant, receipt, night):
            return row
    return None


# ---------------------------------------------------------------------------
# 申请审核：不重复支付、不改动同行人已获批准
# ---------------------------------------------------------------------------

def review_claims(claims, ledger):
    """逐条审核报销申请。

    已支付申请保留原始审批（state='已支付'），仅在底层事实变化时附加 review_reasons；
    未支付申请按经核对的余额判断，余额不足或房晚待核均不得放款。
    """
    results = []
    for claim in claims:
        row = find_ledger_row(ledger, claim['applicant'], claim['receipt'], claim['night'])
        if row is None:
            results.append({**_claim_key(claim), 'state': '无法核对', 'approved_cents': 0,
                            'review_reasons': ['酒店明细中无对应房晚或申请人'],
                            'evidence': []})
            continue
        reasons = list(row['review_reasons'])
        if row['posted_cents'] > 0:
            # 已支付：保留依据；不因同行人更正而顺手改动
            state = '已支付'
            approved = row['posted_cents']
        elif not row['stayed']:
            state = '无足额自付款'
            approved = 0
            reasons.append('申请人该房晚未实际住宿')
        elif row['pending_cents'] > 0:
            state = '待核'
            approved = 0
        elif 0 < claim['cents'] <= row['balance_cents']:
            state = '可报销'
            approved = claim['cents']
        else:
            state = '无足额自付款'
            approved = 0
            reasons.append(
                f"申请 {claim['cents']} 分超过可申报自付款余额 {row['balance_cents']} 分")
        results.append({
            **_claim_key(claim),
            'state': state,
            'approved_cents': approved,
            'claimed_cents': claim['cents'],
            'balance_cents': row['balance_cents'],
            'pending_cents': row['pending_cents'],
            'host_share': row['host_share'],
            'posted_cents': row['posted_cents'],
            'share_changed': row['share_changed'],
            'review_reasons': reasons,
            'evidence': row['evidence'],
        })
    return results


def _claim_key(claim):
    return {'id': claim['id'], 'applicant': claim['applicant'],
            'receipt': claim['receipt'], 'night': claim['night']}


# ---------------------------------------------------------------------------
# 紧急改签例外审批：批准理由只覆盖本事件关联费用
# ---------------------------------------------------------------------------

def build_exception_policy(events):
    """events 为例外事件定义：事件、受影响申请人、允许的票据/费用类型与金额上限。"""
    policy = {}
    for event in events:
        for applicant in event['applicants']:
            policy[(applicant, event['event'])] = event
    return policy


def review_change_fee(fee, policy, records):
    """审核紧急改签/换票费用。无票据、不在批准事件或受影响名单内的，不予覆盖。"""
    event = policy.get((fee['applicant'], fee['event']))
    evidence = [_raw_note(r) for r in records
               if r['source'] == 'itinerary-update']
    if event is None:
        return {'id': fee.get('id'), 'state': '例外不覆盖', 'approved_cents': 0,
                'review_reasons': [f"申请人不在“{fee['event']}”例外批准范围"],
                'evidence': evidence}
    if not fee.get('receipt'):
        return {'id': fee.get('id'), 'state': '待核', 'approved_cents': 0,
                'review_reasons': ['改签费用缺少票据，待补凭据'],
                'evidence': evidence}
    if fee.get('fee_type') not in event.get('fee_types', []):
        return {'id': fee.get('id'), 'state': '例外不覆盖', 'approved_cents': 0,
                'review_reasons': [f"费用类型 {fee.get('fee_type')} 不属于批准理由覆盖范围"],
                'evidence': evidence}
    cap = event.get('cents_cap', fee['cents'])
    if fee['cents'] > cap:
        return {'id': fee.get('id'), 'state': '部分覆盖', 'approved_cents': cap,
                'review_reasons': [f"超出例外上限 {cap} 分的部分自付"],
                'evidence': evidence}
    return {'id': fee.get('id'), 'state': '例外覆盖', 'approved_cents': fee['cents'],
            'review_reasons': [f"属“{event['event']}”批准理由关联费用"],
            'evidence': evidence}


# ---------------------------------------------------------------------------
# 汇总与完整报告
# ---------------------------------------------------------------------------

def summarize_by_applicant(ledger):
    summary = {}
    for name in sorted({row['applicant'] for row in ledger}):
        rows = [row for row in ledger if row['applicant'] == name]
        summary[name] = {
            'gross_share_total': sum(r['gross_share'] for r in rows),
            'host_covered_total': sum(r['host_share'] for r in rows),
            'posted_total': sum(r['posted_cents'] for r in rows),
            'self_pay_total': sum(r['net_self_pay'] for r in rows),
            'balance_total': sum(r['balance_cents'] for r in rows),
            'pending_total': sum(r['pending_cents'] for r in rows),
            'review_nights': [f"{r['receipt']}/{r['night']}" for r in rows
                              if r['review_reasons']],
        }
    return summary


def build_audit_report(facts, records, claims=(), change_fees=(), exception_events=(),
                       unconfirmed_nights=None):
    """生成审核员工作台报告：事实关联、台账、申请结论、待核与证据回链。"""
    hotel_nights = sorted({row['night'] for row in facts['hotel']})
    speaker = identify_speaker(records, facts)
    itinerary = parse_itinerary(records, facts)
    night_index = {}
    for night in hotel_nights:
        links = {}
        for record in records:
            for mention in _mentions_for_night(record['text'], night, hotel_nights):
                links[(record['record_id'], mention)] = {
                    'mention': mention,
                    'record_id': record['record_id'],
                    'quote': record['text'],
                }
        night_index[night] = list(links.values())
    ledger = build_ledger(facts, records, unconfirmed_nights, itinerary)
    claim_results = review_claims(list(claims), ledger)
    policy = build_exception_policy(exception_events)
    fee_results = [review_change_fee(fee, policy, records) for fee in change_fees]
    pending = [
        {'applicant': row['applicant'], 'receipt': row['receipt'], 'night': row['night'],
         'pending_cents': row['pending_cents'], 'reasons': row['review_reasons'],
         'evidence': row['evidence']}
        for row in ledger if row['pending_cents'] > 0
    ]
    return {
        'speaker': speaker,
        'night_reference': night_index,
        'host_paid': facts['host_paid'],
        'itinerary': itinerary,
        'ledger': ledger,
        'claims': claim_results,
        'change_fee_reviews': fee_results,
        'pending': pending,
        'applicant_summary': summarize_by_applicant(ledger),
    }


def _mentions_for_night(text, night, hotel_nights):
    """提取备注中解析到指定房晚的原文片段（供“三号这晚”回链）。"""
    day = int(night.split('-')[1])
    mentions = []
    for raw_day, unit in _scan_day_mentions(text):
        parsed = _CN_DIGITS.get(raw_day)
        if parsed is None and str(raw_day).isdigit():
            parsed = int(raw_day)
        if parsed == day:
            mentions.append(f'{raw_day}{unit}')
    if night in resolve_nights(text, hotel_nights) and not mentions:
        mentions.append('整段住宿表述')
    return mentions
