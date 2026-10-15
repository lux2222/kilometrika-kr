# 설치·백업·세부 설정

[프로젝트 소개로 돌아가기](../README.md)

처음 사용하는 분은 README의 빠른 시작부터 확인하세요. 이 문서는 추가 설정이나 백업이 필요할 때 참고하는 기술 문서입니다. 아래 검증 기록은 GitHub 업로드 준비 당시 기준입니다.

## Docker 실행과 종료

Docker Engine과 Docker Compose가 필요합니다. 저장소 루트에서:

```bash
docker compose -p kilometrika-fork up -d --build
# 브라우저: http://127.0.0.1:8000
docker compose -p kilometrika-fork logs --tail=100
# 컨테이너 종료, 데이터 볼륨 유지
docker compose -p kilometrika-fork stop
# 다시 실행
docker compose -p kilometrika-fork start
```

다른 로컬 포트가 필요하면 실행 명령 앞에 `APP_PORT=18000`을 붙입니다. 기본 바인딩은 `127.0.0.1`입니다. 인증 기능이 없으므로 공용 인터넷에 직접 노출하지 마세요.

Compose는 이 저장소의 `Dockerfile`과 `requirements.txt`, 전체 `app/` 및 정적 자산으로 빌드합니다. 운영 컨테이너나 로컬 전용 Kilometrika 이미지, `Dockerfile.ui`는 필요하지 않습니다. Python 기본 이미지·OS 패키지·Python 의존성을 받는 네트워크 연결은 필요합니다. 기본 이미지 태그와 일부 의존성 버전 범위가 가변적이므로 완전한 재현 빌드를 보장하지 않습니다.

## 데이터 위치와 백업

위 프로젝트명을 사용하면 Docker named volume `kilometrika-fork_kilometrika-data`가 컨테이너 `/data`에 연결됩니다. 호스트의 개인 디렉터리를 연결하지 않습니다. DB `tcx.db`, `inbox/`, `archive/`, `failed/` 및 첨부 자료가 이 볼륨에 저장됩니다.

```bash
# Docker가 관리하는 실제 볼륨 위치 조회
docker volume inspect kilometrika-fork_kilometrika-data --format '{{.Mountpoint}}'
# 쓰기를 중단한 상태로 볼륨 전체 백업
mkdir -p backups
docker compose -p kilometrika-fork stop
docker compose -p kilometrika-fork run --rm --no-deps -T --entrypoint tar kilometrika -C /data -czf - . > "backups/kilometrika-$(date +%Y%m%d-%H%M%S).tar.gz"
# 백업 명령 성공 및 파일 확인 후 다시 시작
docker compose -p kilometrika-fork start
```

백업에는 개인 운동 자료가 포함되므로 Git에 추가하지 마세요. `docker compose down -v`는 데이터 볼륨을 삭제하므로 사용하지 마세요. 다른 프로젝트명을 썼다면 모든 명령에 같은 이름을 사용해야 합니다.

## Other → 근력운동 수동 분류

TCX의 `Other`만으로 근력운동이라고 자동 판정하지 않습니다. 활동 상세 화면의 **운동 관리 · 기존 기능 → 근력운동**을 선택하면 해당 기록의 category가 `strength`로 저장됩니다. API는 `PATCH /api/activities/{id}/category`에 `{"category":"strength"}`를 받습니다.

화면 분기는 특정 개인 기록 ID가 아니라 category를 사용합니다. 근력운동에서는 지도·속도 대신 심박 중심으로 표시합니다. 누락 복구에는 해당 기록과 해시가 일치하는 원본 TCX가 필요합니다. HR 구간 분석도 `/data/archive`의 원본을 읽으며, 데이터가 없으면 분석 불가로 처리합니다.

## 개인용 HR 구간 설정

실제 설정은 **`app/hr_zones.py`의 `ZONES` 상수**입니다. 각 항목은 `(구간명, 최소 bpm, 최대 bpm)`이며 양 끝을 포함하는 정수 범위입니다. 현재 값은 개인용 수동 설정이지 보편적인 운동 처방이 아닙니다.

자신에게 맞게 `ZONES`를 직접 수정하고 `docker compose -p kilometrika-fork up -d --build`로 다시 빌드·실행해야 합니다. UI 설정창, 사용자별 프로필, 환경변수 기반 HR 구간 설정은 없습니다. UI 구간 표시는 API의 `criteria.zones_bpm`을 사용합니다. 같은 파일의 `MAX_GAP_SECONDS`는 인접 측정 간격의 분석 한계이며, 결측 시간을 휴식으로 단정하지 않습니다.

## 외부 연결과 개인정보

Chart.js·Leaflet·html2canvas는 로컬 `app/static/vendor/`에서 제공합니다. 지도 배경은 OpenStreetMap 타일 서버에 연결합니다.

날씨는 `app/static/index.html`의 `weatherRequest()` / `loadActivityWeather()`에서 브라우저가 Open-Meteo에 직접 요청합니다. 최근 활동은 `api.open-meteo.com`, 과거 활동은 `archive-api.open-meteo.com`을 사용합니다. 첫 유효 GPS 좌표와 운동 날짜가 외부 서비스로 전송됩니다. 표시값은 지역·시간 기준 추정치이지 운동기기의 측정값이 아닙니다. 이 구현에는 API 키 입력 설정이 없습니다. 연결 차단·응답 실패·GPS 부재 시 날씨는 표시하지 않습니다. 외부 전송을 원치 않으면 해당 호출 코드를 수정해야 하며, 별도 UI 토글은 없습니다.

Compose에서 `TZ`, `WATCH_INTERVAL`, 선택적 `MQTT_*`, `FRAME_LANG`을 지정할 수 있습니다. MQTT는 기본 비활성입니다. 비밀값은 로컬 환경에만 설정하고 저장소에 넣지 마세요. 한국어 UI 날짜 표시는 현재 `app/static/index.html`에서 `Asia/Seoul`로 고정되어 있으므로 컨테이너 `TZ`만으로 UI 시간대가 바뀌지는 않습니다.

## 테스트와 라이선스

**확인 완료:** 전체 앱 소스 Docker 빌드, 운영 데이터와 분리된 빈 named volume 기동, 기본 페이지·활동/요약 API 응답, 브라우저 빈 화면 및 로컬 Chart.js·Leaflet·html2canvas 로딩, 개인 데이터 없는 복구 회귀 테스트 1개 통과. 이미지 내부 앱 파일과 빌드 소스의 해시 일치를 확인했습니다.

**미검증:** 이 독립 빌드에서 실제 운동 상세 화면 전체, 이미지 내보내기, 외부 날씨·지도 요청, 전체 테스트 모음은 실행하지 않았습니다. 위 주요 변경 목록은 구현 범위이며 전 기능 검증 완료를 뜻하지 않습니다. 개인용 HR 구간과 UI 시간대는 수동 소스 설정이며 의존성 완전 고정도 남아 있습니다.

개인 DB 없이 수행하는 복구 회귀 테스트: `python -m unittest discover -s tests -p test_common_recovery.py` (앱 의존성 설치 필요). 개인 기록을 참조하던 별도 HR API 테스트는 업로드 사본에 포함하지 않았습니다.

원본부터 추적된 `tests/fixtures/`와 `docs/` 이미지는 이력과 함께 그대로 유지합니다. 원본 문서 이미지는 이 포크의 검증 결과가 아닙니다. `.gitignore`는 이미 추적된 파일이나 과거 커밋을 제거하지 않습니다.

원본 MIT `LICENSE` 및 vendor의 라이선스 파일·소스 내 저작권 고지를 보존합니다. html2canvas의 고지는 `app/static/vendor/html2canvas-LICENSE.txt`에 있습니다. 원본 운동 아이콘은 Material Icons(Apache License 2.0)입니다. [`README.ru.md`](../README.ru.md)는 원본 참고 문서로, 이 포크의 볼륨·기능 설명과 다를 수 있습니다.
