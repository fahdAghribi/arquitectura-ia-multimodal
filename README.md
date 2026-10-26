# Arquitectura de IA multimodal: asistente de trámites tributarios

Proyecto open source con una arquitectura completa de agentes, búsqueda en documentación y revisión
humana, lista para arrancar con un comando y adaptar a otro dominio. El ejemplo incluido es un
asistente de trámites con la Agencia Tributaria para los clientes de un despacho.

Asistente para los clientes de un despacho de asesoría fiscal. El cliente pregunta por un trámite o
impuesto, por texto, voz o con una foto de una carta de Hacienda. Un agente (LangGraph + Claude)
busca en la documentación informativa de la AEAT que ha descargado el despacho y prepara una respuesta
que cita documento y página. **Un gestor la revisa antes de que llegue al cliente.**

> No es un servicio oficial de la Agencia Tributaria. Las publicaciones de la AEAT tienen efectos
> meramente informativos y las respuestas no sustituyen el asesoramiento profesional.

La misma arquitectura se usó antes para un asistente de mantenimiento industrial: PWA React,
Keycloak, FastAPI, LangGraph, FastMCP y Qdrant. Cambiar de dominio es sustituir el corpus, las
herramientas, el esquema de respuesta y el vocabulario de los filtros (ver «Adaptarlo a su dominio»).

```
 Cliente / Gestor ── PWA React ── nginx ── FastAPI (JWT, roles cliente/gestor) ◄── Keycloak
                                              │
   input_guardrail (+ enmascarado de NIF, IBAN…) → supervisor ─┬─► agente_tributario ⇄ herramientas (ReAct)
                                                               └─► agente_expedientes
                     output_guardrail → revisión del gestor (interrupt) → registro firmado → entrega
                            │                                                  │
             Qdrant: denso + BM25 + CLIP (RRF)                   FastMCP: expedientes del despacho
```

## Puesta en marcha

Requisitos: Docker con Compose v2 y al menos 8 GB de RAM para Docker durante la ingestión.

```bash
cp .env.example .env              # añada ANTHROPIC_API_KEY (sin ella: modo offline con extractos literales)
docker compose up -d --build      # el backend tarda unos minutos en cargar los modelos de embeddings
# Copie los PDF a data/documentos/ (ver data/documentos/README.md) y lance la ingestión desacoplada:
docker compose exec -d fastapi-backend sh -c 'python scripts/ingest_documents.py --qdrant_url http://vector-db:6333 > /app/data/ingest.log 2>&1'
tail -f data/ingest.log
curl http://localhost:8000/health
```

La ingestión de los 17 PDF (3.852 páginas → 8.968 fragmentos) tiene dos fases: extracción en paralelo
(guardada en `data/.cache/`) y embeddings en CPU. En un portátil de 8 GB tardó unas tres horas; en una
máquina con más núcleos o GPU es mucho más rápida. Es reanudable: un documento solo se da por hecho
cuando se ha indexado completo. Para añadir uno, cópielo a `data/documentos/`, añada su entrada en
`data/catalogo.json` y ejecute `--only <doc_id>`.

Los puertos del anfitrión (80/443 para la PWA, 8000 la API, 8080 Keycloak…) se cambian en `.env`.

| Usuario | Contraseña | Rol | Expedientes de ejemplo (ficticios) |
|---|---|---|---|
| `cliente.ana` | `ana123` | cliente | Rectificación Renta 2025 (alquiler, Andalucía) |
| `cliente.marcos` | `marcos123` | cliente | IVA 4T (303) y pago fraccionado (130), autónomo |
| `cliente.nexo` | `nexo123` | cliente | Sociedades 2025 (200) y alta en OSS (035) |
| `gestor.laura` | `laura123` | gestor | Ve todos los expedientes y la bandeja de revisión |

Son credenciales de prueba del realm de ejemplo (`keycloak/realm-asesoria.json`): cámbielas, igual
que `KEYCLOAK_ADMIN_PASSWORD` y `MCP_APPROVAL_SECRET`, antes de exponer el sistema.

## Adaptarlo a su dominio

| Qué quiere cambiar | Dónde |
|---|---|
| Los documentos y sus metadatos | `data/catalogo.json` |
| Las instrucciones y herramientas del agente | `backend/app/agents/graph.py` |
| El formato de la respuesta | `backend/app/schemas.py` |
| El vocabulario de los filtros y los datos que se enmascaran | `backend/app/guardrails.py`, `backend/app/pii.py` |
| Los datos del negocio (expedientes, máquinas, pedidos…) | `mcp-servers/server.py` |
| Los roles y usuarios | `keycloak/` |

## Flujo de una consulta

1. El cliente escribe o dicta su pregunta, opcionalmente elige un expediente y adjunta una imagen.
2. **Seguridad de entrada.** Se bloquean la manipulación de instrucciones, el acceso a datos de otros
   clientes, las peticiones para defraudar y las consultas ajenas a lo tributario. NIF/NIE/CIF (con
   dígito de control válido), IBAN, teléfonos y correos se sustituyen por marcadores (`[NIF_1]`) antes de
   llegar al modelo.
3. **Supervisor.** Envía las preguntas sobre el estado del expediente al agente de expedientes, que
   responde sin LLM. El resto va al agente tributario.
4. **Agente tributario (Claude, ciclo ReAct).** Usa estas herramientas:
   - `buscar_documentacion`: búsqueda híbrida con filtros por impuesto y ejercicio.
   - `leer_paginas`: amplía el contexto de un fragmento.
   - `listar_expedientes`, `obtener_expediente`, `historial_expediente`: el backend las restringe
     siempre a los expedientes del propio cliente.

   Termina con `responder_consulta`, cuyo esquema se valida con Pydantic: resumen, detalle, modelos,
   requisitos, documentación, pasos, plazos, advertencias, preguntas aclaratorias, fuentes y capturas.
5. **Seguridad de salida.**
   - Descarta fuentes y capturas que no se hayan recuperado de verdad.
   - Avisa si se citan documentos de ejercicios antiguos (el Modelo 200 de 2019 está marcado como no
     vigente).
   - Añade el aviso legal.
   - Si no hay fuentes, baja la confianza.
6. **Revisión del gestor.** El grafo se detiene con `interrupt()`. El cliente ve "En revisión por su
   gestor" y ni el borrador ni el razonamiento. El gestor, en su bandeja, ve el borrador, las fuentes
   y la traza completa; puede editar el resumen y añadir una nota, y después aprobar o rechazar.
7. Al aprobar, la respuesta llega **en vivo** al WebSocket del cliente y se anota en el historial del
   expediente mediante una escritura firmada con HMAC y protegida contra repeticiones. Las consultas
   del gestor no pasan por revisión.

## Documentación indexada

`data/catalogo.json` describe cada PDF: impuesto, ejercicio, tipo y vigencia. En la ingestión:
- **Sección de cada fragmento:** se toma de los marcadores del PDF, p. ej. «Ventas a distancia ›
  Concepto». 14 de los 17 documentos los tienen.
- **Cabeceras y pies repetidos:** se eliminan según su posición en la página.
- **Tablas:** se convierten a Markdown y las largas se dividen repitiendo la cabecera.
- **Imágenes:** se conservan unas 250 capturas útiles de Renta WEB, Sociedades WEB, Censos WEB y
  Verifactu. Se descartan el logo de la AEAT y otras imágenes repetidas.
- **Vectores por fragmento:**
  - denso multilingüe (`paraphrase-multilingual-mpnet-base-v2`, 512 tokens);
  - **BM25 en español**, para términos exactos como «modelo 303», «art. 68» o «casilla 0545»;
  - CLIP en las capturas, para que una captura del cliente encuentre la pantalla equivalente del manual.

## Límites conocidos

- **Sin `ANTHROPIC_API_KEY` no hay razonamiento real.** El modo offline devuelve extractos literales
  de los fragmentos más relevantes y marca siempre `requiere_asesor`. Sirve para probar la
  orquestación, no la calidad.
- **La calidad del RAG con los manuales reales no está evaluada.** Antes de usarlo con clientes
  conviene preparar un conjunto de preguntas con respuesta conocida.
- **Documentación incompleta:** falta la Parte 1 del Manual de Renta 2025 (está la Parte 2,
  deducciones autonómicas, y el Modelo 100). No hay calendario del contribuyente, así que los plazos
  solo aparecen si están en los manuales; las fechas de los expedientes son objetivos internos del
  despacho.
- **Persistencia:** las sesiones y la bandeja de revisión están en memoria y se pierden al reiniciar
  el backend. En producción se usaría `AsyncPostgresSaver` más una tabla de revisiones.
- **Imágenes:** no se enmascaran. La interfaz avisa de que se envían al modelo.
- **Presentación ante la AEAT:** el asistente no presenta nada (requiere certificado o Cl@ve); eso
  sigue haciéndolo el gestor.
- **Datos de ejemplo:** los expedientes y los NIF son ficticios.

## Desarrollo y pruebas

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt
.venv/bin/pytest -q            # sin red: PDF sintético, embeddings deterministas, Qdrant en memoria, MCP en proceso
.venv/bin/ruff check backend scripts tests mcp-servers
cd frontend && npm ci && npm run dev
```

```
backend/app/          Gateway, auth, RAG híbrido, enmascarado de datos, guardrails, modelos, A2A
backend/app/agents/   StateGraph (supervisor, ReAct, revisión del gestor)
mcp-servers/          Servidor FastMCP de expedientes (SQLite con datos ficticios)
scripts/              Ingestión de PDF, token de prueba
data/                 documentos/ (PDF de la AEAT), catalogo.json
frontend/             PWA React (cliente y bandeja del gestor) + nginx
keycloak/             Realm importable (roles cliente / gestor)
tests/                pytest
```

## Licencia

MIT (ver `LICENSE`). Los documentos de la AEAT no se incluyen: cada usuario los descarga de la fuente oficial.
