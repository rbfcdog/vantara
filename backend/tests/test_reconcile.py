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

    def test_credit_without_title_does_not_create_invoice_from_bank_statement(self):
        exports = Exports()
        exports.data[SOURCE_FILES[2]] += b"31/07/2026;PIX SEM REFERENCIA;;13,37;C\n"
        report = reconcile(exports)
        credit = report["bank"][-1]
        self.assertEqual(credit["status"], "credito_sem_vinculo_confirmado")
        self.assertEqual(credit["candidates"], [])
        self.assertEqual(len(report["titles"]), 17)
        self.assertNotIn("linked_title", credit)
        self.assertIn("buscar título/nota no faturamento e nos ERPs atuais", credit["next_action"])
        self.assertIn("Não criar NF a partir do extrato", credit["next_action"])
        self.assertEqual(next(case["next_query"] for case in report["investigations"]["cases"]
                              if (case["source"], case["line"]) == ("bank", credit["line"])),
                         credit["next_action"])
        self.assertEqual(report["summary"]["erp_writeoffs_performed"], 0)

    def test_automatic_trace_covers_every_credit_exception_and_cross_system_duplicate(self):
        exports = Exports()
        with patch.object(exports, "_read", wraps=exports._read) as reads:
            report = reconcile(exports)
        trace = report["investigations"]
        self.assertEqual(trace["method"], "regras_sobre_exports")
        self.assertEqual(trace["consulted"], [call.args[0] for call in reads.call_args_list])
        self.assertEqual(trace["consulted"], list(SOURCE_FILES))
        self.assertEqual(trace["gaps"], report["summary"]["coverage"]["missing"])
        cases = {(case["source"], case["line"]): case for case in trace["cases"]}
        self.assertEqual(len(cases), len(trace["cases"]))
        credits = {("bank", entry["line"]) for entry in report["bank"] if entry["type"] == "C"}
        title_cases = {(title["source"], title["line"]) for title in report["titles"]
                       if title["status"] != "credito_integral_no_extrato"}
        self.assertTrue(credits <= cases.keys())
        self.assertTrue(title_cases <= cases.keys())
        linked = cases["bank", 8]
        self.assertEqual(linked["status"], "credito_com_referencia")
        self.assertEqual(linked["evidence"], [f"{SOURCE_FILES[2]}:8", f"{SOURCE_FILES[0]}:8"])
        self.assertIn("NF 104560", linked["finding"])
        self.assertIn("Crédito R$ 62.500,00", linked["finding"])
        self.assertIn("não comprova baixa no ERP", linked["finding"])
        self.assertIn("histórico de baixas", linked["next_query"])
        pix = cases["bank", 17]
        self.assertIn("Crédito R$ 3.400,00", pix["finding"])
        self.assertIn("origem desconhecida", pix["finding"])
        self.assertIn("NF 104560", pix["finding"])
        self.assertIn("nome_parecido", pix["finding"])
        self.assertIn("FAT-2026-0335", pix["finding"])
        self.assertIn("mesmo_valor", pix["finding"])
        self.assertIn(f"{SOURCE_FILES[1]}:4", pix["evidence"])
        self.assertNotIn("linked_title", next(entry for entry in report["bank"] if entry["line"] == 17))
        unknown = cases["bank", 16]
        self.assertIn("origem desconhecida", unknown["finding"])
        self.assertIn("FAT-2026-0340", unknown["finding"])
        invoice = cases["protheus", 8]
        self.assertIn(f"{SOURCE_FILES[2]}:8", invoice["evidence"])
        self.assertIn(f"{SOURCE_FILES[2]}:17", invoice["evidence"])
        self.assertIn("não atribuído", invoice["finding"])
        duplicate = cases["protheus", 10]
        self.assertEqual(duplicate["status"], "credito_integral_no_extrato")
        self.assertIn(f"{SOURCE_FILES[1]}:2", duplicate["evidence"])
        self.assertIn("FAT-2026-0331", duplicate["finding"])
        payable = cases["bank", 10]
        self.assertIn(f"{SOURCE_FILES[3]}:34", payable["evidence"])
        self.assertIn("NF 88231", payable["finding"])
        self.assertIn(f"{SOURCE_FILES[3]}:36", payable["evidence"])
        self.assertIn(f"{SOURCE_FILES[3]}:39", payable["evidence"])
        self.assertIn(f"{SOURCE_FILES[3]}:64", payable["evidence"])
        self.assertIn("Valor atualizado R$ 19.278,00", payable["finding"])
        self.assertIn("entregue parcialmente", payable["finding"])
        self.assertIn("pagou dia 20", payable["finding"])
        self.assertIn("alegações, não comprovantes", payable["finding"])
        self.assertIn("favorecido", payable["finding"])
        self.assertNotIn(("bank", 7), cases)
        self.assertEqual(report["summary"]["erp_writeoffs_performed"], 0)
        self.assertEqual(report["summary"]["supplier_payments_initiated"], 0)


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

    def test_exact_omie_reference_is_identified_but_shared_reference_and_repeated_debit_are_not_settled(self):
        exports = Exports()
        exports.data[SOURCE_FILES[0]] += (
            b"Loja Teste;12.345.678/0001-90;SP;NF 99001;FAT-2026-0332;Produto;"
            b"10/07/2026;28/07/2026;12.980,00;Aberto\n"
        )
        exports.data[SOURCE_FILES[2]] += (
            b"30/07/2026;COBRANCA OMIE;FAT-2026-0341;8.120,00;C\n"
            b"30/07/2026;COBRANCA AMBIGUA;FAT-2026-0332;12.980,00;C\n"
            b"30/07/2026;PAGAMENTO FORNECEDOR NF 99111;;900,00;D\n"
            b"31/07/2026;PAGAMENTO FORNECEDOR NF 99111;;900,00;D\n"
        )
        report = reconcile(exports)
        omie = next(t for t in report["titles"] if t["reference"] == "FAT-2026-0341")
        self.assertEqual(omie["status"], "credito_integral_no_extrato")
        self.assertEqual(omie["identified_credit"], "8120.00")
        self.assertEqual(report["bank"][-4]["linked_title"], {"source": "omie", "line": omie["line"]})
        ambiguous = report["bank"][-3]
        self.assertEqual(ambiguous["status"], "credito_sem_vinculo_confirmado")
        self.assertEqual({item["source"] for item in ambiguous["candidates"]
                          if "referencia_ambigua" in item["reasons"]}, {"protheus", "omie"})
        self.assertEqual(report["summary"]["receivables"]["unlinked_credits"],
                         sum(row["type"] == "C" and row["status"] != "credito_com_referencia"
                             for row in report["bank"]))
        self.assertEqual(report["summary"]["payables"]["possible_repeated_invoice_debits"],
                         [{"invoice": "NF 99111", "bank_lines": [22, 23], "inbox_lines": []}])
        self.assertEqual(report["summary"]["coverage"]["bank_accounts_supplied"], 1)
        self.assertEqual(report["summary"]["erp_writeoffs_performed"], 0)
        self.assertEqual(json.loads(execute(exports, "get_reconciliation_summary", "{}", report)), report["summary"])
        cases = {(case["source"], case["line"]): case for case in report["investigations"]["cases"]}
        self.assertIn(("bank", 20), cases)
        self.assertEqual(cases["bank", 20]["evidence"],
                         [f"{SOURCE_FILES[2]}:20", f"{SOURCE_FILES[1]}:6"])
        self.assertIn("FAT-2026-0341", cases["bank", 20]["finding"])
        self.assertIn("não comprova baixa", cases["bank", 20]["finding"])
        uncertain = cases["bank", 21]
        self.assertIn("origem desconhecida", uncertain["finding"])
        self.assertIn("referencia_ambigua", uncertain["finding"])
        self.assertIn(f"{SOURCE_FILES[0]}:14", uncertain["evidence"])
        self.assertIn(f"{SOURCE_FILES[1]}:3", uncertain["evidence"])
        self.assertEqual(next(t for t in report["titles"] if t["reference"] == "FAT-2026-0332"
                              and t["source"] == "omie")["bank_lines"], [])
        for line, other in ((22, 23), (23, 22)):
            debit_case = cases["bank", line]
            self.assertIn(f"{SOURCE_FILES[2]}:{other}", debit_case["evidence"])
            self.assertIn("NF 99111", debit_case["finding"])
            self.assertIn("favorecido", debit_case["finding"])
        self.assertEqual(report["summary"]["supplier_payments_initiated"], 0)


    def test_expanded_pack_proves_normal_matches_and_preserves_unresolved_evidence(self):
        exports = Exports()
        expanded = DATA.parent / "feedback" / "amostra-expandida"
        exports.data = {name: (expanded / name).read_bytes() for name in SOURCE_FILES}
        report = reconcile(exports)
        self.assertEqual(report["counts"], {"protheus": 92, "omie": 25, "bank": 83, "inbox_lines": 80})
        full = next(title for title in report["titles"] if title["document"] == "NF 105060")
        self.assertEqual((full["status"], full["bank_lines"]), ("credito_integral_no_extrato", [79]))
        partial = next(title for title in report["titles"] if title["document"] == "NF 105061")
        self.assertEqual((partial["status"], partial["difference_before_unlinked_credits"]),
                         ("credito_parcial", "5200.00"))
        omie = next(title for title in report["titles"] if title["reference"] == "FAT-2026-0401")
        self.assertEqual((omie["status"], omie["bank_lines"]), ("credito_integral_no_extrato", [81]))
        clue = next(title for title in report["titles"] if title["reference"] == "FAT-2026-0402")
        self.assertEqual(clue["identified_credit"], "0.00")
        self.assertIn(82, [candidate["line"] for candidate in clue["candidate_bank_credits"]])
        repeat = report["summary"]["payables"]["possible_repeated_invoice_debits"]
        self.assertEqual(repeat[0]["invoice"], "NF 99374")
        self.assertEqual(repeat[0]["bank_lines"], [83, 84])
        self.assertIn(71, repeat[0]["inbox_lines"])
        self.assertEqual(report["summary"]["receivables"]["full_credit_references"],
                         sum(title["status"] == "credito_integral_no_extrato" for title in report["titles"]))
        self.assertEqual(report["summary"]["erp_writeoffs_performed"], 0)
        cases = {(case["source"], case["line"]): case for case in report["investigations"]["cases"]}
        self.assertIn(("bank", 79), cases)
        self.assertIn(("bank", 81), cases)
        self.assertIn(f"{SOURCE_FILES[2]}:82", cases["omie", clue["line"]]["evidence"])
        for line, other in ((83, 84), (84, 83)):
            debit_case = cases["bank", line]
            self.assertIn(f"{SOURCE_FILES[2]}:{other}", debit_case["evidence"])
            self.assertIn(f"{SOURCE_FILES[3]}:71", debit_case["evidence"])
            self.assertIn("NF 99374", debit_case["finding"])
            self.assertIn(f"{SOURCE_FILES[3]}:74", debit_case["evidence"])
            self.assertIn("R$ 6.840,00", debit_case["finding"])
            self.assertIn("nao estao disponiveis", debit_case["finding"])
            self.assertIn("favorecido", debit_case["finding"])


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
        self.assertNotIn("NF 104580", [row[3] for row in rows[1:]])
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
