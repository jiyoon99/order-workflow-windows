from __future__ import annotations

import io
import zipfile
from email.parser import BytesParser
from email.policy import default

from config import MAX_ARCHIVE_FILES, MAX_ARCHIVE_UNCOMPRESSED_BYTES, MAX_UPLOAD_BYTES, MAX_UPLOAD_FILES
from importers import import_workbook


def archive_excel_files(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    files = [
        info for info in archive.infolist()
        if not info.is_dir() and info.filename.lower().endswith((".xlsx", ".xls"))
    ]
    if len(files) > MAX_ARCHIVE_FILES:
        raise ValueError(f"ZIP 안의 엑셀 파일은 최대 {MAX_ARCHIVE_FILES}개까지 지원합니다.")
    total_size = sum(info.file_size for info in files)
    if total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        raise ValueError("ZIP 압축 해제 크기가 너무 큽니다.")
    return files


def imported_orders_from_multipart(content_type: str, body: bytes) -> tuple[list[dict], list[str], int]:
    if len(body) > MAX_UPLOAD_BYTES:
        raise ValueError("업로드 파일이 너무 큽니다.")
    header_blob = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    message = BytesParser(policy=default).parsebytes(header_blob)
    imported: list[dict] = []
    errors: list[str] = []
    file_count = 0
    parts = list(message.iter_parts())
    password = ""
    for part in parts:
        if part.get_filename() or part.get_param("name", header="content-disposition") != "excelPassword":
            continue
        password = (part.get_content() or "").strip()
        break
    for part in parts:
        filename = part.get_filename()
        if not filename:
            continue
        file_count += 1
        if file_count > MAX_UPLOAD_FILES:
            raise ValueError(f"한 번에 최대 {MAX_UPLOAD_FILES}개 파일까지 가져올 수 있습니다.")
        content = part.get_payload(decode=True) or b""
        lower_name = filename.lower()
        try:
            if lower_name.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    for info in archive_excel_files(archive):
                        try:
                            imported.extend(import_workbook(archive.read(info), info.filename, password))
                        except Exception as error:
                            errors.append(f"{info.filename}: {error}")
            elif lower_name.endswith((".xlsx", ".xls")):
                imported.extend(import_workbook(content, filename, password))
            else:
                errors.append(f"{filename}: 엑셀 또는 ZIP 파일만 지원합니다.")
        except zipfile.BadZipFile:
            errors.append(f"{filename}: ZIP 파일을 열 수 없습니다.")
        except Exception as error:
            errors.append(f"{filename}: {error}")
    if not file_count:
        raise ValueError("업로드된 파일이 없습니다.")
    return imported, errors, file_count
