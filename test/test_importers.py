import sys
import unittest
import io
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from excel import find_libreoffice_command, read_first_sheet, write_xlsx
from importers import import_workbook

COLLECTED_SAMPLE = Path(os.getenv("COLLECTED_SAMPLE_FILE", "test/fixtures/collected-orders.xlsx"))


class ImporterTests(unittest.TestCase):
    def test_imports_kakao_order(self):
        content = write_xlsx(
            ["결제번호", "채널상품번호", "상품명", "수령인명", "수량", "주문일시"],
            [["3371403104", "K-PRODUCT", "카카오 상품", "홍길동", "1", "2026-06-11 10:00:00"]],
        )
        orders = import_workbook(content, "kakao.xlsx")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["channel"], "카카오")
        self.assertEqual(orders[0]["orderNumber"], "3371403104")

    def test_imports_coupang_orders(self):
        content = write_xlsx(
            ["주문번호", "묶음배송번호", "등록상품명", "수취인이름", "구매수(수량)", "주문일"],
            [
                ["COUPANG-1", "BUNDLE-1", "쿠팡 상품 1", "정영희", "1", "2026-06-11 11:00:00"],
                ["COUPANG-2", "BUNDLE-2", "쿠팡 상품 2", "김고객", "1", "2026-06-11 12:00:00"],
                ["COUPANG-3", "BUNDLE-3", "쿠팡 상품 3", "이고객", "1", "2026-06-11 13:00:00"],
            ],
        )
        orders = import_workbook(content, "coupang.xlsx")
        self.assertEqual(len(orders), 3)
        self.assertEqual(orders[0]["channel"], "쿠팡")
        self.assertEqual(orders[0]["recipient"], "정영희")

    def test_imports_smartstore_order_and_converts_excel_date(self):
        content = write_xlsx(
            [
                "상품주문번호", "주문번호", "판매채널", "수취인명", "수취인연락처1",
                "통합배송지", "우편번호", "상품명", "옵션정보", "판매자 상품코드",
                "수량", "최종 상품별 총 주문금액", "주문일시", "배송메세지",
            ],
            [[
                "2026072814836311", "2026072837709401", "스마트스토어", "홍길동",
                "010-1234-5678", "서울특별시 중구 세종대로 1", "04524", "테스트 노트북",
                "A급 외관", "NOTEBOOK-1", "2", "360000", "46231.55195601852", "문 앞",
            ]],
        )
        orders = import_workbook(content, "smartstore.xlsx")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["channel"], "스마트스토어")
        self.assertEqual(orders[0]["orderNumber"], "2026072837709401")
        self.assertEqual(orders[0]["orderedAt"], "2026-07-28 13:14:49")
        self.assertEqual(orders[0]["productCode"], "NOTEBOOK-1")
        self.assertEqual(orders[0]["quantity"], 2)
        self.assertEqual(orders[0]["amount"], 360000)

    def test_groups_smartstore_product_rows_by_order_number(self):
        content = write_xlsx(
            [
                "상품주문번호", "주문번호", "판매채널", "수취인명", "상품명",
                "옵션정보", "판매자 상품코드", "수량", "최종 상품별 총 주문금액", "주문일시",
            ],
            [
                ["PRODUCT-1", "ORDER-1", "스마트스토어", "홍길동", "노트북", "A급", "PC-1", "1", "300000", "2026-08-05 09:00"],
                ["PRODUCT-2", "ORDER-1", "스마트스토어", "홍길동", "메모리 추가", "16GB", "RAM-1", "1", "50000", "2026-08-05 09:00"],
                ["PRODUCT-3", "ORDER-2", "스마트스토어", "김고객", "노트북", "S급", "PC-2", "1", "400000", "2026-08-05 09:10"],
            ],
        )
        orders = import_workbook(content, "smartstore.xlsx")
        self.assertEqual(len(orders), 2)
        self.assertEqual(orders[0]["orderNumber"], "ORDER-1")
        self.assertEqual(orders[0]["amount"], 350000)
        self.assertIn("메모리 추가 / 16GB", orders[0]["optionName"])

    def test_exported_workbook_can_be_read(self):
        content = write_xlsx(["주문번호", "담당자"], [["100", "홍길동"]])
        self.assertEqual(read_first_sheet(content), [{"주문번호": "100", "담당자": "홍길동"}])

    def test_imports_legacy_xls_workbook(self):
        if os.getenv("RUN_LIBREOFFICE_TESTS") != "1":
            self.skipTest("RUN_LIBREOFFICE_TESTS=1일 때 실행합니다.")
        libreoffice = find_libreoffice_command()
        if not libreoffice:
            self.skipTest("LibreOffice가 없습니다.")
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            xlsx = temporary / "legacy-source.xlsx"
            xlsx.write_bytes(write_xlsx(
                ["주문번호", "상품명", "수령인", "수량"],
                [["XLS-1", "구형 엑셀 상품", "홍길동", "2"]],
            ))
            result = subprocess.run(
                [libreoffice, "--headless", "--convert-to", "xls", "--outdir", directory, str(xlsx)],
                check=False,
                capture_output=True,
                timeout=30,
            )
            xls = temporary / "legacy-source.xls"
            if result.returncode != 0 or not xls.exists():
                self.skipTest("LibreOffice에서 .xls 테스트 파일을 만들지 못했습니다.")
            orders = import_workbook(xls.read_bytes(), xls.name)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["orderNumber"], "XLS-1")
        self.assertEqual(orders[0]["quantity"], 2)

    def test_imports_html_workbook_with_xls_extension(self):
        content = """<html><body><table><tr>
        <td>주문 번호</td><td>주문일자</td><td>상품주문번호</td><td>상품코드</td>
        <td>자체상품코드</td><td>상품명</td><td>옵션정보</td><td>상품수량</td><td>판매가</td>
        <td>수취인 이름</td><td>수취인 핸드폰 번호</td><td>수취인 주소</td><td>수취인 나머지 주소</td>
        </tr><tr><td>260615052855</td><td>2026-06-15 05:28:36</td><td>106928</td><td>1000000493</td>
        <td>NT371B5M</td><td>테스트 노트북</td><td>A급</td><td>2</td><td>390000.00</td>
        <td>홍길동</td><td>010-1234-5678</td><td>서울시</td><td>101호</td></tr></table></body></html>""".encode()
        orders = import_workbook(content, "shop-export.xls")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["channel"], "고도몰")
        self.assertEqual(orders[0]["orderNumber"], "260615052855")
        self.assertEqual(orders[0]["quantity"], 2)
        self.assertEqual(orders[0]["address"], "서울시 101호")

    def test_groups_godomall_additions_into_main_order(self):
        content = """<html><body><table><tr>
        <td>주문 번호</td><td>주문일자</td><td>상품주문번호</td><td>상품코드</td><td>자체상품코드</td>
        <td>상품명</td><td>옵션정보</td><td>상품수량</td><td>판매가</td><td>총 결제 금액</td>
        <td>수취인 이름</td><td>수취인 핸드폰 번호</td><td>수취인 주소</td>
        </tr><tr><td>ORDER-1</td><td>2026-06-15</td><td>1</td><td>P1</td><td>MAIN</td>
        <td>기본 노트북</td><td>A급</td><td>1</td><td>390000</td><td>460000</td>
        <td>홍길동</td><td>010-1234-5678</td><td>서울시</td></tr>
        <tr><td>ORDER-1</td><td>2026-06-15</td><td>2</td><td>P2</td><td></td>
        <td>[추가]메모리 업그레이드</td><td></td><td>1</td><td>70000</td><td>460000</td>
        <td>홍길동</td><td>010-1234-5678</td><td>서울시</td></tr></table></body></html>""".encode()
        orders = import_workbook(content, "shop-export.xls")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["productName"], "기본 노트북")
        self.assertEqual(orders[0]["optionName"], "A급 / [추가]메모리 업그레이드")
        self.assertEqual(orders[0]["amount"], 460000)

    def test_imports_generic_order_columns(self):
        content = write_xlsx(
            ["주문번호", "주문일시", "상품명", "옵션명", "수량", "수령인", "연락처", "주소", "배송메시지"],
            [["MANUAL-1", "2026-06-12 12:00", "테스트 상품", "검정", "2", "홍길동", "010-1234-5678", "서울시", "문 앞"]],
        )
        orders = import_workbook(content, "other-channel.xlsx")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["orderNumber"], "MANUAL-1")
        self.assertEqual(orders[0]["recipient"], "홍길동")
        self.assertEqual(orders[0]["quantity"], 2)

    def test_collected_order_uses_excel_order_number(self):
        content = write_xlsx(
            ["플랫폼", "주문번호", "주문일시", "상품명 + 옵션명", "등록옵션명", "수량", "총 상품결제금액", "수취인 이름"],
            [["쿠팡", "CP-ORDER-1", "2026-06-12 12:00", "노트북 / 기본", "기본", "1", "390000", "홍길동"]],
        )
        orders = import_workbook(content, "collected.xlsx")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["channel"], "쿠팡")
        self.assertEqual(orders[0]["orderNumber"], "CP-ORDER-1")
        self.assertEqual(orders[0]["importKey"], "주문수집:쿠팡:CP-ORDER-1")

    def test_collected_order_groups_repeated_order_number_rows(self):
        content = write_xlsx(
            ["플랫폼", "주문번호", "주문일시", "상품명 + 옵션명", "등록옵션명", "수량", "총 상품결제금액", "수취인 이름"],
            [
                ["쿠팡", "CP-ORDER-1", "2026-06-12 12:00", "노트북 / 기본", "기본", "1", "390000", "홍길동"],
                ["쿠팡", "CP-ORDER-1", "2026-06-12 12:00", "무선마우스", "", "1", "0", "홍길동"],
            ],
        )
        orders = import_workbook(content, "collected.xlsx")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["orderNumber"], "CP-ORDER-1")
        self.assertIn("무선마우스", orders[0]["optionName"])

    def test_collected_order_combines_repeated_same_product_into_quantity(self):
        product = "LG전자 15.6인치 인텔 i5 초가성비 노트북 사무용 영상시청용 우측숫자키패드 탑재 윈도우11"
        content = write_xlsx(
            ["플랫폼", "주문번호", "주문일시", "상품명 + 옵션명", "등록옵션명", "수량", "총 상품결제금액", "수취인 이름"],
            [
                ["쿠팡", "CP-ORDER-2", "2026-06-12 12:00", product, "기본", "1", "390000", "나다은"],
                ["쿠팡", "CP-ORDER-2", "2026-06-12 12:00", product, "", "1", "390000", "나다은"],
            ],
        )
        orders = import_workbook(content, "collected.xlsx")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["productName"], product)
        self.assertEqual(orders[0]["quantity"], 2)
        self.assertEqual(orders[0]["optionName"], "기본")

    def test_collected_order_counts_different_notebooks_and_keeps_both_product_lines(self):
        samsung = "삼성전자 노트북7 프로 지포스 MX110 인텔 i7 9세대 15.6인치 대화면 FHD 지문인식 우측넘버패드 탑재 윈도우11\n제품등급선택 (필수): S급 외관 / S급 배터리"
        lg = "LG전자 15.6인치 인텔 i5 초가성비 노트북 사무용 영상시청용 우측숫자키패드 탑재 윈도우11"
        content = write_xlsx(
            ["플랫폼", "주문번호", "주문일시", "상품명 + 옵션명", "등록옵션명", "수량", "총 상품결제금액", "수취인 이름"],
            [
                ["쿠팡", "CP-ORDER-3", "2026-06-12 12:00", samsung, "S급", "1", "390000", "나다은"],
                ["쿠팡", "CP-ORDER-3", "2026-06-12 12:00", lg, "", "1", "390000", "나다은"],
            ],
        )
        orders = import_workbook(content, "collected.xlsx")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["productName"], samsung)
        self.assertEqual(orders[0]["quantity"], 2)
        self.assertIn(lg, orders[0]["optionName"])

    def test_collected_order_groups_repeated_recipient_rows_without_order_number(self):
        content = write_xlsx(
            ["플랫폼", "주문일시", "상품명 + 옵션명", "등록옵션명", "수량", "총 상품결제금액", "수취인 이름", "연락처", "주소"],
            [
                ["쿠팡", "2026-06-12 12:00", "노트북 / 기본", "기본", "1", "390000", "홍길동", "010-1111-2222", "서울시 강남구"],
                ["쿠팡", "2026-06-12 12:00", "무선마우스", "", "1", "0", "홍길동", "010-1111-2222", "서울시 강남구"],
            ],
        )
        orders = import_workbook(content, "collected.xlsx")
        self.assertEqual(len(orders), 1)
        self.assertTrue(orders[0]["orderNumber"].startswith("수집-"))
        self.assertIn("무선마우스", orders[0]["optionName"])

    def test_imports_orders_with_alternate_date_and_address_headers(self):
        content = write_xlsx(
            ["주문번호", "주문일자", "상품명", "옵션명", "수량", "수령인", "배송지 주소", "상세주소"],
            [["ALT-1", "2026-06-16 09:30", "테스트 상품", "기본", "1", "홍길동", "서울특별시 중구", "세종대로 1"]],
        )
        orders = import_workbook(content, "alt-channel.xlsx")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["orderedAt"], "2026-06-16 09:30")
        self.assertEqual(orders[0]["address"], "서울특별시 중구 세종대로 1")

    def test_imports_collected_order_workbook(self):
        if os.getenv("RUN_EXTERNAL_SAMPLE_TESTS") != "1":
            self.skipTest("RUN_EXTERNAL_SAMPLE_TESTS=1일 때 실행합니다.")
        if not COLLECTED_SAMPLE.exists():
            self.skipTest("주문수집 샘플 파일이 없습니다.")
        orders = import_workbook(COLLECTED_SAMPLE.read_bytes(), COLLECTED_SAMPLE.name)
        self.assertEqual(len(orders), 32)
        self.assertEqual(orders[0]["channel"], "쿠팡")
        self.assertEqual(orders[0]["amount"], 390000)
        self.assertEqual(orders[4]["channel"], "고도몰")
        self.assertIn("무선키마세트", orders[4]["optionName"])

    def test_source_zip_contains_two_excel_files(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("kakao.xlsx", write_xlsx(["주문번호"], [["1"]]))
            archive.writestr("coupang.xlsx", write_xlsx(["주문번호"], [["2"]]))
        with zipfile.ZipFile(io.BytesIO(output.getvalue())) as archive:
            excel_names = [name for name in archive.namelist() if name.endswith(".xlsx")]
        self.assertEqual(len(excel_names), 2)


if __name__ == "__main__":
    unittest.main()
