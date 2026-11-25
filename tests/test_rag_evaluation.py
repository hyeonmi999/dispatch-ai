import pytest
from scripts.eval_rag import judge


@pytest.fixture(scope="module", autouse=True)
def sample_model():
    """채점 함수 검사에는 모델 학습이나 외부 연결이 필요 없습니다."""
    yield


def item(keyword):
    return {"id": "T1", "type": "answer", "q": "검증", "must": [[keyword]], "forbid": [], "doc": "시행규칙"}


def response(answer):
    return {"found": True, "answer": answer, "sources": [{"document": "시행규칙"}]}


@pytest.mark.parametrize("keyword,answer", [
    ("50퍼센트 이하", "50% 이하"),
    ("30퍼센트 이내", "30 % 이내"),
    ("5킬로미터 범위", "5km 범위"),
    ("시ㆍ군ㆍ구", "시·군·구"),
    ("사업계획대비표", "사업계획 대비표"),
    ("1,400원", "1400원"),
])
def test_normalized_accepts_equivalent_notation(keyword, answer):
    assert judge(item(keyword), response(answer), scoring="normalized")["ok"]
    assert not judge(item(keyword), response(answer))["ok"]


@pytest.mark.parametrize("keyword,answer", [
    ("50퍼센트 이하", "150% 이하"),
    ("50퍼센트 이하", "50.5% 이하"),
    ("30퍼센트 이내", "130% 이내"),
    ("5킬로미터 범위", "5m 범위"),
    ("5킬로미터 범위", "15km 범위"),
    ("1년 이내", "11년 이내"),
])
def test_normalized_does_not_change_numbers_or_units(keyword, answer):
    assert not judge(item(keyword), response(answer), scoring="normalized")["ok"]


def test_forbidden_words_are_normalized_too():
    question = {**item("자료"), "forbid": ["30퍼센트 초과"]}
    result = judge(question, response("자료: 30% 초과"), scoring="normalized")
    assert not result["ok"] and result["forbidden"] == ["30퍼센트 초과"]


def test_additional_criteria_reject_incomplete_a4_without_editing_baseline():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    questions = json.loads((root / "eval/rag_eval.json").read_text(encoding="utf-8"))
    question = next(x for x in questions if x["id"] == "A4")
    criteria = json.loads((root / "eval/rag_eval_criteria.json").read_text(encoding="utf-8"))["items"]["A4"]
    short = response("차고의 위치를 적은 서류입니다.")
    assert judge(question, short)["ok"]  # 기존 점수 재현
    assert not judge(question, short, scoring="normalized", criteria=criteria)["ok"]
    complete = response("신·구 사업계획을 대비한 서류 또는 도면과 차고 등 시설의 위치 및 수용능력을 적은 서류입니다.")
    assert judge(question, complete, scoring="normalized", criteria=criteria)["ok"]


def test_refusals_keep_existing_found_rule():
    question = {"id": "R", "type": "refuse", "q": "범위 밖"}
    assert judge(question, {"found": False, "answer": "찾을 수 없음"}, scoring="normalized")["ok"]
    assert not judge(question, {"found": True, "answer": "invented"}, scoring="normalized")["ok"]


@pytest.mark.parametrize("keyword,answer", [("50퍼센트 이하", "-50% 이하"), ("50퍼센트 이하", "50% 초과")])
def test_normalization_preserves_sign_and_limit_direction(keyword, answer):
    assert not judge(item(keyword), response(answer), scoring="normalized")["ok"]


@pytest.mark.parametrize("items", [[], [{"id": "X", "type": "answer", "q": "질문", "must": [[]], "doc": "문서"}],
                                    [{"id": "X", "type": "refuse", "q": "질문"}] * 2])
def test_invalid_dataset_is_rejected(items):
    from scripts.eval_rag import validate_items
    with pytest.raises(ValueError):
        validate_items(items)


def test_check_only_does_not_load_models(monkeypatch, capsys):
    import json
    from pathlib import Path
    from scripts import eval_rag
    criteria = Path(__file__).resolve().parents[1] / "eval/rag_eval_criteria.json"
    monkeypatch.setattr(eval_rag.sys, "argv", ["eval_rag.py", "--check-only", "--scoring", "normalized", "--criteria", str(criteria)])
    def no_model(*args, **kwargs):
        raise AssertionError("형식 검사에서 모델을 불러오면 안 됩니다.")
    monkeypatch.setattr(eval_rag, "KnowledgeBase", no_model)
    eval_rag.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "format_check_only"
    assert (result["total"], result["answer"], result["refuse"]) == (40, 24, 16)


def test_literal_reproduces_previously_recorded_judgments():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    count = 0
    for name in ["7_prompt_rules_refusal_hints_qwen1.7b.json", "9_role_search_fix_openai.json", "9_role_search_fix_qwen1.7b.json"]:
        rows = json.loads((root / "eval/results" / name).read_text(encoding="utf-8"))["rows"]
        for row in rows:
            result = {"found": row["found"], "answer": row["answer"], "sources": []}
            assert judge(row, result)["ok"] == row["ok"]
            assert judge(row, result)["hallucination"] == row["hallucination"]
            count += 1
    assert count == 60


def test_all_answer_gold_references_match_real_chunks():
    import json
    from pathlib import Path
    from app.core.rag import load_documents
    from scripts.eval_rag import retrieval_rank
    root = Path(__file__).resolve().parents[1]
    questions = json.loads((root / "eval/rag_eval.json").read_text(encoding="utf-8"))
    chunks = [c["metadata"] for c in load_documents(root / "docs/bus")]
    answers = [q for q in questions if q["type"] == "answer"]
    assert len(answers) == 24
    for q in answers:
        assert retrieval_rank(q["gold_articles"], chunks) is not None, q["id"]
