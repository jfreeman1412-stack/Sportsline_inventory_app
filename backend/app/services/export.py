from __future__ import annotations

import csv
import io
from collections.abc import Iterable

from fastapi.responses import Response


def csv_response(filename: str, header: list[str], rows: Iterable[Iterable]) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
