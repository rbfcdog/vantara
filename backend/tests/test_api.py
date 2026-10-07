import base64
import csv
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from io import StringIO
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from vantara.api import app
from vantara.agent import draft_request
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
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database = patch.dict(os.environ, {"VANTARA_WORKFLOW_DB": f"{temporary.name}/workflow.sqlite3"})
        database.start()
        self.addCleanup(database.stop)
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

    def test_workflow_baseline_and_history_survive_new_clients(self):
        initial = self.client.get("/api/workflow")
        self.assertEqual(initial.status_code, 200)
        body = initial.json()
        self.assertEqual(len(body["dataset"]), 64)
        self.assertEqual(set(body["cases"]),
                         {f"{item['origem']}:{item['linha']}" for item in self.client.get("/api/worklist").json()["items"]})
        self.assertEqual(len(body["cases"]), 30)
        baseline = body["cases"]["protheus:8"]
        self.assertEqual({key: baseline[key] for key in ("source", "line", "status", "owner", "note", "version")},
                         {"source": "protheus", "line": 8, "status": "open", "owner": "", "note": "", "version": 0})
        self.assert_timestamp(baseline["opened_at"])
        self.assert_timestamp(baseline["updated_at"])
        self.assertEqual(baseline["history"], [{"status": "open", "owner": "", "note": "",
                                                "at": baseline["opened_at"]}])
        steps = [("open", "Ana", "", 0), ("inconclusive", "Ana", "Falta comprovante", 1),
                 ("closed", "Ana", "Referência conferida manualmente", 2),
                 ("open", "Bruno", "Revisão solicitada", 3)]
        for status, owner, note, version in steps:
            result = TestClient(app).patch("/api/workflow", params={"source": "protheus", "line": 8},
                                           json={"status": status, "owner": owner, "note": note, "version": version})
            self.assertEqual(result.status_code, 200, result.text)
            current = result.json()
            self.assertEqual((current["status"], current["owner"], current["note"], current["version"]),
                             (status, owner, note, version + 1))
            self.assertEqual(current["opened_at"], baseline["opened_at"])
            self.assert_timestamp(current["updated_at"])
            self.assertEqual(current["history"][-1], {"status": status, "owner": owner, "note": note,
                                                       "at": current["updated_at"]})
        stored = TestClient(app).get("/api/workflow").json()["cases"]["protheus:8"]
        self.assertEqual(stored, current)
        self.assertEqual(len(stored["history"]), 5)

    def test_workflow_rejects_conflicts_invalid_decisions_and_noncurrent_cases(self):
        params = {"source": "bank", "line": 17}
        update = {"status": "closed", "owner": "Equipe AR", "note": "Conferido por humano", "version": 0}
        self.assertEqual(self.client.patch("/api/workflow", params=params,
                                           json={**update, "note": "  "}).status_code, 422)
        self.assertEqual(self.client.patch("/api/workflow", params=params,
                                           json={**update, "status": "inconclusive", "note": ""}).status_code, 422)
        bad_status = self.client.patch("/api/workflow", params=params,
                                       json={**update, "status": "paid"})
        self.assertEqual(bad_status.status_code, 422)
        self.assertIsInstance(bad_status.json()["detail"], str)
        self.assertEqual(self.client.patch("/api/workflow", params=params, json=update).status_code, 200)
        stale = TestClient(app).patch("/api/workflow", params=params, json=update)
        self.assertEqual(stale.status_code, 409)
        self.assertIsInstance(stale.json()["detail"], str)
        invalid = self.client.patch("/api/workflow", params=params,
                                    json={**update, "status": "inconclusive", "version": 1})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.client.patch("/api/workflow", params=params,
                                           json={**update, "version": 1}).status_code, 422)
        self.assertEqual(self.client.patch("/api/workflow", params=params,
                                           json={**update, "status": "open", "note": "", "version": 1}).status_code, 422)
        self.assertEqual(self.client.get("/api/workflow").json()["cases"]["bank:17"]["version"], 1)
        missing = self.client.patch("/api/workflow", params={"source": "bank", "line": 8}, json=update)
        self.assertEqual(missing.status_code, 404)
        self.assertIsInstance(missing.json()["detail"], str)

    def test_workflow_dataset_isolated_by_each_export_byte_and_current_worklist(self):
        before = self.client.get("/api/workflow").json()
        self.client.patch("/api/workflow", params={"source": "protheus", "line": 8},
                          json={"status": "closed", "owner": "Ana", "note": "Verificado", "version": 0})
        for name in SOURCE_FILES:
            changed = Exports()
            changed.data[name] += b"\n"
            with patch("vantara.api._sources", return_value=changed):
                isolated = TestClient(app).get("/api/workflow").json()
                self.assertNotEqual(before["dataset"], isolated["dataset"])
                self.assertEqual(isolated["cases"]["protheus:8"]["version"], 0)
                self.assertEqual(isolated["cases"]["protheus:8"]["status"], "open")
                self.assertEqual(isolated["cases"]["protheus:8"]["opened_at"], isolated["cases"]["protheus:8"]["history"][0]["at"])
        restored = TestClient(app).get("/api/workflow").json()
        self.assertEqual(before["dataset"], restored["dataset"])
        self.assertEqual(restored["cases"]["protheus:8"]["version"], 1)
        empty = Exports()
        for name in SOURCE_FILES:
            empty.data[name] = b"" if name == INBOX_KEY else empty.data[name].splitlines(keepends=True)[0]
        with patch("vantara.api._sources", return_value=empty):
            self.assertEqual(TestClient(app).get("/api/workflow").json()["cases"], {})
            self.assertEqual(TestClient(app).patch("/api/workflow", params={"source": "protheus", "line": 8},
                                                  json={"status": "open", "owner": "", "note": "", "version": 0}).status_code, 404)

    def test_workflow_storage_failure_is_not_an_empty_queue(self):
        with patch("vantara.api.list_cases", side_effect=sqlite3.OperationalError("disk unavailable")):
            response = self.client.get("/api/workflow")
        self.assertEqual(response.status_code, 503)
        self.assertIsInstance(response.json()["detail"], str)
        self.assertNotIn("cases", response.json())

    def test_draft_only_eligible_and_no_send(self):
        with patch("vantara.api.draft_request", return_value="Por favor, envie a referência do crédito.") as draft:
            bank = self.client.post("/api/workflow/draft", json={"source": "bank", "line": 17})
            self.assertEqual(bank.status_code, 200)
            self.assertEqual(bank.json(), {"draft": "Por favor, envie a referência do crédito."})
            self.assertEqual(draft.call_args.args[0],
                             {"credit": {"date": "2026-07-29", "amount_brl": "3400.00"}})
            title = self.client.post("/api/workflow/draft", json={"source": "protheus", "line": 8})
            self.assertEqual(title.status_code, 200)
            self.assertEqual(draft.call_args.args[0],
                             {"credits": [{"date": "2026-07-29", "amount_brl": "3400.00"}]})
            self.assertEqual(self.client.post("/api/workflow/draft", json={"source": "bank", "line": 8}).status_code, 404)
            self.assertEqual(self.client.post("/api/workflow/draft", json={"source": "protheus", "line": 900}).status_code, 404)
            self.assertEqual(self.client.post("/api/workflow/draft", json={"source": "bank", "line": 7}).status_code, 422)
            self.assertEqual(draft.call_count, 2)
        with patch("vantara.api.draft_request", side_effect=RuntimeError("Modelo não retornou um rascunho completo.")):
            failure = self.client.post("/api/workflow/draft", json={"source": "bank", "line": 17})
        self.assertEqual(failure.status_code, 503)
        self.assertIsInstance(failure.json()["detail"], str)

    def test_draft_model_receives_only_unsent_evidence_and_rejects_incomplete_output(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), patch("vantara.agent.OpenAI") as client:
            client.return_value.responses.create.return_value = Mock(status="completed", output_text="Envie a referência, por favor.")
            self.assertEqual(draft_request({"bank": {"line": 17}}), "Envie a referência, por favor.")
            request = client.return_value.responses.create.call_args.kwargs
            self.assertEqual(request["input"], '{"bank": {"line": 17}}')
            self.assertFalse(request["store"])
            self.assertEqual(request["tools"], [])
            self.assertIn("NÃO ENVIADO", request["instructions"])
            self.assertIn("Não afirme pagamento", request["instructions"])
            client.return_value.responses.create.return_value = Mock(status="incomplete", output_text="Texto parcial")
            with self.assertRaises(RuntimeError):
                draft_request({"bank": {"line": 17}})

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
