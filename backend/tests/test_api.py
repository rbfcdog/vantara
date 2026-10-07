import base64
import csv
import unittest
from datetime import datetime
from io import StringIO
from unittest.mock import patch

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from vantara.api import app
from vantara.sources import BUCKET, DATA, INBOX_KEY, SOURCE_FILES, Sources


class Exports(Sources):
    def __init__(self):
        self.data = {name: (DATA / name).read_bytes() for name in SOURCE_FILES}

    def _read(self, filename):
        return self.data[filename]


class FakeS3:
    def __init__(self, missing_bucket=False):
        self.missing_bucket = missing_bucket
        self.created = []
        self.writes = []

    def head_bucket(self, *, Bucket):
        if self.missing_bucket:
            raise ClientError({"Error": {"Code": "NoSuchBucket", "Message": "Missing"}}, "HeadBucket")

    def create_bucket(self, *, Bucket):
        self.created.append(Bucket)

    def put_object(self, *, Bucket, Key, Body):
        self.writes.append((Bucket, Key, Body))


class AnalystApiTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.sources = patch("vantara.api._sources", return_value=Exports())
        self.sources.start()
        self.addCleanup(self.sources.stop)


    def export_payload(self):
        return {name: base64.b64encode((DATA / name).read_bytes()).decode("ascii") for name in SOURCE_FILES}

    def assert_timestamp(self, value):
        self.assertIsNotNone(datetime.fromisoformat(value).utcoffset())

    def test_worklist_and_download_include_unallocated_candidate(self):
        response = self.client.get("/api/worklist")
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assert_timestamp(response.json()["processed_at"])
        invoice = next(row for row in items if row["documento_ou_historico"] == "NF 104560")
        self.assertEqual(invoice["prioridade"], "alta")
        self.assertIn("03_extrato_bancario_jul2026.csv:17", invoice["evidencias"])
        self.assertEqual(set(invoice), {"prioridade", "origem", "linha", "documento_ou_historico",
                                        "valor_brl", "situacao", "evidencias", "proxima_acao"})
        download = self.client.get("/api/worklist.csv")
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment;", download.headers["content-disposition"])
        self.assertEqual(list(csv.DictReader(StringIO(download.text), delimiter=";")), items)

    def test_zero_rows_are_ready_not_missing(self):
        exports = Exports()
        for name in SOURCE_FILES:
            exports.data[name] = b"" if name == INBOX_KEY else exports.data[name].splitlines(keepends=True)[0]
        with patch("vantara.api._sources", return_value=exports):
            response = self.client.get("/api/worklist")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])
        self.assert_timestamp(response.json()["processed_at"])
        s3 = FakeS3()
        with patch("vantara.api._sources", return_value=Sources(s3)):
            upload = self.client.post("/api/exports", json={"files": {
                name: base64.b64encode(exports.data[name]).decode("ascii") for name in SOURCE_FILES
            }})
        self.assertEqual(upload.status_code, 200)
        self.assertEqual(upload.json()["items_count"], 0)
        self.assert_timestamp(upload.json()["processed_at"])

    def test_upload_validates_then_creates_missing_bucket_and_writes_exact_bytes(self):
        for missing_bucket in (False, True):
            with self.subTest(missing_bucket=missing_bucket):
                s3 = FakeS3(missing_bucket=missing_bucket)
                with patch("vantara.api._sources", return_value=Sources(s3)):
                    response = self.client.post("/api/exports", json={"files": self.export_payload()})
                self.assertEqual(response.status_code, 200)
                self.assert_timestamp(response.json()["processed_at"])
                self.assertEqual(response.json()["items_count"], len(self.client.get("/api/worklist").json()["items"]))
                self.assertEqual(s3.created, [BUCKET] if missing_bucket else [])
                self.assertEqual(s3.writes, [(BUCKET, name, (DATA / name).read_bytes()) for name in SOURCE_FILES])

    def test_bad_exports_do_not_mutate_s3(self):
        files = self.export_payload()
        bad_cases = [
            ({name: data for name, data in files.items() if name != INBOX_KEY}, "quatro arquivos"),
            ({**files, "unexpected.csv": "eA=="}, "quatro arquivos"),
            ({**files, SOURCE_FILES[0]: "not base64!"}, "base64"),
            ({**files, SOURCE_FILES[0]: base64.b64encode(b"not a csv").decode()}, "colunas"),
            ({**files, INBOX_KEY: base64.b64encode(b"\xff").decode()}, "UTF-8"),
            ({**files, SOURCE_FILES[0]: base64.b64encode(b"x" * (2 * 1024 * 1024 + 1)).decode()}, "2 MiB"),
        ]
        for invalid, detail in bad_cases:
            with self.subTest(detail=detail):
                s3 = FakeS3(missing_bucket=True)
                with patch("vantara.api._sources", return_value=Sources(s3)):
                    response = self.client.post("/api/exports", json={"files": invalid})
                self.assertEqual(response.status_code, 422)
                self.assertIn(detail, response.json()["detail"])
                self.assertEqual(s3.created, [])
                self.assertEqual(s3.writes, [])

    def test_invalid_date_and_money_do_not_write(self):
        for column, replacement, error in (("vencimento", "31/02/2026", "data inválida"),
                                           ("valor", "not money", "valor inválido")):
            with self.subTest(column=column):
                files = self.export_payload()
                name = SOURCE_FILES[0]
                rows = list(csv.DictReader(StringIO((DATA / name).read_text(encoding="utf-8-sig")), delimiter=";"))
                rows[0][column] = replacement
                output = StringIO()
                writer = csv.DictWriter(output, fieldnames=rows[0], delimiter=";")
                writer.writeheader()
                writer.writerows(rows)
                files[name] = base64.b64encode(output.getvalue().encode()).decode()
                s3 = FakeS3()
                with patch("vantara.api._sources", return_value=Sources(s3)):
                    response = self.client.post("/api/exports", json={"files": files})
                self.assertEqual(response.status_code, 422)
                self.assertIn(error, response.json()["detail"])
                self.assertEqual(s3.writes, [])

    def test_title_case_keeps_linked_and_candidate_bank_evidence(self):
        response = self.client.get("/api/case", params={"source": "protheus", "line": 8})
        self.assertEqual(response.status_code, 200)
        case = response.json()
        self.assertEqual(case["kind"], "title")
        self.assertEqual(case["title"]["document"], "NF 104560")
        self.assertEqual([credit["line"] for credit in case["linked_credits"]], [8])
        self.assertEqual([credit["line"] for credit in case["candidate_credits"]], [17])
        self.assertTrue(any(item["source"] == "omie" and item["line"] == 4 and
                            "mesmo_valor" in item["reasons"]
                            for item in case["candidate_credits"][0]["candidates"]))
        self.assertIn(("omie", 4), [(item["source"], item["line"]) for item in case["related_titles"]])

    def test_bank_case_exposes_full_candidate_title_and_reasons(self):
        response = self.client.get("/api/case", params={"source": "bank", "line": 17})
        self.assertEqual(response.status_code, 200)
        case = response.json()
        self.assertEqual(case["kind"], "bank")
        self.assertEqual(case["bank"]["status"], "credito_sem_vinculo_confirmado")
        self.assertTrue(any(item["title"]["document"] == "NF 104560" and
                            item["reasons"] == ["nome_parecido"] for item in case["candidates"]))

    def test_missing_source_and_nonexistent_row_report_actionable_errors(self):
        self.assertEqual(self.client.get("/api/case", params={"line": 8}).status_code, 422)
        response = self.client.get("/api/case", params={"source": "protheus", "line": 900})
        self.assertEqual(response.status_code, 404)
        self.assertIn("linha 900", response.json()["detail"])
        self.assertEqual(self.client.get("/api/case", params={"source": "bank", "line": 1}).status_code, 422)

    def test_missing_export_is_actionable(self):
        with patch("vantara.api._sources", side_effect=FileNotFoundError("Protheus ausente no Floci; execute `seed` primeiro.")):
            response = self.client.get("/api/worklist")
        self.assertEqual(response.status_code, 503)
        self.assertIn("seed", response.json()["detail"])

    def test_ask_passes_validated_history_and_rejects_unpaired_messages(self):
        history = [
            {"role": "user", "content": "O que falta conferir na NF 104560?"},
            {"role": "assistant", "content": "Conferir baixa no Protheus e origem do PIX."},
        ]
        with patch("vantara.api.run", return_value="PIX sem atribuição confirmada.") as run:
            response = self.client.post("/api/ask", json={"question": "E esse PIX?", "history": history})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "PIX sem atribuição confirmada.")
        self.assertEqual(run.call_args.kwargs["history"], history)
        for invalid in (history[:1], list(reversed(history))):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.client.post("/api/ask", json={"question": "E esse PIX?", "history": invalid}).status_code, 422)




if __name__ == "__main__":
    unittest.main()
