"""Guardrails de entrada y salida del agente de seguridad.

Clasificadores ligeros y deterministas: no consumen tokens ni dependen de red. La revisión del
gestor (HitL), el RBAC y el enmascarado de datos personales completan la contención.
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field

from PIL import Image, UnidentifiedImageError

from .schemas import RespuestaTramite

INJECTION_PATTERNS: list[tuple[str, float]] = [
    (r"\bignor\w*\s+(todas?\s+)?(las\s+|tus\s+)?(instrucciones|reglas|indicaciones)", 1.0),
    (r"\bignore\s+(all\s+|any\s+)?(previous|prior|above|the)\s+(instructions|rules)", 1.0),
    (r"\b(olvida|forget)\b.{0,30}\b(instrucciones|instructions|reglas|rules)", 0.8),
    (r"\b(system\s*prompt|prompt\s+del\s+sistema|mensaje\s+del\s+sistema)\b", 0.7),
    (r"</?\s*(system|assistant|instructions?)\s*>", 0.9),
    (r"\b(eres\s+ahora|ahora\s+eres|you\s+are\s+now|act[uú]a\s+como|act\s+as)\b", 0.6),
    (r"\b(jailbreak|DAN\b|modo\s+desarrollador|developer\s+mode)", 0.9),
    (r"\b(sin|without)\s+(la\s+)?(revisi[oó]n|aprobaci[oó]n|review|approval)(\s+(del?\s+)?(gestor|asesor|humana|manager))?\b", 0.8),
    (r"\b(sin|without)\s+(la\s+)?(revisi[oó]n|aprobaci[oó]n)\s+(del?\s+)?(gestor|asesor|humana)", 0.2),
    (r"\b(desactiva|deshabilita|disable|bypass|salta(te)?|evita)\b.{0,25}\b(seguridad|guardrail|revisi[oó]n|gestor|safety)", 1.0),
    (r"\b(revela|muestra|reveal|print|dame)\b.{0,30}\b(clave|secret[oa]?|token|api\s*key|contraseñ?a|password)", 0.9),
    (r"\b(expedientes?|datos|declaraci[oó]n)\b.{0,25}\b(de\s+otros?\s+clientes?|de\s+todos\s+los\s+clientes|ajen[oa]s)\b", 1.0),
    (r"\b(c[oó]mo|forma\s+de)\b.{0,30}\b(defraudar|evadir\s+impuestos|ocultar\s+(ingresos|dinero)\s+a\s+hacienda|blanquear)\b", 1.0),
]
BLOCK_THRESHOLD = 0.9

DOMAIN_TERMS = re.compile(
    r"\b(hacienda|aeat|agencia\s+tributaria|tribut\w*|impuestos?|fiscal\w*|renta|irpf|iva|sociedades|patrimonio|"
    r"no\s+residentes?|irnr|modelo\s*\d{3}|\d{3}\b|declaraci[oó]n\w*|borrador|autoliquidaci[oó]n|liquidaci[oó]n|"
    r"deducci[oó]n\w*|deducir|desgrav\w*|retenci[oó]n\w*|reten\w*|devoluci[oó]n\w*|ingreso|pagar|pago|cuota|base\s+imponible|"
    r"factura\w*|verifactu|aut[oó]nomo\w*|empresa|sociedad|sl\b|sa\b|actividad|alta|baja|censo|censal|036|037|"
    r"nif|nie|cif|certificados?|cl@ve|clave\s+pin|sede\s+electr[oó]nica|renta\s+web|sociedades\s+web|"
    r"plazo\w*|fecha\s+l[ií]mite|calendario|aplazamiento|fraccionamiento|requerimientos?|notificaci\w*|cartas?|"
    r"sanci[oó]n|recargo|multa|inspecci[oó]n|recurso|alegaci\w*|"
    r"alquiler|vivienda|hipoteca|pensi[oó]n|n[oó]mina|salario|dividendos?|acciones|fondos?|criptomonedas?|"
    r"herencia|donaci[oó]n|ganancia\w*|p[eé]rdida\w*|patrimonial|inmuebles?|rendimientos?|"
    r"maternidad|discapacidad|familia\s+numerosa|hijos?|ascendientes?|conyuge|c[oó]nyuge|"
    r"importaci[oó]n|exportaci[oó]n|aduana\w*|oss|ioss|ventanilla\s+[uú]nica|intracomunitari\w*|"
    r"expedientes?|documentaci[oó]n\w*|documentos?|tr[aá]mite\w*|gestor\w*|asesor\w*)\b",
    re.IGNORECASE,
)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
DISCLAIMER = (
    "Información orientativa basada en publicaciones de la AEAT con efectos meramente informativos. "
    "No sustituye la normativa vigente ni el asesoramiento profesional."
)


@dataclass
class GuardrailResult:
    allowed: bool
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace("​", "")


def check_input(text: str, image: bytes | None = None, expediente_id: str | None = None) -> GuardrailResult:
    text = _normalize(text or "")
    reasons, score = [], 0.0
    for pattern, weight in INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            score += weight
            reasons.append(f"patrón no permitido: /{pattern[:40]}…/")
    if score >= BLOCK_THRESHOLD:
        return GuardrailResult(False, round(score, 2), reasons)

    if image is not None:
        if len(image) > MAX_IMAGE_BYTES:
            return GuardrailResult(False, score, ["imagen demasiado grande (>10 MB)"])
        try:
            with Image.open(io.BytesIO(image)) as img:
                img.verify()
            with Image.open(io.BytesIO(image)) as img:
                if min(img.size) < 64:
                    return GuardrailResult(False, score, ["imagen demasiado pequeña"])
        except (UnidentifiedImageError, OSError):
            return GuardrailResult(False, score, ["el archivo adjunto no es una imagen válida"])

    if not text.strip() and image is None:
        return GuardrailResult(False, score, ["consulta vacía: escriba su pregunta o adjunte una imagen"])
    if len(text) > 4000:
        return GuardrailResult(False, score, ["consulta demasiado larga (>4000 caracteres)"])
    if text.strip() and image is None and not expediente_id and len(text) > 15 and not DOMAIN_TERMS.search(text):
        return GuardrailResult(False, score, ["la consulta no parece relacionada con trámites tributarios"])
    return GuardrailResult(True, round(score, 2), reasons)


def sanitize_answer(answer: RespuestaTramite, retrieved: dict[str, dict]) -> tuple[RespuestaTramite, list[str]]:
    """Valida la salida: fuentes y capturas deben existir entre lo recuperado; avisa de vigencia."""
    notes: list[str] = []
    fuentes = [f for f in answer.fuentes if f in retrieved]
    if len(fuentes) != len(answer.fuentes):
        notes.append(f"descartadas {len(answer.fuentes) - len(fuentes)} fuentes no recuperadas")
    images = {r["image_name"] for r in retrieved.values() if r.get("image_name")}
    capturas = [c for c in answer.capturas if c in images]
    if len(capturas) != len(answer.capturas):
        notes.append(f"descartadas {len(answer.capturas) - len(capturas)} capturas no recuperadas")

    advertencias = list(answer.advertencias)
    update: dict = {"fuentes": fuentes, "capturas": capturas}
    antiguas = sorted({retrieved[f]["documento"] for f in fuentes if retrieved[f].get("aviso")})
    if antiguas:
        advertencias.append("Parte de la respuesta se basa en documentación de ejercicios anteriores: " + "; ".join(antiguas) + ".")
    if not fuentes:
        notes.append("respuesta sin fuentes verificables")
        advertencias.append("No se ha encontrado respaldo en la documentación indexada: confirme con su gestor.")
        update["confianza"] = min(answer.confianza, 0.3)
        update["requiere_asesor"] = True
    if DISCLAIMER not in advertencias:
        advertencias.append(DISCLAIMER)
    update["advertencias"] = advertencias
    if re.search("|".join(p for p, _ in INJECTION_PATTERNS[:6]), answer.resumen + answer.respuesta_detallada, re.IGNORECASE):
        notes.append("la respuesta contenía instrucciones sospechosas procedentes del contexto")
        update["requiere_asesor"] = True
    return answer.model_copy(update=update), notes
