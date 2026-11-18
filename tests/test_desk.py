from pathlib import Path

from nlp_travel_policy_interpreter.desk_cli import load_case


def test_host_payment_and_previous_reimbursement_leave_distinct_balances():
    fixture = Path(__file__).parents[1] / 'fixtures'
    output = load_case(fixture / 'work.json', fixture / 'business.json')
    assert output[0]['state'] == '已支付'
    assert output[1]['state'] == '可报销'
    assert output[2]['state'] == '无足额自付款'
