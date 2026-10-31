# -*- coding: utf-8 -*-
"""生成“有文本层”的最小 PDF，供补充资料附件测试使用（不依赖任何 PDF 库）。"""
from __future__ import annotations

from pathlib import Path


def _esc(line: str) -> str:
    return (line.replace("\\", r"\\")
                .replace("(", r"\(")
                .replace(")", r"\)"))


def write_text_pdf(path, text: str) -> Path:
    """写一个单页 PDF：正文以 (…)Tj 字面串出现，core/pdftext 能抠出来。"""
    stream = ("BT /F1 11 Tf 50 780 Td 14 TL\n"
              + "\n".join("(%s) Tj T*" % _esc(line) for line in text.splitlines())
              + "\nET").encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    for i, obj in enumerate(objs, 1):
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    out += b"trailer\n<< /Root 1 0 R /Size 6 >>\n%%EOF\n"
    path = Path(path)
    path.write_bytes(bytes(out))
    return path


PATHOLOGY = (
    "Pathology report: the specimen shows moderately differentiated HCC, "
    "MVI negative, capsule intact. Background liver shows cirrhosis, "
    "no satellite nodules. The resection margin is free of tumour and the "
    "patient has no lymph node metastasis. Recurrence risk is estimated "
    "from the resection specimen; patients with this profile are usually "
    "eligible for the 12-month recurrence model."
)
