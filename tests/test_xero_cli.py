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
            result = self.run_cli("--json", "analyze", "--input", str(path), "--unsafe-stdout")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["top_customers"][0]["name"], "Northwind")

    def test_analyze_suppresses_sensitive_stdout_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invoices.json"
            path.write_text(json.dumps({"Invoices": SAMPLE_INVOICES}), encoding="utf-8")
            result = self.run_cli("--json", "analyze", "--input", str(path))

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["stdout_suppressed"])
        self.assertNotIn("Northwind", result.stdout)

    def test_dry_run_redacts_body_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "accounts.json"
            config_path.write_text(
                json.dumps({"profiles": {"default": {"active_tenant_id": "tenant-123"}}}),
                encoding="utf-8",
            )
            result = self.run_cli(
                "request",
                "--config",
                str(config_path),
                "PUT",
                "Invoices",
                "--body",
                '{"Invoices":[{"Contact":{"Name":"Sensitive Customer"}}]}',
                "--dry-run",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["body_redacted"])
        self.assertIn("body_summary", payload)
        self.assertNotIn("Sensitive Customer", result.stdout)

    def test_secure_write_uses_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "export.json"
            xero_helper.secure_write_text(str(out), '{"ok": true}\n')

            mode = out.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_sensitive_output_refuses_git_worktree_by_default(self) -> None:
        out = REPO_ROOT / "accidental-export.json"

        with self.assertRaises(Exception) as context:
            xero_helper.secure_write_text(str(out), '{"leak": true}\n')

        self.assertIn("Git worktree", str(context.exception))

    def test_store_token_requires_secure_store_or_explicit_plaintext(self) -> None:
        original_keychain_available = xero_helper.keychain_available
        original_env = dict(xero_helper.os.environ)
        try:
            xero_helper.keychain_available = lambda: False
            xero_helper.os.environ.pop(xero_helper.PLAINTEXT_TOKEN_ENV, None)
            with self.assertRaises(Exception) as context:
                xero_helper.store_token("default", {}, {"access_token": "a", "refresh_token": "r"})
            self.assertIn("No secure token store", str(context.exception))
        finally:
            xero_helper.keychain_available = original_keychain_available
            xero_helper.os.environ.clear()
            xero_helper.os.environ.update(original_env)

    def test_encrypted_export_round_trips_with_openssl(self) -> None:
        openssl = xero_helper.shutil.which("openssl")
        if not openssl:
            self.skipTest("openssl is not available")
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "export.xero.enc"
            env_name = "XERO_CODEX_TEST_PASSPHRASE"
            original = xero_helper.os.environ.get(env_name)
            try:
                xero_helper.os.environ[env_name] = "test-passphrase"
                xero_helper.encrypt_text_to_file(str(out), "sensitive report\n", passphrase_env=env_name)
                result = subprocess.run(
                    [
                        openssl,
                        "enc",
                        "-d",
                        "-aes-256-cbc",
                        "-pbkdf2",
                        "-md",
                        "sha256",
                        "-in",
                        str(out),
                        "-pass",
                        f"env:{env_name}",
                    ],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env={**xero_helper.os.environ, env_name: "test-passphrase"},
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "sensitive report\n")
            finally:
                if original is None:
                    xero_helper.os.environ.pop(env_name, None)
                else:
                    xero_helper.os.environ[env_name] = original


if __name__ == "__main__":
    unittest.main()
