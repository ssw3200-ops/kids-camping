"""키 없이 화면 확인용 샘플 데이터 (실제 캠핑장 아님)"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

CENTERS = [("세종", "세종시", 36.55, 127.25), ("충남", "공주시", 36.45, 127.10), ("충북", "괴산군", 36.80, 127.80),
           ("대전", "유성구", 36.38, 127.30), ("강원", "홍천군", 37.70, 128.10), ("경기", "가평군", 37.80, 127.45),
           ("충남", "태안군", 36.75, 126.30), ("전북", "무주군", 35.95, 127.65)]
NAMES = ["숲속", "별빛", "솔바람", "달빛", "푸른계곡", "다람쥐", "반딧불", "햇살", "도토리", "구름"]
TAILS = ["오토캠핑장", "캠핑장", "글램핑", "카라반파크", "가족캠핑장"]
SB = ["전기", "무선인터넷", "장작판매", "온수", "트렘폴린", "물놀이장", "놀이터", "산책로", "운동장", "마트.편의점"]
PF = ["계곡 물놀이", "산책로", "해수욕", "농어촌체험시설", "어린이놀이시설", "수상레저"]


def make(now: datetime):
    from build import camp_flags  # noqa: PLC0415
    rnd = random.Random(3)
    camps = []
    for i in range(48):
        sd, sg, la, lo = CENTERS[i % len(CENTERS)]
        tail = rnd.choice(TAILS)
        sb = rnd.sample(SB, rnd.randint(2, 7))
        pf = rnd.sample(PF, rnd.randint(0, 3))
        it = {"sbrsCl": ",".join(sb), "posblFcltyCl": ",".join(pf),
              "induty": "글램핑" if "글램핑" in tail else "카라반" if "카라반" in tail else rnd.choice(["자동차야영장", "일반야영장,자동차야영장"]),
              "animalCmgCl": rnd.choice(["불가능", "가능(소형견)", ""]),
              "exprnProgrmAt": rnd.choice(["Y", "N", "N"]), "lineIntro": "아이랑 놀기 좋은 샘플 캠핑장"}
        camps.append({
            "i": f"S{i}", "n": f"{rnd.choice(NAMES)}{tail} (샘플{i})", "li": rnd.choice(["계곡 옆 아이랑 물놀이", "트램펄린 있는 가족 캠핑장", "별 보기 좋은 숲속", ""]),
            "ty": it["induty"], "lc": rnd.choice(["산", "숲", "계곡", "해변", "강"]),
            "a": f"{sd} {sg} 샘플로 {i}", "sd": sd, "sg": sg,
            "la": round(la + rnd.uniform(-.15, .15), 5), "lo": round(lo + rnd.uniform(-.15, .15), 5),
            "t": f"010-0000-{i:04d}", "h": "", "r": "https://www.gocamping.or.kr/" if i % 2 else "",
            "rc": rnd.choice(["온라인실시간예약", "전화", ""]), "img": "",
            "f": camp_flags(it), "sb": it["sbrsCl"], "pf": it["posblFcltyCl"], "an": it["animalCmgCl"],
            "op": "봄,여름,가을,겨울", "od": "평일+주말", "x": "숲 체험" if it["exprnProgrmAt"] == "Y" else "",
        })
    med = {"e": [], "h": [], "p": []}
    for i, (sd, sg, la, lo) in enumerate(CENTERS):
        med["e"].append([f"{sg} 샘플응급의료센터", "044-000-0119", la + .2, lo - .1, "", "지역응급의료기관"])
        for j in range(4):
            t = "09001800" * 5 + "09001300" + "--------" * 2 if j % 2 else "09002100" * 5 + "09001800" + "10001800" * 2
            med["h"].append([f"{sg} 튼튼소아청소년과{j}", "044-111-0000", la + rnd.uniform(-.2, .2), lo + rnd.uniform(-.2, .2), t, "P" if j else ""])
            med["p"].append([f"{sg} 온누리약국{j}", "044-222-0000", la + rnd.uniform(-.2, .2), lo + rnd.uniform(-.2, .2), t, ""])
    stores = []
    for c in camps:
        stores.append([rnd.choice(["GS25", "CU", "세븐일레븐"]) + f" {c['sg']}점", "c", c["la"] + rnd.uniform(-.03, .03), c["lo"] + rnd.uniform(-.03, .03)])
        stores.append([rnd.choice(["하나로마트", "탑마트"]) + f" {c['sg']}점", "m", c["la"] + rnd.uniform(-.06, .06), c["lo"] + rnd.uniform(-.06, .06)])
    spots = []
    kinds = [("어린이 과학관", "문화시설", 8), ("공룡 박물관", "문화시설", 8), ("치즈 체험 목장", "관광지", 8),
             ("자연휴양림", "관광지", 3), ("레일바이크", "레포츠", 3), ("향교", "관광지", -4), ("수목원", "관광지", 3)]
    for sd, sg, la, lo in CENTERS:
        for t, k, sc in kinds:
            spots.append([f"{sg} {t}", k, la + rnd.uniform(-.15, .15), lo + rnd.uniform(-.15, .15), "", sc, ""])
    d0 = now.date()
    fests = [[f"{sg} 겨울 별빛 축제", (d0 + timedelta(days=rnd.randint(0, 6))).strftime("%Y%m%d"),
              (d0 + timedelta(days=rnd.randint(7, 20))).strftime("%Y%m%d"), la + .05, lo + .05, f"{sd} {sg} 일원", ""]
             for sd, sg, la, lo in CENTERS]
    short = {"base": now.strftime("%Y%m%d") + "0500", "g": {}}
    mid = {"tmFc": now.strftime("%Y%m%d") + "0600", "r": {}}
    from build import mid_reg, to_grid  # noqa: PLC0415
    for c in camps:
        g = to_grid(c["la"], c["lo"])
        k = f"{g[0]},{g[1]}"
        if k not in short["g"]:
            short["g"][k] = {(d0 + timedelta(days=d)).strftime("%Y%m%d"): [
                rnd.randint(3, 14), rnd.randint(-6, 4), rnd.choice([0, 10, 20, 30, 60, 80]),
                rnd.choice([0, 0, 0, 1]), rnd.randint(10, 90), rnd.choice([1, 3, 4])] for d in range(4)}
        r = mid_reg(c["sd"], c["lo"])
        if r and r not in mid["r"]:
            mid["r"][r] = {(d0 + timedelta(days=d)).strftime("%Y%m%d"): [
                rnd.choice(["맑음", "구름많음", "흐림", "흐리고 비"]), rnd.choice(["맑음", "구름많음", "흐림"]),
                rnd.choice([0, 10, 20, 40, 60]), rnd.choice([0, 10, 20, 40, 60])] for d in range(4, 11)}
    return camps, med, stores, spots, fests, short, mid
