from collections import defaultdict


class HotelNights:
    def __init__(self, rooms, covered):
        self.rooms = {(room['receipt'], room['night']): room for room in rooms}
        self.covered = {(item['receipt'], item['night']): item['cents'] for item in covered}

    def net_for(self, row):
        key = row['receipt'], row['night']
        room = self.rooms[key]
        if row['applicant'] not in room['occupants']:
            return None
        return max(0, room['cents'] - self.covered.get(key, 0))


class ClaimEligibility:
    def __init__(self, travelers, hotel):
        self.travelers = set(travelers)
        self.hotel = hotel

    def check(self, row):
        if row['applicant'] not in self.travelers:
            return '申请人未登记', 0
        if self.hotel.net_for(row) is None:
            return '不在住宿名单', 0
        return '可核对', self.hotel.net_for(row)


class ReimbursementDesk:
    def __init__(self, eligibility, posted):
        self.eligibility = eligibility
        self.used = defaultdict(int)
        self.posted = {}
        for item in posted:
            if item['paid']:
                key = item['receipt'], item['night']
                self.used[key] += item['cents']
                self.posted[(item['applicant'], *key)] = item

    def preview(self, claims):
        output = []
        used = self.used.copy()
        for row in claims:
            status, spend = self.eligibility.check(row)
            key = row['receipt'], row['night']
            old = self.posted.get((row['applicant'], *key))
            if old:
                status = '已支付'
            elif status == '可核对':
                if 0 < row['cents'] <= spend - used[key]:
                    used[key] += row['cents']
                    status = '可报销'
                else:
                    status = '无足额自付款'
            output.append({'id': row['id'], 'state': status, 'paid': old})
        return output


def assess(case, registry):
    hotels = HotelNights(registry['hotel'], registry['host_paid'])
    desk = ReimbursementDesk(ClaimEligibility(registry['applicants'], hotels), registry['reimbursements'])
    return desk.preview(case['claims'])
