"""
LLM 다시 쓰기 검증 가드 채점 — 맞는 문장은 채택하고 틀린 문장은 거절하는지 확인

대상: app/core/agent.py 의 same_numbers(숫자·단위·순서·설명) + keeps_meaning(권고·단계·추천안 뜻).
실제 채택 조건(agent.py L792)을 guard()에 그대로 옮겼습니다.
  accepted = bool(answer) and "�" not in answer and same_numbers(answer, template) and keeps_meaning(answer, facts)

채점
- 맞는 문장(right): 문장 틀 그대로(identity)와 어순·어미만 바꾼 문장(paraphrase). 채택돼야 정답(ok).
  거절된 paraphrase는 지우지 않고 과잉 거절(false_rejects)로 기록
- 틀린 문장(wrong): 맞는 문장을 한 가지 방식(kind)으로만 비틀어 만든 문장. 거절돼야 정답(ok).
  expected_guard는 그 kind를 막아야 할 가드의 기록용 표시(none = 원리상 막지 못할 수도 있음. 그래도 측정해서 보고)
- 두 가드는 단락 평가 없이 항상 둘 다 호출 → 탐지 주체별(detected_by_numbers/meaning) 수치를 냄
- identity 채택률이 1.0이 아니면 코퍼스 구성 버그이므로 먼저 의심

결과: eval/results/<이름>.json  (summary + rows)

사용 (LLM·서버 불필요. API 서버를 끈 상태 전제)
  .venv\\Scripts\\python scripts/eval_rewrite_guard.py --name rewrite_guard_1
  .venv\\Scripts\\python scripts/eval_rewrite_guard.py --name rewrite_guard_1 --seed 20261122 --per-kind 30
"""

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.agent import keeps_meaning, same_numbers  # noqa: E402
from app.domains.bus.advisor import ACTION_LABELS  # noqa: E402

SEED = 20261122
PER_KIND = 30
ATTEMPTS = 2                      # 원본 문장 하나당 kind별 변형 시도 횟수
ROUTES = ("143", "271", "160")
LEVELS = ("매우혼잡", "혼잡", "보통", "여유")
# 문장 변주용 값의 범위 (혼잡 단계 기준이 아니라 문장 다양화 목적)
LEVEL_RATIO_RANGE = {"여유": (0.20, 0.45), "보통": (0.46, 0.70), "혼잡": (0.71, 0.95), "매우혼잡": (0.96, 1.40)}
LEVEL_MARK = {"여유": "🟢", "보통": "🟡", "혼잡": "🟠", "매우혼잡": "🔴"}
ACTION_MARK = {"increase": "⬆️", "increase_limited": "⚠️", "decrease": "⬇️", "keep": "✅"}
STOPS = (("정릉", "길음"), ("미아", "수유"), ("종로", "동대문"))
ACTION_FLIP = {"keep": "decrease", "increase": "keep", "decrease": "increase", "increase_limited": "decrease"}
KEYWORD_SWAPS = (("증차", "감차"), ("감차", "증차"), ("단축", "유지"), ("유지", "단축"))
LABEL_PAIRS = (("작년", "예상"), ("지금", "권고"))
UNIT_CHANGES = {"명": ("회", "대"), "분": ("시간",), "시": ("시간",)}
MODIFIER_SUFFIXES = ("의 두 배", " 미만", " 이상", "의 절반")
NEGATION_KEYS = ("권고", "유지", "증차", "감차", "단축", "혼잡", "여유", "보통", "한계", "대책")
NEGATION_ENDINGS = (("해도 됩니다", "하지 않아도 됩니다"), ("권고합니다", "권고하지 않습니다"),
                    ("입니다", "이 아닙니다"), ("이에요", "이 아니에요"))
CLAIMS = {"혼잡": "아주 한산합니다", "매우혼잡": "아주 한산합니다", "여유": "많이 붐빕니다"}
CLAIMS_BOTH = ("아주 한산합니다", "많이 붐빕니다")
KOR_DIGIT = "영일이삼사오육칠팔구"
KOR_UNIT = ("", "십", "백", "천")

_NUM_RE = re.compile(r"(\d+(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(km|명|분|회|대|시(?!간)|번|개|곳|안|m|%|건|배)?")
_ISO_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}")
_KOR_DATE_RE = re.compile(r"\d{1,2}\s*월\s*\d{1,2}\s*일")
_HOUR_RE = re.compile(r"(?<!\d)(\d{1,2})시(?!간)")
_HALF_RE = re.compile(r"(오전|오후)\s*(\d{1,2})시")
_CLAUSE_SPLIT_RE = re.compile(r"((?<!\d)[.,]|[.,](?!\d)|\n)")
_CLAUSE_UNIT_RE = re.compile(r"\d[\d,.]*\s*(?:명|분|회|대)")


# ==================================================
# 1. 채점 (순수 함수)
# ==================================================

def guard(answer: str, template: str, facts: dict) -> dict:
    """agent.py L792 와 같은 채택 조건. 두 가드를 항상 둘 다 호출"""
    sn = same_numbers(answer, template)
    km = keeps_meaning(answer, facts)
    accepted = bool(answer) and "�" not in answer and sn and km
    return {"same_numbers": sn, "keeps_meaning": km, "accepted": accepted}


def judge(item: dict) -> dict:
    g = guard(item["text"], item["template"], item["facts"])
    by = [name for name in ("same_numbers", "keeps_meaning") if not g[name]]
    ok = g["accepted"] if item["label"] == "right" else not g["accepted"]
    return {**g, "by": [] if g["accepted"] else by, "ok": ok}


# ==================================================
# 2. 문장 틀 (agent.py _predict / _advise / 길안내 형식)
# ==================================================

def _g(x) -> str:
    return f"{x:g}"


def build_predict(v: dict) -> tuple[str, list[str]]:
    m, d = int(v["date"][5:7]), int(v["date"][8:10])
    r, h, lv, c = v["route"], v["hour"], v["level"], v["ratio"]
    tpl = (f"🚌 {r}번 {v['date']} {h}시 예상 승차는 {v['pred']}명입니다. 작년 같은 요일·시간대는 {v['base']}명이었습니다."
           f"\n{LEVEL_MARK[lv]} 가장 붐비는 구간의 혼잡도는 {c}({lv})입니다.")
    p1 = f"{r}번은 {m}월 {d}일 {h}시에 {v['pred']}명이 탈 것으로 예상되고, 작년에는 {v['base']}명이었어요. 혼잡도는 {c}({lv})입니다."
    p2 = (f"{v['date']} {h}시 {r}번의 예상 승차는 {v['pred']}명이고, 작년 같은 요일·시간대는 {v['base']}명이었습니다."
          f" 가장 붐비는 구간은 혼잡도 {c}({lv})로 나타났습니다.")
    return tpl, [p1, p2]


def build_advise(v: dict) -> tuple[str, list[str]]:
    m, d = int(v["date"][5:7]), int(v["date"][8:10])
    r, h, lv, c, act, lab = v["route"], v["hour"], v["level"], v["ratio"], v["action"], ACTION_LABELS[v["action"]]
    a, b = v["stops"]
    head = f"{ACTION_MARK[act]} {r}번 {v['date']} {h}시 권고는 '{lab}'입니다."
    now = f"\n🚌 지금은 약 {v['iv']}분 간격(시간당 {v['trips']}회)이고,"
    cong = f"\n{LEVEL_MARK[lv]} 가장 붐비는 구간({a} → {b})의 혼잡도는 {c}({lv})입니다."
    if act == "keep":
        tail, tail_inline = "\n지금 배차간격을 유지해도 됩니다.", " 지금 배차간격을 유지해도 됩니다."
    else:
        tail = f"\n👉 권고 배차간격은 {v['riv']}분이고, 그때 예상 혼잡도는 {v['ec']}입니다."
        tail_inline = f" 권고 배차간격은 {v['riv']}분이고, 그때 예상 혼잡도는 {v['ec']}입니다."
        if v["extra"] > 0:
            tail += f" 버스가 약 {v['extra']}대 더 필요합니다."
            tail_inline += f" 버스가 약 {v['extra']}대 더 필요합니다."
    end = "\n📝 최종 결정은 담당자 검토가 필요합니다."
    tpl = head + now + cong + tail + end
    p1 = (f"{r}번 {m}월 {d}일 {h}시 권고안은 '{lab}'이에요. 지금은 약 {v['iv']}분 간격(시간당 {v['trips']}회)이고,"
          f" 가장 붐비는 구간({a} → {b})의 혼잡도는 {c}({lv})입니다.{tail_inline} 최종 결정은 담당자 검토가 필요합니다.")
    p2 = (f"{r}번 {v['date']} {h}시 기준으로 가장 붐비는 구간({a} → {b})의 혼잡도는 {c}({lv})입니다."
          f" 지금은 약 {v['iv']}분 간격(시간당 {v['trips']}회)이며, 권고는 '{lab}'입니다.{tail_inline}"
          f" 최종 결정은 담당자 검토가 필요합니다.")
    return tpl, [p1, p2]


def build_guide(n: int) -> tuple[str, list[str]]:
    tpl = f"👉 추천은 {n}안입니다. 가장 빨리 도착합니다."
    return tpl, [f"{n}안을 추천드려요. 가장 빨리 도착합니다.", f"가장 빨리 도착하는 {n}안을 추천합니다."]


def _ratio_for(rng: random.Random, level: str) -> float:
    lo, hi = LEVEL_RATIO_RANGE[level]
    return round(rng.uniform(lo, hi), 2)


def build_corpus(rng: random.Random) -> list[dict]:
    """템플릿 목록. T01·T02는 tests/test_review_fixes.py 의 원문과 같아야 함(어긋나면 예외)"""
    out: list[dict] = []

    def add(tpl: str, paras: list[str], facts: dict):
        out.append({"template_id": f"T{len(out) + 1:02d}", "template": tpl, "paraphrases": paras, "facts": facts})

    # T01, T02: 고정 원문
    t1, p1 = build_predict({"route": "143", "date": "2026-11-20", "hour": 8, "pred": "3,133", "base": "3,065",
                            "ratio": "0.61", "level": "혼잡"})
    assert t1 == ("🚌 143번 2026-11-20 8시 예상 승차는 3,133명입니다. 작년 같은 요일·시간대는 3,065명이었습니다."
                  "\n🟠 가장 붐비는 구간의 혼잡도는 0.61(혼잡)입니다."), "T01 원문 불일치"
    add(t1, p1, {"level": "혼잡"})
    t2, p2 = build_advise({"route": "143", "date": "2026-11-20", "hour": 8, "action": "increase", "iv": "6.7",
                           "trips": "9", "ratio": "0.82", "level": "혼잡", "stops": ("정릉", "길음"),
                           "riv": "6", "ec": "0.73", "extra": 2})
    assert t2 == ("⬆️ 143번 2026-11-20 8시 권고는 '배차간격 단축 (증차)'입니다."
                  "\n🚌 지금은 약 6.7분 간격(시간당 9회)이고,"
                  "\n🟠 가장 붐비는 구간(정릉 → 길음)의 혼잡도는 0.82(혼잡)입니다."
                  "\n👉 권고 배차간격은 6분이고, 그때 예상 혼잡도는 0.73입니다. 버스가 약 2대 더 필요합니다."
                  "\n📝 최종 결정은 담당자 검토가 필요합니다."), "T02 원문 불일치"
    add(t2, p2, {"action": "increase", "level": "혼잡"})

    def common() -> dict:
        return {"route": rng.choice(ROUTES), "date": f"2026-11-{rng.randint(1, 28):02d}", "hour": rng.randint(6, 22)}

    for level in ("보통", "여유", "매우혼잡"):
        pred = rng.randint(300, 6000)
        base = pred + rng.choice((-1, 1)) * rng.randint(30, max(31, pred // 8))
        v = {**common(), "pred": f"{pred:,}", "base": f"{base:,}", "ratio": f"{_ratio_for(rng, level):.2f}", "level": level}
        add(*build_predict(v), {"level": level})

    for action, level in (("increase", "매우혼잡"), ("increase_limited", "매우혼잡"), ("decrease", "여유"),
                          ("keep", "보통"), ("keep", "여유")):
        iv = round(rng.uniform(5, 12), 1)
        cur = _ratio_for(rng, level)
        if action == "decrease":
            riv, ec, extra = int(iv) + rng.randint(2, 4), round(cur + rng.uniform(0.05, 0.2), 2), 0
        elif action == "keep":
            riv, ec, extra = int(iv), cur, 0
        else:
            riv, ec, extra = rng.randint(2, int(iv) - 1), round(max(0.2, cur - rng.uniform(0.1, 0.3)), 2), \
                rng.randint(1, 4) if action == "increase" else 0
        v = {**common(), "action": action, "iv": _g(iv), "trips": _g(round(60 / iv, 1)), "ratio": f"{cur:.2f}",
             "level": level, "stops": rng.choice(STOPS), "riv": str(riv), "ec": f"{ec:.2f}", "extra": extra}
        add(*build_advise(v), {"action": action, "level": level})

    for _ in range(2):
        n = rng.randint(1, 3)
        add(*build_guide(n), {"recommended_no": n})
    return out


# ==================================================
# 3. 틀린 문장 만들기 (kind마다 따로. 적용할 수 없으면 None)
# ==================================================

def _date_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in _ISO_RE.finditer(text)] + [m.span() for m in _KOR_DATE_RE.finditer(text)]


def _tokens(text: str, skip_units=("번", "안")) -> list[tuple[int, int, str, str]]:
    """날짜 밖의 (시작, 끝, 숫자 글자, 단위). 숫자 글자는 단위를 뺀 부분"""
    spans, out = _date_spans(text), []
    for m in _NUM_RE.finditer(text):
        if any(s <= m.start(1) < e for s, e in spans):
            continue
        unit = m.group(2) or ""
        if unit in skip_units:
            continue
        out.append((m.start(1), m.end(1), m.group(1), unit))
    return out


def _like(orig: str, value: float) -> str | None:
    """원래 표기(쉼표·소수 자릿수)를 따라 value를 글자로. 값이 원래와 같으면 None"""
    if value < 0:
        return None
    if "." in orig:
        new = f"{value:.{len(orig.split('.')[1])}f}"
    elif "," in orig:
        new = f"{round(value):,}"
    else:
        new = str(round(value))
    return None if new == orig else new


def _replace(text: str, start: int, end: int, new: str) -> str:
    return text[:start] + new + text[end:]


def _kor_number(n: int) -> str | None:
    if not 0 < n < 10000:
        return None
    digits, out = str(n), ""
    for i, ch in enumerate(digits):
        pos, d = len(digits) - 1 - i, int(ch)
        if d:
            out += ("" if d == 1 and pos > 0 else KOR_DIGIT[d]) + KOR_UNIT[pos]
    return out


def m_number_change(text, facts, rng):
    toks = [t for t in _tokens(text) if t[3] in ("명", "분", "회", "대", "시", "")]
    if not toks:
        return None
    s, e, num, unit = rng.choice(toks)
    value = float(num.replace(",", ""))
    if unit == "시":
        new = str(rng.choice([h for h in range(5, 24) if h != int(value)]))
    elif rng.random() < 0.5:                                  # 자릿수 바꿈
        new = _like(num, value * 10 if (value < 10 or "." in num) else value / 10)
    else:                                                      # ±10~50%
        new = _like(num, value * (1 + rng.choice((-1, 1)) * rng.uniform(0.1, 0.5)))
    return None if new is None else _replace(text, s, e, new)


def m_number_delete(text, facts, rng):
    parts = _CLAUSE_SPLIT_RE.split(text)
    idx = [i for i in range(0, len(parts), 2) if _CLAUSE_UNIT_RE.search(parts[i])]
    if not idx:
        return None
    i = rng.choice(idx)
    del parts[i:i + 2]
    return "".join(parts)


def m_number_add(text, facts, rng):
    pos = text.find(".")
    if pos < 0:
        return None
    if rng.random() < 0.5:
        n = rng.randrange(100, 9999)
        phrase = f" 이 중 약 {n:,}명은 환승 승객입니다."
        token = f"{n:,}명"
    else:
        n = rng.randint(3, 59)
        phrase = f" 배차 조정은 {n}분 안에 이뤄집니다."
        token = f"{n}분"
    return None if token in text else _replace(text, pos + 1, pos + 1, phrase)


def m_number_swap(text, facts, rng):
    groups: dict[str, list] = {}
    for t in _tokens(text):
        groups.setdefault(t[3], []).append(t)
    usable = [sorted(g) for u, g in sorted(groups.items()) if len({float(t[2].replace(",", "")) for t in g}) >= 2]
    if not usable:
        return None
    group = rng.choice(usable)
    a, b = sorted(rng.sample(range(len(group)), 2))
    (s1, e1, n1, _), (s2, e2, n2, _) = group[a], group[b]
    if float(n1.replace(",", "")) == float(n2.replace(",", "")):
        return None
    return text[:s1] + n2 + text[e1:s2] + n1 + text[e2:]


def m_unit_change(text, facts, rng):
    cands = []
    for m in re.finditer(r"(?<=\d)(명|분|시(?!간))", text):
        if any(s <= m.start() < e for s, e in _date_spans(text)):
            continue
        for new in UNIT_CHANGES[m.group(1)]:
            cands.append((m.start(), m.end(), new))
    if not cands:
        return None
    s, e, new = rng.choice(cands)
    return _replace(text, s, e, new)


def m_date_change(text, facts, rng):
    m = _ISO_RE.search(text)
    if m:
        y, mo, d = m.group(0).split("-")
        new = f"{y}-{mo}-{rng.choice([x for x in range(1, 29) if x != int(d)]):02d}"
        return _replace(text, m.start(), m.end(), new)
    m = _KOR_DATE_RE.search(text)
    if m:
        mo, d = re.findall(r"\d+", m.group(0))
        return _replace(text, m.start(), m.end(), f"{mo}월 {rng.choice([x for x in range(1, 29) if x != int(d)])}일")
    return None


def m_half_day_change(text, facts, rng):
    m = _HALF_RE.search(text)
    if m:
        other = "오후" if m.group(1) == "오전" else "오전"
        return _replace(text, m.start(1), m.end(1), other)
    m = _HOUR_RE.search(text)
    if not m:
        return None
    return _replace(text, m.start(), m.start(), rng.choice(("오전 ", "오후 ")))


def m_modifier_add(text, facts, rng):
    toks = [t for t in _tokens(text) if t[3] in ("명", "분", "회", "대")]
    if not toks:
        return None
    s, e, num, unit = rng.choice(toks)
    kor = _kor_number(int(float(num.replace(",", "")))) if unit in ("명", "회") and "." not in num else None
    if kor and rng.random() < 0.5:
        return _replace(text, s, e + len(unit), f"{kor} {unit}")
    return _replace(text, e + len(unit), e + len(unit), rng.choice(MODIFIER_SUFFIXES))


def m_label_swap(text, facts, rng):
    pairs = []
    for a, b in LABEL_PAIRS:
        i = text.find(a)
        j = text.find("권고 배차간격") if b == "권고" and "권고 배차간격" in text else text.find(b)
        if i >= 0 and j >= 0 and i != j:
            pairs.append((a, b, i, j))
    if not pairs:
        return None
    a, b, i, j = rng.choice(pairs)
    lo, hi = sorted(((i, b), (j, a)))
    return text[:lo[0]] + lo[1] + text[lo[0] + 2:hi[0]] + hi[1] + text[hi[0] + 2:]


def m_negation(text, facts, rng):
    cands = []
    for src, dst in NEGATION_ENDINGS:
        for m in re.finditer(re.escape(src) + r"(?=[.])", text):
            start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
            if any(k in text[start:m.end()] for k in NEGATION_KEYS):
                cands.append((m.start(), m.end(), dst))
    if not cands:
        return None
    s, e, dst = rng.choice(sorted(cands))
    return _replace(text, s, e, dst)


def m_action_flip(text, facts, rng):
    action = facts.get("action")
    label = ACTION_LABELS.get(action or "")
    if not label or label not in text:
        return None
    out = text.replace(label, ACTION_LABELS[ACTION_FLIP[action]], 1)
    if action == "keep":
        out = out.replace("유지해도", "감차해도")
    return out


def m_keyword_swap(text, facts, rng):
    cands = [(a, b) for a, b in KEYWORD_SWAPS if a in text]
    if not cands:
        return None
    a, b = rng.choice(cands)
    return text.replace(a, b)


def m_level_change(text, facts, rng):
    level = facts.get("level")
    m = re.search(r"(매우혼잡|혼잡|보통|여유)(?=\))", text)
    if not level or not m:
        return None
    return _replace(text, m.start(), m.end(), rng.choice([x for x in LEVELS if x != level]))


def m_level_claim(text, facts, rng):
    level = facts.get("level")
    m = re.search(rf"\({level}\)", text) if level else None
    if not m:
        return None
    claim = CLAIMS.get(level) or rng.choice(CLAIMS_BOTH)
    out = _replace(text, m.start(), m.end(), "")
    sentence = f" 이 구간은 {claim}."
    pos = out.find("\n📝")
    return out + sentence if pos < 0 else _replace(out, pos, pos, sentence)


def m_option_change(text, facts, rng):
    n = facts.get("recommended_no")
    if not n:
        return None
    for m in re.finditer(r"(\d+)안", text):
        if int(m.group(1)) == n:
            return _replace(text, m.start(1), m.end(1), str(rng.choice([x for x in (1, 2, 3) if x != n])))
    return None


def m_target_change(text, facts, rng):
    m = re.search(r"(" + "|".join(ROUTES) + r")번", text)
    if not m:
        return None
    return _replace(text, m.start(1), m.end(1), rng.choice([r for r in ROUTES if r != m.group(1)]))


MUTATORS: dict[str, Callable[[str, dict, random.Random], str | None]] = {
    "number_change": m_number_change, "number_delete": m_number_delete, "number_add": m_number_add,
    "number_swap": m_number_swap, "unit_change": m_unit_change, "date_change": m_date_change,
    "half_day_change": m_half_day_change, "modifier_add": m_modifier_add, "label_swap": m_label_swap,
    "negation": m_negation, "action_flip": m_action_flip, "keyword_swap": m_keyword_swap,
    "level_change": m_level_change, "level_claim": m_level_claim, "option_change": m_option_change,
    "target_change": m_target_change,
}
EXPECTED_GUARD = {
    "number_change": "same_numbers", "number_delete": "none", "number_add": "same_numbers",
    "number_swap": "same_numbers", "unit_change": "same_numbers", "date_change": "same_numbers",
    "half_day_change": "same_numbers", "modifier_add": "same_numbers", "label_swap": "same_numbers",
    "negation": "keeps_meaning", "action_flip": "keeps_meaning", "keyword_swap": "keeps_meaning",
    "level_change": "keeps_meaning", "level_claim": "keeps_meaning", "option_change": "keeps_meaning",
    "target_change": "none",
}


# ==================================================
# 4. 실행·집계
# ==================================================

def _rate(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def build_items(seed: int, per_kind: int) -> tuple[list[dict], dict[str, int]]:
    rng = random.Random(seed)
    corpus = build_corpus(rng)
    items: list[dict] = []
    for t in corpus:
        for variant, text in [("identity", t["template"])] + [("paraphrase", p) for p in t["paraphrases"]]:
            items.append({"id": f"R{len(items) + 1:03d}", "label": "right", "kind": None, "variant": variant,
                          "template_id": t["template_id"], "text": text, "template": t["template"], "facts": t["facts"]})
    sources = list(items)
    skipped = {kind: 0 for kind in MUTATORS}
    for kind, mutate in MUTATORS.items():
        cands, seen = [], set()
        for src in sources:
            for _ in range(ATTEMPTS):
                new = mutate(src["text"], src["facts"], rng)
                if new is None or new == src["text"]:
                    skipped[kind] += 1
                    continue
                if (src["template_id"], new) in seen:
                    continue
                seen.add((src["template_id"], new))
                cands.append({**src, "label": "wrong", "kind": kind, "text": new})
        if len(cands) > per_kind:
            cands = rng.sample(cands, per_kind)
        for i, c in enumerate(cands, 1):
            items.append({**c, "id": f"W_{kind}_{i:03d}"})
    return items, skipped


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--per-kind", type=int, default=PER_KIND)
    args = p.parse_args()

    t0 = time.time()
    items, skipped = build_items(args.seed, args.per_kind)
    rows = []
    for it in items:
        j = judge(it)
        rows.append({"id": it["id"], "label": it["label"], "kind": it["kind"], "variant": it["variant"],
                     "template_id": it["template_id"], "text": it["text"], "template": it["template"],
                     "facts": it["facts"], **j})
        print(f"{'O' if j['ok'] else 'X'} {it['id']} {it['label']} {it['kind'] or '-'} {it['variant']}", flush=True)

    right = [r for r in rows if r["label"] == "right"]
    wrong = [r for r in rows if r["label"] == "wrong"]
    by_variant = {}
    for v in ("identity", "paraphrase"):
        rs = [r for r in right if r["variant"] == v]
        acc = sum(r["accepted"] for r in rs)
        by_variant[v] = {"total": len(rs), "accepted": acc, "accept_rate": _rate(acc, len(rs))}
    by_kind = {}
    for kind in MUTATORS:
        ws = [r for r in wrong if r["kind"] == kind]
        det = [r for r in ws if r["ok"]]
        by_kind[kind] = {
            "expected_guard": EXPECTED_GUARD[kind], "total": len(ws), "detected": len(det),
            "detect_rate": _rate(len(det), len(ws)),
            "detected_by_numbers": sum(not r["same_numbers"] for r in ws if not r["accepted"]),
            "detected_by_meaning": sum(not r["keeps_meaning"] for r in ws if not r["accepted"]),
            "skipped": skipped[kind], "missed_ids": [r["id"] for r in ws if not r["ok"]],
        }
    right_acc = sum(r["accepted"] for r in right)
    wrong_det = sum(r["ok"] for r in wrong)
    summary = {
        "name": args.name, "seed": args.seed, "per_kind": args.per_kind, "guard": "same_numbers+keeps_meaning",
        "n_templates": len({r["template_id"] for r in rows}),
        "right_total": len(right), "right_accepted": right_acc, "right_accept_rate": _rate(right_acc, len(right)),
        "right_by_variant": by_variant,
        "false_rejects": [{"id": r["id"], "template_id": r["template_id"], "variant": r["variant"],
                           "text": r["text"], "by": r["by"]} for r in right if not r["accepted"]],
        "wrong_total": len(wrong), "wrong_detected": wrong_det, "wrong_detect_rate": _rate(wrong_det, len(wrong)),
        "by_kind": by_kind, "llm_used": False, "seconds": round(time.time() - t0, 1),
    }
    print("=" * 60, "\n", json.dumps({k: v for k, v in summary.items() if k not in ("by_kind", "false_rejects")},
                                      ensure_ascii=False))
    out = ROOT / "eval/results"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.name}.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1),
                                           encoding="utf-8")


if __name__ == "__main__":
    main()
