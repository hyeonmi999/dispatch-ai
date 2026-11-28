# ==================================================
# 서울 TOPIS 실시간 돌발·소통 (서울 열린데이터광장 Open API, 이슈 #42 방법 2)
# ==================================================
# 주소: {TOPIS_BASE_URL}/{키}/xml/{서비스}/{시작}/{끝}/{추가 인자}   (XML만 됨. JSON은 ERROR-301)
#   AccInfo                  실시간 돌발 (서울 전체)         acc_id, occr_date, occr_time, exp_clr_date, exp_clr_time,
#                                                            acc_type, acc_dtype, link_id, grs80tm_x, grs80tm_y, acc_info
#   AccMainCode · AccSubCode 돌발 종류·세부 종류 이름
#   TrafficInfo/{LINK_ID}    링크 하나의 지금 속도         link_id, prcs_spd(km/h), prcs_trv_time(초)
#   LinkVerInfo/{LINK_ID}    링크 꼭짓점                   ver_seq, grs80tm_x, grs80tm_y
# 좌표 grs80tm_x/y = GRS80 TM 중부원점 (EPSG:5181: 원점 38°N 127°E, 가산 200,000 / 500,000)
#
# 외부 HTTP는 이 파일의 _get() 한 곳에서만 합니다 (테스트는 conftest가 이 함수를 막음).
# 키는 주소 안에 들어가므로 로그·예외 문구에는 항상 mask()로 가린 주소만 씁니다.

import json
import logging
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from app.config import SEOUL_OPEN_API_KEY, TOPIS_BASE_URL, TOPIS_PAGE_ROWS, TOPIS_SNAPSHOT_MAX, TOPIS_TIMEOUT

logger = logging.getLogger(__name__)
# httpx는 INFO에서 요청 주소를 그대로 찍어 키가 로그에 남음 → 스크립트가 INFO로 설정해도 경고만 남게 고정
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

SNAPSHOT_NAME = re.compile(r"^\d{8}_\d{4,6}$")   # YYYYMMDD_HHMM(SS)


class TopisError(Exception):
    """TOPIS 호출 실패 (키 없음·연결 실패·오류 응답). status는 API 에러의 status 값"""

    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ==================================================
# 1. 좌표 변환 (GRS80 TM 중부원점 ↔ WGS84). pyproj 없이 표준 횡메르카토르 식
# ==================================================

_A = 6378137.0
_F = 1 / 298.257222101
_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)
_LAT0 = math.radians(38.0)
_LON0 = math.radians(127.0)
_FE, _FN, _K0 = 200000.0, 500000.0, 1.0


def _meridian(phi: float) -> float:
    e2, e4, e6 = _E2, _E2 ** 2, _E2 ** 3
    return _A * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
                 - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
                 + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
                 - (35 * e6 / 3072) * math.sin(6 * phi))


_M0 = _meridian(_LAT0)


def wgs_to_tm(lat: float, lng: float) -> tuple[float, float]:
    """WGS84 (위도, 경도) → TM (x 동쪽, y 북쪽) 미터"""
    phi, lam = math.radians(lat), math.radians(lng)
    n = _A / math.sqrt(1 - _E2 * math.sin(phi) ** 2)
    t = math.tan(phi) ** 2
    c = _EP2 * math.cos(phi) ** 2
    a = (lam - _LON0) * math.cos(phi)
    x = _FE + _K0 * n * (a + (1 - t + c) * a ** 3 / 6 + (5 - 18 * t + t * t + 72 * c - 58 * _EP2) * a ** 5 / 120)
    y = _FN + _K0 * (_meridian(phi) - _M0 + n * math.tan(phi) * (
        a * a / 2 + (5 - t + 9 * c + 4 * c * c) * a ** 4 / 24 + (61 - 58 * t + t * t + 600 * c - 330 * _EP2) * a ** 6 / 720))
    return x, y


def tm_to_wgs(x: float, y: float) -> tuple[float, float]:
    """TM (x, y) 미터 → WGS84 (위도, 경도)"""
    m = _M0 + (y - _FN) / _K0
    mu = m / (_A * (1 - _E2 / 4 - 3 * _E2 ** 2 / 64 - 5 * _E2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    c1 = _EP2 * math.cos(phi1) ** 2
    t1 = math.tan(phi1) ** 2
    n1 = _A / math.sqrt(1 - _E2 * math.sin(phi1) ** 2)
    r1 = _A * (1 - _E2) / (1 - _E2 * math.sin(phi1) ** 2) ** 1.5
    d = (x - _FE) / (n1 * _K0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d * d / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * _EP2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * _EP2 - 3 * c1 * c1) * d ** 6 / 720)
    lng = _LON0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                   + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * _EP2 + 24 * t1 * t1) * d ** 5 / 120) / math.cos(phi1)
    return math.degrees(lat), math.degrees(lng)


# ==================================================
# 2. 호출과 XML 읽기
# ==================================================

def mask(url: str) -> str:
    """주소의 키 자리를 가림 (로그·예외 문구용)"""
    return re.sub(r"(:8088/)[^/]+/", r"\1***/", url)


def _get(url: str, timeout: float) -> str:
    """외부 HTTP GET 한 곳. 테스트에서는 conftest가 이 함수를 막음"""
    with httpx.Client(timeout=timeout) as client:
        res = client.get(url)
        res.raise_for_status()
        return res.text


def parse(service: str, text: str) -> tuple[list[dict], int]:
    """XML → (행 목록, 전체 개수). INFO-200(데이터 없음)은 빈 목록, 그 밖의 오류 코드는 TopisError"""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise TopisError("topis_bad_response", f"TOPIS {service} 응답을 읽지 못했습니다: {e}") from e
    code = root.findtext("RESULT/CODE") or root.findtext("CODE") or ""
    if code == "INFO-200":
        return [], 0
    if code != "INFO-000":
        message = (root.findtext("RESULT/MESSAGE") or root.findtext("MESSAGE") or "").strip()
        status = "topis_key_invalid" if code in {"INFO-100", "ERROR-300"} else "topis_error"
        raise TopisError(status, f"TOPIS {service} 오류 {code}: {message}")
    rows = [{child.tag: (child.text or "").strip() for child in row} for row in root.findall("row")]
    total = int(root.findtext("list_total_count") or len(rows))
    return rows, total


class TopisClient:
    def __init__(self, key: str | None = None, base_url: str = TOPIS_BASE_URL, timeout: float = TOPIS_TIMEOUT):
        self.key = SEOUL_OPEN_API_KEY if key is None else key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def ready(self) -> bool:
        return bool(self.key)

    def call(self, service: str, start: int, end: int, *args: str) -> tuple[list[dict], int]:
        if not self.key:
            raise TopisError("topis_key_missing",
                             "실시간 정보를 받으려면 .env에 SEOUL_OPEN_API_KEY(서울 열린데이터광장 일반 인증키)가 필요합니다")
        parts = [self.base_url, self.key, "xml", service, str(start), str(end), *[str(a) for a in args]]
        url = "/".join(parts) + "/"
        try:
            text = _get(url, self.timeout)
        except httpx.TimeoutException as e:
            logger.warning("TOPIS 시간 초과: %s", mask(url))
            raise TopisError("topis_timeout", f"TOPIS {service} 응답이 {self.timeout:g}초 안에 오지 않았습니다") from e
        except httpx.HTTPError as e:
            logger.warning("TOPIS 호출 실패: %s (%s)", mask(url), type(e).__name__)
            raise TopisError("topis_unavailable", f"TOPIS {service}에 연결하지 못했습니다 ({type(e).__name__})") from e
        return parse(service, text)

    def call_all(self, service: str, *args: str) -> list[dict]:
        """1,000행씩 나눠 끝까지 받음"""
        rows, total = self.call(service, 1, TOPIS_PAGE_ROWS, *args)
        start = TOPIS_PAGE_ROWS + 1
        while start <= total:
            more, _ = self.call(service, start, start + TOPIS_PAGE_ROWS - 1, *args)
            if not more:
                break
            rows += more
            start += TOPIS_PAGE_ROWS
        return rows


# ==================================================
# 3. 기록 (시연용 다시 보기). 키·주소는 넣지 않음
# ==================================================

def save_snapshot(folder: Path, name: str, data: dict, keep: int = TOPIS_SNAPSHOT_MAX) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    files = sorted(folder.glob("*.json"))
    for old in files[:max(0, len(files) - keep)]:
        old.unlink(missing_ok=True)
    return path


def list_snapshots(folder: Path, limit: int) -> list[dict]:
    """최근 기록부터. 이름·받은 시각·돌발 수·소통 링크 수"""
    items = []
    for path in sorted(folder.glob("*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("기록을 읽지 못했습니다 (%s): %s", path.name, e)
            continue
        items.append({"name": path.stem, "fetched_at": data.get("fetched_at"),
                      "incidents": len(data.get("incidents", [])), "links": len(data.get("link_seconds", {}))})
    return items


def load_snapshot(folder: Path, name: str) -> dict | None:
    if not SNAPSHOT_NAME.match(name):
        return None
    path = folder / f"{name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("기록을 읽지 못했습니다 (%s): %s", path.name, e)
        return None
