# ==================================================
# 배차 상담 챗봇 (F-25)
# ==================================================
# 질문 한 건을 받아 알맞은 기능을 골라 부르고 그 결과로 답합니다.
#
# 처리 흐름
#   질문
#     ↓ 의도 분류 (키워드 규칙. 작은 모델에 기능 선택을 맡기지 않음)
#   predict / advise / ask / simulate / optimize / 범위 밖
#     ↓ 값 뽑기 (노선 · 날짜 · 시간 · 배차간격). 이전 대화의 값을 이어 씀
#   빠진 값이 있으면 되묻기 (clarify)
#     ↓ 기능 호출 (어댑터의 기존 기능을 그대로 부름. 로직을 다시 만들지 않음)
#   답변에 쓸 값(facts)만 추림
#     ↓ 문장 틀에 값 채우기
#   숫자 검증: 답변 속 숫자가 모두 facts·질문 값에 있는지 확인
#
# 원칙
# - 숫자는 기능이 돌려준 값만 씁니다. 문장 틀에 고정 숫자를 적지 않습니다.
# - 모든 답변은 needs_review=True (담당자 검토 필요)

import datetime as dt
import json
import logging
import math
import re
import time

from fastapi import HTTPException

from app.config import NOT_FOUND_ANSWER, RAG_TOP_K
from app.core import llm as llm_module
from app.core.domain import DomainAdapter
from app.core.llm import LLMConfigError
from app.core.prompts import CHAT_SYSTEM_MESSAGE

logger = logging.getLogger(__name__)

# ==================================================
# 1. 의도 분류 (키워드 규칙)
# ==================================================
# 위에서부터 차례로 확인합니다. 놓친 표현은 여기에 키워드를 추가합니다.

# 규정 질문이 분명한 단어: 노선이 함께 나와도 규정 질의응답으로 보냄
ASK_STRONG = ("법", "규정", "조례", "규칙", "절차", "신고", "인가", "허가", "과징금", "과태료",
              "사업계획", "준공영", "려면")
# 노선이 없을 때만 규정 질문으로 보는 단어
# ("해야"는 넣지 않음: "증차해야 해?"는 규정이 아니라 배차 권고 질문)
ASK_WEAK = ("기준", "가능한가", "의무", "역할", "어떻게 구분")
OPTIMIZE_WORDS = ("최적화", "최적 배차", "전체 노선", "전 노선", "모든 노선")
SIMULATE_WORDS = ("시뮬", "바꾸면", "바꿨을 때", "바꾼다면", "으로 하면", "로 하면", "줄이면", "늘리면", "좁히면", "넓히면")
ADVISE_WORDS = ("권고", "추천", "증차", "감차", "늘려야", "줄여야", "늘릴까", "줄일까", "조정", "적당", "적정")
# 길안내: "A에서 B 어떻게 가?" (요금·규정을 묻는 말이 섞이면 규정 질문으로 봄)
GUIDE_WORDS = ("어떻게 가", "어떻게가", "가는 법", "가는법", "가는 방법", "가는 길", "가려면", "가고 싶", "갈 수 있",
               "갈래", "길안내", "길 안내", "길 알려", "환승", "몇 번 타", "뭐 타", "무슨 버스")
GUIDE_BLOCK = ("규정", "법", "조례", "요금", "할인", "절차")
# 노선 기본 정보: "160번 노선 알려줘", "143번 첫차 몇 시야?" (노선을 말했을 때만)
ROUTE_INFO_WORDS = ("노선 알려", "노선알려", "노선 정보", "노선정보", "노선도", "노선 좀", "어디서 어디", "어디 가", "어디가",
                    "어디로 가", "어디까지", "어디를", "경유", "지나", "정류장", "첫차", "막차", "기점", "종점",
                    "몇 분마다", "몇분마다", "배차간격이", "배차 간격이", "배차간격은", "배차 간격은", "운행시간", "운행 시간",
                    "인가대수", "몇 대", "어떤 버스", "무슨 노선", "소개")
PREDICT_WORDS = ("몇 명", "몇명", "승객", "승차", "붐", "혼잡", "예측", "수요", "사람 많", "사람이 많", "여유", "한가", "탈까", "타나")
# 버스 업무와 관련된 질문인지 가리는 단어 (하나도 없고 노선도 없으면 범위 밖)
DOMAIN_WORDS = ("버스", "노선", "배차", "운행", "정류", "운수", "요금", "기사", "차량", "증차", "감차", "승객", "혼잡")

# 기능마다 꼭 있어야 하는 값
REQUIRED = {
    "predict": ("route", "date"),
    "advise": ("route", "date", "hour"),
    "simulate": ("route", "date", "hour", "interval"),
    "optimize": ("date", "hour"),
    "route_info": ("route",),
}
NOT_CONNECTED = {"simulate": "배차간격 시뮬레이션", "optimize": "전 노선 배차 최적화"}   # 엔진 연결 전

OUT_OF_SCOPE_ANSWER = "🙏 버스 승객 예측, 배차 권고, 노선 정보, 길안내, 운수 규정에 대한 질문에만 답할 수 있어요."

# 답변을 읽기 쉽게 하는 표시 (숫자가 아니므로 숫자 검증과 무관)
LEVEL_MARK = {"여유": "🟢", "보통": "🟡", "혼잡": "🟠", "매우혼잡": "🔴"}
ACTION_MARK = {"increase": "⬆️", "increase_limited": "⚠️", "decrease": "⬇️", "keep": "✅"}


def _has(text: str, words: tuple) -> bool:
    return any(w in text for w in words)


def classify(message: str, has_route: bool) -> str | None:
    """질문 한 건의 의도. 키워드가 하나도 안 맞으면 None"""
    if _has(message, GUIDE_WORDS):
        rest = message
        for word in GUIDE_WORDS:          # "가는 법"의 '법'을 규정 단어로 보지 않게 길안내 표현을 먼저 지움
            rest = rest.replace(word, " ")
        if not _has(rest, GUIDE_BLOCK):
            return "guide"
    if _has(message, ASK_STRONG):
        return "ask"
    if _has(message, OPTIMIZE_WORDS):
        return "optimize"
    if _has(message, SIMULATE_WORDS) and ("분" in message or "간격" in message):
        return "simulate"
    if not has_route and _has(message, ASK_WEAK):
        return "ask"                                   # "혼잡도는 어떻게 구분하나요?"
    if _has(message, ADVISE_WORDS):
        return "advise"
    if _has(message, PREDICT_WORDS):
        return "predict"
    if _has(message, ROUTE_INFO_WORDS):
        return "route_info"
    return None


# ==================================================
# 2. 값 뽑기 (노선 · 날짜 · 시간 · 배차간격)
# ==================================================

RELATIVE_DAYS = {"그저께": -2, "어제": -1, "오늘": 0, "내일": 1, "모레": 2}
PM_WORDS = ("오후", "저녁", "밤", "낮")
COMMUTE_HOURS = {"출근": 8, "퇴근": 18}     # "출근 시간"처럼 시각 없이 물을 때의 대표 시간대

_ROUTE_RE = re.compile(r"(\d{1,4}(?:-\d)?)\s*번")
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_KOR_DATE_RE = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_SLASH_DATE_RE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])")
_HOUR_RE = re.compile(r"(오전|오후|아침|낮|저녁|밤|새벽)?\s*(\d{1,2})\s*시(?!간)")
_CLOCK_RE = re.compile(r"(?<!\d)(\d{1,2}):\d{2}(?!\d)")
_MINUTE_AFTER_HOUR_RE = re.compile(r"(\d{1,2}\s*시)\s*\d{1,2}\s*분")
_INTERVAL_RE = re.compile(r"(\d+(?:\.\d+)?)\s*분")


def _safe_date(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def extract(message: str, routes: list[str], today: dt.date) -> dict:
    """질문에서 찾은 값만 담아 돌려줍니다 (없는 값은 키 자체가 없음)"""
    slots: dict = {}

    # 노선: "160번"이 먼저, 없으면 아는 노선번호가 단독으로 나온 경우
    m = _ROUTE_RE.search(message)
    if m:
        slots["route"] = m.group(1)
    else:
        for route in sorted(routes, key=len, reverse=True):
            if re.search(rf"(?<![\d.\-/:]){re.escape(route)}(?![\d.\-/:]|\s*[월일시분명회대년%])", message):
                slots["route"] = route
                break

    # 날짜
    found = None
    if m := _ISO_DATE_RE.search(message):
        found = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    elif m := _KOR_DATE_RE.search(message):
        found = _safe_date(today.year, int(m.group(1)), int(m.group(2)))
    elif m := _SLASH_DATE_RE.search(message):
        found = _safe_date(today.year, int(m.group(1)), int(m.group(2)))
    else:
        for word, offset in RELATIVE_DAYS.items():
            if word in message:
                found = today + dt.timedelta(days=offset)
                break
    if found:
        slots["date"] = found.isoformat()

    # 시간: "오후 3시" · "15시" · "15:00" · "출근 시간"
    hour = None
    if m := _HOUR_RE.search(message):
        hour = int(m.group(2))
        if m.group(1) in PM_WORDS and hour < 12:
            hour += 12
        elif m.group(1) in ("오전", "밤", "새벽") and hour == 12:
            hour = 0
    elif m := _CLOCK_RE.search(message):
        hour = int(m.group(1))
    else:
        for word, value in COMMUTE_HOURS.items():
            if word in message:
                hour = value
                break
    if hour is not None and 0 <= hour <= 23:
        slots["hour"] = hour

    # 배차간격(분): "3시 30분"의 분은 간격이 아니므로 먼저 지움
    m = _INTERVAL_RE.search(_MINUTE_AFTER_HOUR_RE.sub(r"\1", message))
    if m:
        slots["interval"] = float(m.group(1))

    return slots


# 길안내의 출발·도착: "정릉우체국앞에서 종로3가역 어떻게 가?" / "여기서 신동중학교 가는 법"
FROM_SUFFIXES = ("에서", "부터", "서")
HERE_WORDS = ("여기", "위치", "이곳", "지금", "내가 있는", "근처")
_NOT_A_PLACE_RE = re.compile(r"^(?:[0-9]+(?:시|월|일|분|번)?|오전|오후|아침|저녁|밤|새벽|오늘|내일|모레|버스로?|지금)$")


def extract_places(message: str) -> dict:
    """길안내 질문에서 출발·도착 이름을 뽑음. 조사는 떼지 않고 그대로 넘김 (정류장 이름 대조는 길안내 기능이 함)

    돌려주는 값: origin(출발 이름) · from_here(현재 위치에서 출발) · destination(도착 이름) 중 찾은 것만
    """
    positions = [message.find(w) for w in GUIDE_WORDS if w in message]
    head = message[:min(positions)] if positions else message
    out: dict = {}
    rest = []
    for token in head.split():
        suffix = next((s for s in FROM_SUFFIXES if token.endswith(s) and len(token) > len(s)), None)
        if suffix and "origin" not in out and "from_here" not in out:
            name = token[: -len(suffix)]
            if _has(name, HERE_WORDS):
                out["from_here"] = True
            else:
                out["origin"] = name
        elif not _NOT_A_PLACE_RE.match(token) and not _has(token, HERE_WORDS):
            rest.append(token)
    if rest:
        out["destination"] = rest[-1]
    return out


# ==================================================
# 3. 숫자 검증
# ==================================================

_NUMBER_RE = re.compile(r"\d+(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def _numbers_in(text: str) -> list[tuple[float, int]]:
    """문장 속 숫자와 소수 자릿수: "1,234.5" → (1234.5, 1)"""
    out = []
    for token in _NUMBER_RE.findall(text):
        token = token.replace(",", "")
        out.append((float(token), len(token.split(".")[1]) if "." in token else 0))
    return out


def _collect_numbers(value) -> set[float]:
    """dict·list·문자열 안의 숫자를 모두 모음 (bool 제외)"""
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float)):
        return set() if isinstance(value, float) and math.isnan(value) else {float(value)}
    if isinstance(value, str):
        return {n for n, _ in _numbers_in(value)}
    if isinstance(value, dict):
        value = list(value.values())
    out: set[float] = set()
    for item in value:
        out |= _collect_numbers(item)
    return out


def verify_numbers(answer: str, *evidence) -> bool:
    """답변 속 숫자가 모두 근거 값에 있으면 True.

    표시 자릿수가 달라도 같은 값이면 통과합니다 (0.618을 0.61이나 0.62로 쓴 경우).
    """
    allowed = _collect_numbers(list(evidence))
    for shown, digits in _numbers_in(answer):
        scale = 10 ** digits
        if not any(
            abs(round(a, digits) - shown) < 1e-9 or abs(math.floor(a * scale + 1e-9) / scale - shown) < 1e-9
            for a in allowed
        ):
            return False
    return True


# LLM이 문장을 다듬다가 결론을 뒤집는 것을 막는 확인 (숫자는 같아도 "유지"를 "감차해도 됨"으로 바꾸는 경우)
# 권고마다 답변에 꼭 있어야 하는 말과, 있으면 안 되는 말
ACTION_MUST = {
    "increase": ("단축", "증차", "늘리", "늘려"),
    "increase_limited": ("최소 배차간격", "별도 대책", "한계"),
    "decrease": ("감차", "줄이", "줄여"),
    "keep": ("유지",),
}
ACTION_FORBID = {
    "increase": ("유지", "않아도", "필요 없", "필요없", "감차"),
    "increase_limited": ("감차해도",),
    "decrease": ("증차해야", "늘려야", "단축해야"),
    "keep": ("증차해야", "증차가 필요", "늘려야", "단축해야", "감차해도", "감차가 필요", "감차해야", "줄여도", "줄여야"),
}
LEVELS = ("매우혼잡", "혼잡", "보통", "여유")


def keeps_meaning(answer: str, facts: dict) -> bool:
    """다시 쓴 답변이 권고 방향과 혼잡 단계를 그대로 전하면 True"""
    action = facts.get("action")
    if action:
        if not _has(answer, ACTION_MUST.get(action, ())) or _has(answer, ACTION_FORBID.get(action, ())):
            return False
    level = facts.get("level")
    if level:
        # 답변에 혼잡 단계를 적었다면 기능 결과의 단계여야 함 ("혼잡"은 "매우혼잡"에도 들어 있어 긴 말부터 지우며 확인)
        rest = answer.replace("혼잡도", " ").replace("혼잡 구간", " ").replace("혼잡 단계", " ")   # 단계가 아닌 말
        for word in LEVELS:
            if word in rest and word != level:
                return False
            rest = rest.replace(word, " ")
    return True


# ==================================================
# 4. 기능 호출 → 답변에 쓸 값(facts) → 문장
# ==================================================

def _people(value) -> str:
    return f"{int(round(value)):,}명"


def _short(value) -> str:
    """7.0 → 7, 7.5 → 7.5"""
    return f"{value:g}"


def _floor2(value) -> str:
    """혼잡도는 화면(fmtCongestion)과 같게 소수 둘째 자리에서 버림 (0.798을 0.80으로 올리면 단계와 어긋남)"""
    return f"{math.floor(value * 100 + 1e-9) / 100:.2f}"


def _sources(items: list[dict] | None) -> list[dict]:
    keys = ("id", "document", "article", "page", "url", "score", "snippet")
    return [{k: s.get(k) for k in keys} for s in items or []]


def _predict(adapter: DomainAdapter, slots: dict, **_) -> tuple[dict, str, list[dict], dict]:
    args = {k: slots[k] for k in ("route", "date", "hour") if k in slots}
    result = adapter.predict(dict(args))
    route, date = result["route"], result["date"]

    if "hour" not in args:
        peak = max(result["hourly"], key=lambda h: h["pred"] or 0)
        s = result["summary"]
        facts = {"route": route, "date": date, "pred_total": s["pred_total"], "baseline_total": s["baseline_total"],
                 "actual_total": s["actual_total"], "peak_hour": peak["hour"], "peak_pred": peak["pred"]}
        answer = f"🚌 {route}번 {date} 하루 예상 승차는 {_people(s['pred_total'])}입니다."
        if s["baseline_total"] is not None:
            answer += f" 작년 같은 요일은 {_people(s['baseline_total'])}이었습니다."
        if s["actual_total"] is not None and not result["is_forecast"]:
            answer += f" 실제 승차는 {_people(s['actual_total'])}이었습니다."
        answer += f"\n⏰ 승차가 가장 많은 시간대는 {peak['hour']}시({_people(peak['pred'])})입니다."
        return args, answer, [], facts

    hour = args["hour"]
    row = next((h for h in result["hourly"] if h["hour"] == hour), None)
    if row is None or row["pred"] is None:
        raise HTTPException(422, f"{route}번 {hour}시는 운행 데이터가 없는 시간대입니다")
    congestion = result.get("congestion") or {}
    facts = {"route": route, "date": date, "hour": hour, "pred": row["pred"], "baseline": row["baseline"],
             "actual": row["actual"], "congestion": congestion.get("ratio"), "level": congestion.get("level")}
    answer = f"🚌 {route}번 {date} {hour}시 예상 승차는 {_people(row['pred'])}입니다."
    if row["baseline"] is not None:
        answer += f" 작년 같은 요일·시간대는 {_people(row['baseline'])}이었습니다."
    if row["actual"] is not None and not result["is_forecast"]:
        answer += f" 실제 승차는 {_people(row['actual'])}이었습니다."
    if congestion.get("ratio") is not None:
        answer += (f"\n{LEVEL_MARK.get(congestion['level'], '')} 가장 붐비는 구간의 혼잡도는"
                   f" {_floor2(congestion['ratio'])}({congestion['level']})입니다.")
    return args, answer, [], facts


def _advise(adapter: DomainAdapter, slots: dict, llm: str, embedding: str, **_) -> tuple[dict, str, list[dict], dict]:
    args = {k: slots[k] for k in ("route", "date", "hour")}
    # 규정 요약은 LLM 없이 근거 조항만 붙임 (빠름). 문장이 필요하면 규정을 따로 물으면 됨
    result = adapter.advise(dict(args), llm, embedding, False)
    rec, cur, con = result["recommendation"], result["current"], result["congestion"]
    peak = result["prediction"]["peak_segment"]
    facts = {
        "route": result["route"], "date": result["date"], "hour": result["hour"],
        "action": rec["action"], "label": rec["label"],
        "current_interval_min": cur["interval_min"], "current_trips_per_hour": cur["trips_per_hour"],
        "congestion": con["ratio"], "level": con["level"],
        "peak_from": peak["from_name"], "peak_to": peak["to_name"],
        "recommended_interval_min": rec["recommended_interval_min"],
        "expected_congestion": rec["expected_congestion"], "extra_buses": rec["extra_buses"],
    }
    answer = (
        f"{ACTION_MARK.get(rec['action'], '')} {facts['route']}번 {facts['date']} {facts['hour']}시 권고는 '{rec['label']}'입니다."
        f"\n🚌 지금은 약 {_short(cur['interval_min'])}분 간격(시간당 {_short(cur['trips_per_hour'])}회)이고,"
        f"\n{LEVEL_MARK.get(con['level'], '')} 가장 붐비는 구간({peak['from_name']} → {peak['to_name']})의"
        f" 혼잡도는 {_floor2(con['ratio'])}({con['level']})입니다."
    )
    if rec["action"] == "keep":
        answer += "\n지금 배차간격을 유지해도 됩니다."
    else:
        answer += (f"\n👉 권고 배차간격은 {_short(rec['recommended_interval_min'])}분이고,"
                   f" 그때 예상 혼잡도는 {_floor2(rec['expected_congestion'])}입니다.")
        if rec["extra_buses"] > 0:
            answer += f" 버스가 약 {rec['extra_buses']}대 더 필요합니다."
    answer += "\n📝 최종 결정은 담당자 검토가 필요합니다."
    return args, answer, _sources(result["evidence"].get("sources")), facts


def _ask(adapter: DomainAdapter, message: str, llm: str, embedding: str, use_llm: bool, **_) -> tuple[dict, str, list[dict], dict]:
    result = adapter.knowledge_base.ask(message, llm, embedding, RAG_TOP_K, use_llm)
    sources = _sources(result.get("sources"))
    if not result["found"]:
        return {"question": message}, NOT_FOUND_ANSWER, [], {}
    if result.get("answer"):
        answer = result["answer"]
    else:   # use_llm=False: 근거 조항만
        lines = "\n".join(f"[{s['id']}] {s['document']} · {s['article']}" for s in sources)
        answer = f"📚 관련 조항 {len(sources)}건을 찾았습니다. 아래 근거를 확인하세요.\n{lines}"
    return {"question": message}, answer, sources, {"sources": sources, "llm_answer": bool(result.get("answer"))}


def _guide(adapter: DomainAdapter, slots: dict, **_) -> tuple[dict, str, list[dict], dict]:
    args = {k: slots[k] for k in ("origin", "destination", "lat", "lng", "date", "hour") if slots.get(k) is not None}
    result = adapter.guide(dict(args))
    origin, dest, options = result["origin"], result["destination"], result["options"]
    history_note = (f"↩️ 출발지는 앞에서 말씀하신 {origin['stop']}(으)로 했습니다. 다른 곳이면 '○○에서' 또는 '여기서'라고 알려 주세요."
                    if slots.get("origin_from_history") else None)

    lines = [f"🧭 {origin['stop']} → {dest['stop']} ({result['date']} {result['hour']}시 기준)"]
    facts = {"date": result["date"], "hour": result["hour"], "origin": origin["stop"], "destination": dest["stop"],
             "numbers": list(range(1, len(options) + 1)), "options": []}
    if history_note:
        lines.append(history_note)
    if result.get("requested_date"):
        facts["requested_date"] = result["requested_date"]
        lines.append(f"📅 {result['requested_date']}은 예측 범위 밖이라, 같은 요일인 {result['date']}의 혼잡도·배차간격으로 안내합니다.")
    if origin["far"]:
        km = round(origin["walk_m"] / 1000, 1)
        facts["origin_km"] = km
        lines.append(f"⚠️ 현재 위치에서 가장 가까운 정류장({origin['stop']})까지 약 {km}km입니다. 그 정류장에서 타는 경로로 안내합니다.")
    elif origin["walk_m"]:
        facts["origin_walk_m"] = origin["walk_m"]
        lines.append(f"📍 가장 가까운 정류장은 {origin['stop']}(약 {origin['walk_m']}m)입니다.")

    if not options:
        lines.append("😢 두 정류장을 잇는 경로를 찾지 못했습니다 (직행·환승 한 번 기준).")
        return args, "\n".join(lines), [], {**facts, "data": result}

    for number, option in enumerate(options, start=1):
        kind = "직행" if option["kind"] == "direct" else "환승"
        tag = " · 가장 빠름" if option["fastest"] else ""
        tag += " · 덜 붐빔" if option["least_crowded"] and not option["fastest"] else ""
        lines.append(f"{'🚌' if option['kind'] == 'direct' else '🔁'} {number}안 {kind} · 약 {option['total_min']}분{tag}")
        for leg in option["legs"]:
            crowd = (f", 혼잡도 {_floor2(leg['congestion'])}({leg['level']}) {LEVEL_MARK.get(leg['level'], '')}"
                     if leg["congestion"] is not None else ", 혼잡도 예측 없음")
            walk = f" (걸어서 약 {leg['walk_m']}m)" if leg["walk_m"] else ""
            exit_walk = f" (내려서 약 {leg['exit_walk_m']}m 걷기)" if leg.get("exit_walk_m") else ""
            lines.append(
                f"   {leg['route']}번: {leg['board']['name']}{walk} → {leg['alight']['name']}{exit_walk}"
                f" · 정류장 {leg['stops']}개 · 약 {leg['ride_min']}분 · 배차간격 약 {_short(leg['interval_min'])}분{crowd}"
            )
        facts["options"].append({
            "total_min": option["total_min"],
            "legs": [{k: leg[k] for k in ("route", "stops", "walk_m", "exit_walk_m", "ride_min", "interval_min", "congestion")}
                     | {"board": leg["board"]["name"], "alight": leg["alight"]["name"]} for leg in option["legs"]],
        })

    rec = result["recommendation"]
    if rec:
        number = rec["index"] + 1
        if rec["reason"] == "less_crowded":
            facts["slower_by_min"] = rec["slower_by_min"]
            lines.append(f"👉 추천은 {number}안입니다. 약 {rec['slower_by_min']}분 더 걸리지만 덜 붐빕니다.")
        else:
            lines.append(f"👉 추천은 {number}안입니다. 가장 빨리 도착합니다.")
    lines.append("ℹ️ 버스만 안내하고(지하철 제외), 소요 시간은 어림한 값입니다.")
    return args, "\n".join(lines), [], {**facts, "data": result}


def _route_info(adapter: DomainAdapter, slots: dict, **_) -> tuple[dict, str, list[dict], dict]:
    args = {"route": slots["route"]}
    r = adapter.route_info(dict(args))
    kind = " · ".join(x for x in (r.get("route_type"), r.get("company")) if x)
    lines = [f"🚌 {r['route']}번" + (f" ({kind})" if kind else "")]
    if r.get("start") and r.get("end"):
        length = f" · 약 {_short(r['distance_km'])}km" if r.get("distance_km") else ""
        lines.append(f"📍 {r['start']} ↔ {r['end']} · 정류장 {r['stops_count']}개{length}")
    if r.get("first_bus") and r.get("last_bus"):
        lines.append(f"⏰ 첫차 {r['first_bus']} · 막차 {r['last_bus']}")
    if r.get("interval_weekday") is not None:
        line = f"⏱️ 평일 배차간격 약 {_short(r['interval_weekday'])}분"
        if r.get("min_interval") is not None and r.get("max_interval") is not None:
            line += f" (시간대에 따라 {_short(r['min_interval'])}~{_short(r['max_interval'])}분)"
        lines.append(line)
    extra = []
    if r.get("run_minutes") is not None:
        extra.append(f"한 바퀴 약 {_short(r['run_minutes'])}분")
    if r.get("authorized_buses") is not None:
        extra.append(f"인가대수 {_short(r['authorized_buses'])}대" + (" (추정)" if r.get("authorized_estimated") else ""))
    if extra:
        lines.append("🚏 " + " · ".join(extra))
    if r.get("key_stops"):
        lines.append("🗺️ 주요 정류장: " + " → ".join(r["key_stops"]))
    if r.get("predicted") is False:
        lines.append("ℹ️ 이 노선은 승객 수·혼잡도 예측을 하지 않습니다. 기본 정보만 안내합니다.")
    return args, "\n".join(lines), [], dict(r)


HANDLERS = {"predict": _predict, "advise": _advise, "ask": _ask, "guide": _guide, "route_info": _route_info}


def _rewrite(llm: str, message: str, template: str, facts: dict) -> str | None:
    """문장 틀 답변을 LLM이 자연스럽게 다시 씀. 숫자가 하나라도 달라지면 None (문장 틀 답변을 그대로 씀)"""
    shown = {k: v for k, v in facts.items() if k != "data"}
    user = f"[기능 결과]{chr(10)}{json.dumps(shown, ensure_ascii=False)}{chr(10)}{chr(10)}[기본 답변]{chr(10)}{template}{chr(10)}{chr(10)}[질문]{chr(10)}{message}"
    try:
        answer = llm_module.generate(llm, CHAT_SYSTEM_MESSAGE, user).strip()
    except LLMConfigError:
        raise
    except Exception as error:      # 모델 로드 실패·메모리 부족 등: 문장 틀 답변으로 계속
        logger.warning("챗봇 LLM 문장 작성 실패, 문장 틀 답변 사용: %s", error)
        return None
    ok = bool(answer) and verify_numbers(answer, shown, template) and keeps_meaning(answer, shown)
    return answer if ok else None


# ==================================================
# 5. 실행
# ==================================================

def _clarify(intent: str, slots: dict, routes: list[str]) -> str | None:
    """꼭 있어야 하는 값이 빠졌으면 되묻는 문장, 다 있으면 None"""
    ask_for = {
        "route": f"노선(가능: {', '.join(routes)})" if routes else "노선",
        "date": "날짜(예: 내일, 11월 20일)",
        "hour": "시간(예: 오후 3시)",
        "interval": "바꿔 볼 배차간격(예: 5분)",
    }
    missing = [ask_for[k] for k in REQUIRED.get(intent, ()) if k not in slots]
    return f"🙋 다음 값을 알려 주세요: {' · '.join(missing)}" if missing else None


def run(adapter: DomainAdapter, message: str, history: list[dict] | None = None,
        llm: str = "qwen", embedding: str = "bge", use_llm: bool = False,
        location: dict | None = None, now: dt.datetime | None = None) -> dict:
    """location: 화면에서 보낸 현재 위치 {"lat", "lng"} (길안내에 사용). now: 테스트에서 '지금'을 고정할 때"""
    t0 = time.time()
    now = now or dt.datetime.now()
    today = now.date()
    routes = adapter.targets()
    message = message.strip()

    # 이전 질문의 값과 의도를 이어 씀: "그럼 4시는?" → 앞에서 말한 노선·날짜 유지
    slots: dict = {}
    places: dict = {}
    previous_intent = None
    for turn in history or []:
        if turn.get("role") != "user":
            continue
        past = extract(turn["content"], routes, today)
        past_intent = classify(turn["content"], "route" in past)
        if past_intent == "guide":
            past.pop("route", None)                   # "143번 정류장"처럼 길안내 문장 속 숫자는 노선으로 이어 쓰지 않음
            places.update(extract_places(turn["content"]))
        elif previous_intent == "guide" and past_intent is None and not past:
            _fill_place(places, turn["content"])      # 되묻기에 정류장 이름만 답한 경우
        slots.update(past)
        previous_intent = past_intent or previous_intent
    current = extract(message, routes, today)
    slots.update(current)

    intent = classify(message, "route" in current)
    said: dict = {}          # 이번 질문에서 직접 말한 출발·도착
    if intent == "guide":
        said = extract_places(message)
        places.update(said)
    elif intent is None and previous_intent == "guide" and not _has(message, DOMAIN_WORDS):
        intent = "guide"                              # 길안내 되묻기에 대한 답
        if not current:
            _fill_place(places, message)
    if intent is None and current and previous_intent:
        intent = previous_intent                      # 값만 말한 경우 (되묻기에 대한 답 포함)
    if intent is None:
        intent = "ask" if _has(message, DOMAIN_WORDS) else "out_of_scope"
    elif intent in ("predict", "advise") and "route" not in slots:
        if not _has(message, DOMAIN_WORDS):
            intent = "out_of_scope"                   # "점심 메뉴 추천해줘" 같은 질문
        elif "date" not in current and "hour" not in current:
            intent = "ask"                            # 노선·날짜·시간이 없는 일반 질문은 규정에서 찾음

    def done(intent, answer, tools=(), sources=(), verified=None, used_llm=None, rejected=False, data=None):
        return {
            "answer": answer, "intent": intent, "slots": slots, "tools": list(tools), "sources": list(sources),
            "numbers_verified": verified, "llm": used_llm, "llm_rejected": rejected, "data": data,
            "elapsed_sec": round(time.time() - t0, 2), "needs_review": True,
        }

    if intent == "out_of_scope":
        return done(intent, OUT_OF_SCOPE_ANSWER)

    if intent == "guide":
        # 길안내는 '지금'이 기본: 날짜·시간을 말하지 않으면 오늘 이 시각
        slots = {"date": slots.get("date", today.isoformat()), "hour": slots.get("hour", now.hour)}
        if places.get("destination"):
            slots["destination"] = places["destination"]
        # 출발지 우선순위: ① 이번 질문에서 말한 정류장 ② 이번 질문의 "여기서·내 위치에서" ③ 켜 둔 현재 위치
        #                 ④ 앞 대화에서 말한 정류장 (이때는 답변에 그 사실을 적음)
        wants_here = bool(said.get("from_here")) or (not said.get("origin") and places.get("from_here") and not places.get("origin"))
        if said.get("origin"):
            slots["origin"] = said["origin"]
        elif wants_here or location:
            if location:
                slots["lat"], slots["lng"] = location["lat"], location["lng"]
            else:
                places["from_here"] = True
                places.pop("origin", None)
        elif places.get("origin"):
            slots["origin"] = places["origin"]
            slots["origin_from_history"] = True
        if "destination" not in slots:
            return done("clarify", "🙋 어디로 가시나요? 도착 정류장 이름을 알려 주세요. (예: 종로3가역 어떻게 가?)")
        if "origin" not in slots and "lat" not in slots:
            if places.get("from_here"):
                slots["from_here"] = True     # 화면이 이 값을 보고 현재 위치를 얻어 다시 물음
                return done("clarify", "📍 현재 위치가 필요합니다. '현재 위치 사용'을 켜거나 출발 정류장 이름을 알려 주세요.")
            return done("clarify", "🙋 어디에서 출발하시나요? 출발 정류장 이름을 알려 주시거나 '현재 위치 사용'을 켜 주세요.")
    elif question := _clarify(intent, slots, routes):
        return done("clarify", question)
    if intent in NOT_CONNECTED:
        return done(intent, f"🛠️ {NOT_CONNECTED[intent]} 기능은 아직 챗봇에 연결되지 않았습니다. 대시보드의 해당 패널을 이용해 주세요.")

    try:
        args, answer, sources, facts = HANDLERS[intent](
            adapter, slots=slots, message=message, llm=llm, embedding=embedding, use_llm=use_llm)
    except HTTPException as error:
        # 없는 노선·범위를 벗어난 날짜 등: 기능이 알려 준 이유를 그대로 답함
        keys = REQUIRED.get(intent) or ("origin", "destination", "lat", "lng", "date", "hour")
        args = {k: slots[k] for k in keys if k in slots} or {"question": message}
        return done(intent, f"⚠️ {error.detail}", tools=[{"name": intent, "args": args, "ok": False}])

    tools = [{"name": intent, "args": args, "ok": True}]
    if intent == "ask":
        # 규정 답변의 숫자는 근거 조항 원문에서 오므로 여기서 대조하지 않음 (출처로 확인)
        used_llm = llm if facts.get("llm_answer") else None
        return done(intent, answer, tools, sources, verified=None, used_llm=used_llm)

    data = facts.get("data")          # 길안내 결과 전체 (화면이 지도에 그림)
    evidence = {k: v for k, v in facts.items() if k != "data"}
    # 문장 틀 답변이 기본. use_llm이면 LLM이 다시 쓰되, 숫자가 달라지면 버리고 문장 틀 답변을 씀
    if use_llm and intent in ("predict", "advise"):   # 길안내·노선 정보는 줄 단위 목록이라 다시 쓰지 않음
        rewritten = _rewrite(llm, message, answer, evidence)
        if rewritten:
            return done(intent, rewritten, tools, sources, verified=True, used_llm=llm, data=data)
        return done(intent, answer, tools, sources, verified=verify_numbers(answer, evidence, slots), rejected=True, data=data)
    return done(intent, answer, tools, sources, verified=verify_numbers(answer, evidence, slots), data=data)


def _fill_place(places: dict, text: str) -> None:
    """정류장 이름만 답한 문장을 빠진 자리(도착 → 출발 순)에 넣음"""
    name = text.strip().rstrip("?.!요")
    if not name:
        return
    if "destination" not in places:
        places["destination"] = name
    elif "origin" not in places and "from_here" not in places:
        places["origin"] = name
