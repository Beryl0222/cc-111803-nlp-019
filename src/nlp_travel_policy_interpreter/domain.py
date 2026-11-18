import json
from pathlib import Path

def load_facts(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def summarize(facts):
    results = {}
    for stay in facts['hotel']:
        share = stay['cents'] // len(stay['occupants'])
        host_paid = sum(item['cents'] for item in facts['host_paid']
                        if item['receipt'] == stay['receipt'] and item['night'] == stay['night'])
        already_paid = sum(item['cents'] for item in facts['reimbursements']
                           if item['applicant'] == '林青' and item['receipt'] == stay['receipt']
                           and item['night'] == stay['night'] and item['paid'])
        results[stay['night']] = max(0, share - host_paid - already_paid)
    return results
