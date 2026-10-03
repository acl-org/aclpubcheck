from pathlib import Path


TEXT = b"BT /F1 10 Tf 100 700 Td (The quick brown fox jumps over the lazy dog.) Tj ET"
# red text in the right margin
MARGIN_TEXT = TEXT + b"\n1 0 0 rg BT /F1 10 Tf 560 400 Td (Overflow) Tj ET"


def write_pdf(path, content):
    """Write a one-page A4 PDF whose text uses a font accepted by the font check."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /TimesNewRomanPSMT "
        b"/FirstChar 32 /LastChar 126 /Widths [" + b" ".join([b"500"] * 95) + b"] "
        b"/FontDescriptor 6 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /FontDescriptor /FontName /TimesNewRomanPSMT /Flags 34 "
        b"/FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 900 /Descent -200 "
        b"/CapHeight 700 /StemV 80 >>",
    ]
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
