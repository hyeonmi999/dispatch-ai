"""
챗봇 "문장 틀 + LLM 한 줄" 구조 채점 (이슈 #21, 계획서 oh-evaluation.md Task E-12)

1) 틀린 문장이 사용자에게 닿는가
   예전 채점(eval_rewrite_guard.py)이 만든 틀린 문장을 그대로 가져와, LLM이 그 문장을 냈다고 치고
   예전 구조(다시 쓴 문장을 검증기로 거름)와 지금 구조(한 줄 확인)에서 각각 몇 개가 답변에 실리는지 셈.
2) 실제 모델이 쓴 한 줄이 얼마나 채택되는가 (--live)
   실데이터로 예측·권고 질문을 보내 LLM의 한 줄이 채택된 비율과 그 문장들을 남김.

사용
  python scripts/eval_chat_note.py --name chat_note_1                 # 1)만 (모델 없음)
  python scripts/eval_chat_note.py --name chat_note_1 --live          # 1) + 2) (로컬 LLM, 서버는 끄고 실행)
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import CHAT_NOTE_MARK  # noqa: E402
from app.core.agent import safe_note  # noqa: E402

# 이슈 #21을 고친 뒤(PR #26)에도 예전 검증기를 통과하던 문장 (낱말만 바꾼 틀린 결론·수량)
EXTRA_WRONG = [
    "증차는 시기상조입니다. 혼잡입니다.", "증차는 다음에 하시죠. 혼잡입니다.", "굳이 증차할 이유가 있을까요? 혼잡입니다.",
    "유지는 곤란합니다. 보통입니다.", "증차를 미루세요. 혼잡입니다.", "증차는 과합니다. 혼잡입니다.",
    "혼잡하다고 보기는 힘듭니다. 증차를 권고합니다.", "예상 승차는 3,133명 남짓에 그칩니다.", "예상 승차는 3,133명을 훌쩍 넘습니다.",
    "예상 승차는 3,133명보다 적습니다.", "예상 승차는 3,133명에 못 미칩니다.", "예상 승차는 최대 3,133명입니다.",
    "예상 승차는 매일 3,133명입니다.", "예상 승차는 많습니다.", "버스가 더 필요합니다.",
]
LIVE_HOURS = (8, 14, 18)


def replay(source: Path) -> dict:
    rows = json.loads(source.read_text(encoding="utf-8"))["rows"]
    wrong = [r for r in rows if r["label"] == "wrong"]
    by_kind: dict[str, dict] = {}
    for r in wrong:
        kind = by_kind.setdefault(r["kind"], {"total": 0, "old_passed": 0, "new_passed": 0})
        kind["total"] += 1
        kind["old_passed"] += bool(r["accepted"])
        kind["new_passed"] += safe_note(r["text"])
    extra_new = [text for text in EXTRA_WRONG if safe_note(text)]
    return {
        "source": source.name, "wrong_total": len(wrong),
        "old_reached_user": sum(k["old_passed"] for k in by_kind.values()),
        "new_reached_user": sum(k["new_passed"] for k in by_kind.values()),
        "by_kind": by_kind,
        "extra_wrong_total": len(EXTRA_WRONG), "extra_new_reached_user": len(extra_new), "extra_new_passed": extra_new,
    }


def live(llm: str) -> dict:
    from fastapi.testclient import TestClient

    from app.main import app

    rows, t0 = [], time.time()
    with TestClient(app) as client:
        meta = client.get("/api/bus/meta").json()
        date = meta["dates"]["max_date"]
        for route in [r["route"] for r in meta["routes"]]:
            for hour in LIVE_HOURS:
                for question in (f"{route}번 {date} {hour}시에 혼잡해?", f"{route}번 {date} {hour}시 증차해야 해?"):
                    plain = client.post("/api/bus/chat", json={"message": question}).json()
                    out = client.post("/api/bus/chat", json={"message": question, "use_llm": True, "llm": llm}).json()
                    note = out["answer"][len(plain["answer"]):].strip().removeprefix(CHAT_NOTE_MARK).strip()
                    rows.append({"q": question, "intent": out["intent"], "accepted": not out["llm_rejected"],
                                 "template_kept": out["answer"].startswith(plain["answer"]),
                                 "numbers_verified": out["numbers_verified"], "note": note or None})
                    print(f"  {'채택' if rows[-1]['accepted'] else '버림'} {question} → {note!r}", flush=True)
    accepted = [r for r in rows if r["accepted"]]
    return {"llm": llm, "total": len(rows), "accepted": len(accepted),
            "template_kept": sum(r["template_kept"] for r in rows),
            "numbers_verified": sum(r["numbers_verified"] is True for r in rows),
            "distinct_notes": sorted({r["note"] for r in accepted}), "seconds": round(time.time() - t0), "rows": rows}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--source", default="eval/results/rewrite_guard_1.json", help="틀린 문장 묶음 (eval_rewrite_guard.py 결과)")
    p.add_argument("--live", action="store_true", help="실제 로컬 LLM으로 한 줄 채택 비율도 잼")
    p.add_argument("--llm", default="qwen")
    args = p.parse_args()

    result = {"name": args.name, "replay": replay(ROOT / args.source)}
    r = result["replay"]
    print(f"틀린 문장 {r['wrong_total']}개: 예전 구조에서 답변에 실림 {r['old_reached_user']} → 지금 구조 {r['new_reached_user']}")
    print(f"예전 검증기를 통과하던 변형 {r['extra_wrong_total']}개: 지금 구조에서 실림 {r['extra_new_reached_user']}")
    if args.live:
        result["live"] = live(args.llm)
        v = result["live"]
        print(f"실제 모델 {v['total']}회: 한 줄 채택 {v['accepted']} · 문장 틀 그대로 {v['template_kept']} · 숫자 검증 통과 {v['numbers_verified']}")
    out = ROOT / "eval/results" / f"{args.name}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("결과:", out)


if __name__ == "__main__":
    main()
