import subprocess
import unittest
from unittest.mock import patch

from parse_bdi import _read_pdf_text_isolated


class PdfReadTimeoutTests(unittest.TestCase):
    @patch("parse_bdi.subprocess.run")
    def test_returns_extracted_text(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Baltic data", stderr=""
        )

        self.assertEqual(_read_pdf_text_isolated("report.pdf"), "Baltic data")

    @patch("parse_bdi.subprocess.run")
    def test_raises_timeout_error(self, run):
        run.side_effect = subprocess.TimeoutExpired(cmd=["python"], timeout=5)

        with self.assertRaisesRegex(TimeoutError, "exceeded 5s"):
            _read_pdf_text_isolated("report.pdf", timeout_seconds=5)

    @patch("parse_bdi.subprocess.run")
    def test_reports_child_process_failure(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="broken PDF\n"
        )

        with self.assertRaisesRegex(RuntimeError, "broken PDF"):
            _read_pdf_text_isolated("report.pdf")


if __name__ == "__main__":
    unittest.main()
