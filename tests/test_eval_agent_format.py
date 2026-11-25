import pytest
from scripts import eval_agent


@pytest.fixture(scope="module", autouse=True)
def sample_model():
    yield


def result(tool=None, intent=None, answer="예상 승객은 20명입니다."):
    return {"tools": [{"name": tool, "ok": True}] if tool else [],
            "intent": intent or tool or "out_of_scope", "slots": {}, "answer": answer}


def test_requested_tools_are_graded():
    assert eval_agent.judge({"expect_tools": ["predict"]}, result("predict"))["ok"]


def test_none_requires_out_of_scope_not_clarification():
    item = {"expect_tools": ["none"]}
    assert eval_agent.judge(item, result())["ok"]
    assert not eval_agent.judge(item, result(intent="clarify"))["ok"]


def test_legacy_format_stays_compatible():
    assert eval_agent.judge({"tools": []}, result(intent="clarify"))["ok"]
    assert eval_agent.judge({"tools": ["guide"]}, result("guide"))["ok"]
    assert not eval_agent.judge({"tools": ["predict"], "slots": {"hour": 8}}, result("predict"))["ok"]


def test_conflicting_fields_are_rejected():
    with pytest.raises(ValueError):
        eval_agent.judge({"tools": ["advise"], "expect_tools": ["predict"]}, result("advise"))


def test_must_groups_and_forbid_are_graded():
    item = {"expect_tools": ["predict"], "must": [["예상", "예측"], ["20명"]], "forbid": ["확정"]}
    assert eval_agent.judge(item, result("predict"))["ok"]
    assert not eval_agent.judge(item, result("predict", answer="예측입니다."))["ok"]
    assert not eval_agent.judge(item, result("predict", answer="예상 20명 확정"))["ok"]


@pytest.mark.parametrize("tools", [["none", "predict"], ["typo"], []])
def test_invalid_requested_tools_are_rejected(tools):
    with pytest.raises(ValueError):
        eval_agent.judge({"expect_tools": tools}, result())


def test_missing_tools_field_is_not_silently_treated_as_out_of_scope():
    with pytest.raises(ValueError):
        eval_agent.judge({"q": "질문"}, result())


def test_check_only_does_not_start_adapter(monkeypatch, capsys):
    import json
    def forbidden():
        raise AssertionError("형식 검사에서 실제 기능을 시작하면 안 됩니다.")
    monkeypatch.setattr(eval_agent, "BusAdapter", forbidden)
    monkeypatch.setattr(eval_agent.sys, "argv", ["eval_agent.py", "--check-only"])
    eval_agent.main()
    assert json.loads(capsys.readouterr().out)["total"] == 15


def test_previous_saved_legacy_result_is_reproduced():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    rows = json.loads((root / "eval/results/kim_f33_initial_20261124.json").read_text(encoding="utf-8"))["rows"]
    for row in rows:
        res = {"tools": row["got_tools"], "intent": row["got_intent"], "slots": row["got_slots"], "answer": row["answer"]}
        actual = eval_agent.judge(row, res)
        assert actual["ok"] == row["ok"]
        assert actual["wrong"] == row["wrong"]


def test_duplicate_ids_are_rejected():
    item = {"id": "A", "q": "질문", "expect_tools": ["none"]}
    with pytest.raises(ValueError):
        eval_agent.validate_items([item, item])


@pytest.mark.parametrize("intent", [{"bad": True}, ["predict"]])
def test_malformed_intent_raises_validation_error(intent):
    item = {"id": "X", "q": "질문", "expect_tools": ["predict"], "intent": intent}
    with pytest.raises(ValueError):
        eval_agent.validate_items([item])


def test_current_dataset_rejects_right_tool_with_wrong_route_or_hour():
    import json
    from pathlib import Path
    items = json.loads((Path(__file__).resolve().parents[1] / "eval/agent_eval.json").read_text(encoding="utf-8"))
    item = items[0]
    assert item["slots"] == {"route": "160", "date": "2026-10-28", "hour": 15}
    res = result("predict")
    res["slots"] = dict(item["slots"])
    assert eval_agent.judge(item, res)["ok"]
    res["slots"]["route"] = "143"
    assert not eval_agent.judge(item, res)["ok"]
    res["slots"] = {**item["slots"], "hour": 18}
    assert not eval_agent.judge(item, res)["ok"]


def test_current_dataset_checks_simulation_interval():
    import json
    from pathlib import Path
    items = json.loads((Path(__file__).resolve().parents[1] / "eval/agent_eval.json").read_text(encoding="utf-8"))
    item = next(x for x in items if x["id"] == "G7")
    res = result("simulate")
    res["slots"] = {**item["slots"], "interval": 8.0}
    assert not eval_agent.judge(item, res)["ok"]
