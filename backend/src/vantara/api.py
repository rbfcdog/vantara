import base64
import binascii
import csv
from datetime import datetime, timezone
from io import StringIO
from typing import Literal

import boto3
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from openai import APIError, AuthenticationError
from pydantic import BaseModel, Field

from .agent import run
from .reconcile import reconcile, title_case, worklist
from .sources import BUCKET, SOURCE_FILES, Sources


app = FastAPI(title="Vantara analyst API")


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)


class ExportRequest(BaseModel):
    files: dict[str, str]


class UploadedSources(Sources):
    def __init__(self, files: dict[str, bytes]):
        self.files = files

    def _read(self, filename: str) -> bytes:
        return self.files[filename]


def _sources():
    s3 = boto3.client("s3", endpoint_url="http://127.0.0.1:4566", region_name="us-east-1",
                      aws_access_key_id="test", aws_secret_access_key="test")
    return Sources(s3)


@app.exception_handler(FileNotFoundError)
def missing_export(request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(EndpointConnectionError)
def floci_unavailable(request, exc):
    return JSONResponse(status_code=503, content={"detail": "Floci local indisponível. Execute `docker compose up -d --wait`."})


@app.exception_handler(ClientError)
def floci_error(request, exc):
    return JSONResponse(status_code=502, content={"detail": "Falha ao acessar exportações no Floci local. Verifique o serviço e o bucket vantara-case-raw."})


@app.exception_handler(BotoCoreError)
def floci_client_error(request, exc):
    return JSONResponse(status_code=502, content={"detail": "Falha na conexão com o Floci local. Verifique o serviço em 127.0.0.1:4566."})


@app.exception_handler(AuthenticationError)
def ai_auth_error(request, exc):
    return JSONResponse(status_code=502, content={"detail": "OPENAI_API_KEY foi recusada pela API. Configure uma chave válida."})


@app.exception_handler(ValueError)
def invalid_export(request, exc):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(APIError)
def ai_service_error(request, exc):
    return JSONResponse(status_code=502, content={"detail": "Falha na análise da IA. Verifique a conexão com a API e tente novamente."})


@app.exception_handler(RuntimeError)
def analysis_error(request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)})




@app.get("/api/worklist")
def get_worklist():
    header, *rows = worklist(reconcile(_sources()))
    return {"items": [dict(zip(header, row)) for row in rows],
            "processed_at": datetime.now(timezone.utc).isoformat()}

@app.post("/api/exports")
def upload_exports(payload: ExportRequest):
    if set(payload.files) != set(SOURCE_FILES):
        raise ValueError("Envie exatamente os quatro arquivos de exportação esperados.")
    files = {}
    for filename in SOURCE_FILES:
        encoded = payload.files[filename]
        if len(encoded) > 4 * ((2 * 1024 * 1024 + 2) // 3):
            raise ValueError(f"{filename}: arquivo maior que 2 MiB.")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"{filename}: base64 inválido.") from exc
        if len(data) > 2 * 1024 * 1024:
            raise ValueError(f"{filename}: arquivo maior que 2 MiB.")
        files[filename] = data
    try:
        report = reconcile(UploadedSources(files))
        items_count = len(worklist(report)) - 1
    except UnicodeError as exc:
        raise ValueError("Exportação inválida. Verifique a codificação UTF-8 dos arquivos.") from exc
    s3 = _sources().s3
    try:
        s3.head_bucket(Bucket=BUCKET)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchBucket"}:
            raise
        s3.create_bucket(Bucket=BUCKET)
    for filename in SOURCE_FILES:
        s3.put_object(Bucket=BUCKET, Key=filename, Body=files[filename])
    return {"processed_at": datetime.now(timezone.utc).isoformat(), "items_count": items_count}


@app.get("/api/worklist.csv")
def get_worklist_csv():
    output = StringIO(newline="")
    csv.writer(output, delimiter=";", lineterminator="\n").writerows(worklist(reconcile(_sources())))
    return Response(content=output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="vantara-worklist.csv"'})


@app.get("/api/case")
def get_case(source: Literal["protheus", "omie", "bank"], line: int = Query(ge=2)):
    report = reconcile(_sources())
    if source == "bank":
        bank = next((item for item in report["bank"] if item["line"] == line), None)
        if bank is None:
            raise HTTPException(status_code=404, detail=f"Lançamento bancário da linha {line} não encontrado.")
        titles = {(title["source"], title["line"]): title for title in report["titles"]}
        candidates = [{"title": titles[(candidate["source"], candidate["line"])],
                       "reasons": candidate["reasons"]} for candidate in bank.get("candidates", [])]
        return {"kind": "bank", "bank": bank, "candidates": candidates}
    title = next((item for item in report["titles"] if item["source"] == source and item["line"] == line), None)
    if title is None:
        raise HTTPException(status_code=404, detail=f"Título {source} da linha {line} não encontrado.")
    details = title_case(report, title)
    return {"kind": "title", "title": details["title"], "linked_credits": details["linked_credits"],
            "candidate_credits": details["candidate_credits"], "related_titles": details["same_tax_id_titles"]}


@app.post("/api/ask")
def ask(payload: AskRequest):
    history = [message.model_dump() for message in payload.history]
    if any(message["role"] != ("user" if index % 2 == 0 else "assistant")
           for index, message in enumerate(history)) or len(history) % 2:
        raise HTTPException(status_code=422, detail="O histórico deve conter pares de pergunta e resposta.")
    return {"answer": run(payload.question, _sources(), include_dossier=False, history=history)}
