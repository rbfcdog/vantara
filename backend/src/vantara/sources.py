import csv
from io import StringIO
from pathlib import Path

from botocore.exceptions import ClientError


DATA = Path(__file__).resolve().parents[3] / "data_pack"
BUCKET = "vantara-case-raw"
CSV_SOURCES = {
    "protheus": ("01_contas_a_receber_protheus_jul2026.csv", ";"),
    "omie": ("02_titulos_unidade_norte_omie_jul2026.csv", ","),
    "bank": ("03_extrato_bancario_jul2026.csv", ";"),
}
INBOX_KEY = "04_caixa_de_entrada_contas_a_pagar.txt"
SOURCE_FILES = tuple(filename for filename, _ in CSV_SOURCES.values()) + (INBOX_KEY,)


def _query(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 120:
        raise ValueError("query deve ter de 1 a 120 caracteres")
    return value.strip().casefold()


def _matches(text: str, query: str) -> bool:
    if query.isdecimal() and len(query) >= 8:
        return query in "".join(char for char in text if char.isdecimal())
    return query in text.casefold()


def search_csv_bytes(data: bytes, filename: str, delimiter: str, query: str) -> dict:
    query = _query(query)
    rows = []
    total = 0
    reader = csv.DictReader(StringIO(data.decode("utf-8-sig"), newline=""), delimiter=delimiter)
    for row in reader:
        if any(_matches(value or "", query) for value in row.values()):
            total += 1
            if len(rows) < 12:
                rows.append({"line": reader.line_num, "fields": row})
    return {"file": filename, "matches": total, "rows": rows, "truncated": total > len(rows)}


def search_text_bytes(data: bytes, query: str) -> dict:
    query = _query(query)
    lines = data.decode("utf-8").splitlines()
    hits = [i for i, line in enumerate(lines) if _matches(line, query)]
    included = set()
    for index in hits[:6]:
        included.update(range(max(0, index - 3), min(len(lines), index + 4)))
    return {"file": INBOX_KEY, "matches": len(hits),
            "lines": [{"line": i + 1, "text": lines[i]} for i in sorted(included)],
            "truncated": len(hits) > 6}


class Sources:
    def __init__(self, s3):
        self.s3 = s3

    def _read(self, filename: str) -> bytes:
        try:
            return self.s3.get_object(Bucket=BUCKET, Key=filename)["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchBucket", "NoSuchKey", "404"}:
                raise FileNotFoundError(f"{filename} ausente no Floci; execute `seed` primeiro.") from exc
            raise

    def list_sources(self) -> dict:
        sources = {}
        for alias, (filename, delimiter) in CSV_SOURCES.items():
            data = self._read(filename)
            columns = next(csv.reader(StringIO(data.decode("utf-8-sig")), delimiter=delimiter))
            sources[alias] = {"file": filename, "columns": columns, "bytes": len(data)}
        sources["inbox"] = {"file": INBOX_KEY, "type": "text", "bytes": len(self._read(INBOX_KEY))}
        return sources

    def search_csv(self, source: str, query: str) -> dict:
        if source not in CSV_SOURCES:
            raise ValueError("source fora da lista permitida")
        filename, delimiter = CSV_SOURCES[source]
        return search_csv_bytes(self._read(filename), filename, delimiter, query)

    def search_text(self, query: str) -> dict:
        return search_text_bytes(self._read(INBOX_KEY), query)
