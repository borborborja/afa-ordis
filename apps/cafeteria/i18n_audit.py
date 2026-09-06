"""Strict, dependency-free checks for text that reaches the portal UI.

The source language of the project is Catalan.  Django can ensure that
translated strings appear in a ``.po`` file, but it does not report literals
that were never marked for translation.  These helpers cover both cases and
are deliberately small enough to run in every CI build.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path


TRANSLATED_BLOCK_RE = re.compile(
    r"{%\s*(?:blocktranslate|blocktrans)\b.*?%}.*?{%\s*end(?:blocktranslate|blocktrans)\s*%}",
    re.IGNORECASE | re.DOTALL,
)
DJANGO_TAG_RE = re.compile(r"(?:{%.*?%}|{{.*?}}|{#.*?#})", re.DOTALL)
WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]{2,}")

# Product names, language abbreviations and document formats are not prose.
SAFE_VISIBLE_LITERALS = {"AFA Ordis", "CA", "ES", "CSV", "PDF", "SMTP", "SQLite", "ZIP"}

# These are deliberately UI words rather than a language detector.  They
# catch the common accidental source-language changes without rejecting names
# or content created by a person using the portal.
SOURCE_FOREIGN_WORDS = {
    "save", "cancel", "delete", "settings", "dashboard", "family", "student",
    "students", "teacher", "teachers", "welcome", "login", "logout", "password", "email",
    "next", "previous", "available", "close", "open", "submit", "search", "account",
    "guardar", "cancelar", "eliminar", "editar", "configuración", "inicio", "familia",
    "alumno", "alumnos", "comedor", "bienvenida", "contraseña", "correo", "siguiente",
    "cuenta", "buscar",
}
SPANISH_FOREIGN_WORDS = {
    "desar", "cerca", "calendari", "menjador", "família", "alumnat", "contrasenya",
    "enllaç", "tanca", "inici", "curs", "gestió", "usuari", "dades", "contacte",
    "excursió", "festiu", "incidència", "ajut", "quota", "sòcia",
}
ENGLISH_FOREIGN_WORDS = {
    "save", "cancel", "delete", "edit", "settings", "dashboard", "family", "student",
    "teacher", "welcome", "login", "logout", "password", "email", "next", "previous",
    "available", "close", "open", "submit", "search", "account",
}
CATALAN_FALSE_POSITIVE_WORDS = {"cancel", "editar"}

MESSAGE_CALLS = {"success", "error", "warning", "info"}
TRANSLATION_FUNCTIONS = {"_", "gettext", "gettext_lazy", "gettext_noop", "ngettext", "pgettext"}
USER_FACING_KEYWORDS = {"label", "help_text", "placeholder", "title", "error_messages"}
USER_FACING_CALLS = {"ValidationError", "HttpResponseForbidden", "Http404"}
OUTPUT_METHODS = {"append", "extend", "writerow", "writerows"}
JS_TEXT_ASSIGNMENT_RE = re.compile(
    r"(?:textContent|innerText|innerHTML|window\.alert|alert)\s*(?:=|\()\s*(['\"])(?P<text>.*?)\1",
    re.DOTALL,
)
JS_ACCESSIBLE_ATTRIBUTE_RE = re.compile(
    r"setAttribute\(\s*['\"](?:aria-label|title|placeholder)['\"]\s*,\s*(['\"])(?P<text>.*?)\1",
    re.DOTALL,
)


@dataclass(frozen=True)
class CatalogEntry:
    msgid: str
    translations: tuple[str, ...]
    flags: frozenset[str]
    line: int


def _unquote(value: str) -> str:
    return ast.literal_eval(value.strip())


def parse_po(path: Path) -> list[CatalogEntry]:
    """Read the small PO surface we need without adding a runtime package."""
    entries: list[CatalogEntry] = []
    current: dict[str, object] = {"flags": set(), "translations": {}, "line": 0}
    active: tuple[str, int | None] | None = None

    def finish() -> None:
        nonlocal current, active
        msgid = current.get("msgid")
        if msgid is not None:
            translations = current["translations"]
            assert isinstance(translations, dict)
            ordered = tuple(value for _index, value in sorted(translations.items()))
            entries.append(CatalogEntry(
                msgid=str(msgid),
                translations=ordered,
                flags=frozenset(current["flags"]),
                line=int(current["line"]),
            ))
        current = {"flags": set(), "translations": {}, "line": 0}
        active = None

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            finish()
            continue
        if line.startswith("#, "):
            flags = current["flags"]
            assert isinstance(flags, set)
            flags.update(flag.strip() for flag in line[3:].split(","))
            continue
        if line.startswith("#"):
            continue
        if line.startswith("msgid_plural "):
            current["msgid_plural"] = _unquote(line[len("msgid_plural "):])
            active = ("msgid_plural", None)
            continue
        if line.startswith("msgid "):
            current["msgid"] = _unquote(line[len("msgid "):])
            current["line"] = line_number
            active = ("msgid", None)
            continue
        translation_match = re.match(r"msgstr(?:\[(\d+)\])?\s+(.*)", line)
        if translation_match:
            index = int(translation_match.group(1) or 0)
            translations = current["translations"]
            assert isinstance(translations, dict)
            translations[index] = _unquote(translation_match.group(2))
            active = ("translation", index)
            continue
        if line.startswith('"') and active:
            value = _unquote(line)
            field, index = active
            if field == "msgid":
                current["msgid"] = f"{current.get('msgid', '')}{value}"
            elif field == "msgid_plural":
                current["msgid_plural"] = f"{current.get('msgid_plural', '')}{value}"
            else:
                translations = current["translations"]
                assert isinstance(translations, dict)
                translations[index or 0] = f"{translations.get(index or 0, '')}{value}"
    finish()
    return entries


def _foreign_words(value: str, forbidden_words: set[str]) -> set[str]:
    value = re.sub(r"%\([^)]+\)[#0 +\-.0-9]*[A-Za-z]", "", value)
    return {
        token.casefold()
        for token in WORD_RE.findall(value)
        if token.casefold() in forbidden_words and token.casefold() not in CATALAN_FALSE_POSITIVE_WORDS
    }


def audit_catalog(path: Path, language: str) -> list[str]:
    errors: list[str] = []
    compiled = subprocess.run(
        ["msgfmt", "--check", "--output-file", "/dev/null", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if compiled.returncode:
        detail = (compiled.stderr or compiled.stdout).strip()
        errors.append(f"{path}: el catàleg PO no és vàlid: {detail}")
        return errors
    try:
        entries = parse_po(path)
    except (SyntaxError, ValueError) as error:
        errors.append(f"{path}: no s'ha pogut llegir el catàleg PO: {error}")
        return errors
    seen: set[str] = set()
    for entry in entries:
        if not entry.msgid:  # PO metadata header
            continue
        if entry.msgid in seen:
            errors.append(f"{path}:{entry.line}: msgid duplicat: {entry.msgid!r}")
        seen.add(entry.msgid)
        if "fuzzy" in entry.flags:
            errors.append(f"{path}:{entry.line}: traducció marcada com a fuzzy: {entry.msgid!r}")
        if not entry.translations or any(not translation.strip() for translation in entry.translations):
            errors.append(f"{path}:{entry.line}: falta la traducció de: {entry.msgid!r}")
        if language == "es":
            foreign = _foreign_words(entry.msgid, SOURCE_FOREIGN_WORDS)
            if foreign:
                errors.append(
                    f"{path}:{entry.line}: el text font ha de ser en català, però conté: {', '.join(sorted(foreign))}"
                )
            for translation in entry.translations:
                foreign = _foreign_words(translation, SPANISH_FOREIGN_WORDS | ENGLISH_FOREIGN_WORDS)
                if foreign:
                    errors.append(
                        f"{path}:{entry.line}: la traducció castellana conté termes no castellans: {', '.join(sorted(foreign))}"
                    )
    return errors


def _is_visible_text(value: str) -> bool:
    normalized = " ".join(value.split())
    if normalized in SAFE_VISIBLE_LITERALS or "@" in normalized:
        return False
    normalized_without_punctuation = normalized.strip("·,;:–—- ")
    if normalized_without_punctuation in SAFE_VISIBLE_LITERALS:
        return False
    return bool(normalized and WORD_RE.search(normalized))


class _VisibleTextParser(HTMLParser):
    def __init__(self, path: Path):
        super().__init__(convert_charrefs=True)
        self.path = path
        self.errors: list[str] = []

    def _record(self, value: str, kind: str) -> None:
        if _is_visible_text(value):
            line, _column = self.getpos()
            normalized = " ".join(value.split())
            self.errors.append(
                f"{self.path}:{line}: text {kind} sense {{% translate %}}: {normalized!r}"
            )

    def handle_data(self, data: str) -> None:
        self._record(data, "visible")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in {"alt", "aria-label", "placeholder", "title"} and value:
                self._record(value, f"de l'atribut {name}")


def audit_templates(template_root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(template_root.rglob("*.html")):
        source = path.read_text(encoding="utf-8")
        source = TRANSLATED_BLOCK_RE.sub("", source)
        source = DJANGO_TAG_RE.sub("", source)
        parser = _VisibleTextParser(path)
        parser.feed(source)
        errors.extend(parser.errors)
    return errors


def audit_javascript(static_root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(static_root.rglob("*.js")):
        source = path.read_text(encoding="utf-8")
        for pattern in (JS_TEXT_ASSIGNMENT_RE, JS_ACCESSIBLE_ATTRIBUTE_RE):
            for match in pattern.finditer(source):
                value = match.group("text")
                if not _is_visible_text(value):
                    continue
                line = source.count("\n", 0, match.start()) + 1
                errors.append(
                    f"{path}:{line}: text JavaScript visible sense una cadena traduïda del servidor: {value!r}"
                )
    return errors


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


class _PythonLiteralVisitor(ast.NodeVisitor):
    def __init__(self, path: Path):
        self.path = path
        self.errors: list[str] = []

    def _record(self, node: ast.AST, value: str) -> None:
        if _is_visible_text(value):
            self.errors.append(
                f"{self.path}:{node.lineno}: text Python visible sense _(): {value!r}"
            )

    def _record_output_literals(self, node: ast.AST) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            self._record(node, node.value)
        elif isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    self._record(value, value.value)
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for value in node.elts:
                self._record_output_literals(value)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - ast API
        name = _call_name(node.func)
        if name in MESSAGE_CALLS and len(node.args) > 1:
            argument = node.args[1]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                self._record(argument, argument.value)
        elif name in USER_FACING_CALLS and node.args:
            argument = node.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                self._record(argument, argument.value)
        for keyword in node.keywords:
            if keyword.arg in USER_FACING_KEYWORDS and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                self._record(keyword.value, keyword.value.value)
        if self.path.name in {"services.py", "tasks.py"} and name in OUTPUT_METHODS:
            for argument in node.args:
                self._record_output_literals(argument)
        self.generic_visit(node)


def audit_python(source_root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(source_root.glob("*.py")):
        if path.name in {"tests.py", "apps.py"}:
            continue
        visitor = _PythonLiteralVisitor(path)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        errors.extend(visitor.errors)
    return errors


def audit_project(base_dir: Path) -> list[str]:
    """Return all blocking i18n issues in a repository checkout."""
    locale_file = base_dir / "locale" / "es" / "LC_MESSAGES" / "django.po"
    errors = audit_catalog(locale_file, "es")
    errors.extend(audit_templates(base_dir / "templates"))
    errors.extend(audit_javascript(base_dir / "apps" / "cafeteria" / "static"))
    errors.extend(audit_python(base_dir / "apps" / "cafeteria"))
    return errors
