"""서울시 보도자료(교통) 페이지를 텍스트로 저장해 RAG 문서(docs/bus)로 사용합니다.

사용: python scripts/fetch_press_docs.py [저장폴더]
"""
import re, sys, urllib.request
from pathlib import Path
from bs4 import BeautifulSoup
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "docs" / "bus"
PAGES = [
 ("출퇴근맞춤버스_다람쥐버스_운행개시", "https://news.seoul.go.kr/traffic/archives/33776"),
 ("버스혼잡도_안내서비스_개시", "https://news.seoul.go.kr/traffic/?p=33643"),
 ("빅데이터_시내버스_정기노선_조정", "https://news.seoul.go.kr/traffic/?p=502346"),
 ("시내버스_야간운행_정상화_혼잡도기반", "https://news.seoul.go.kr/traffic/?p=505367"),
 ("혼잡도별_대중교통_특별대책", "https://mediahub.seoul.go.kr/archives/1280193"),
 ("서소문고가_사고_버스_집중배차", "https://news.seoul.go.kr/traffic/archives/517299"),
 ("대중교통_혼잡_해법_시민기자", "https://mediahub.seoul.go.kr/archives/2010746"),
 ("지하철파업_비상수송대책", "https://mediahub.seoul.go.kr/archives/2012886"),
]
JUNK = {"트위터","페이스북","카카오톡","페이지 URL 복사","인쇄하기 새창열림","글자 크기 크게 변경하기","글자 크기 작게 변경하기","좋아요","조회","발행일","수정일"}
for name, url in PAGES:
    html = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"}), timeout=20).read().decode("utf-8","replace")
    s = BeautifulSoup(html, "html.parser")
    title = (s.find("meta", property="og:title") or {}).get("content") or s.title.string
    body = s.select_one(".post-content, .entry-content, .detail-content, .view_content, article, #content") or s.body
    for t in body(["script","style","nav","footer","button"]): t.decompose()
    for br in body.find_all("br"): br.replace_with("\n")
    for b in body.find_all(["p","li","div","h1","h2","h3","h4","tr","table"]): b.insert_after("\n")
    text = body.get_text("")
    lines = [re.sub(r"[ \t\r\xa0]+", " ", l).strip() for l in text.split("\n")]
    lines = [l for l in lines if l and l not in JUNK and not re.fullmatch(r"[\d,]+", l)]
    text = "\n".join(lines)
    m = re.search(r"(20\d\d)[.\-]\s?(\d{1,2})[.\-]\s?(\d{1,2})", text)
    if "버스" not in text or len(text) < 500:
        print("SKIP", name, len(text)); continue
    header = f"문서명: 서울시 보도자료 - {title}\n출처: {url}\n게시일: {'-'.join(m.groups()) if m else '미상'}\n수집일: 2026-10-27\n\n"
    p = OUT / f"서울시보도자료_{name}.txt"; p.write_text(header + text, encoding="utf-8")
    print(f"OK {p.name} ({len(text)} chars, 게시일 {m.group(0) if m else '미상'})")
