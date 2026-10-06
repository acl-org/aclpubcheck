from pathlib import Path

TEXT = b"BT /F1 10 Tf 100 700 Td (The quick brown fox jumps over the lazy dog.) Tj ET"
# red text in the right margin
MARGIN_TEXT = TEXT + b"\n1 0 0 rg BT /F1 10 Tf 560 400 Td (Overflow) Tj ET"
REFERENCES = b"BT /F1 10 Tf 100 700 Td (References) Tj ET"


def write_pdf(path: str | Path, content: bytes) -> None:
    """Write a one-page A4 PDF whose text uses a font accepted by the font check."""
    write_pages(path, [content])


def write_pages(path: str | Path, pages: list[bytes]) -> None:
    """Write an A4 PDF with one page per content stream, all sharing one accepted font."""
    count = len(pages)
    font, descriptor = 3, 4
    page_ids = [5 + 2 * i for i in range(count)]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + b" ".join(b"%d 0 R" % i for i in page_ids)
        + b"] /Count %d >>" % count,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /TimesNewRomanPSMT "
        b"/FirstChar 32 /LastChar 126 /Widths [" + b" ".join([b"500"] * 95) + b"] "
        b"/FontDescriptor %d 0 R >>" % descriptor,
        (
            b"<< /Type /FontDescriptor /FontName /TimesNewRomanPSMT /Flags 34 "
            b"/FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 900 /Descent -200 "
            b"/CapHeight 700 /StemV 80 >>"
        ),
    ]
    for page_id, content in zip(page_ids, pages):
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>" % (font, page_id + 1)
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    Path(path).write_bytes(pdf)
