"""Tính bảng xếp hạng team tổng hợp.

Công thức (đã chốt với người dùng):
    điểm  = Σ (điểm contest × trọng số), team không dự thì lấy absent_score × trọng số
    hoà   = so tổng penalty, thấp hơn xếp trên

Bốn cái bẫy trong dữ liệu, bỏ qua cái nào cũng cho ra bảng sai:

1. virtual=LIVE là BẮT BUỘC. Một user có nhiều ContestParticipation cho cùng một
   contest (unique_together là (contest, user, virtual)). Contest 2026training01
   có 68 participation nhưng chỉ 41 là lượt thi chính thức; team14 có 3 dòng, dòng
   LIVE 6 điểm còn hai dòng ảo 0 điểm. Quên lọc là hoặc nhân đôi team, hoặc lấy
   nhầm dòng 0 điểm.

2. is_disqualified=True thì score bị GHI ĐÈ thành -9999 trong DB
   (judge/models/contest.py:675). Nhân trọng số vào đó là bảng nát. Ở đây coi như
   không dự, tức lấy absent_score.

3. Dùng score/cumtime, KHÔNG dùng frozen_score/frozen_cumtime. frozen_* chỉ là bản
   chụp phục vụ scoreboard trong giai đoạn đóng băng, không phải kết quả cuối.

4. cumtime KHÔNG cùng đơn vị giữa các format: ICPC tính bằng PHÚT, format default
   tính bằng GIÂY. Cộng penalty qua nhiều contest khác format là cộng nhầm đơn vị
   — xem Ranking.mixed_penalty_units, giao diện phải cảnh báo.

Team RA ĐỀ một contest (RankingContest.setters) được điểm tối đa và penalty 0 cho
contest đó mà không cần thi. Ưu tiên này đặt TRƯỚC mọi thứ khác: người ra đề có
thể vẫn có participation (vào xem, thi thử) với điểm thấp, và điểm đó không được
phép lấn át.
"""
from judge.models import ContestParticipation


def compute(ranking):
    """Trả về danh sách hàng đã xếp hạng.

    Mỗi hàng: {rank, team, username, name, org, total, penalty, cells}
    cells song song với danh sách contest, mỗi ô:
        {participated, score, weighted, penalty}
    """
    rcs = list(ranking.contests.select_related('contest').prefetch_related('setters').all())
    # prefetch organizations: thiếu nó thì mỗi team một truy vấn riêng
    # (đo thật: 48 team -> 53 truy vấn, 52ms; có prefetch -> 6 truy vấn, 19ms)
    teams = list(ranking.teams.select_related('user').prefetch_related('organizations').all())
    if not teams:
        return [], rcs

    # Một truy vấn cho toàn bảng, không phải mỗi team một lần
    rows = (ContestParticipation.objects
            .filter(contest_id__in=[rc.contest_id for rc in rcs],
                    user_id__in=[t.id for t in teams],
                    virtual=ContestParticipation.LIVE,
                    is_disqualified=False)
            .values_list('contest_id', 'user_id', 'score', 'cumtime'))
    data = {(cid, uid): (score, cumtime) for cid, uid, score, cumtime in rows}

    setter_ids = {rc.id: {p.id for p in rc.setters.all()} for rc in rcs}
    max_scores = {rc.id: rc.max_score for rc in rcs}

    result = []
    for team in teams:
        total = 0.0
        penalty = 0
        cells = []
        for rc in rcs:
            if team.id in setter_ids[rc.id]:
                # Ra đề thì được điểm tối đa, penalty 0. Kiểm TRƯỚC khi tra
                # participation: người ra đề vẫn có thể có lượt thi thử điểm thấp.
                score = max_scores[rc.id]
                weighted = score * rc.weight
                cells.append({'participated': True, 'setter': True, 'score': score,
                              'weighted': weighted, 'penalty': 0})
                total += weighted
                continue

            hit = data.get((rc.contest_id, team.id))
            if hit is None:
                weighted = ranking.absent_score * rc.weight
                cells.append({'participated': False, 'setter': False, 'score': None,
                              'weighted': weighted, 'penalty': None})
            else:
                score, cumtime = hit
                weighted = score * rc.weight
                penalty += cumtime
                cells.append({'participated': True, 'setter': False, 'score': score,
                              'weighted': weighted, 'penalty': cumtime})
            total += weighted

        # list(...) chứ KHÔNG .first(): .first() phát truy vấn mới, vô hiệu hoá prefetch
        orgs = list(team.organizations.all())
        org = orgs[0] if orgs else None
        result.append({
            'team': team,
            'username': team.user.username,
            'name': team.username_display_override or team.user.first_name or team.user.username,
            'org': org.short_name if org else '',
            'total': total,
            'penalty': penalty,
            'cells': cells,
        })

    # Điểm cao trước; hoà thì penalty thấp trước
    result.sort(key=lambda r: (-r['total'], r['penalty']))

    # Hạng: cùng (điểm, penalty) thì đồng hạng
    prev, rank = None, 0
    for i, row in enumerate(result, start=1):
        key = (row['total'], row['penalty'])
        if key != prev:
            rank, prev = i, key
        row['rank'] = rank
        # Bảng vàng tính theo VỊ TRÍ trong danh sách, không theo hạng: đồng hạng ở
        # ranh giới thì cả nhóm cùng vào, không cắt ngang giữa hai team bằng điểm.
        row['medal'] = {1: 'gold', 2: 'silver', 3: 'bronze'}.get(rank)
        row['honour'] = bool(ranking.hall_of_fame) and rank <= ranking.hall_of_fame

    return result, rcs


def compute_cached(ranking, ttl=300):
    """Như compute() nhưng có cache, dùng cho trang chủ — trang được xem nhiều nhất.

    Khoá gồm cả `modified` nên sửa bảng là khoá đổi, không phải xoá cache tay.
    RankingContest đổi (trọng số, thêm/bớt contest, đổi team ra đề) KHÔNG tự đụng
    Ranking.modified vì là model khác — signal trong hcmus/models.py lo việc đó.

    Bỏ khoá 'team' khỏi mỗi hàng: đó là Profile object, pickle vào Redis thì tốn
    mà template không dùng tới.
    """
    from django.core.cache import cache

    # Lấy tới MICRO giây, không phải giây: hai lần sửa trong cùng một giây sẽ cho
    # khoá giống hệt nhau và lần sửa thứ hai đọc phải số liệu cũ. Đã đo thấy thật:
    # đổi trọng số 2->3->2 liên tiếp thì lần cuối vẫn trả điểm của trọng số 3.
    key = 'hcmus_rank:%d:%d' % (ranking.id, int(ranking.modified.timestamp() * 1e6))
    hit = cache.get(key)
    if hit is not None:
        return hit
    rows, _ = compute(ranking)
    light = [{k: v for k, v in row.items() if k != 'team'} for row in rows]
    cache.set(key, light, ttl)
    return light
