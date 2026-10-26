"""Enmascarado de datos personales antes de enviar texto a un LLM externo (RGPD, minimización).

Se sustituyen NIF/NIE/CIF (validando el carácter de control), IBAN, teléfonos y correos por
marcadores estables ([NIF_1], [IBAN_1]…). El mapa se guarda en el estado de la sesión para
restaurar los valores en la respuesta que ve el usuario; el modelo nunca los recibe.
Las imágenes no se enmascaran: la interfaz avisa de que se envían al modelo.
"""

from __future__ import annotations

import re

DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
CIF_LETTERS = "JABCDEFGHI"

NIF_RE = re.compile(r"\b(\d{8})[-\s]?([A-Za-z])\b")
NIE_RE = re.compile(r"\b([XYZxyz])[-\s]?(\d{7})[-\s]?([A-Za-z])\b")
CIF_RE = re.compile(r"\b([ABCDEFGHJNPQRSUVWabcdefghjnpqrsuvw])[-\s]?(\d{7})[-\s]?([0-9A-Ja-j])\b")
IBAN_RE = re.compile(r"\b[Ee][Ss]\d{2}(?:[\s-]?\d{4}){5}\b")
PHONE_RE = re.compile(r"(?<![\d\w])(?:\+34[\s-]?)?[6789]\d{2}[\s-]?\d{3}[\s-]?\d{3}(?![\d\w])")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def valid_dni(number: str, letter: str) -> bool:
    return DNI_LETTERS[int(number) % 23] == letter.upper()


def valid_nie(prefix: str, number: str, letter: str) -> bool:
    return valid_dni(str("XYZ".index(prefix.upper())) + number, letter)


def valid_cif(letter: str, digits: str, control: str) -> bool:
    total = 0
    for i, d in enumerate(digits):
        n = int(d)
        if i % 2 == 0:
            n = sum(divmod(n * 2, 10))
        total += n
    check = (10 - total % 10) % 10
    return control.upper() in (str(check), CIF_LETTERS[check])


class Masker:
    def __init__(self, mapping: dict[str, str] | None = None):
        self.mapping: dict[str, str] = dict(mapping or {})  # marcador -> valor original

    def token(self, kind: str, value: str) -> str:
        for token, original in self.mapping.items():
            if original == value:
                return token
        n = sum(1 for t in self.mapping if t.startswith(f"[{kind}_")) + 1
        token = f"[{kind}_{n}]"
        self.mapping[token] = value
        return token

    def mask(self, text: str) -> str:
        if not text:
            return text
        text = IBAN_RE.sub(lambda m: self.token("IBAN", m.group(0)), text)
        text = EMAIL_RE.sub(lambda m: self.token("EMAIL", m.group(0)), text)
        text = NIE_RE.sub(lambda m: self.token("NIE", m.group(0)) if valid_nie(*m.groups()) else m.group(0), text)
        text = NIF_RE.sub(lambda m: self.token("NIF", m.group(0)) if valid_dni(*m.groups()) else m.group(0), text)
        text = CIF_RE.sub(lambda m: self.token("CIF", m.group(0)) if valid_cif(*m.groups()) else m.group(0), text)
        text = PHONE_RE.sub(lambda m: self.token("TEL", m.group(0)), text)
        return text

    def unmask(self, value):
        """Restaura los valores originales en cadenas, listas y dicts anidados."""
        if isinstance(value, str):
            for token, original in self.mapping.items():
                value = value.replace(token, original)
            return value
        if isinstance(value, list):
            return [self.unmask(v) for v in value]
        if isinstance(value, dict):
            return {k: self.unmask(v) for k, v in value.items()}
        return value
