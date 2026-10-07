import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch


from vantara.agent import run
from vantara.sources import CSV_SOURCES, DATA, INBOX_KEY, Sources, search_csv_bytes, search_text_bytes
from vantara.tools import execute


class SourceLookupTest(unittest.TestCase):
    def test_cross_format_identity_and_partial_payment(self):
        protheus_file, protheus_delimiter = CSV_SOURCES["protheus"]
        omie_file, omie_delimiter = CSV_SOURCES["omie"]
        bank_file, bank_delimiter = CSV_SOURCES["bank"]
        protheus = search_csv_bytes((DATA / protheus_file).read_bytes(), protheus_file,
                                    protheus_delimiter, "55666777000144")
        omie = search_csv_bytes((DATA / omie_file).read_bytes(), omie_file,
                                omie_delimiter, "55666777000144")
        bank = search_csv_bytes((DATA / bank_file).read_bytes(), bank_file,
                                bank_delimiter, "000012345720")
        self.assertEqual(protheus["rows"][0]["line"], 10)
        self.assertEqual(protheus["rows"][0]["fields"]["valor"], "31.250,00")
        self.assertEqual(omie["rows"][0]["fields"]["amount"], "31250.00")
        self.assertEqual(bank["rows"][0]["fields"]["valor"], "62.500,00")

    def test_inbox_evidence_and_source_allowlist(self):
        lines = search_text_bytes((DATA / INBOX_KEY).read_bytes(), "088231")["lines"]
        self.assertIn(34, [item["line"] for item in lines])
        self.assertIn("19.278,00", " ".join(item["text"] for item in lines))
        rejected = json.loads(execute(Sources(None), "search_csv", '{"source":"../../docs","query":"88231"}'))
        self.assertIn("fora da lista", rejected["error"])

    def test_follow_up_uses_prior_turns_and_rechecks_sources(self):
        history = [
            {"role": "user", "content": "O que falta conferir na NF 104560?"},
            {"role": "assistant", "content": "O PIX é apenas candidato."},
        ]
        function = SimpleNamespace(type="function_call", call_id="lookup", name="search_csv",
                                   arguments='{"source":"bank","query":"PIX"}')
        responses = iter([
            SimpleNamespace(status="completed", output=[function], output_text=""),
            SimpleNamespace(status="completed", output=[], output_text="Confira extrato:17."),
        ])
        requests = []

        def create(**kwargs):
            requests.append({**kwargs, "input": list(kwargs["input"])})
            return next(responses)

        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), \
             patch("vantara.agent.OpenAI", return_value=client), \
             patch("vantara.agent.reconcile", return_value={}), \
             patch("vantara.agent.case_bundle", return_value=None), \
             patch("vantara.agent.overview", return_value={"scope": "amostra"}), \
             patch("vantara.agent.execute", return_value='{"rows":[{"line":17}]}') as lookup:
            answer = run("E esse PIX?", Sources(None), include_dossier=False, history=history)
        self.assertEqual(answer, "Confira extrato:17.")
        self.assertEqual(requests[0]["input"], [*history, {"role": "user", "content": "E esse PIX?"}])
        self.assertEqual(requests[0]["tool_choice"], "required")
        self.assertEqual(requests[1]["input"][-1]["output"], '{"rows":[{"line":17}]}')
        self.assertEqual(lookup.call_count, 1)



if __name__ == "__main__":
    unittest.main()
