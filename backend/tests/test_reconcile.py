import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from vantara.reconcile import case_bundle, format_case, reconcile, worklist
from vantara.sources import DATA, SOURCE_FILES, Sources
from vantara.tools import execute


class Exports(Sources):
    def __init__(self):
        self.data = {name: (DATA / name).read_bytes() for name in SOURCE_FILES}

    def _read(self, filename):
        if filename not in self.data:
            raise FileNotFoundError(filename)
        return self.data[filename]


class ReconcileTest(unittest.TestCase):
    def test_partial_credit_and_unallocated_pix_remain_separate(self):
        exports = Exports()
        report = reconcile(exports)
        self.assertEqual(report["counts"], {"protheus": 12, "omie": 5, "bank": 18, "inbox_lines": 65})
        invoice = next(t for t in report["titles"] if t["document"] == "NF 104560")
        self.assertEqual(invoice["status"], "credito_parcial")
        self.assertEqual(invoice["identified_credit"], "62500.00")
        self.assertEqual(invoice["difference_before_unlinked_credits"], "62500.00")
        self.assertEqual(invoice["bank_lines"], [8])
        self.assertIn(17, [candidate["line"] for candidate in invoice["candidate_bank_credits"]])
        pix = next(b for b in report["bank"] if b["line"] == 17)
        self.assertEqual(pix["status"], "credito_sem_vinculo_confirmado")
        self.assertIn({"source": "protheus", "line": 8, "reasons": ["nome_parecido"]}, pix["candidates"])
        self.assertTrue(any(c["source"] == "omie" and c["line"] == 4 and
                            "mesmo_valor" in c["reasons"] for c in pix["candidates"]))
        deposit = next(b for b in report["bank"] if b["line"] == 16)
        self.assertEqual(deposit["status"], "credito_sem_vinculo_confirmado")
        self.assertIn({"source": "omie", "line": 5, "reasons": ["mesmo_valor"]}, deposit["candidates"])

    def test_cross_system_duplicate_and_payable_reference_are_not_settlements(self):
        report = reconcile(Exports())
        omie = next(t for t in report["titles"] if t["document"] == "FAT-2026-0331")
        protheus = next(t for t in report["titles"] if t["document"] == "NF 104580")
        self.assertEqual(omie["status"], "possivel_duplicidade_entre_sistemas")
        self.assertEqual(omie["possible_duplicate_of"], [{"source": "protheus", "line": 10}])
        self.assertEqual(protheus["identified_credit"], "31250.00")
        self.assertEqual(protheus["status"], "credito_integral_no_extrato")
        debit = next(b for b in report["bank"] if b["line"] == 10)
        self.assertEqual(debit["status"], "debito_a_conferir")
        self.assertIn(34, debit["inbox_lines"])
        self.assertNotIn("linked_title", debit)

    def test_multiple_exact_credits_sum_and_bad_exports_fail_closed(self):
        exports = Exports()
        bank_file = SOURCE_FILES[2]
        exports.data[bank_file] += b"\n30/07/2026;LIQUIDACAO COBRANCA SIMPLES;000012345720;62.500,00;C\n"
        report = reconcile(exports)
        invoice = next(t for t in report["titles"] if t["document"] == "NF 104560")
        self.assertEqual(invoice["identified_credit"], "125000.00")
        self.assertEqual(invoice["status"], "credito_integral_no_extrato")
        self.assertEqual(len(invoice["bank_lines"]), 2)
        exports.data[bank_file] = exports.data[bank_file].replace(b"62.500,00", b"not-money", 1)
        with self.assertRaisesRegex(ValueError, "valor inválido"):
            reconcile(exports)
        del exports.data[SOURCE_FILES[0]]
        with self.assertRaises(FileNotFoundError):
            reconcile(exports)

    def test_agent_tools_page_all_records_and_reject_invalid_source(self):
        exports = Exports()
        report = reconcile(exports)
        page = json.loads(execute(exports, "list_reconciliation", '{"offset":0}', report))
        self.assertEqual(page["total"], len(report["titles"]) +
                         sum(b["status"] != "credito_com_referencia" for b in report["bank"]))
        last = json.loads(execute(exports, "list_reconciliation", '{"offset":20}', report))
        self.assertGreater(len(last["items"]), 0)
        record = json.loads(execute(exports, "get_record", '{"source":"bank","line":17}', report))
        self.assertIn({"source": "protheus", "line": 8, "reasons": ["nome_parecido"]}, record["candidates"])
        rejected = json.loads(execute(exports, "get_record", '{"source":"../../docs","line":2}', report))
        self.assertIn("não permitidos", rejected["error"])


    def test_named_invoice_dossier_keeps_every_lead_even_if_model_omits_it(self):
        exports = Exports()
        report = reconcile(exports)
        bundle = case_bundle(report, "O crédito de R$ 62.500 quita a NF 104560?")
        self.assertEqual([b["line"] for b in bundle["cases"][0]["linked_credits"]], [8])
        self.assertEqual([b["line"] for b in bundle["cases"][0]["candidate_credits"]], [17])
        self.assertIn(("omie", 4), [(t["source"], t["line"]) for t in bundle["cases"][0]["same_tax_id_titles"]])
        self.assertIsNone(case_bundle(report, "A NF 88231 foi paga?"))
        omie = case_bundle(report, "FAT-2026-0335")
        self.assertEqual([b["line"] for b in omie["cases"][0]["candidate_credits"]], [17])
        self.assertIn(("protheus", 8), [(t["source"], t["line"]) for t in omie["cases"][0]["same_tax_id_titles"]])
        card = format_case(bundle)
        self.assertIn("03_extrato_bancario_jul2026.csv:17", card)
        self.assertIn("02_titulos_unidade_norte_omie_jul2026.csv:4", card)
        self.assertIn("R$ 125.000,00", card)
        self.assertIn("R$ 3.400,00", card)
        self.assertIn("NÃO atribuído", card)

        from vantara.agent import run
        reply = SimpleNamespace(status="completed", output=[], output_text="Não quita.")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "smoke-only"}), patch("vantara.agent.OpenAI") as api:
            api.return_value.responses.create.return_value = reply
            answer = run("O crédito de R$ 62.500 quita a NF 104560?", exports)
            analysis = run("O crédito de R$ 62.500 quita a NF 104560?", exports, include_dossier=False)
        self.assertIn("03_extrato_bancario_jul2026.csv:17", answer)
        self.assertIn("02_titulos_unidade_norte_omie_jul2026.csv:4", answer)
        self.assertIn("Não quita.", answer)
        self.assertEqual(analysis, "Não quita.")

    def test_worklist_prioritizes_exceptions_and_keeps_safe_evidence(self):
        exports = Exports()
        rows = worklist(reconcile(exports))
        self.assertEqual(rows[0][:3], ["prioridade", "origem", "linha"])
        self.assertEqual([row[0] for row in rows[1:]], sorted(
            [row[0] for row in rows[1:]], key={"alta": 0, "media": 1, "baixa": 2}.get))
        invoice = next(row for row in rows[1:] if row[3] == "NF 104560")
        self.assertEqual(invoice[0], "alta")
        self.assertEqual(invoice[4], "125.000,00")
        self.assertIn("03_extrato_bancario_jul2026.csv:17", invoice[6])
        pix = next(row for row in rows[1:] if row[1:3] == ["bank", "17"])
        self.assertIn("02_titulos_unidade_norte_omie_jul2026.csv:4", pix[6])
        debit = next(row for row in rows[1:] if row[1:3] == ["bank", "10"])
        self.assertEqual(debit[0], "alta")
        self.assertIn("04_caixa_de_entrada_contas_a_pagar.txt:34", debit[6])
        fee = next(row for row in rows[1:] if row[1:3] == ["bank", "7"])
        self.assertEqual(fee[0], "baixa")
        self.assertIn("classificação contábil", fee[7])

        protheus = SOURCE_FILES[0]
        exports.data[protheus] = exports.data[protheus].replace(b"NF 104560", b"=1+1")
        injected = worklist(reconcile(exports))
        self.assertIn("'=1+1", [row[3] for row in injected])


if __name__ == "__main__":
    unittest.main()
