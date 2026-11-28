"""챗봇 LLM 덧붙임 한 줄 (오정훈, 이슈 #21의 근본책)

예전: LLM이 문장 틀 답변을 통째로 다시 쓰고, 검증기(same_numbers·keeps_meaning)가 틀린 문장을 걸러냄.
      금지어 방식이라 낱말만 바꾼 틀린 문장이 계속 통과했음 ("증차는 시기상조입니다", "3,133명에 못 미칩니다").
지금: 숫자·권고·혼잡 단계가 든 답변은 문장 틀 그대로 내보내고, LLM은 확인 사항 한 줄만 덧붙임.
      그 한 줄에는 숫자·권고·혼잡·수량을 말하는 낱말이 하나도 없어야 함 (있으면 버림).
"""
import datetime as dt

import pytest

from app.core import agent
from app.core.agent import safe_note
from tests.test_guide import FakeAdapter

NOW = dt.datetime(2026, 11, 16, 9, 0)
QUESTION = "160번 내일 15시 몇 명 타?"


@pytest.fixture(scope="module", autouse=True)
def sample_model():
    """가짜 어댑터만 쓰므로 버스 예측 모델 학습이 필요하지 않습니다."""
    yield


@pytest.mark.parametrize("note", [
    "최종 결정 전에 현장 상황을 함께 확인해 주세요.",
    "행사나 날씨처럼 평소와 다른 일정이 있는지 살펴보세요.",
    "담당자 검토를 거친 뒤 반영해 주세요.",
])
def test_plain_check_note_is_accepted(note):
    assert safe_note(note)


@pytest.mark.parametrize("note", [
    # 이슈 #21 뒤에도 예전 검증기를 통과하던 문장 (2026-11-27 확인: 18개 중 14개 통과)
    "증차는 시기상조입니다. 혼잡입니다.",
    "증차는 다음에 하시죠.",
    "굳이 증차할 이유가 있을까요?",
    "유지는 곤란합니다.",
    "증차를 미루세요.",
    "증차는 과합니다.",
    "혼잡하다고 보기는 힘듭니다.",
    "예상 승차는 3,133명 남짓에 그칩니다.",
    "예상 승차는 3,133명을 훌쩍 넘습니다.",
    "예상 승차는 3,133명보다 적습니다.",
    "예상 승차는 최대 3,133명입니다.",
    "예상 승차는 매일 3,133명입니다.",
    "예상 승차는 많습니다.",
    "버스가 더 필요합니다.",
    # 숫자·수량·판단을 말하는 그 밖의 문장
    "삼천 명 정도 탑니다.",
    "평소의 두 배입니다.",
    "지금보다 한산할 것입니다.",
    "배차간격을 조정하는 편이 좋습니다.",
    "권고를 따르지 않아도 됩니다.",
    "승객이 급증할 전망입니다.",
    # 형식: 여러 줄, 너무 긴 글, 빈 글, 깨진 글자
    "현장 상황을 확인해 주세요.\n그리고 다시 검토하세요.",
    "현장 상황을 확인해 주세요. " * 12,
    "",
    "현장 � 확인",
    "현장에서의 실况을 확인하시면 좋습니다.",                          # 실제 Qwen 출력: 한자가 섞임
])
def test_note_with_numbers_or_judgement_is_rejected(note):
    assert not safe_note(note)


def _template():
    return agent.run(FakeAdapter(), QUESTION, use_llm=False, now=NOW)["answer"]


def test_answer_keeps_template_and_appends_note(monkeypatch):
    note = "최종 결정 전에 현장 상황을 함께 확인해 주세요."
    monkeypatch.setattr(agent.llm_module, "generate", lambda *a, **k: f"  {note}\n")
    out = agent.run(FakeAdapter(), QUESTION, use_llm=True, now=NOW)
    template = _template()
    assert out["answer"].startswith(template)                      # 숫자가 든 답변은 한 글자도 바뀌지 않음
    assert out["answer"] == f"{template}\n{agent.CHAT_NOTE_MARK} {note}"
    assert out["llm"] == "qwen" and out["llm_rejected"] is False and out["numbers_verified"] is True


@pytest.mark.parametrize("llm_output", [
    "160번은 15시에 약 1,500명이 탈 것으로 보여요.",                 # 지어낸 숫자
    "160번은 2026-11-17 15시에 1,234명이 탈 것으로 보여요.",          # 맞는 숫자라도 다시 쓰지 않음 (문장 틀에 이미 있음)
    "증차는 시기상조입니다.",                                        # 예전 검증기를 통과하던 틀린 결론
])
def test_rejected_note_leaves_template_only(monkeypatch, llm_output):
    monkeypatch.setattr(agent.llm_module, "generate", lambda *a, **k: llm_output)
    out = agent.run(FakeAdapter(), QUESTION, use_llm=True, now=NOW)
    assert out["answer"] == _template()
    assert out["llm"] is None and out["llm_rejected"] is True and out["numbers_verified"] is True


def test_prompt_does_not_ask_for_a_rewrite(monkeypatch):
    seen = {}

    def fake(provider, system, user, *a, **k):
        seen.update(system=system, user=user)
        return "현장 상황을 함께 확인해 주세요."
    monkeypatch.setattr(agent.llm_module, "generate", fake)
    agent.run(FakeAdapter(), QUESTION, use_llm=True, now=NOW)
    assert "한 문장" in seen["system"] and "다시 쓰세요" not in seen["system"]
    assert _template() in seen["user"]                             # 문장 틀 답변을 보여 주고 그 아래에 붙일 말만 받음
