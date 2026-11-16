# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

def preview_pdf(page_count=3, *, outline=False):
    pdf = b"%PDF-1.4\n"
    offsets = [0]
    kids = ' '.join(f'{index + 3} 0 R' for index in range(page_count))
    objects = [
        f"<< /Type /Catalog /Pages 2 0 R{' /Outlines ' + str(page_count + 3) + ' 0 R' if outline else ''} >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>",
        *["<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>"] * page_count,
    ]
    if outline:
        objects.extend([
            f"<< /Type /Outlines /First {page_count + 4} 0 R /Last {page_count + 4} 0 R /Count 1 >>",
            f"<< /Title (Fixture section) /Parent {page_count + 3} 0 R /Dest [3 0 R /Fit] >>",
        ])
    for index, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += f"{index} 0 obj\n{obj}\nendobj\n".encode()
    xref = len(pdf)
    size = len(objects) + 1
    pdf += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    pdf += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    pdf += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return pdf
