# Code Review Guide

이 문서는 주문 관리 코드 리뷰를 빠르게 하기 위한 운영 기준이다.

## 핵심 데이터

- 주문 저장소: `data/orders.json`
- 사용자 저장소: `data/users.json`
- 감사 로그: `data/audit.jsonl`
- 서버는 별도 DB를 쓰지 않고 JSON 파일을 읽고 쓴다.

## 주요 파일

- `src/server.py`: API, 권한, 주문 상태 변경, 중복 판정, 파일 저장
- `src/config.py`: 파일 경로, 권한 상수, 업로드 제한, 시간대 설정
- `src/orders/dedupe.py`: 중복 주문 판정/정리 순수 로직
- `src/orders/import_service.py`: multipart/ZIP 업로드 해석과 엑셀 importer 호출
- `src/importers.py`: 엑셀/ZIP 주문 파싱, 채널별 필드 매핑, 주문수집 그룹화
- `src/excel.py`: xlsx/html workbook 읽기와 xlsx 내보내기
- `public/app.js`: 주문 화면 상태, 권한별 UI, 주문 수정/취소/중복 정리 호출
- `public/app-state.js`: 프론트 전역 상태와 권한/역할 상수
- `public/utils.js`: HTML escape, 날짜/금액 포맷 등 공통 유틸
- `public/api.js`: fetch 호출 래퍼
- `public/index.html`: 화면 구조와 버튼 위치
- `public/styles.css`, `public/operations.css`: 운영 화면 스타일

## 리뷰 우선순위

1. `src/server.py`
   - `do_POST`, `do_PATCH`: 권한과 상태 변경 API
   - `write_orders`: 주문 파일 저장과 백업 경로

2. `src/orders/dedupe.py`
   - `new_unique_orders`: 엑셀 import 중복 제외
   - `cleanup_duplicate_orders`: 이미 저장된 중복 주문 정리
   - `order_duplicate_keys`: 주문별 중복 후보 키 생성

3. `src/orders/import_service.py`
   - `imported_orders_from_multipart`: 업로드 파일 개수/크기/ZIP 제한
   - `archive_excel_files`: ZIP 내부 엑셀 파일 필터링

4. `src/importers.py`
   - `_collected_orders`: 주문수집 파일 여러 행을 한 주문으로 묶는 로직
   - `_coupang`, `_kakao`, `_godomall`: 채널별 엑셀 컬럼 매핑

5. `public/app.js`
   - `updateAccessUI`: 권한별 버튼 노출
   - `updateOrder`: 체크박스/관리번호/상세수정 API 호출
   - `cancelOrder`: 취소 후 화면 목록 동기화
   - `dedupe-button` click handler: 중복 정리 API 호출

## 중복 주문 판정

중복 판정은 서버에서만 최종 결정한다.

- 실제 주문번호가 있으면 주문번호를 우선한다.
- 주문수집 임시번호(`수집-...`)는 수취인/상품/옵션/주소/수량 등 내용 기반으로 본다.
- 쿠팡은 주문수집 파일과 DeliveryList 파일의 주문번호 체계가 달라서 `쿠팡 + 주문시각(분) + 수취인 + 상품 + 수량` 보강 키를 사용한다.
- 중복으로 판단되면 기존 주문의 작업 상태는 유지하고, 비어 있는 전화/주소/금액만 보강한다.

## 중복 주문 정리 버튼

- API: `POST /api/orders/dedupe`
- 권한: `owner`, `md`
- 실제로 `data/orders.json`에서 중복 행을 제거한다.
- 취소 주문은 자동 정리 대상에서 제외한다.
- 작업 진행 상태가 더 많은 주문을 남긴다.
- 삭제될 주문의 전화/주소/금액/메모 등은 남길 주문이 비어 있을 때만 병합한다.

## 권한 체크

- 회원 관리: `owner`, `developer`
- 주문 import/수기등록/출고 엑셀: `owner`, `developer`, `sales_manager`, `md`
- 주문 취소: `owner`, `developer`, `as_manager`, `sales_manager`, `md`
- 중복 주문 정리: `owner`, `md`
- 월간 현황: `owner`

프론트의 버튼 숨김은 편의 기능이고, 실제 권한은 서버에서 다시 검증해야 한다.

## 테스트

전체 테스트:

```powershell
python -m unittest discover -s test -p "test_*.py" -v
```

중복/서버 테스트만 볼 때:

```powershell
python -m unittest discover -s test -p test_server.py -v
```

엑셀 파싱 테스트만 볼 때:

```powershell
python -m unittest discover -s test -p test_importers.py -v
```

## 운영 주의

- `data/orders.json`을 직접 수정하기 전에는 반드시 백업 파일을 남긴다.
- 서버 실행 중 직접 파일을 수정하면 브라우저 화면은 새로고침 전까지 예전 상태일 수 있다.
- 중복 정리 버튼은 후보 미리보기 없이 바로 저장 파일을 정리하므로, 화면에서 명확히 중복이 보일 때만 사용한다.
