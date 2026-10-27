# ==================================================
# 배차 권고 공통 계층 (Advisor)
# ==================================================
# 권고 = 도메인별 수치 판단(예측·혼잡도·권고 간격) + 규정 지식베이스 근거
#
# 원칙
# - AI는 보조 역할: 모든 권고는 needs_review=True, 최종 결정은 담당자
# - 근거를 못 찾으면 그 사실을 그대로 표시하고 신뢰도를 낮춤

from app.core.rag import KnowledgeBase

EVIDENCE_NOT_FOUND_PENALTY = 0.15


class Advisor:
    def __init__(self, knowledge_base: KnowledgeBase):
        self.kb = knowledge_base

    def evidence(self, question: str, llm: str, embedding: str, use_llm: bool) -> dict:
        """권고 근거: 규정 지식베이스 질의 (/ask와 같은 경로)"""
        return self.kb.ask(question, llm=llm, embedding=embedding, use_llm=use_llm)

    @staticmethod
    def finalize(advice: dict, evidence: dict, base_confidence: float) -> dict:
        confidence = base_confidence - (0 if evidence.get("found") else EVIDENCE_NOT_FOUND_PENALTY)
        advice.update({
            "evidence": evidence,
            "confidence": round(min(max(confidence, 0.05), 0.95), 2),
            "needs_review": True,
            "review_status": "pending",
        })
        return advice
