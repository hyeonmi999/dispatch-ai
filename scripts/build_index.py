"""
규정 문서 FAISS 인덱스 생성

docs/<domain>/ 의 PDF·TXT를 조항 단위로 나눠 임베딩합니다.
서버도 처음 /ask 호출 때 인덱스가 없으면 자동 생성하지만, 미리 만들어 두면 첫 응답이 빨라집니다.

사용: python scripts/build_index.py [--domain bus] [--embedding bge|openai]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import DOCS_DIR  # noqa: E402
from app.core.rag import KnowledgeBase  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", default="bus")
    parser.add_argument("--embedding", default="bge", choices=["bge", "openai"])
    args = parser.parse_args()

    kb = KnowledgeBase(domain=args.domain, docs_dir=DOCS_DIR / args.domain)
    kb.build(args.embedding)
    for q in ["노선버스 운행횟수를 늘리려면 사업계획 변경 절차가 어떻게 되나요?", "오늘 점심 메뉴 추천해줘"]:
        hits = kb.search(q, args.embedding, k=2)
        print(f"\nQ: {q}")
        for h in hits:
            print(f"  {h['score']:.3f}  {h['document']} {h['article']}")


if __name__ == "__main__":
    main()
