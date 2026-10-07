import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pdf_fixtures import MARGIN_TEXT, REFERENCES, TEXT, write_pages
from pdfplumber.page import Page

from aclpubcheck.formatchecker import CheckConfig, Error, Formatter, Warn


class LibraryCallTest(unittest.TestCase):
    """Formatter used directly, without main()."""

    def check(
        self, content_pages: list[bytes], paper_type: str = "long", **config: bool
    ) -> tuple[dict[str, list[str]], dict[Error | Warn, list[str]], str]:
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "1234_paper.pdf"
            write_pages(pdf, content_pages)
            formatter = Formatter(CheckConfig(**config))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stdout):
                result = formatter.format_check(str(pdf), paper_type, output_dir=directory)
            return result, formatter.logs, stdout.getvalue()

    def test_clean_paper_has_no_spurious_parsing_error(self) -> None:
        result, logs, output = self.check([TEXT])
        self.assertEqual(result, {})
        self.assertNotIn("Traceback", output)
        self.assertNotIn("Parsing", output)
        self.assertFalse(logs)

    def test_margin_and_page_limit_are_still_checked(self) -> None:
        result, _, _ = self.check([MARGIN_TEXT])
        self.assertIn("Error.MARGIN", result)
        result, _, _ = self.check([TEXT] * 10 + [REFERENCES])
        self.assertIn("Error.PAGELIMIT", result)

    def test_bottom_check_can_be_disabled(self) -> None:
        bottom = TEXT + b"\n1 0 0 rg BT /F1 10 Tf 290 20 Td (1) Tj ET"
        result, _, _ = self.check([bottom])
        self.assertIn("bottom margin", " ".join(result.get("Error.MARGIN", [])))
        result, _, _ = self.check([bottom], bottom_check=False)
        self.assertEqual(result, {})

    def test_unparsed_page_is_not_reported_as_clean(self) -> None:
        with patch.object(Page, "extract_words", side_effect=RuntimeError("broken page")):
            result, _, _ = self.check([TEXT])
        self.assertIn("Error.PARSING", result)

    def test_format_check_does_not_build_the_name_check_database(self) -> None:
        # building it takes seconds and about 1 GB; only the reference check needs it
        with patch("aclpubcheck.formatchecker.PDFNameCheck") as database:
            self.check([TEXT])
            self.check([MARGIN_TEXT])
        database.assert_not_called()

    def test_interrupt_is_not_swallowed_as_parsing_error(self) -> None:
        with (
            patch.object(Page, "extract_words", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.check([TEXT])


if __name__ == "__main__":
    unittest.main()
