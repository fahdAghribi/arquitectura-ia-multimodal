# Documentos

Los PDF no se incluyen en el repositorio. Descárguelos desde la web de la Agencia Tributaria
(sede.agenciatributaria.gob.es, sección de manuales y ayudas) y cópielos en esta carpeta con el nombre
de archivo que indica `data/catalogo.json`.

Documentos usados en el ejemplo:

- Modelo 100. Declaración del Impuesto sobre la Renta de las Personas Físicas 2025
- Manual práctico de Renta 2025. Parte 2: deducciones autonómicas
- Manual de ayuda Renta WEB 2025
- Cartera de valores
- Manual práctico de Sociedades 2025
- Manual de ayuda Sociedades WEB 2025
- Modelo 200. Declaración del Impuesto sobre Sociedades 2019 (se mantiene como ejemplo de documento no vigente)
- Manual práctico de IVA 2026
- Guía de importación y exportación de envíos de escaso valor (cambios en IVA 2021)
- Modelo 035. Registro censal de los regímenes especiales de ventanilla única
- Manual práctico de Patrimonio 2025
- Patrimonio 2025 (Modelo 714)
- Manual de Tributación de No Residentes (octubre 2026)
- Manual de ayuda Censos WEB
- Guía de usuario de la aplicación gratuita de facturación (Verifactu)
- Manual técnico del servicio web masivo de calidad de datos identificativos
- Información sobre la presentación mediante Web Service

Para usar sus propios documentos, sustituya las entradas de `data/catalogo.json` y ejecute
`scripts/ingest_documents.py`. Los que falten se omiten con un aviso.
