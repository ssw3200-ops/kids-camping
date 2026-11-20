"""아이랑 캠핑 한 번에 — 데이터 만들기

매일 (GitHub Actions)
  1) 기상청 단기예보 (캠핑장 시·군·구 대표 격자)   → 3~4일 날씨 (밤 최저기온·비·바람)
  2) 기상청 중기예보 육상 (권역)                   → 4~10일 하늘·강수확률
  3) 관광공사 TourAPI 행사 searchFestival2        → 앞으로 5주 행사 (페이지에서 캠핑장 근처만 표시)
매주 (지난 결과가 7일 넘었거나 --force-base)
  4) 관광공사 고캠핑 basedList                    → 전국 캠핑장
  5) 국립중앙의료원 응급실·소아과·약국            → 캠핑장별 가까운 곳
  6) 소상공인 상가정보 (편의점·슈퍼·마트)         → 캠핑장별 가까운 가게
  7) 관광공사 TourAPI areaBasedList2 (관광지·문화시설·레포츠) → 캠핑장별 주변 가볼 곳

결과 (site/ → gh-pages 배포, 커밋 기록 안 쌓임)
  site/camps/<시도>.enc.json   캠핑장 + 근처 정보 (암호화, 주 1회)
  site/daily.enc.json          날씨·행사 (암호화, 매일)
  site/meta.json, status.json  (비암호화)

사용: python src/build.py --prev prev --out site     (DATA_GO_KR_KEY, PAGE_PASSWORD)
      python src/build.py --out site --sample        (키 없이 샘플)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from lock import decrypt, encrypt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
APP = {"MobileOS": "ETC", "MobileApp": "kidscamping", "_type": "json"}

CAMP_URL = "https://apis.data.go.kr/B551011/GoCamping/basedList"
TOUR = "https://apis.data.go.kr/B551011/KorService2"
VF_URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
MID_URL = "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst"
HOSP_URL = "https://apis.data.go.kr/B552657/HsptlAsembySearchService/getHsptlMdcncListInfoInqire"
ER_URL = "https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEgytListInfoInqire"
PHARM_URL = "https://apis.data.go.kr/B552657/ErmctInsttInfoInqireService/getParmacyListInfoInqire"
SDSC = "https://apis.data.go.kr/B553077/api/open/sdsc2"

BASE_DAYS = 7      # 캠핑장·병원·가게·관광지 다시 받는 주기
STORE_CAP = 900    # 상가정보를 캠핑장별로 받아야 할 때 하루 최대 호출 수 (나머지는 다음 날)

REGIONS = {
    "서울": ("서울",), "부산": ("부산",), "대구": ("대구",), "인천": ("인천",), "광주": ("광주",),
    "대전": ("대전",), "울산": ("울산",), "세종": ("세종",), "경기": ("경기",), "강원": ("강원",),
    "충북": ("충청북도", "충북"), "충남": ("충청남도", "충남"), "전북": ("전라북도", "전북", "전북특별자치도"),
    "전남": ("전라남도", "전남"), "경북": ("경상북도", "경북"), "경남": ("경상남도", "경남"), "제주": ("제주",),
}
CODES = {
    "서울": "seoul", "부산": "busan", "대구": "daegu", "인천": "incheon", "광주": "gwangju", "대전": "daejeon",
    "울산": "ulsan", "세종": "sejong", "경기": "gyeonggi", "강원": "gangwon", "충북": "chungbuk", "충남": "chungnam",
    "전북": "jeonbuk", "전남": "jeonnam", "경북": "gyeongbuk", "경남": "gyeongnam", "제주": "jeju",
}
# 중기 육상예보 권역
MID_REG = {"서울": "11B00000", "인천": "11B00000", "경기": "11B00000", "대전": "11C20000", "세종": "11C20000",
           "충남": "11C20000", "충북": "11C10000", "광주": "11F20000", "전남": "11F20000", "전북": "11F10000",
           "대구": "11H10000", "경북": "11H10000", "부산": "11H20000", "울산": "11H20000", "경남": "11H20000",
           "제주": "11G00000"}

status: dict = {"sources": {}, "errors": [], "reused": []}


def region_of(text: str) -> str:
    first = (text or "").strip().split(" ")[0]
    for short, keys in REGIONS.items():
        if any(first.startswith(k) for k in keys):
            return short
    return ""


def mid_reg(sido: str, lon: float) -> str:
    if sido == "강원":
        return "11D20000" if lon >= 128.55 else "11D10000"
    return MID_REG.get(sido, "")


def err(where: str, e) -> None:
    status["errors"].append(f"{where}: {e}"[:300])


# ---------------------------------------------------------------- API 호출 (JSON/XML 둘 다)
def _find_items(obj) -> list[dict]:
    if isinstance(obj, dict):
        for k in ("item", "items", "list", "row", "data"):
            v = obj.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
            if isinstance(v, dict) and k == "item":
                return [v]
        for v in obj.values():
            got = _find_items(v)
            if got:
                return got
    elif isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj
    return []


def _find_key(obj, names: tuple[str, ...]):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in names and not isinstance(v, (dict, list)):
                return v
        for v in obj.values():
            got = _find_key(v, names)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _find_key(v, names)
            if got is not None:
                return got
    return None


def call(url: str, params: dict, tries: int = 3, timeout: int = 60) -> tuple[list[dict], int | None]:
    """레코드 목록과 totalCount. 오류 코드면 예외, 데이터 없음(03)은 빈 목록."""
    last: Exception | None = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            txt = r.text.strip()
            if txt.startswith("{") or txt.startswith("["):
                js = r.json()
                code = _find_key(js, ("resultCode",))
                if code is not None and str(code) not in ("00", "0", "0000", "INFO-000", "NORMAL_SERVICE"):
                    if str(code) == "03":
                        return [], 0
                    raise RuntimeError(f"resultCode={code} {_find_key(js, ('resultMsg',)) or ''}")
                total = _find_key(js, ("totalCount", "totCnt", "totalCnt"))
                return _find_items(js), int(total) if str(total or "").isdigit() else None
            root = ET.fromstring(r.content)
            code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode") or "00"
            if code == "03":
                return [], 0
            if code not in ("00", "0000", "0"):
                msg = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or ""
                raise RuntimeError(f"resultCode={code} {msg}".strip())
            its = [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]
            tot = root.findtext(".//totalCount")
            return its, int(tot) if tot and tot.isdigit() else None
        except ET.ParseError:
            last = RuntimeError(f"응답이 XML/JSON 아님: {r.text[:150]!r}")
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(1.5 * (i + 1))
    raise last  # type: ignore[misc]


def fetch_pages(name: str, url: str, params: dict, rows: int = 1000, max_pages: int = 300,
                page_key: str = "pageNo", rows_key: str = "numOfRows") -> list[dict]:
    out, page, total = [], 1, None
    while page <= max_pages:
        got, tot = call(url, {**params, page_key: page, rows_key: rows})
        if total is None:
            total = tot
        out += got
        if not got or len(got) < rows or (total is not None and len(out) >= total):
            break
        page += 1
    status["sources"][name] = {"total": total, "got": len(out), "pages": page,
                               "fields": sorted(out[0].keys()) if out else []}
    return out


def fnum(v) -> float | None:
    try:
        f = float(str(v).strip())
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def latlon(a, b) -> tuple[float, float] | None:
    """두 값 중 한국 위도 범위인 것을 위도로"""
    x, y = fnum(a), fnum(b)
    if x is None or y is None:
        return None
    for lat, lon in ((x, y), (y, x)):
        if 32.5 < lat < 39.5 and 123.5 < lon < 132.5:
            return round(lat, 5), round(lon, 5)
    return None


def https(u: str) -> str:
    u = (u or "").strip()
    return "https://" + u[7:] if u.startswith("http://") else u


def dist(a: float, b: float, c: float, d: float) -> float:
    r = math.pi / 180
    x = (d - b) * r * math.cos((a + c) / 2 * r)
    y = (c - a) * r
    return math.sqrt(x * x + y * y) * 6371


class Grid:
    """가까운 점 찾기용 격자 (0.1도 ≈ 11km)"""

    def __init__(self, pts: list, lat_i: int, lon_i: int, cell: float = 0.1):
        self.cell, self.li, self.oi, self.b = cell, lat_i, lon_i, {}
        for p in pts:
            self.b.setdefault((int(p[lat_i] / cell), int(p[lon_i] / cell)), []).append(p)

    def near(self, lat: float, lon: float, k: int, max_km: float, keep=None) -> list[tuple[float, list]]:
        cy, cx = int(lat / self.cell), int(lon / self.cell)
        reach = int(max_km / (self.cell * 88)) + 1
        found = []
        for dy in range(-reach, reach + 1):
            for dx in range(-reach, reach + 1):
                for p in self.b.get((cy + dy, cx + dx), ()):
                    if keep and not keep(p):
                        continue
                    d = dist(lat, lon, p[self.li], p[self.oi])
                    if d <= max_km:
                        found.append((d, p))
        found.sort(key=lambda x: x[0])
        return found[:k]


# ---------------------------------------------------------------- 기상청 격자 변환 (LCC)
def to_grid(lat: float, lon: float) -> tuple[int, int]:
    RE, GRID, SLAT1, SLAT2, OLON, OLAT, XO, YO = 6371.00877, 5.0, 30.0, 60.0, 126.0, 38.0, 43, 136
    D = math.pi / 180
    re_ = RE / GRID
    s1, s2, olon, olat = SLAT1 * D, SLAT2 * D, OLON * D, OLAT * D
    sn = math.log(math.cos(s1) / math.cos(s2)) / math.log(math.tan(math.pi / 4 + s2 / 2) / math.tan(math.pi / 4 + s1 / 2))
    sf = math.tan(math.pi / 4 + s1 / 2) ** sn * math.cos(s1) / sn
    ro = re_ * sf / math.tan(math.pi / 4 + olat / 2) ** sn
    ra = re_ * sf / math.tan(math.pi / 4 + lat * D / 2) ** sn
    th = lon * D - olon
    if th > math.pi:
        th -= 2 * math.pi
    if th < -math.pi:
        th += 2 * math.pi
    th *= sn
    return int(math.floor(ra * math.sin(th) + XO + 0.5)), int(math.floor(ro - ra * math.cos(th) + YO + 0.5))


# ================================================================ 1) 캠핑장
KID_RULES = [  # (플래그, 찾을 말, 볼 항목)
    ("T", ("트렘폴린", "트램폴린", "방방"), "sb"),
    ("W", ("물놀이장", "수영장"), "sb"),
    ("P", ("놀이터", "놀이시설", "키즈"), "sb"),
    ("M", ("마트", "편의점", "매점"), "sb"),
    ("E", ("전기",), "sb"),
    ("H", ("온수",), "sb"),
    ("V", ("계곡", "물놀이", "해수욕", "강"), "pf"),
]


def camp_flags(it: dict) -> str:
    sb = (it.get("sbrsCl") or "") + "," + (it.get("sbrsEtc") or "")
    pf = it.get("posblFcltyCl") or ""
    fl = ""
    for f, words, col in KID_RULES:
        src = sb if col == "sb" else pf
        if any(w in src for w in words):
            fl += f
    ind = it.get("induty") or ""
    if "글램핑" in ind:
        fl += "G"
    if "카라반" in ind:
        fl += "C"
    an = it.get("animalCmgCl") or ""
    if an and "불가" not in an:
        fl += "A"
    if (it.get("exprnProgrmAt") or "") == "Y" or (it.get("exprnProgrm") or "").strip():
        fl += "X"
    if (it.get("eqpmnLendCl") or "").strip():
        fl += "R"
    if "어린이" in (it.get("intro") or "") + (it.get("lineIntro") or "") + (it.get("featureNm") or "") or "키즈" in (it.get("featureNm") or ""):
        if "P" not in fl:
            fl += "K"
    return fl


def fetch_camps(key: str) -> list[dict]:
    its = fetch_pages("camping", CAMP_URL, {"serviceKey": key, **APP}, rows=1000, max_pages=20)
    out, skip = [], {"좌표": 0, "지역": 0, "휴업": 0}
    for it in its:
        ll = latlon(it.get("mapY"), it.get("mapX"))
        if not ll:
            skip["좌표"] += 1
            continue
        addr = (it.get("addr1") or "").strip()
        sido = region_of(addr) or region_of(it.get("doNm") or "")
        if not sido:
            skip["지역"] += 1
            continue
        st = it.get("manageSttus") or ""
        if st and "운영" not in st:
            skip["휴업"] += 1
            continue
        sg = (it.get("sigunguNm") or "").strip() or (addr.split()[1] if len(addr.split()) > 1 else "")
        out.append({
            "i": str(it.get("contentId") or ""), "n": (it.get("facltNm") or "").strip(),
            "li": (it.get("lineIntro") or "").strip()[:80], "ty": (it.get("induty") or "").strip(),
            "lc": (it.get("lctCl") or "").strip(), "a": addr, "sd": sido, "sg": sg,
            "la": ll[0], "lo": ll[1], "t": (it.get("tel") or "").strip(),
            "h": https(it.get("homepage") or ""), "r": https(it.get("resveUrl") or ""),
            "rc": (it.get("resveCl") or "").strip(), "img": https(it.get("firstImageUrl") or ""),
            "f": camp_flags(it), "sb": (it.get("sbrsCl") or "").strip(),
            "pf": (it.get("posblFcltyCl") or "").strip(), "an": (it.get("animalCmgCl") or "").strip(),
            "op": (it.get("operPdCl") or "").strip(), "od": (it.get("operDeCl") or "").strip(),
            "x": (it.get("exprnProgrm") or "").strip()[:60],
        })
    status["sources"]["camping"]["kept"] = len(out)
    status["sources"]["camping"]["skipped"] = skip
    if its:
        status["sources"]["camping"]["sample_sbrsCl"] = sorted({s for it in its[:400] for s in (it.get("sbrsCl") or "").split(",") if s})
        status["sources"]["camping"]["sample_posblFcltyCl"] = sorted({s for it in its[:400] for s in (it.get("posblFcltyCl") or "").split(",") if s})
    if len(out) < 300:
        raise RuntimeError(f"캠핑장이 너무 적음 ({len(out)})")
    return out


# ================================================================ 2) 병원·약국·응급실
def hhmm(v: str) -> str:
    v = re.sub(r"\D", "", v or "")
    return v.zfill(4)[-4:] if v else ""


def times_of(it: dict) -> str:
    out = []
    for d in range(1, 9):
        s, c = hhmm(it.get(f"dutyTime{d}s", "")), hhmm(it.get(f"dutyTime{d}c", ""))
        out.append(s + c if s and c and s != c else "--------")
    return "".join(out)


def fetch_medical(key: str) -> dict[str, list]:
    """{'e': [...], 'h': [...], 'p': [...]} 레코드 = [이름, 전화, 위도, 경도, 진료시간, 플래그]"""
    out = {"e": [], "h": [], "p": []}
    srcs = (("e", "emergency", ER_URL, {}), ("h", "pediatric", HOSP_URL, {"QD": "D002"}), ("p", "pharmacy", PHARM_URL, {}))
    for kind, name, url, extra in srcs:
        try:
            rows = fetch_pages(name, url, {"serviceKey": key, **extra}, rows=1000)
        except Exception as e:  # noqa: BLE001
            err(name, e)
            continue
        seen = set()
        for it in rows:
            ll = latlon(it.get("wgs84Lat"), it.get("wgs84Lon"))
            nm = (it.get("dutyName") or "").strip()
            if not ll or not nm or nm in seen:
                continue
            seen.add(nm)
            if kind == "e":
                tel = it.get("dutyTel3") or it.get("dutyTel1") or ""
                out["e"].append([nm, tel, ll[0], ll[1], "", (it.get("dutyEmclsName") or "")[:12]])
            else:
                fl = ""
                if kind == "h" and any(w in nm for w in ("소아", "아동", "어린이")):
                    fl = "P"
                out[kind].append([nm, it.get("dutyTel1") or "", ll[0], ll[1], times_of(it), fl])
    return out


# ================================================================ 3) 편의점·마트
STORE_KINDS = (("c", ("편의점",)), ("m", ("대형마트", "슈퍼마켓", "할인점", "하나로", "농협")))
CHAIN_C = ("GS25", "지에스25", "CU", "씨유", "세븐일레븐", "이마트24", "미니스톱", "emart24")
CHAIN_M = ("하나로마트", "농협", "이마트", "홈플러스", "롯데마트", "탑마트", "노브랜드", "마트")


def store_kind(name: str, cat: str) -> str | None:
    if "편의점" in cat or any(c.lower() in name.lower() for c in CHAIN_C):
        return "c"
    if any(w in cat for w in ("슈퍼", "마트", "할인점")) or any(c in name for c in CHAIN_M):
        return "m"
    return None


def store_rec(it: dict) -> list | None:
    ll = latlon(it.get("lat"), it.get("lon"))
    nm = ((it.get("bizesNm") or "") + (" " + it["brchNm"] if it.get("brchNm") else "")).strip()
    if not ll or not nm:
        return None
    k = store_kind(nm, it.get("indsSclsNm") or "")
    if not k:
        return None
    return [nm[:30], k, ll[0], ll[1]]


def store_codes(key: str) -> list[tuple[str, str]]:
    """소분류 업종 목록에서 편의점·슈퍼마켓·대형마트 코드 찾기"""
    its, _ = call(f"{SDSC}/smallUpjongList", {"serviceKey": key, "type": "json"})
    out = []
    for it in its:
        nm, cd = it.get("indsSclsNm") or "", it.get("indsSclsCd") or ""
        if cd and any(w in nm for w in ("편의점", "슈퍼마켓", "대형마트", "할인점")):
            out.append((cd, nm))
    status["sources"]["store_codes"] = out
    return out


def fetch_stores_bulk(key: str) -> list[list]:
    """업종별 전국 목록 (호출 수 적음)"""
    codes = store_codes(key)
    if not codes:
        raise RuntimeError("업종코드를 못 찾음")
    out = []
    for cd, nm in codes:
        rows = fetch_pages(f"store_{nm}", f"{SDSC}/storeListInUpjong",
                           {"serviceKey": key, "type": "json", "divId": "indsSclsCd", "key": cd}, rows=1000, max_pages=200)
        for it in rows:
            r = store_rec(it)
            if r:
                out.append(r)
    if len(out) < 1000:
        raise RuntimeError(f"가게가 너무 적음 ({len(out)})")
    return out


def fetch_stores_radius(key: str, camps: list[dict], cache: dict) -> dict:
    """캠핑장마다 반경 5km 조회 (업종별 전국 조회가 안 될 때). 하루 STORE_CAP 건씩, 30일 캐시"""
    todo = [c for c in camps if age_days((cache.get(c["i"]) or {}).get("at")) > 30]
    todo = todo[:STORE_CAP]
    fails = 0

    def one(c):
        try:
            its, _ = call(f"{SDSC}/storeListInRadius", {"serviceKey": key, "type": "json", "radius": 5000,
                                                       "cx": c["lo"], "cy": c["la"], "numOfRows": 1000, "pageNo": 1},
                          tries=2)
            return c, [r for r in (store_rec(it) for it in its) if r]
        except Exception as e:  # noqa: BLE001
            return c, e

    with ThreadPoolExecutor(6) as ex:
        for c, res in ex.map(one, todo):
            if isinstance(res, Exception):
                fails += 1
                if fails <= 3:
                    err(f"상가 반경 {c['n']}", res)
            else:
                cache[c["i"]] = {"at": NOW.isoformat(timespec="minutes"), "s": res}
    status["sources"]["store_radius"] = {"called": len(todo), "fails": fails, "cached": len(cache),
                                         "left": sum(1 for c in camps if c["i"] not in cache)}
    return cache


# ================================================================ 4) 주변 가볼 곳 (TourAPI)
SPOT_TYPES = {"12": "관광지", "14": "문화시설", "28": "레포츠"}
KID_WORDS = {  # 아이랑 점수
    5: ("어린이", "키즈", "동물원", "아쿠아리움", "과학관", "체험관", "목장", "테마파크", "놀이공원", "공룡", "수족관"),
    3: ("체험", "박물관", "농장", "생태", "자연휴양림", "수목원", "식물원", "천문", "레일바이크", "썰매", "물놀이", "곤충"),
    2: ("공원", "전시관", "미술관", "숲", "해수욕장", "케이블카", "짚라인", "카트", "승마"),
}
NOT_KIDS = ("골프", "카지노", "사찰", "묘", "고분", "향교", "서원", "릉", "총", "유적", "성당", "교회", "술", "와이너리", "양조")


def kid_score(title: str, cat: str) -> int:
    s = 0
    t = title + " " + cat
    for pts, words in KID_WORDS.items():
        if any(w in t for w in words):
            s += pts
    if any(w in title for w in NOT_KIDS):
        s -= 4
    return s


def fetch_spots(key: str) -> list[list]:
    """[제목, 종류, 위도, 경도, 이미지, 아이점수, contentid]"""
    out = []
    for ct, label in SPOT_TYPES.items():
        try:
            rows = fetch_pages(f"tour_{label}", f"{TOUR}/areaBasedList2",
                               {"serviceKey": key, **APP, "contentTypeId": ct, "arrange": "C"}, rows=1000, max_pages=40)
        except Exception as e:  # noqa: BLE001
            err(f"TourAPI {label}", e)
            continue
        for it in rows:
            ll = latlon(it.get("mapy"), it.get("mapx"))
            title = (it.get("title") or "").strip()
            if not ll or not title:
                continue
            cat = " ".join(str(it.get(k) or "") for k in ("cat3", "lclsSystm3"))
            sc = kid_score(title, cat) + (1 if ct == "14" else 0)
            out.append([title[:30], label, ll[0], ll[1], https(it.get("firstimage") or ""), sc, str(it.get("contentid") or "")])
    if not out:
        raise RuntimeError("관광지 0건")
    return out


def fetch_festivals(key: str) -> list[list]:
    """[제목, 시작, 끝, 위도, 경도, 주소, 이미지]  (오늘~5주 사이에 열리는 것)"""
    rows = fetch_pages("festival", f"{TOUR}/searchFestival2",
                       {"serviceKey": key, **APP, "arrange": "C",
                        "eventStartDate": (NOW.date() - timedelta(days=60)).strftime("%Y%m%d")}, rows=200, max_pages=30)
    lo, hi = NOW.strftime("%Y%m%d"), (NOW + timedelta(days=35)).strftime("%Y%m%d")
    out = []
    for it in rows:
        s, e = str(it.get("eventstartdate") or ""), str(it.get("eventenddate") or "")
        ll = latlon(it.get("mapy"), it.get("mapx"))
        if not ll or len(s) != 8 or len(e) != 8 or e < lo or s > hi:
            continue
        out.append([(it.get("title") or "").strip()[:40], s, e, ll[0], ll[1], (it.get("addr1") or "").strip()[:40],
                    https(it.get("firstimage") or "")])
    status["sources"]["festival"]["kept"] = len(out)
    return out


# ================================================================ 5) 날씨
def latest_base() -> tuple[str, str]:
    t = NOW - timedelta(minutes=15)
    for d in (0, 1):
        day = t - timedelta(days=d)
        for h in (23, 20, 17, 14, 11, 8, 5, 2):
            cand = day.replace(hour=h, minute=0, second=0, microsecond=0)
            if cand <= t:
                return cand.strftime("%Y%m%d"), f"{h:02d}00"
    raise RuntimeError("발표시각 계산 실패")


def day_summary(its: list[dict]) -> dict:
    """{'YYYYMMDD': [낮최고, 밤최저(그날 18시~다음날 08시), 강수확률최대, 비/눈(0/1/2), 바람최대(0.1m/s), 하늘(1맑음/3구름/4흐림)]}"""
    by: dict[str, dict] = {}
    for it in its:
        cat, d, t = it.get("category"), str(it.get("fcstDate") or ""), str(it.get("fcstTime") or "")
        v = fnum(it.get("fcstValue"))
        if not d:
            continue
        h = int(t[:2]) if t[:2].isdigit() else 0
        # 밤은 18시~다음날 08시 → 다음날 0~8시는 전날 밤으로
        night_day = d
        if h < 9:
            night_day = (datetime.strptime(d, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        x = by.setdefault(d, {"tx": None, "pop": 0, "pty": 0, "w": 0, "sky": []})
        n = by.setdefault(night_day, {"tx": None, "pop": 0, "pty": 0, "w": 0, "sky": []})
        if cat == "TMP" and v is not None:
            if 9 <= h <= 18:
                x["tx"] = v if x["tx"] is None else max(x["tx"], v)
            if h >= 18 or h <= 8:
                n["tn"] = v if n.get("tn") is None else min(n["tn"], v)
        elif cat == "TMX" and v is not None:
            x["tx"] = v if x["tx"] is None else max(x["tx"], v)
        elif cat == "POP" and v is not None and 6 <= h <= 23:
            x["pop"] = max(x["pop"], int(v))
        elif cat == "PTY" and v:
            x["pty"] = max(x["pty"], 2 if int(v) in (3, 7) else 1)
        elif cat == "WSD" and v is not None and 6 <= h <= 23:
            x["w"] = max(x["w"], round(v * 10))
        elif cat == "SKY" and v is not None and 7 <= h <= 18:
            x["sky"].append(int(v))
    out = {}
    for d, x in by.items():
        if x["tx"] is None and x.get("tn") is None:
            continue
        sky = round(sum(x["sky"]) / len(x["sky"])) if x["sky"] else 0
        out[d] = [None if x["tx"] is None else round(x["tx"]), None if x.get("tn") is None else round(x["tn"]),
                  x["pop"], x["pty"], x["w"], sky]
    return out


def fetch_short(key: str, grids: list[tuple[int, int]], prev: dict) -> dict:
    base = latest_base()
    bkey = base[0] + base[1]
    old = prev.get("g", {}) if prev.get("base") == bkey else {}
    todo = [g for g in grids if f"{g[0]},{g[1]}" not in old]
    got, fails = dict(old), 0

    def one(g):
        try:
            its, _ = call(VF_URL, {"serviceKey": key, "dataType": "JSON", "numOfRows": 1500, "pageNo": 1,
                                   "base_date": base[0], "base_time": base[1], "nx": g[0], "ny": g[1]}, tries=2)
            s = day_summary(its)
            if not s:
                raise RuntimeError("값 없음")
            return g, s
        except Exception as e:  # noqa: BLE001
            return g, e

    with ThreadPoolExecutor(8) as ex:
        for g, res in ex.map(one, todo):
            if isinstance(res, Exception):
                fails += 1
                if fails <= 3:
                    err(f"단기예보 {g}", res)
            else:
                got[f"{g[0]},{g[1]}"] = res
    status["sources"]["short"] = {"base": bkey, "grids": len(grids), "called": len(todo), "fails": fails, "have": len(got)}
    if prev.get("g") and len(got) < len(grids) * 0.5:
        status["reused"].append(f"short (새 발표 {len(got)}/{len(grids)})")
        return prev
    return {"base": bkey, "g": got}


def mid_tmfc() -> str:
    t = NOW - timedelta(minutes=30)
    if t.hour >= 18:
        return t.strftime("%Y%m%d") + "1800"
    if t.hour >= 6:
        return t.strftime("%Y%m%d") + "0600"
    return (t - timedelta(days=1)).strftime("%Y%m%d") + "1800"


def fetch_mid(key: str) -> dict:
    """{'regId': {'YYYYMMDD': [오전하늘, 오후하늘, 오전강수확률, 오후강수확률]}}"""
    tm = mid_tmfc()
    base = datetime.strptime(tm[:8], "%Y%m%d")
    out = {}
    for reg in sorted(set(MID_REG.values()) | {"11D10000", "11D20000"}):
        try:
            its, _ = call(MID_URL, {"serviceKey": key, "dataType": "JSON", "numOfRows": 10, "pageNo": 1,
                                    "regId": reg, "tmFc": tm}, tries=2)
        except Exception as e:  # noqa: BLE001
            err(f"중기예보 {reg}", e)
            continue
        if not its:
            continue
        it = its[0]
        days: dict = {}
        for k, v in it.items():
            m = re.match(r"(wf|rnSt)(\d+)(Am|Pm)?$", k)
            if not m:
                continue
            n, half = int(m.group(2)), m.group(3) or ""
            d = (base + timedelta(days=n)).strftime("%Y%m%d")
            x = days.setdefault(d, ["", "", None, None])
            if m.group(1) == "wf":
                if half in ("Am", ""):
                    x[0] = str(v)
                if half in ("Pm", ""):
                    x[1] = str(v)
            else:
                p = fnum(v)
                if half in ("Am", ""):
                    x[2] = None if p is None else int(p)
                if half in ("Pm", ""):
                    x[3] = None if p is None else int(p)
        out[reg] = days
    status["sources"]["mid"] = {"tmFc": tm, "regions": len(out)}
    return {"tmFc": tm, "r": out}


# ================================================================ 이전 결과
def age_days(iso: str | None) -> float:
    if not iso:
        return 999
    try:
        return (NOW - datetime.fromisoformat(iso)).total_seconds() / 86400
    except ValueError:
        return 999


def load_enc(p: Path, password: str):
    try:
        return decrypt(json.loads(p.read_text(encoding="utf-8")), password)
    except Exception:  # noqa: BLE001
        return None


# ================================================================ 조립
def attach(camps: list[dict], med: dict, stores: list[list] | None, store_cache: dict, spots: list[list]) -> None:
    ge = Grid(med.get("e", []), 2, 3, 0.2)
    gh = Grid(med.get("h", []), 2, 3)
    gp = Grid(med.get("p", []), 2, 3)
    gs = Grid(stores or [], 2, 3, 0.05) if stores else None
    gt = Grid(spots, 2, 3)
    cnt = {"e": 0, "h": 0, "p": 0, "s": 0, "g": 0}
    R = lambda v: round(v, 4)  # noqa: E731
    for c in camps:
        la, lo = c["la"], c["lo"]
        e = ge.near(la, lo, 2, 80)
        h = gh.near(la, lo, 3, 40)
        p = gp.near(la, lo, 2, 30)
        c["ne"] = [[r[0], r[1], round(d, 1), R(r[2]), R(r[3]), r[5]] for d, r in e]
        c["nh"] = [[r[0], r[1], round(d, 1), R(r[2]), R(r[3]), r[4], r[5]] for d, r in h]
        c["np"] = [[r[0], r[1], round(d, 1), R(r[2]), R(r[3]), r[4]] for d, r in p]
        if gs:
            sc = gs.near(la, lo, 3, 15, keep=lambda r: r[1] == "c")
            sm = gs.near(la, lo, 3, 25, keep=lambda r: r[1] == "m")
        else:
            got = (store_cache.get(c["i"]) or {}).get("s") or []
            sl = sorted(((dist(la, lo, r[2], r[3]), r) for r in got), key=lambda x: x[0])
            sc = [x for x in sl if x[1][1] == "c"][:3]
            sm = [x for x in sl if x[1][1] == "m"][:3]
        c["ns"] = [[r[0], r[1], round(d, 1), R(r[2]), R(r[3])] for d, r in sorted(sc + sm, key=lambda x: x[0])]
        sp = gt.near(la, lo, 60, 25)
        sp = sorted(sp, key=lambda x: (-(x[1][5] >= 3), -x[1][5] + x[0] / 6))[:10]
        c["ng"] = [[r[0], r[1], round(d, 1), R(r[2]), R(r[3]), r[4], r[5], r[6]] for d, r in sorted(sp, key=lambda x: (-(x[1][5] >= 3), x[0]))]
        for k in cnt:
            cnt[k] += 1 if c.get("n" + k) else 0
    status["near_counts"] = cnt


def camp_row(c: dict) -> dict:
    keep = ("i", "n", "li", "ty", "lc", "a", "sg", "la", "lo", "t", "h", "r", "rc", "img", "f", "sb", "pf", "an",
            "op", "od", "x", "ne", "nh", "np", "ns", "ng", "wg", "mr")
    return {k: c[k] for k in keep if c.get(k) not in (None, "", [])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prev", default="")
    ap.add_argument("--out", default="site")
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--force-base", action="store_true")
    a = ap.parse_args()

    out = ROOT / a.out
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "docs", out)
    (out / "camps").mkdir(exist_ok=True)

    password = os.environ.get("PAGE_PASSWORD", "").strip() or ("1234" if a.sample else "")
    key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not password:
        print("PAGE_PASSWORD 가 없어요")
        return 1
    if not a.sample and not key:
        print("DATA_GO_KR_KEY 가 없어요")
        return 1
    prev = Path(a.prev) if a.prev else None
    pmeta = {}
    if prev and (prev / "meta.json").exists():
        try:
            pmeta = json.loads((prev / "meta.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    pdaily = (load_enc(prev / "daily.enc.json", password) if prev else None) or {}
    pcache = (load_enc(prev / "cache.enc.json", password) if prev else None) or {}

    meta = {"updated": NOW.strftime("%Y-%m-%d %H:%M"), "sample": a.sample, "base_at": pmeta.get("base_at"),
            "regions": pmeta.get("regions", {}), "counts": pmeta.get("counts", {})}

    # ---------- 매주: 캠핑장 + 근처
    camps = None
    if a.sample:
        import sample  # noqa: PLC0415
        camps, med, stores, spots, fests, short, mid = sample.make(NOW)
        attach(camps, med, stores, {}, spots)
        meta["base_at"] = NOW.isoformat(timespec="minutes")
    else:
        need = a.force_base or age_days(pmeta.get("base_at")) > BASE_DAYS or pmeta.get("sample") or not pmeta.get("regions")
        if not need and prev:
            f = next((prev / "camps").glob("*.enc.json"), None) if (prev / "camps").exists() else None
            if not f or load_enc(f, password) is None:  # 비밀번호 바뀜
                need = True
        store_cache = pcache.get("stores", {})
        if need:
            try:
                camps = fetch_camps(key)
            except Exception as e:  # noqa: BLE001
                err("고캠핑", e)
            if camps:
                med = fetch_medical(key)
                stores = None
                try:
                    stores = fetch_stores_bulk(key)
                    status["sources"]["stores"] = {"mode": "업종별 전국", "got": len(stores)}
                except Exception as e:  # noqa: BLE001
                    err("상가 업종별 (반경 조회로 대신함)", e)
                    try:
                        store_cache = fetch_stores_radius(key, camps, store_cache)
                    except Exception as e2:  # noqa: BLE001
                        err("상가 반경", e2)
                try:
                    spots = fetch_spots(key)
                except Exception as e:  # noqa: BLE001
                    err("관광지", e)
                    spots = []
                attach(camps, med, stores, store_cache, spots)
                meta["base_at"] = NOW.isoformat(timespec="minutes")
                if not stores and (status["sources"].get("store_radius") or {}).get("left"):
                    # 가게를 다 못 채웠으면 내일 다시 이어서 받도록
                    meta["base_at"] = (NOW - timedelta(days=BASE_DAYS + 1)).isoformat(timespec="minutes")
                pcache["stores"] = store_cache if not stores else {}

    # 날씨 격자·중기권역 붙이기
    grids: dict = {}
    if camps:
        by_sg: dict[str, list] = {}
        for c in camps:
            by_sg.setdefault(c["sd"] + " " + c["sg"], []).append(c)
        for sgk, cs in by_sg.items():
            # 시·군·구 안 캠핑장 평균 좌표가 아니라, 가장 많이 모인 0.15도 칸 중심 격자 (산간 캠핑장 기준)
            cells: dict = {}
            for c in cs:
                cells.setdefault((round(c["la"] / .15), round(c["lo"] / .15)), []).append(c)
            for cl in cells.values():
                la = sum(c["la"] for c in cl) / len(cl)
                lo = sum(c["lo"] for c in cl) / len(cl)
                g = to_grid(la, lo)
                for c in cl:
                    c["wg"] = f"{g[0]},{g[1]}"
                grids[g] = 1
        for c in camps:
            c["mr"] = mid_reg(c["sd"], c["lo"])
        meta["grids"] = sorted(f"{g[0]},{g[1]}" for g in grids)
    else:
        meta["grids"] = pmeta.get("grids", [])
        grids = {tuple(int(x) for x in g.split(",")): 1 for g in meta["grids"]}

    # ---------- 매일: 날씨·행사
    if not a.sample:
        try:
            short = fetch_short(key, sorted(grids), pdaily.get("short", {})) if grids else pdaily.get("short", {})
        except Exception as e:  # noqa: BLE001
            err("단기예보", e)
            short = pdaily.get("short", {})
        try:
            mid = fetch_mid(key)
            if not mid["r"]:
                mid = pdaily.get("mid", mid)
        except Exception as e:  # noqa: BLE001
            err("중기예보", e)
            mid = pdaily.get("mid", {})
        try:
            fests = fetch_festivals(key)
        except Exception as e:  # noqa: BLE001
            err("행사", e)
            fests = pdaily.get("fest", [])
    daily = {"updated": meta["updated"], "short": short, "mid": mid, "fest": fests}
    (out / "daily.enc.json").write_text(json.dumps(encrypt(daily, password, compress=True)), encoding="utf-8")
    (out / "cache.enc.json").write_text(json.dumps(encrypt(pcache, password, compress=True)), encoding="utf-8")

    # ---------- 캠핑장 파일
    if camps:
        meta["regions"], meta["counts"] = {}, {"camps": len(camps)}
        by_reg: dict[str, list] = {}
        for c in camps:
            by_reg.setdefault(c["sd"], []).append(c)
        for reg, cs in by_reg.items():
            cs.sort(key=lambda c: c["n"])
            rows = [camp_row(c) for c in cs]
            (out / "camps" / f"{CODES[reg]}.enc.json").write_text(
                json.dumps(encrypt({"region": reg, "c": rows}, password, compress=True)), encoding="utf-8")
            meta["regions"][reg] = {"code": CODES[reg], "n": len(cs),
                                    "box": [min(c["la"] for c in cs), min(c["lo"] for c in cs),
                                            max(c["la"] for c in cs), max(c["lo"] for c in cs)]}
    elif prev and (prev / "camps").exists():
        for f in (prev / "camps").glob("*.enc.json"):
            shutil.copy(f, out / "camps" / f.name)
        status["reused"].append("camps")
    else:
        status["errors"].append("캠핑장 데이터 없음 — 활용신청 승인 여부 확인")

    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    status["updated"] = meta["updated"]
    (out / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in status.items() if k != "sources"}, ensure_ascii=False))
    print({k: ({kk: vv for kk, vv in v.items() if kk not in ("fields",) and not kk.startswith("sample")} if isinstance(v, dict) else v)
           for k, v in status["sources"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
