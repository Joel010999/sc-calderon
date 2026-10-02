from pathlib import Path
import re

from django.conf import settings
from django.test import SimpleTestCase


MOJIBAKE_RE = re.compile(r"[\u00c3\u00c2\ufffd\u0192]|\u00e2(?:\u20ac|\u2020|\u2122|\u0153|\u201d|\u009d)")
REMOTE_FRONTEND_RE = re.compile(
    r"""(?ix)
    <(?:script|link)\b[^>]+(?:src|href)\s*=\s*["']https?://
    |@import\s+(?:url\(\s*)?["']?https?://
    |url\(\s*["']https?://
    """
)
TEXT_EXTENSIONS = {".css", ".html", ".js", ".md", ".py", ".txt", ".yml", ".yaml"}


class ProductionHygieneTests(SimpleTestCase):
    """Guards for user-visible encoding and local-only frontend dependencies."""

    def _text_files(self):
        roots = [
            settings.BASE_DIR / "templates",
            settings.BASE_DIR / "static",
            settings.BASE_DIR / "docs",
            settings.BASE_DIR / "core",
            settings.BASE_DIR / "customers",
            settings.BASE_DIR / "operations",
            settings.BASE_DIR / "panel",
            settings.BASE_DIR / "payments",
            settings.BASE_DIR / "sales",
            settings.BASE_DIR / "tickets",
        ]
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if (
                    path.is_file()
                    and path.suffix.lower() in TEXT_EXTENSIONS
                    and "migrations" not in path.parts
                ):
                    yield path

    def test_project_text_files_are_valid_utf8_without_visible_mojibake(self):
        offenders = []
        for path in self._text_files():
            content = path.read_text(encoding="utf-8")
            has_literal_newlines = path.suffix.lower() in {".html", ".md"} and r"\n" in content
            if MOJIBAKE_RE.search(content) or has_literal_newlines:
                offenders.append(str(path.relative_to(settings.BASE_DIR)))
        self.assertEqual(offenders, [], f"Se encontraron secuencias de codificación: {offenders}")

    def test_frontend_dependencies_are_local(self):
        offenders = []
        for path in self._text_files():
            if path.parent.name not in {"templates", "static"} and "templates" not in path.parts and "static" not in path.parts:
                continue
            content = path.read_text(encoding="utf-8")
            if REMOTE_FRONTEND_RE.search(content):
                offenders.append(str(path.relative_to(settings.BASE_DIR)))
        self.assertEqual(offenders, [], f"Dependencias frontend remotas: {offenders}")

    def test_required_static_entrypoints_exist(self):
        required = [
            settings.BASE_DIR / "static/css/styles.css",
            settings.BASE_DIR / "static/css/checkout.css",
            settings.BASE_DIR / "static/js/script.js",
            settings.BASE_DIR / "static/js/checkout.js",
        ]
        self.assertTrue(all(path.is_file() for path in required))
