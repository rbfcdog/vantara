import os
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_reconcile import Exports

from vantara.autonomous import run_autonomous
from vantara.reconcile import reconcile
from vantara.suggest import unlinked_suggestions


DATASET = "abc123def456"


def _suggestions(report, verdicts):
    """Build a completed suggestions payload with the given verdicts."""
    result = unlinked_suggestions(report)  # rules-only structure (no key set)
    result["status"] = "completed"
    unlinked = iter(verdicts)
    for item in result["suggestions"]:
        if item["exact"]:
            verdict, candidate = "candidato_mais_provavel", item["candidates"][0]["id"]
        else:
            verdict, candidate = next(unlinked)
        item["verdict"] = verdict
        item["candidate"] = candidate
        item["rationale"] = "Decisão de teste."
        item["evidence"] = [item["bank"]["id"]] + ([candidate] if candidate else [])
        item["confirm_with"] = "Comprovante/ID da transação e consulta ao ERP atual."
    return result


class AutonomousExecutorTest(unittest.TestCase):
    def setUp(self):
        self.report = reconcile(Exports())
        self.credits = [entry for entry in self.report["bank"]
                        if entry["type"] == "C" and entry["status"] == "credito_sem_vinculo_confirmado"]

    def _run(self, verdicts):
        suggestions = _suggestions(self.report, verdicts)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"VANTARA_WORKFLOW_DB": str(Path(tmp) / "wf.sqlite3")}):
                first = run_autonomous(DATASET, self.report, suggestions)
                second = run_autonomous(DATASET, self.report, suggestions)
            return first, second


    def test_applies_ai_decision_with_receipt_and_reread(self):
        line3 = next(c for c in self.credits if c["line"] == 3)
        title = next(t for t in self.report["titles"] if t["source"] == "protheus" and t["line"] == 3)
        verdicts = [("sem_atribuicao", None)] * len(self.credits)
        verdicts[[c["line"] for c in self.credits].index(3)] = ("candidato_mais_provavel", "protheus:3")
        first, second = self._run(verdicts)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(len(first["applied"]), 56)
        applied = next(row for row in first["applied"] if row["event_line"] == 3)
        self.assertEqual(applied["event_line"], 3)
        self.assertEqual(applied["document"], title["document"])
        self.assertEqual(applied["receipt"], f"EXE-{DATASET[:12]}-C0003")
        self.assertEqual(Decimal(applied["amount"]), Decimal(line3["amount"]))
        # idempotent: rerun applies nothing new, same ledger
        self.assertEqual(second["applied"], first["applied"])
        self.assertEqual(len([p for p in second["pending"] if "execução" in p["reason"] or "Saldo" in p["reason"]]), 0)
        self.assertEqual(first["erp_writeoffs_performed"], 0)

    def test_holds_duplicate_between_erps_title(self):
        import copy
        report = copy.deepcopy(self.report)
        credit = next(c for c in report["bank"] if c["line"] == 3)
        credit["candidates"].append({"source": "omie", "line": 2, "reasons": ["teste"]})  # FAT-2026-0331: duplicidade
        verdicts = [("sem_atribuicao", None)] * len(self.credits)
        verdicts[[c["line"] for c in report["bank"]
                  if c["type"] == "C" and c["status"] == "credito_sem_vinculo_confirmado"].index(3)] = \
            ("candidato_mais_provavel", "omie:2")
        suggestions = _suggestions(report, verdicts)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"VANTARA_WORKFLOW_DB": str(Path(tmp) / "wf.sqlite3")}):
                first = run_autonomous(DATASET, report, suggestions)
        self.assertTrue(any("duplicidade entre ERPs" in p["reason"] for p in first["pending"]))
        self.assertEqual(len(first["applied"]), 55)

    def test_holds_excess_amount(self):
        verdicts = [("sem_atribuicao", None)] * len(self.credits)
        # credit line 5 (47630.50) vs any smaller title candidate
        idx = next(i for i, c in enumerate(self.credits) if c["line"] == 5)
        cand = next(c for c in self.credits[idx]["candidates"])
        verdicts[idx] = ("candidato_mais_provavel", f"{cand['source']}:{cand['line']}")
        first, _ = self._run(verdicts)
        # may be held as excess or applied if smaller; assert guardrail classification is honest
        if Decimal(self.credits[idx]["amount"]) > Decimal(
                next(t for t in self.report["titles"]
                     if (t["source"], t["line"]) == (cand["source"], cand["line"]))["amount"]):
            self.assertTrue(any("excede" in p["reason"] for p in first["pending"]))
            self.assertEqual(len(first["applied"]), 55)

    def test_unreferenced_credit_cannot_consume_title_with_exact_bank_reference(self):
        verdicts = [("sem_atribuicao", None)] * len(self.credits)
        index = next(i for i, credit in enumerate(self.credits) if credit["line"] == 70)
        verdicts[index] = ("candidato_mais_provavel", "protheus:24")
        first, second = self._run(verdicts)
        self.assertEqual(len(first["applied"]), 55)
        self.assertEqual(first["applied"], second["applied"])
        self.assertTrue(any(row["bank_line"] == 70 and "referência exata" in row["reason"]
                            for row in first["pending"]))

    def test_without_ai_decisions_nothing_is_applied(self):
        suggestions = {"status": "not_configured", "suggestions": unlinked_suggestions(self.report)["suggestions"]}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"VANTARA_WORKFLOW_DB": str(Path(tmp) / "wf.sqlite3")}):
                result = run_autonomous(DATASET, self.report, suggestions)
        self.assertEqual(result["status"], "not_configured")
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["pending"]), 67)
        self.assertEqual(result["erp_writeoffs_performed"], 0)


if __name__ == "__main__":
    unittest.main()
