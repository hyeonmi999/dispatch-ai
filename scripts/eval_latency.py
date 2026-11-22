"""
챗봇 기능별 응답 시간 + OpenAI 사용 시 토큰 수·월 운영비 추정 (docs/plans/oh-evaluation.md Task S-2)

1) 응답 시간: POST /api/bus/chat 을 기능별로 --repeat 번 부른 중앙값 (서버를 띄우지 않고 앱 안에서 호출)
   - use_llm=False (문장 틀 답변): 항상 잼
   - use_llm=True: --llm qwen | openai 를 줬을 때만 잼 (실제 모델 호출. openai는 비용이 듦)
2) 토큰 수: LLM을 부르지 않고, 보낼 프롬프트를 가로채 글자 수 대신 토큰 수만 셈 (비용 없음)
   - 출력 토큰은 실제 값이 아님: 다듬기는 문장 틀 답변의 토큰 수, 규정 답변은 저장된 OpenAI 답변의 중앙값을 씀
3) 월 운영비: 하루 질문 수·기능 비율·단가·환율은 모두 **가정**이고 결과 파일에 그대로 적음

결과: eval/results/<이름>.json , eval/results/<비용 이름>.json

사용 (API 서버를 끄고 실행)
  python scripts/eval_latency.py --name chat_latency_1 --cost-name cost_estimate_1
  python scripts/eval_latency.py --name chat_latency_2 --llm openai      # 실제 OpenAI 호출 시간까지
"""

import argparse
import datetime as dt
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["ASYNC_DB_URL"] = ""          # 채점 중 대화가 이력 DB에 쌓이지 않게

from fastapi.testclient import TestClient  # noqa: E402

from app.core import agent  # noqa: E402
from app.core import llm as llm_module  # noqa: E402
from app.main import app  # noqa: E402

TARGET_SECONDS = 5.0                     # 수업 교안(24차시)의 점검 기준 "전체 질의응답 3~5초 이내"
DAILY_QUESTIONS = {"적음": 100, "보통": 500, "많음": 2000}       # 가정
DAYS_PER_MONTH = 30


def questions(client) -> list[dict]:
    """기능마다 질문 하나. 날짜는 예측이 가능한 마지막 날, 길안내 정류장은 143번에서 고름"""
    date = client.get("/api/bus/meta").json()["dates"]["max_date"]
    stops = client.get("/api/bus/routes/143/geometry").json()["stops"]
    a, b = stops[len(stops) // 2], stops[len(stops) // 2 + 6]
    name = lambda s: s["name"].replace(" ", "")  # noqa: E731
    return [
        {"intent": "predict", "q": f"143번 {date} 8시에 혼잡해?"},
        {"intent": "advise", "q": f"143번 {date} 8시 증차해야 해?"},
        {"intent": "ask", "q": "시내버스 운수종사자 휴식시간 규정 알려줘"},
        {"intent": "guide", "q": f"{name(a)}에서 {name(b)} 어떻게 가? {date} 8시"},
        {"intent": "route_info", "q": "143번 노선 정보 알려줘"},
    ]


def call(client, q: str, **options) -> tuple[float, dict]:
    t0 = time.perf_counter()
    res = client.post("/api/bus/chat", json={"message": q, **options})
    seconds = time.perf_counter() - t0
    if res.status_code != 200:
        raise RuntimeError(f"{res.status_code} {res.text[:200]}")
    return seconds, res.json()


def measure(client, items: list[dict], repeat: int, **options) -> list[dict]:
    rows = []
    for it in items:
        call(client, it["q"], **options)                       # 첫 호출은 모델·색인 로드가 섞이므로 버림
        times, last = [], {}
        for _ in range(repeat):
            seconds, last = call(client, it["q"], **options)
            times.append(seconds)
        rows.append({
            "intent": it["intent"], "q": it["q"], "got_intent": last.get("intent"),
            "intent_ok": last.get("intent") == it["intent"],      # False면 다른 기능의 시간을 잰 것
            "median_seconds": round(statistics.median(times), 3),
            "min_seconds": round(min(times), 3), "max_seconds": round(max(times), 3),
            "within_target": statistics.median(times) <= TARGET_SECONDS,
            "llm_used": last.get("llm_used"),
        })
        print(f"{it['intent']:10s} 중앙값 {rows[-1]['median_seconds']:.3f}s  (최소 {rows[-1]['min_seconds']:.3f} · 최대 {rows[-1]['max_seconds']:.3f})", flush=True)
    return rows


def count_tokens(client, items: list[dict]) -> list[dict]:
    """LLM을 부르지 않고 보낼 프롬프트의 토큰 수만 셈. 다듬기는 문장 틀 답변을 그대로 돌려줘 검증을 통과시킴"""
    import tiktoken
    enc = tiktoken.get_encoding("o200k_base")                   # gpt-4o 계열 토크나이저
    seen: list[dict] = []
    original = llm_module.generate

    def fake(provider, system, user, max_new_tokens=None):
        marker = "[기본 답변]\n"
        template = user.split(marker, 1)[1].split("\n\n[질문]", 1)[0] if marker in user else ""
        seen.append({"input": len(enc.encode(system)) + len(enc.encode(user)),
                     "output_stub": len(enc.encode(template)), "kind": "rewrite" if template else "rag"})
        return template or "근거 조항에 따라 답변합니다."

    llm_module.generate = fake
    rows = []
    try:
        for it in items:
            seen.clear()
            call(client, it["q"], use_llm=True, llm="openai")
            rows.append({"intent": it["intent"], "llm_calls": len(seen),
                         "input_tokens": sum(s["input"] for s in seen),
                         "calls": [dict(s) for s in seen]})
    finally:
        llm_module.generate = original
    return rows, enc


def stored_answer_tokens(enc) -> int | None:
    """저장된 실제 OpenAI 규정 답변의 토큰 수 중앙값 (규정 답변의 출력 토큰 추정에 씀)"""
    path = ROOT / "eval/results/10_pr18_review_openai.json"
    if not path.exists():
        return None
    answers = [r["answer"] for r in json.loads(path.read_text(encoding="utf-8"))["rows"] if r.get("answer")]
    return int(statistics.median(len(enc.encode(a)) for a in answers)) if answers else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--cost-name", default=None, help="주면 토큰 수·월 운영비 추정을 eval/results/<이름>.json 에 저장")
    p.add_argument("--repeat", type=int, default=10)
    p.add_argument("--llm", choices=llm_module.LLM_CHOICES, default=None, help="주면 use_llm=True 응답 시간도 잼 (실제 모델 호출)")
    p.add_argument("--price-in", type=float, default=0.15, help="입력 100만 토큰당 달러 (가정. 공식 가격표로 확인할 것)")
    p.add_argument("--price-out", type=float, default=0.60, help="출력 100만 토큰당 달러 (가정)")
    p.add_argument("--krw", type=float, default=1400.0, help="달러당 원 (가정)")
    args = p.parse_args()
    if args.repeat < 1:
        p.error("--repeat는 1 이상이어야 합니다")

    out = ROOT / "eval/results"
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with TestClient(app) as client:
        items = questions(client)
        print("== 문장 틀 답변 (use_llm=False)")
        result = {"template": measure(client, items, args.repeat, use_llm=False)}
        if args.llm:
            print(f"== LLM 다듬기 (use_llm=True, {args.llm})")
            result[f"llm_{args.llm}"] = measure(client, items, args.repeat, use_llm=True, llm=args.llm)
        tokens, enc = count_tokens(client, items) if args.cost_name else (None, None)

    wrong = [r["intent"] for rows in result.values() for r in rows if not r["intent_ok"]]
    if wrong:
        sys.exit(f"질문이 다른 기능으로 갔습니다 (결과를 저장하지 않음): {wrong}")
    summary = {
        "name": args.name, "date": dt.date.today().isoformat(), "repeat": args.repeat,
        "target_seconds": TARGET_SECONDS, "llm_measured": args.llm,
        "how": "앱 안에서 POST /api/bus/chat 호출 (네트워크 왕복 없음, 이력 DB 저장 없음). 기능별 첫 호출은 버림",
        "seconds": round(time.time() - t0),
    }
    (out / f"{args.name}.json").write_text(json.dumps({"summary": summary, **result}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("=" * 60, "\n", json.dumps(summary, ensure_ascii=False))

    if tokens is None:
        return
    rag_out = stored_answer_tokens(enc)
    per_question = []
    for row in tokens:
        outputs = sum((rag_out or 0) if c["kind"] == "rag" else c["output_stub"] for c in row["calls"])
        usd = row["input_tokens"] / 1e6 * args.price_in + outputs / 1e6 * args.price_out
        per_question.append({"intent": row["intent"], "llm_calls": row["llm_calls"], "input_tokens": row["input_tokens"],
                             "output_tokens_estimated": outputs, "usd": round(usd, 6), "krw": round(usd * args.krw, 3)})
    mean_krw = statistics.mean(r["krw"] for r in per_question)
    cost = {
        "summary": {
            "name": args.cost_name, "date": dt.date.today().isoformat(), "model": llm_module.OPENAI_MODEL_NAME,
            "mean_krw_per_question": round(mean_krw, 3),
            "monthly_krw": {k: round(mean_krw * n * DAYS_PER_MONTH) for k, n in DAILY_QUESTIONS.items()},
        },
        "assumptions": {
            "price_in_usd_per_1m": args.price_in, "price_out_usd_per_1m": args.price_out, "krw_per_usd": args.krw,
            "price_checked": False,
            "daily_questions": DAILY_QUESTIONS, "days_per_month": DAYS_PER_MONTH,
            "mix": "다섯 기능을 같은 비율로 묻는다고 가정",
            "all_questions_use_llm": "모든 질문에 use_llm=True·llm=openai 를 쓴다고 가정 (실제 기본값은 use_llm=False 라 0원)",
            "output_tokens": "실제 값이 아님. 다듬기는 문장 틀 답변의 토큰 수, 규정 답변은 저장된 OpenAI 답변 중앙값",
            "not_included": "로컬 모델의 전기·장비, 서버, 사람 인건비",
        },
        "per_question": per_question,
    }
    (out / f"{args.cost_name}.json").write_text(json.dumps(cost, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(cost["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
