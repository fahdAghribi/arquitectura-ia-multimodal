"""Servidor FastMCP de expedientes del despacho (clientes, trámites, documentación, historial).

Expone vía MCP (Streamable HTTP, endpoint /mcp) una base SQLite con datos ficticios de ejemplo.
Las herramientas de lectura aceptan `cliente_username`: si se indica, solo se devuelven datos de
ese cliente (el API Gateway lo fuerza siempre para usuarios con rol cliente). La escritura en el
historial exige una aprobación del gestor firmada (HMAC) por el API Gateway.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

DB_PATH = Path(os.getenv("EXPEDIENTES_DB_PATH", "./data/expedientes.db"))
SHARED_SECRET = os.getenv("MCP_APPROVAL_SECRET", "change-me-approval-secret").encode()
APPROVAL_MAX_AGE_S = int(os.getenv("MCP_APPROVAL_MAX_AGE_S", "600"))
DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def fake_nif(number: int) -> str:
    return f"{number:08d}{DNI_LETTERS[number % 23]}"


# Datos ficticios de demostración. Las fechas límite son objetivos internos del despacho,
# no plazos oficiales de la AEAT.
SEED_CLIENTES = [
    ("cliente.ana", "Ana Martín Ruiz", fake_nif(12345678), "particular", "Andalucía", "residente"),
    ("cliente.marcos", "Marcos Gil Navarro", fake_nif(87654321), "autonomo", "Comunidad de Madrid", "residente"),
    ("cliente.nexo", "Nexo Digital SL", "B00000000", "sociedad", "Comunitat Valenciana", "residente"),
]
SEED_EXPEDIENTES = [
    ("EXP-2026-001", "cliente.ana", "Rectificación de autoliquidación de Renta", "Modelo 100", 2025, "anual", "en_preparacion", "2026-11-15",
     "Olvidó aplicar la deducción autonómica por alquiler de vivienda habitual."),
    ("EXP-2026-014", "cliente.marcos", "Declaración trimestral de IVA", "Modelo 303", 2026, "4T", "pendiente_documentacion", "2027-01-20",
     "Actividad profesional de diseño gráfico. Facturas emitidas a clientes de la UE."),
    ("EXP-2026-015", "cliente.marcos", "Pago fraccionado IRPF (estimación directa)", "Modelo 130", 2026, "4T", "pendiente_documentacion", "2027-01-20", ""),
    ("EXP-2026-020", "cliente.nexo", "Impuesto sobre Sociedades", "Modelo 200", 2025, "anual", "presentado", "2026-07-25",
     "Presentado en plazo. Pendiente de revisar aplicación de la reserva de capitalización."),
    ("EXP-2026-021", "cliente.nexo", "Alta en el régimen de ventanilla única (OSS)", "Modelo 035", 2026, "", "en_estudio", "2026-12-01",
     "Empieza a vender a consumidores finales de otros Estados miembros."),
]
SEED_DOCUMENTOS = [
    ("EXP-2026-001", "Certificado de retenciones de la empresa", "aportado"),
    ("EXP-2026-001", "Contrato de alquiler de la vivienda habitual", "pendiente"),
    ("EXP-2026-001", "Justificantes de pago del alquiler (transferencias)", "pendiente"),
    ("EXP-2026-014", "Libro registro de facturas emitidas del 4T", "pendiente"),
    ("EXP-2026-014", "Libro registro de facturas recibidas del 4T", "pendiente"),
    ("EXP-2026-015", "Ingresos y gastos de la actividad del 4T", "pendiente"),
    ("EXP-2026-020", "Cuentas anuales 2025", "aportado"),
    ("EXP-2026-021", "Relación de ventas a distancia por Estado miembro", "pendiente"),
]

mcp = FastMCP(
    name="asesoria-expedientes",
    instructions="Consulta de clientes y expedientes del despacho. La escritura exige aprobación firmada del gestor.",
)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(_db()) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS clientes (
                username TEXT PRIMARY KEY, nombre TEXT, nif TEXT, tipo TEXT, comunidad_autonoma TEXT, residencia TEXT
            );
            CREATE TABLE IF NOT EXISTS expedientes (
                expediente_id TEXT PRIMARY KEY, cliente TEXT REFERENCES clientes(username), tramite TEXT, modelo TEXT,
                ejercicio INTEGER, periodo TEXT, estado TEXT, fecha_objetivo_despacho TEXT, notas TEXT
            );
            CREATE TABLE IF NOT EXISTS documentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, expediente_id TEXT, nombre TEXT, estado TEXT
            );
            CREATE TABLE IF NOT EXISTS historial (
                id INTEGER PRIMARY KEY AUTOINCREMENT, expediente_id TEXT, tipo TEXT, pregunta TEXT, resumen TEXT,
                autor TEXT, approval_id TEXT UNIQUE, ts REAL
            );
            """
        )
        if conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0] == 0:
            conn.executemany("INSERT INTO clientes VALUES (?,?,?,?,?,?)", SEED_CLIENTES)
            conn.executemany("INSERT INTO expedientes VALUES (?,?,?,?,?,?,?,?,?)", SEED_EXPEDIENTES)
            conn.executemany("INSERT INTO documentos (expediente_id, nombre, estado) VALUES (?,?,?)", SEED_DOCUMENTOS)


def _expediente(conn: sqlite3.Connection, expediente_id: str, cliente_username: str | None) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM expedientes WHERE expediente_id = ?", (expediente_id,)).fetchone()
    if row is None or (cliente_username and row["cliente"] != cliente_username):
        raise ValueError(f"Expediente '{expediente_id}' no encontrado")
    return row


ClienteFiltro = Annotated[str | None, Field(description="Limita la consulta a este cliente (username)")]


@mcp.tool(tags={"lectura"}, annotations={"readOnlyHint": True})
def listar_expedientes(cliente_username: ClienteFiltro = None) -> list[dict]:
    """Lista los expedientes (trámite, modelo, ejercicio, periodo y estado) y documentación pendiente."""
    with closing(_db()) as conn:
        sql = "SELECT e.*, c.nombre, c.tipo AS tipo_cliente FROM expedientes e JOIN clientes c ON c.username = e.cliente"
        rows = conn.execute(sql + (" WHERE e.cliente = ?" if cliente_username else "") + " ORDER BY e.expediente_id",
                            (cliente_username,) if cliente_username else ()).fetchall()
        out = []
        for r in rows:
            pend = conn.execute("SELECT COUNT(*) FROM documentos WHERE expediente_id = ? AND estado = 'pendiente'", (r["expediente_id"],)).fetchone()[0]
            out.append({k: r[k] for k in ("expediente_id", "cliente", "nombre", "tipo_cliente", "tramite", "modelo", "ejercicio", "periodo", "estado")}
                       | {"documentos_pendientes": pend})
    return out


@mcp.tool(tags={"lectura"}, annotations={"readOnlyHint": True})
def obtener_expediente(
    expediente_id: Annotated[str, Field(description="Identificador, p. ej. EXP-2026-014")],
    cliente_username: ClienteFiltro = None,
) -> dict:
    """Detalle de un expediente: datos del cliente, trámite, estado, documentación y notas del despacho."""
    with closing(_db()) as conn:
        e = _expediente(conn, expediente_id, cliente_username)
        c = conn.execute("SELECT * FROM clientes WHERE username = ?", (e["cliente"],)).fetchone()
        docs = conn.execute("SELECT nombre, estado FROM documentos WHERE expediente_id = ? ORDER BY id", (expediente_id,)).fetchall()
    return {
        "expediente_id": e["expediente_id"], "tramite": e["tramite"], "modelo": e["modelo"], "ejercicio": e["ejercicio"],
        "periodo": e["periodo"], "estado": e["estado"], "notas": e["notas"],
        "fecha_objetivo_despacho": e["fecha_objetivo_despacho"],
        "aviso_fecha": "Fecha objetivo interna del despacho; no es un plazo oficial de la AEAT.",
        "cliente": {k: c[k] for k in ("username", "nombre", "nif", "tipo", "comunidad_autonoma", "residencia")},
        "documentacion": [dict(d) for d in docs],
    }


@mcp.tool(tags={"lectura"}, annotations={"readOnlyHint": True})
def historial_expediente(
    expediente_id: Annotated[str, Field(description="Identificador del expediente")],
    cliente_username: ClienteFiltro = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> list[dict]:
    """Consultas anteriores ya revisadas por el gestor en este expediente, de la más reciente a la más antigua."""
    with closing(_db()) as conn:
        _expediente(conn, expediente_id, cliente_username)
        rows = conn.execute(
            "SELECT tipo, pregunta, resumen, autor, datetime(ts, 'unixepoch') AS fecha_utc FROM historial "
            "WHERE expediente_id = ? ORDER BY ts DESC LIMIT ?", (expediente_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def _verify(payload: dict, signature: str) -> None:
    if abs(time.time() - payload["issued_at"]) > APPROVAL_MAX_AGE_S:
        raise PermissionError("Aprobación caducada")
    expected = hmac.new(SHARED_SECRET, json.dumps(payload, sort_keys=True).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise PermissionError("Firma de aprobación inválida: operación rechazada")


@mcp.tool(tags={"escritura"}, annotations={"readOnlyHint": False})
def registrar_respuesta(
    expediente_id: Annotated[str, Field(description="Expediente al que se asocia la consulta")],
    approval_id: Annotated[str, Field(description="Identificador único de la revisión")],
    aprobado_por: Annotated[str, Field(description="Gestor que revisó la respuesta")],
    decision: Annotated[Literal["aprobada", "rechazada"], Field(description="Resultado de la revisión")],
    issued_at: Annotated[int, Field(description="Marca temporal Unix de la firma")],
    signature: Annotated[str, Field(description="Firma HMAC-SHA256 emitida por el API Gateway")],
    pregunta: str = "",
    resumen: str = "",
) -> dict:
    """Anota en el historial del expediente una consulta revisada por el gestor."""
    _verify({"approval_id": approval_id, "expediente_id": expediente_id, "aprobado_por": aprobado_por,
             "decision": decision, "issued_at": issued_at}, signature)
    with closing(_db()) as conn, conn:
        _expediente(conn, expediente_id, None)
        try:
            conn.execute(
                "INSERT INTO historial (expediente_id, tipo, pregunta, resumen, autor, approval_id, ts) VALUES (?,?,?,?,?,?,?)",
                (expediente_id, f"consulta_{decision}", pregunta[:2000], resumen[:4000], aprobado_por, approval_id, time.time()),
            )
        except sqlite3.IntegrityError as exc:
            raise PermissionError("Revisión ya registrada (protección anti-replay)") from exc
    return {"registrado": True, "expediente_id": expediente_id, "decision": decision}


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    with closing(_db()) as conn:
        conn.execute("SELECT 1")
    return JSONResponse({"status": "ok", "service": "asesoria-expedientes"})


init_db()

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=int(os.getenv("PORT", "9000")), show_banner=False)
