import base64
import os
import sqlite3
import tempfile
import unittest
from io import BytesIO
from unittest.mock import patch

from fastapi.testclient import TestClient

from vantara.api import app
from vantara.sources import DATA, SOURCE_FILES, Sources


class Pack(Sources):
    def __init__(self, files):
        self.files = files

    def _read(self, filename):
        return self.files[filename]


class UploadStore:
    def __init__(self):
        self.files = {}

    def head_bucket(self, *, Bucket):
        pass

    def put_object(self, *, Bucket, Key, Body):
        self.files[Key] = Body

    def get_object(self, *, Bucket, Key):
        return {"Body": BytesIO(self.files[Key])}


class DemoTest(unittest.TestCase):
    def test_local_writeoffs_are_idempotent_and_never_erp_writeoffs(self):
        files = {name: (DATA / name).read_bytes() for name in SOURCE_FILES}
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"VANTARA_WORKFLOW_DB": f"{directory}/workflow.sqlite3", "OPENAI_API_KEY": ""}), \
                patch("vantara.api._sources", return_value=Pack(files)):
            client = TestClient(app)
            original = client.get("/api/reconciliation").json()
            before = client.get("/api/demo/operations").json()
            self.assertEqual(set(before), {"mode", "dataset", "supported", "reason", "eligible_count",
                                           "simulated_writeoffs_performed", "processed", "pending"})
            self.assertEqual((before["mode"], before["dataset"], before["supported"], before["reason"]),
                             ("local_simulation", original["dataset"], True, None))
            self.assertEqual((before["eligible_count"], before["simulated_writeoffs_performed"], before["processed"]),
                             (2, 0, []))
            with sqlite3.connect(f"{directory}/workflow.sqlite3") as database:
                self.assertEqual(database.execute("SELECT count(*) FROM demo_writeoffs").fetchone()[0], 0)
            self.assertEqual(next(row for row in before["pending"] if row["event_line"] == 4)["status"],
                             "apta_para_simulacao")

            after = client.post("/api/demo/operations/run").json()
            self.assertEqual((after["eligible_count"], after["simulated_writeoffs_performed"]), (2, 2))
            self.assertEqual([row["event_line"] for row in after["processed"]], [4, 8])
            total, partial = after["processed"]
            self.assertEqual((total["title_source"], total["title_line"], total["document"],
                              total["amount"], total["kind"], total["balance_after"]),
                             ("protheus", 5, "NF 104540", "9870.00", "total", "0.00"))
            self.assertEqual((partial["title_source"], partial["title_line"], partial["document"],
                              partial["amount"], partial["kind"], partial["balance_after"]),
                             ("protheus", 8, "NF 104560", "62500.00", "partial", "62500.00"))
            self.assertTrue(all(row["receipt"].startswith("SIM-") and len(row["evidence"]) >= 2
                                for row in after["processed"]))
            held = {row["event_line"]: row for row in after["pending"]}
            self.assertEqual(held[11]["status"], "duplicidade_entre_erps")
            self.assertEqual(held[2]["status"], "sem_estado_erp")
            self.assertEqual(held[17]["status"], "sem_vinculo")
            self.assertIn("transação bancária", held[17]["next_query"])
            self.assertNotIn(10, held)  # D: pagamento de fornecedor nunca é baixa de recebível.
            self.assertEqual(client.post("/api/demo/operations/run").json(), after)
            self.assertEqual(client.get("/api/demo/operations").json(), after)
            with sqlite3.connect(f"{directory}/workflow.sqlite3") as database:
                self.assertEqual(database.execute("SELECT count(*) FROM demo_writeoffs").fetchone()[0], 2)
            self.assertEqual(client.get("/api/reconciliation").json()["summary"]["erp_writeoffs_performed"], 0)

            changed = dict(files)
            changed[SOURCE_FILES[-1]] += b"\n"
            store = UploadStore()
            with patch("vantara.api._sources", return_value=Sources(store)):
                uploaded = client.post("/api/exports", json={"files": {
                    name: base64.b64encode(changed[name]).decode("ascii") for name in SOURCE_FILES}})
            self.assertEqual(uploaded.status_code, 200)
            self.assertNotEqual(uploaded.json()["dataset"], original["dataset"])
            unsupported = client.post("/api/demo/operations/run").json()
            self.assertEqual((unsupported["supported"], unsupported["eligible_count"],
                              unsupported["simulated_writeoffs_performed"], unsupported["processed"],
                              unsupported["pending"]), (False, 0, 0, [], []))
            self.assertTrue(unsupported["reason"])
            self.assertEqual(client.get("/api/demo/operations").json(), unsupported)
            self.assertEqual(client.get("/api/reconciliation").json()["summary"]["erp_writeoffs_performed"], 0)
            with sqlite3.connect(f"{directory}/workflow.sqlite3") as database:
                self.assertEqual(database.execute("SELECT count(*) FROM demo_writeoffs").fetchone()[0], 2)
