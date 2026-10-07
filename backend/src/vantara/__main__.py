import argparse
import csv
import json
import sys

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError
from openai import AuthenticationError

from .agent import run
from .reconcile import case_bundle, format_case, reconcile, worklist
from .sources import BUCKET, DATA, SOURCE_FILES, Sources


ENDPOINTS = {"vantara": "http://127.0.0.1:4566", "barte": "http://127.0.0.1:4567"}


def seed(s3) -> None:
    try:
        s3.head_bucket(Bucket=BUCKET)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchBucket"}:
            raise
        s3.create_bucket(Bucket=BUCKET)
    for filename in SOURCE_FILES:
        data = (DATA / filename).read_bytes()
        s3.put_object(Bucket=BUCKET, Key=filename, Body=data)
        print(f"s3://{BUCKET}/{filename} ({len(data)} bytes)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Investigar exports Vantara no Floci local.")
    parser.add_argument("--target", choices=ENDPOINTS, default="vantara")
    parser.add_argument("--leadership-approved", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="Copiar os quatro arquivos originais para Floci")
    commands.add_parser("sources", help="Listar arquivos e colunas lidos do Floci")
    commands.add_parser("reconcile", help="Conciliar toda a amostra e listar exceções com evidência, sem API externa")
    commands.add_parser("worklist", help="Exportar fila de revisão priorizada em CSV para a analista")
    case = commands.add_parser("case", help="Mostrar dossiê verificável de uma NF ou referência sem IA")
    case.add_argument("reference", help="Ex.: 'NF 104560' ou 'FAT-2026-0335'")
    ask = commands.add_parser("ask", help="Investigar os arquivos no Floci com o agente")
    ask.add_argument("question", help="Pergunta sobre os dados da amostra")
    ask.add_argument("--model", default="gpt-6-luna")
    args = parser.parse_args()
    if args.target == "barte" and not args.leadership_approved:
        parser.error("Barte exige aprovação da liderança; --leadership-approved confirma aprovação externa.")

    s3 = boto3.client("s3", endpoint_url=ENDPOINTS[args.target], region_name="us-east-1",
                      aws_access_key_id="test", aws_secret_access_key="test")
    try:
        if args.command == "seed":
            seed(s3)
        else:
            sources = Sources(s3)
            if args.command == "sources":
                print(json.dumps(sources.list_sources(), ensure_ascii=False, indent=2))
            elif args.command == "reconcile":
                print(json.dumps(reconcile(sources), ensure_ascii=False, indent=2))
            elif args.command == "worklist":
                writer = csv.writer(sys.stdout, delimiter=";", lineterminator="\n")
                writer.writerows(worklist(reconcile(sources)))
            elif args.command == "case":
                dossier = case_bundle(reconcile(sources), args.reference)
                if not dossier:
                    parser.exit(2, "Título não encontrado; informe NF ou referência completa.\n")
                print(format_case(dossier))
            else:
                print(run(args.question, sources, args.model))
    except FileNotFoundError as exc:
        parser.exit(2, f"{exc}\n")
    except EndpointConnectionError:
        parser.exit(2, "Floci indisponível. Execute `docker compose up -d --wait`.\n")
    except AuthenticationError:
        parser.exit(1, "OPENAI_API_KEY foi recusada pela API. Configure uma chave válida.\n")
    except ValueError as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
