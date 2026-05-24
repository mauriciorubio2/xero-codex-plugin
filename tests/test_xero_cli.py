from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "plugins" / "xero" / "scripts" / "xero.py"

spec = importlib.util.spec_from_file_location("xero_helper", SCRIPT)
xero_helper = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(xero_helper)


SAMPLE_INVOICES = [
    {
        "InvoiceID": "sales-1",
        "InvoiceNumber": "INV-001",
        "Type": "ACCREC",
        "Status": "AUTHORISED",
        "DateString": "2026-01-05T00:00:00",
        "DueDateString": "2026-01-20T00:00:00",
        "CurrencyCode": "AUD",
        "Total": "110.00",
        "AmountDue": "110.00",
        "Contact": {"Name": "Northwind"},
    },
    {
        "InvoiceID": "sales-2",
        "InvoiceNumber": "INV-002",
        "Type": "ACCREC",
        "Status": "PAID",
        "DateString": "2026-02-10T00:00:00",
        "DueDateString": "2026-02-24T00:00:00",
        "CurrencyCode": "AUD",
        "Total": "220.00",
        "AmountDue": "0.00",
        "Contact": {"Name": "Northwind"},
    },
    {
        "InvoiceID": "bill-1",
        "InvoiceNumber": "BILL-001",
        "Type": "ACCPAY",
        "Status": "AUTHORISED",
        "DateString": "2026-02-12T00:00:00",
        "DueDateString": "2026-02-26T00:00:00",
        "CurrencyCode": "AUD",
        "Total": "50.00",
        "AmountDue": "50.00",
        "Contact": {"Name": "Supply Co"},
    },
]


class XeroCliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_analyze_invoice_trends(self) -> None:
        analysis = xero_helper.analyze_invoices(SAMPLE_INVOICES, today=xero_helper.dt.date(2026, 3, 1))

        self.assertEqual(analysis["invoice_count"], 3)
        self.assertEqual(analysis["sales_by_currency"], {"AUD": "330.00"})
        self.assertEqual(analysis["bills_by_currency"], {"AUD": "50.00"})
        self.assertEqual(analysis["receivable_due_by_currency"], {"AUD": "110.00"})
        self.assertEqual(analysis["payable_due_by_currency"], {"AUD": "50.00"})
        self.assertEqual(analysis["overdue_count"], 2)
        self.assertEqual(
            analysis["monthly"],
            [
                {"month": "2026-01", "sales": "110.00", "bills": "0.00", "net": "110.00"},
                {"month": "2026-02", "sales": "220.00", "bills": "50.00", "net": "170.00"},
            ],
        )

    def test_csv_export_flattens_nested_values(self) -> None:
        csv_text = xero_helper.records_to_csv([{"Name": "A", "Contact": {"Name": "Nested"}}])

        self.assertIn("Contact,Name", csv_text)
        self.assertIn('"{""Name"": ""Nested""}",A', csv_text)

    def test_svg_chart_from_summary(self) -> None:
        analysis = xero_helper.analyze_invoices(SAMPLE_INVOICES, today=xero_helper.dt.date(2026, 3, 1))
        svg = xero_helper.svg_chart(xero_helper.metric_series(analysis, "sales"), "Sales")

        self.assertIn("<svg", svg)
        self.assertIn("2026-01", svg)
        self.assertIn("Xero", xero_helper.svg_chart(xero_helper.metric_series(analysis, "net"), "Xero monthly net"))

    def test_template_outputs_valid_sales_invoice_payload(self) -> None:
        result = self.run_cli("template", "sales-invoice")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["Invoices"][0]["Type"], "ACCREC")
        self.assertEqual(payload["Invoices"][0]["Status"], "DRAFT")

    def test_write_request_requires_confirmation_before_config(self) -> None:
        result = self.run_cli("request", "PUT", "Invoices", "--body", '{"Invoices":[]}')

        self.assertEqual(result.returncode, 2)
        self.assertIn("Write requests require --yes", result.stderr)

    def test_analyze_command_reads_exported_invoices(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invoices.json"
            path.write_text(json.dumps({"Invoices": SAMPLE_INVOICES}), encoding="utf-8")
            result = self.run_cli("--json", "analyze", "--input", str(path))

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["top_customers"][0]["name"], "Northwind")


if __name__ == "__main__":
    unittest.main()
