import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_reconcile import Exports

from vantara.reconcile import reconcile
from vantara.suggest import unlinked_suggestions


def _model_response(items):
    return SimpleNamespace(status="completed", output_text=json.dumps({"items": items}, ensure_ascii=False))


def _item(line, **overrides):
    item = {"bank_line": line, "verdict": "sem_atribuicao", "candidate": None,
            "rationale": "Sem referência única; pistas não diferenciam os candidatos.",
            "evidence": [f"03_extrato_bancario_jul2026.csv:{line}"],
            "confirm_with": "Comprovante/ID da transação e consulta ao ERP atual."}
    item.update(overrides)
    return item


class UnlinkedSuggestionsTest(unittest.TestCase):
    def setUp(self):
        self.report = reconcile(Exports())
        self.lines = [entry["line"] for entry in self.report["bank"]
                      if entry["type"] == "C" and entry["status"] == "credito_sem_vinculo_confirmado"]
        self.assertEqual(len(self.lines), 12)
        self.all_lines = [entry["line"] for entry in self.report["bank"] if entry["type"] == "C"]
        self.exact = {entry["line"]: f"{entry['linked_title']['source']}:{entry['linked_title']['line']}"
                      for entry in self.report["bank"] if entry["status"] == "credito_com_referencia"}

    def _responses(self, overrides=None):
        items = [_item(line, verdict="candidato_mais_provavel", candidate=self.exact[line])
                 if line in self.exact else _item(line) for line in self.all_lines]
        for item in items:
            item.update((overrides or {}).get(item["bank_line"], {}))
        return [_model_response(items[start:start + 10]) for start in range(0, len(items), 10)]

    def test_rules_only_suggestions_without_api_key(self):
        result = unlinked_suggestions(self.report)
        self.assertEqual(result["method"], "regras_sobre_exports")
        self.assertEqual(result["status"], "not_configured")
        self.assertEqual([item["bank"]["line"] for item in result["suggestions"]], self.all_lines)
        for item in result["suggestions"]:
            self.assertIn("next_action", item)
            self.assertIsInstance(item["candidates"], list)
            self.assertNotIn("verdict", item)

    def test_model_suggestion_accepts_candidate_from_deterministic_list(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}), \
             patch("vantara.suggest.OpenAI") as client:
            client.return_value.responses.create.side_effect = self._responses(
                {3: {"verdict": "candidato_mais_provavel", "candidate": "protheus:3",
                     "evidence": ["03_extrato_bancario_jul2026.csv:3", "protheus:3"]}})
            result = unlinked_suggestions(self.report)
        self.assertEqual(client.return_value.responses.create.call_count, 7)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["suggestions"]), 67)
        self.assertEqual(next(item for item in result["suggestions"] if item["bank"]["line"] == 3)["candidate"],
                         "protheus:3")
        self.assertEqual(next(item for item in result["suggestions"] if item["bank"]["line"] == 2)["candidate"],
                         self.exact[2])

    def test_model_suggestion_rejects_candidate_outside_deterministic_list(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}), \
             patch("vantara.suggest.OpenAI") as client:
            client.return_value.responses.create.side_effect = self._responses(
                {3: {"verdict": "candidato_mais_provavel", "candidate": "omie:2"}})
            result = unlinked_suggestions(self.report)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(all("verdict" not in item for item in result["suggestions"]))

    def test_model_cannot_override_exact_reference(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}), \
             patch("vantara.suggest.OpenAI") as client:
            client.return_value.responses.create.side_effect = self._responses(
                {2: {"verdict": "sem_atribuicao", "candidate": None}})
            result = unlinked_suggestions(self.report)
        self.assertEqual(result["status"], "failed")

    def test_model_suggestion_rejects_fabricated_evidence_line(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}), \
             patch("vantara.suggest.OpenAI") as client:
            client.return_value.responses.create.side_effect = self._responses(
                {3: {"evidence": ["03_extrato_bancario_jul2026.csv:99"]}})
            result = unlinked_suggestions(self.report)
        self.assertEqual(result["status"], "failed")

    def test_model_failure_keeps_rules_view(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}), \
             patch("vantara.suggest.OpenAI") as client:
            client.return_value.responses.create.return_value = SimpleNamespace(status="incomplete", output_text="")
            result = unlinked_suggestions(self.report)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["suggestions"]), 67)

    def test_no_bank_credits_reports_no_cases(self):
        empty = {"titles": [], "bank": []}
        self.assertEqual(unlinked_suggestions(empty),
                         {"method": "regras_sobre_exports", "status": "no_cases", "suggestions": []})


if __name__ == "__main__":
    unittest.main()
