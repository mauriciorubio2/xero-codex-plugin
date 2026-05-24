#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import decimal
import hashlib
import html
import http.server
import json
import os
from pathlib import Path
import secrets
import socketserver
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser


AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"
ACCOUNTING_API_BASE = "https://api.xero.com/api.xro/2.0"

DEFAULT_SCOPES = [
    "offline_access",
    "openid",
    "profile",
    "email",
    "accounting.settings.read",
    "accounting.contacts.read",
    "accounting.transactions.read",
    "accounting.reports.read",
]

WRITE_SCOPES = [
    "accounting.settings",
    "accounting.contacts",
    "accounting.transactions",
    "accounting.attachments",
]

RESOURCE_DEFS: dict[str, dict[str, str]] = {
    "accounts": {"path": "Accounts", "root": "Accounts", "date_field": ""},
    "bank-transactions": {"path": "BankTransactions", "root": "BankTransactions", "date_field": "Date"},
    "bank-transfers": {"path": "BankTransfers", "root": "BankTransfers", "date_field": "Date"},
    "bills": {"path": "Invoices", "root": "Invoices", "date_field": "Date", "where": 'Type=="ACCPAY"'},
    "contacts": {"path": "Contacts", "root": "Contacts", "date_field": ""},
    "credit-notes": {"path": "CreditNotes", "root": "CreditNotes", "date_field": "Date"},
    "invoices": {"path": "Invoices", "root": "Invoices", "date_field": "Date"},
    "items": {"path": "Items", "root": "Items", "date_field": ""},
    "journals": {"path": "Journals", "root": "Journals", "date_field": "JournalDate"},
    "manual-journals": {"path": "ManualJournals", "root": "ManualJournals", "date_field": "Date"},
    "payments": {"path": "Payments", "root": "Payments", "date_field": "Date"},
    "purchase-orders": {"path": "PurchaseOrders", "root": "PurchaseOrders", "date_field": "Date"},
    "quotes": {"path": "Quotes", "root": "Quotes", "date_field": "Date"},
    "sales-invoices": {"path": "Invoices", "root": "Invoices", "date_field": "Date", "where": 'Type=="ACCREC"'},
    "tax-rates": {"path": "TaxRates", "root": "TaxRates", "date_field": ""},
    "tracking-categories": {"path": "TrackingCategories", "root": "TrackingCategories", "date_field": ""},
    "users": {"path": "Users", "root": "Users", "date_field": ""},
}

REPORT_DEFS: dict[str, str] = {
    "aged-payables": "Reports/AgedPayablesByContact",
    "aged-receivables": "Reports/AgedReceivablesByContact",
    "balance-sheet": "Reports/BalanceSheet",
    "bank-summary": "Reports/BankSummary",
    "budget-summary": "Reports/BudgetSummary",
    "executive-summary": "Reports/ExecutiveSummary",
    "profit-and-loss": "Reports/ProfitAndLoss",
    "trial-balance": "Reports/TrialBalance",
}

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
Decimal = decimal.Decimal


class XeroCliError(Exception):
    pass


def default_config_path() -> Path:
    if os.environ.get("XERO_CODEX_CONFIG"):
        return Path(os.environ["XERO_CODEX_CONFIG"]).expanduser()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "codex-xero" / "accounts.json"


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"profiles": {}}
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise XeroCliError(f"Config file is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise XeroCliError(f"Config file must contain a JSON object: {path}")
    payload.setdefault("profiles", {})
    return payload


def save_config(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def get_profile(config: dict[str, Any], name: str, create: bool = False) -> dict[str, Any]:
    profiles = config.setdefault("profiles", {})
    if name not in profiles:
        if not create:
            raise XeroCliError(
                f"Profile '{name}' is not configured. Run: xero.py auth login --client-id <id>"
            )
        profiles[name] = {}
    profile = profiles[name]
    if not isinstance(profile, dict):
        raise XeroCliError(f"Profile '{name}' is malformed")
    return profile


def emit(payload: Any, as_json: bool = False) -> None:
    if as_json or isinstance(payload, (dict, list)):
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(payload)


def parse_scopes(values: list[str] | None, include_write: bool = False) -> list[str]:
    scopes: list[str] = []
    for value in values or DEFAULT_SCOPES:
        scopes.extend(value.split())
    if include_write:
        scopes.extend(WRITE_SCOPES)
    deduped: list[str] = []
    for scope in scopes:
        if scope and scope not in deduped:
            deduped.append(scope)
    return deduped


def random_token_urlsafe(length: int = 64) -> str:
    return secrets.token_urlsafe(length)[:128]


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def make_authorize_url(client_id: str, redirect_uri: str, scopes: list[str], state: str, challenge: str) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def token_headers(client_id: str, client_secret: str | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    if client_secret:
        encoded = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {encoded}"
    return headers


def http_json_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: int = 60,
) -> Any:
    request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            raw = response.read()
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise XeroCliError(f"Xero API returned HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise XeroCliError(f"Could not reach Xero API: {exc}") from exc
    if not raw:
        return None
    if "json" in content_type.lower() or raw[:1] in (b"{", b"["):
        return json.loads(raw.decode("utf-8"))
    return raw.decode("utf-8", errors="replace")


def exchange_code_for_token(
    client_id: str,
    redirect_uri: str,
    code: str,
    verifier: str,
    client_secret: str | None = None,
) -> dict[str, Any]:
    form: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    if not client_secret:
        form["client_id"] = client_id
    body = urllib.parse.urlencode(form).encode("utf-8")
    token = http_json_request("POST", TOKEN_URL, headers=token_headers(client_id, client_secret), body=body)
    return normalize_token(token)


def refresh_access_token(profile: dict[str, Any]) -> dict[str, Any]:
    token = profile.get("token") or {}
    refresh_token = token.get("refresh_token")
    client_id = profile.get("client_id")
    if not refresh_token or not client_id:
        raise XeroCliError("Profile does not have a refresh token. Run auth login again.")
    client_secret = get_client_secret(profile)
    form: dict[str, str] = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    if not client_secret:
        form["client_id"] = client_id
    body = urllib.parse.urlencode(form).encode("utf-8")
    refreshed = http_json_request("POST", TOKEN_URL, headers=token_headers(client_id, client_secret), body=body)
    profile["token"] = normalize_token(refreshed)
    return profile["token"]


def normalize_token(token: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(token, dict) or "access_token" not in token:
        raise XeroCliError("Token response did not contain an access_token")
    normalized = dict(token)
    expires_in = int(normalized.get("expires_in", 0) or 0)
    normalized["expires_at"] = int(time.time()) + max(expires_in - 60, 0)
    return normalized


def token_expired(profile: dict[str, Any]) -> bool:
    token = profile.get("token") or {}
    expires_at = int(token.get("expires_at", 0) or 0)
    return expires_at <= int(time.time())


def get_client_secret(profile: dict[str, Any]) -> str | None:
    env_name = profile.get("client_secret_env")
    if env_name:
        return os.environ.get(env_name)
    return None


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    server_version = "XeroCodexOAuth/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        self.server.callback_query = query  # type: ignore[attr-defined]
        if "error" in query:
            title = "Xero connection failed"
            message = html.escape(query.get("error_description", query["error"])[0])
        else:
            title = "Xero connected"
            message = "You can close this browser tab and return to Codex."
        body = f"<html><body><h1>{title}</h1><p>{message}</p></body></html>".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def wait_for_oauth_callback(redirect_uri: str, timeout: int) -> dict[str, list[str]]:
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise XeroCliError("OAuth login requires a localhost http redirect URI for this helper")
    port = parsed.port
    if not port:
        raise XeroCliError("Redirect URI must include a localhost port, e.g. http://localhost:45009/callback")
    path = parsed.path or "/"

    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    with ReusableTCPServer(("127.0.0.1", port), OAuthCallbackHandler) as server:
        server.timeout = timeout
        server.callback_query = None  # type: ignore[attr-defined]
        deadline = time.time() + timeout
        while time.time() < deadline:
            server.handle_request()
            query = getattr(server, "callback_query", None)
            if query is not None:
                return query
        raise XeroCliError(f"Timed out waiting {timeout} seconds for OAuth callback at {path}")


def xero_connections(access_token: str) -> list[dict[str, Any]]:
    payload = http_json_request(
        "GET",
        CONNECTIONS_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    if not isinstance(payload, list):
        raise XeroCliError("Connections response did not contain a tenant list")
    return payload


def ensure_token(profile: dict[str, Any], config_path: Path, config: dict[str, Any]) -> str:
    token = profile.get("token") or {}
    if not token.get("access_token"):
        raise XeroCliError("Profile is not connected. Run auth login first.")
    if token_expired(profile):
        refresh_access_token(profile)
        save_config(config_path, config)
    return str(profile["token"]["access_token"])


def active_tenant_id(profile: dict[str, Any], requested: str | None = None) -> str:
    tenant = requested or profile.get("active_tenant_id")
    if tenant:
        return str(tenant)
    tenants = profile.get("tenants") or []
    if len(tenants) == 1 and tenants[0].get("tenantId"):
        return str(tenants[0]["tenantId"])
    raise XeroCliError("No active Xero tenant selected. Run auth tenants, then auth select <tenant-id-or-name>.")


def build_api_url(path: str, params: dict[str, str | int | None] | None = None) -> str:
    clean_path = path.strip()
    if clean_path.startswith("http://") or clean_path.startswith("https://"):
        base = clean_path
    else:
        clean_path = clean_path.lstrip("/")
        base = f"{ACCOUNTING_API_BASE}/{clean_path}"
    cleaned_params = {key: value for key, value in (params or {}).items() if value not in (None, "")}
    if cleaned_params:
        return f"{base}?{urllib.parse.urlencode(cleaned_params)}"
    return base


def api_request(
    profile: dict[str, Any],
    config: dict[str, Any],
    config_path: Path,
    method: str,
    path: str,
    *,
    tenant_id: str | None = None,
    params: dict[str, str | int | None] | None = None,
    payload: Any = None,
    idempotency_key: str | None = None,
    dry_run: bool = False,
) -> Any:
    method = method.upper()
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    url = build_api_url(path, params)
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if method in MUTATING_METHODS:
        headers["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())
    if not path.startswith("http"):
        headers["xero-tenant-id"] = active_tenant_id(profile, tenant_id)
    if dry_run:
        return {
            "dry_run": True,
            "method": method,
            "url": url,
            "headers": {key: ("Bearer <redacted>" if key.lower() == "authorization" else value) for key, value in headers.items()},
            "body": payload,
        }
    access_token = ensure_token(profile, config_path, config)
    headers["Authorization"] = f"Bearer {access_token}"
    return http_json_request(method, url, headers=headers, body=body)


def read_json_body(body: str | None = None, body_file: str | None = None) -> Any:
    if body and body_file:
        raise XeroCliError("Use either --body or --body-file, not both")
    if body_file:
        with Path(body_file).expanduser().open("r", encoding="utf-8") as handle:
            return json.load(handle)
    if body:
        return json.loads(body)
    return None


def parse_query_pairs(pairs: list[str] | None) -> dict[str, str]:
    params: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise XeroCliError(f"Query parameter must be key=value: {pair}")
        key, value = pair.split("=", 1)
        params[key] = value
    return params


def parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    text = str(value)
    if text.startswith("/Date("):
        millis = int(text.split("(", 1)[1].split(")", 1)[0].split("+", 1)[0].split("-", 1)[0])
        return dt.datetime.utcfromtimestamp(millis / 1000).date()
    if "T" in text:
        text = text.split("T", 1)[0]
    return dt.date.fromisoformat(text[:10])


def date_where(field: str, start: str | None, end: str | None) -> str | None:
    clauses: list[str] = []
    for op, value in ((">=", start), ("<=", end)):
        parsed = parse_date(value)
        if parsed:
            clauses.append(f"{field}{op}DateTime({parsed.year}, {parsed.month}, {parsed.day})")
    if clauses:
        return " AND ".join(clauses)
    return None


def combine_where(*clauses: str | None) -> str | None:
    clean = [f"({clause})" for clause in clauses if clause]
    if not clean:
        return None
    return " AND ".join(clean)


def extract_records(payload: Any, root: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        records = payload.get(root)
        if isinstance(records, list):
            return [item for item in records if isinstance(item, dict)]
        if isinstance(records, dict):
            return [records]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def fetch_resource_records(
    args: argparse.Namespace,
    profile: dict[str, Any],
    config: dict[str, Any],
    config_path: Path,
) -> list[dict[str, Any]]:
    resource = RESOURCE_DEFS[args.resource]
    params = parse_query_pairs(getattr(args, "param", None))
    built_where = combine_where(
        resource.get("where"),
        getattr(args, "where", None),
        date_where(resource.get("date_field", ""), getattr(args, "from_date", None), getattr(args, "to_date", None))
        if resource.get("date_field")
        else None,
        f'Status=="{args.status}"' if getattr(args, "status", None) else None,
    )
    if built_where:
        params["where"] = built_where
    if getattr(args, "order", None):
        params["order"] = args.order
    records: list[dict[str, Any]] = []
    paged = bool(getattr(args, "all_pages", False) or getattr(args, "page", None))
    page = int(getattr(args, "page", None) or 1)
    max_pages = int(getattr(args, "max_pages", None) or 100)
    while True:
        if paged:
            params["page"] = page
        payload = api_request(
            profile,
            config,
            config_path,
            "GET",
            resource["path"],
            tenant_id=getattr(args, "tenant", None),
            params=params,
        )
        page_records = extract_records(payload, resource["root"])
        records.extend(page_records)
        if not getattr(args, "all_pages", False) or len(page_records) == 0 or page >= max_pages:
            break
        page += 1
    return records


def flatten_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return value


def records_to_csv(records: list[dict[str, Any]]) -> str:
    if not records:
        return ""
    fieldnames = sorted({key for record in records for key in record.keys()})
    from io import StringIO

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for record in records:
        writer.writerow({key: flatten_value(record.get(key, "")) for key in fieldnames})
    return buffer.getvalue()


def write_records(records: list[dict[str, Any]], out: str | None, fmt: str, as_json: bool) -> None:
    if fmt == "csv" or (out and out.lower().endswith(".csv")):
        content = records_to_csv(records)
    else:
        content = json.dumps(records, indent=2, sort_keys=True, default=str) + "\n"
    if out:
        Path(out).expanduser().write_text(content, encoding="utf-8")
        emit({"wrote": out, "records": len(records)}, as_json=True)
    else:
        print(content, end="")


def decimal_from(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value))


def decimal_to_str(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def invoice_date(invoice: dict[str, Any]) -> dt.date | None:
    for key in ("DateString", "Date"):
        try:
            parsed = parse_date(invoice.get(key))
        except (ValueError, TypeError):
            parsed = None
        if parsed:
            return parsed
    return None


def invoice_due_date(invoice: dict[str, Any]) -> dt.date | None:
    for key in ("DueDateString", "DueDate"):
        try:
            parsed = parse_date(invoice.get(key))
        except (ValueError, TypeError):
            parsed = None
        if parsed:
            return parsed
    return None


def month_key(value: dt.date | None) -> str:
    if not value:
        return "undated"
    return f"{value.year:04d}-{value.month:02d}"


def add_decimal(bucket: dict[str, Decimal], key: str, amount: Decimal) -> None:
    bucket[key] = bucket.get(key, Decimal("0")) + amount


def sorted_money_dict(bucket: dict[str, Decimal]) -> dict[str, str]:
    return {key: decimal_to_str(bucket[key]) for key in sorted(bucket)}


def top_money(bucket: dict[str, Decimal], limit: int = 10) -> list[dict[str, str]]:
    items = sorted(bucket.items(), key=lambda item: item[1], reverse=True)
    return [{"name": key, "amount": decimal_to_str(amount)} for key, amount in items[:limit]]


def analyze_invoices(invoices: list[dict[str, Any]], today: dt.date | None = None) -> dict[str, Any]:
    today = today or dt.date.today()
    by_status: dict[str, int] = {}
    sales_by_currency: dict[str, Decimal] = {}
    bills_by_currency: dict[str, Decimal] = {}
    receivable_due_by_currency: dict[str, Decimal] = {}
    payable_due_by_currency: dict[str, Decimal] = {}
    top_customers: dict[str, Decimal] = {}
    top_suppliers: dict[str, Decimal] = {}
    monthly: dict[str, dict[str, Decimal]] = {}
    overdue: list[dict[str, Any]] = []

    for invoice in invoices:
        invoice_type = str(invoice.get("Type") or "")
        status = str(invoice.get("Status") or "UNKNOWN")
        by_status[status] = by_status.get(status, 0) + 1
        currency = str(invoice.get("CurrencyCode") or "UNSPECIFIED")
        total = decimal_from(invoice.get("Total"))
        amount_due = decimal_from(invoice.get("AmountDue"))
        contact = invoice.get("Contact") if isinstance(invoice.get("Contact"), dict) else {}
        contact_name = str(contact.get("Name") or "Unknown contact")
        date_value = invoice_date(invoice)
        due_value = invoice_due_date(invoice)
        month = month_key(date_value)
        monthly.setdefault(month, {"sales": Decimal("0"), "bills": Decimal("0"), "net": Decimal("0")})

        if invoice_type == "ACCREC":
            add_decimal(sales_by_currency, currency, total)
            add_decimal(top_customers, contact_name, total)
            monthly[month]["sales"] += total
            monthly[month]["net"] += total
            if amount_due > 0:
                add_decimal(receivable_due_by_currency, currency, amount_due)
        elif invoice_type == "ACCPAY":
            add_decimal(bills_by_currency, currency, total)
            add_decimal(top_suppliers, contact_name, total)
            monthly[month]["bills"] += total
            monthly[month]["net"] -= total
            if amount_due > 0:
                add_decimal(payable_due_by_currency, currency, amount_due)

        if due_value and due_value < today and amount_due > 0 and status not in {"PAID", "VOIDED", "DELETED"}:
            overdue.append(
                {
                    "invoice_id": invoice.get("InvoiceID"),
                    "invoice_number": invoice.get("InvoiceNumber"),
                    "type": invoice_type,
                    "contact": contact_name,
                    "due_date": due_value.isoformat(),
                    "days_overdue": (today - due_value).days,
                    "currency": currency,
                    "amount_due": decimal_to_str(amount_due),
                    "status": status,
                }
            )

    monthly_rows = []
    for month in sorted(monthly):
        values = monthly[month]
        monthly_rows.append(
            {
                "month": month,
                "sales": decimal_to_str(values["sales"]),
                "bills": decimal_to_str(values["bills"]),
                "net": decimal_to_str(values["net"]),
            }
        )

    return {
        "invoice_count": len(invoices),
        "status_counts": dict(sorted(by_status.items())),
        "sales_by_currency": sorted_money_dict(sales_by_currency),
        "bills_by_currency": sorted_money_dict(bills_by_currency),
        "receivable_due_by_currency": sorted_money_dict(receivable_due_by_currency),
        "payable_due_by_currency": sorted_money_dict(payable_due_by_currency),
        "overdue_count": len(overdue),
        "overdue": sorted(overdue, key=lambda item: item["days_overdue"], reverse=True),
        "top_customers": top_money(top_customers),
        "top_suppliers": top_money(top_suppliers),
        "monthly": monthly_rows,
    }


def load_analysis_input(path: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], None
    if isinstance(payload, dict):
        if isinstance(payload.get("analysis"), dict):
            data = payload.get("data") or {}
            invoices = data.get("Invoices") or data.get("invoices") or payload.get("Invoices") or []
            return [item for item in invoices if isinstance(item, dict)], payload["analysis"]
        invoices = payload.get("Invoices") or payload.get("invoices")
        if isinstance(invoices, list):
            return [item for item in invoices if isinstance(item, dict)], None
    raise XeroCliError("Analysis input must be an invoice list, an object with Invoices, or a snapshot")


def render_human_analysis(analysis: dict[str, Any]) -> str:
    lines = [
        f"Invoices: {analysis['invoice_count']}",
        f"Overdue invoices/bills: {analysis['overdue_count']}",
        f"Sales by currency: {analysis['sales_by_currency']}",
        f"Bills by currency: {analysis['bills_by_currency']}",
        f"Receivables due: {analysis['receivable_due_by_currency']}",
        f"Payables due: {analysis['payable_due_by_currency']}",
    ]
    if analysis["monthly"]:
        lines.append("Monthly trend:")
        for row in analysis["monthly"]:
            lines.append(f"  {row['month']}: sales {row['sales']}, bills {row['bills']}, net {row['net']}")
    if analysis["top_customers"]:
        lines.append("Top customers:")
        for item in analysis["top_customers"][:5]:
            lines.append(f"  {item['name']}: {item['amount']}")
    if analysis["overdue"]:
        lines.append("Largest overdue items:")
        for item in analysis["overdue"][:5]:
            lines.append(
                f"  {item.get('invoice_number') or item.get('invoice_id')}: "
                f"{item['contact']} {item['amount_due']} {item['currency']} "
                f"({item['days_overdue']} days)"
            )
    return "\n".join(lines)


def metric_series(analysis: dict[str, Any], metric: str) -> list[tuple[str, Decimal]]:
    rows = analysis.get("monthly") or []
    series: list[tuple[str, Decimal]] = []
    for row in rows:
        if metric not in row:
            raise XeroCliError(f"Unknown metric '{metric}'. Use sales, bills, or net.")
        series.append((str(row["month"]), decimal_from(row[metric])))
    return series


def svg_chart(series: list[tuple[str, Decimal]], title: str) -> str:
    width, height = 900, 420
    margin_left, margin_right, margin_top, margin_bottom = 72, 32, 48, 86
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom
    values = [float(value) for _, value in series] or [0.0]
    minimum = min(0.0, min(values))
    maximum = max(0.0, max(values))
    if maximum == minimum:
        maximum = minimum + 1.0
    zero_y = margin_top + plot_height - ((0.0 - minimum) / (maximum - minimum) * plot_height)
    bar_gap = 10
    bar_width = max(8, (plot_width - bar_gap * max(len(series) - 1, 0)) / max(len(series), 1))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{margin_left}" y="30" font-family="Arial, sans-serif" font-size="20" fill="#1f2937">{html.escape(title)}</text>',
        f'<line x1="{margin_left}" y1="{zero_y:.1f}" x2="{width - margin_right}" y2="{zero_y:.1f}" stroke="#9ca3af" stroke-width="1"/>',
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_height}" stroke="#d1d5db"/>',
    ]
    for index, (label, value) in enumerate(series):
        amount = float(value)
        x = margin_left + index * (bar_width + bar_gap)
        y = margin_top + plot_height - ((max(amount, 0.0) - minimum) / (maximum - minimum) * plot_height)
        if amount < 0:
            y = zero_y
            bar_height = margin_top + plot_height - ((amount - minimum) / (maximum - minimum) * plot_height) - zero_y
            fill = "#ef4444"
        else:
            bar_height = zero_y - y
            fill = "#13B5EA"
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{abs(bar_height):.1f}" fill="{fill}"/>')
        parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{height - 48}" text-anchor="middle" '
            f'font-family="Arial, sans-serif" font-size="11" fill="#374151">{html.escape(label)}</text>'
        )
    parts.extend(
        [
            f'<text x="{margin_left}" y="{margin_top + 14}" font-family="Arial, sans-serif" font-size="12" fill="#6b7280">{maximum:.2f}</text>',
            f'<text x="{margin_left}" y="{margin_top + plot_height - 4}" font-family="Arial, sans-serif" font-size="12" fill="#6b7280">{minimum:.2f}</text>',
            "</svg>",
        ]
    )
    return "\n".join(parts)


def contact_template() -> dict[str, Any]:
    return {
        "Contacts": [
            {
                "Name": "Example Customer",
                "EmailAddress": "accounts@example.com",
                "ContactStatus": "ACTIVE",
                "Addresses": [{"AddressType": "STREET", "AddressLine1": "1 Example Street", "City": "Sydney"}],
            }
        ]
    }


def invoice_template(invoice_type: str) -> dict[str, Any]:
    today = dt.date.today()
    due = today + dt.timedelta(days=14)
    return {
        "Invoices": [
            {
                "Type": invoice_type,
                "Contact": {"Name": "Example Customer" if invoice_type == "ACCREC" else "Example Supplier"},
                "Date": today.isoformat(),
                "DueDate": due.isoformat(),
                "LineItems": [
                    {
                        "Description": "Example service",
                        "Quantity": 1,
                        "UnitAmount": 100.0,
                        "AccountCode": "200" if invoice_type == "ACCREC" else "400",
                        "TaxType": "OUTPUT" if invoice_type == "ACCREC" else "INPUT",
                    }
                ],
                "Status": "DRAFT",
            }
        ]
    }


def payment_template() -> dict[str, Any]:
    today = dt.date.today()
    return {
        "Payments": [
            {
                "Invoice": {"InvoiceID": "00000000-0000-0000-0000-000000000000"},
                "Account": {"Code": "090"},
                "Date": today.isoformat(),
                "Amount": 100.0,
            }
        ]
    }


def command_auth(args: argparse.Namespace) -> None:
    config_path = Path(args.config).expanduser() if args.config else default_config_path()
    config = load_config(config_path)
    profile = get_profile(config, args.profile, create=args.auth_command in {"configure", "login", "status"})

    if args.auth_command == "configure":
        scopes = parse_scopes(args.scope, args.write_scopes)
        profile.update(
            {
                "client_id": args.client_id,
                "redirect_uri": args.redirect_uri,
                "scopes": scopes,
            }
        )
        if args.client_secret_env:
            profile["client_secret_env"] = args.client_secret_env
        save_config(config_path, config)
        emit({"configured": args.profile, "config": str(config_path), "scopes": scopes}, args.json)
        return

    if args.auth_command == "login":
        client_id = args.client_id or profile.get("client_id") or os.environ.get("XERO_CLIENT_ID")
        if not client_id:
            raise XeroCliError("Provide --client-id or set XERO_CLIENT_ID")
        redirect_uri = args.redirect_uri or profile.get("redirect_uri") or "http://localhost:45009/callback"
        client_secret_env = args.client_secret_env or profile.get("client_secret_env")
        scopes = parse_scopes(args.scope or profile.get("scopes"), args.write_scopes)
        state = random_token_urlsafe(32)
        verifier = random_token_urlsafe(96)
        authorize_url = make_authorize_url(client_id, redirect_uri, scopes, state, pkce_challenge(verifier))
        print("Open this URL to connect Xero:")
        print(authorize_url)
        if not args.no_browser:
            webbrowser.open(authorize_url)
        query = wait_for_oauth_callback(redirect_uri, args.timeout)
        if query.get("state", [""])[0] != state:
            raise XeroCliError("OAuth callback state did not match")
        if "error" in query:
            raise XeroCliError(query.get("error_description", query["error"])[0])
        code = query.get("code", [None])[0]
        if not code:
            raise XeroCliError("OAuth callback did not include a code")
        profile.update({"client_id": client_id, "redirect_uri": redirect_uri, "scopes": scopes})
        if client_secret_env:
            profile["client_secret_env"] = client_secret_env
        profile["token"] = exchange_code_for_token(client_id, redirect_uri, code, verifier, get_client_secret(profile))
        profile["tenants"] = xero_connections(profile["token"]["access_token"])
        if profile["tenants"] and not profile.get("active_tenant_id"):
            profile["active_tenant_id"] = profile["tenants"][0].get("tenantId")
        save_config(config_path, config)
        emit({"connected": args.profile, "tenants": profile.get("tenants", []), "config": str(config_path)}, args.json)
        return

    if args.auth_command == "refresh":
        refresh_access_token(profile)
        save_config(config_path, config)
        emit({"refreshed": args.profile, "expires_at": profile["token"].get("expires_at")}, args.json)
        return

    if args.auth_command == "tenants":
        access_token = ensure_token(profile, config_path, config)
        profile["tenants"] = xero_connections(access_token)
        save_config(config_path, config)
        emit(profile["tenants"], True)
        return

    if args.auth_command == "select":
        needle = args.tenant.lower()
        tenants = profile.get("tenants") or []
        for tenant in tenants:
            if needle in {str(tenant.get("tenantId", "")).lower(), str(tenant.get("tenantName", "")).lower()}:
                profile["active_tenant_id"] = tenant.get("tenantId")
                save_config(config_path, config)
                emit({"selected": tenant}, args.json)
                return
        raise XeroCliError(f"No known tenant matched '{args.tenant}'. Run auth tenants first.")

    if args.auth_command == "status":
        token = profile.get("token") or {}
        safe = {
            "profile": args.profile,
            "configured": bool(profile.get("client_id")),
            "connected": bool(token.get("access_token")),
            "token_expired": token_expired(profile) if token else None,
            "expires_at": token.get("expires_at"),
            "active_tenant_id": profile.get("active_tenant_id"),
            "tenants": profile.get("tenants", []),
            "scopes": profile.get("scopes", []),
            "config": str(config_path),
        }
        emit(safe, True)
        return

    if args.auth_command == "disconnect":
        if not args.yes:
            raise XeroCliError("Disconnect removes local Xero tokens. Re-run with --yes to confirm.")
        config.get("profiles", {}).pop(args.profile, None)
        save_config(config_path, config)
        emit({"disconnected": args.profile}, args.json)
        return

    raise XeroCliError("Unknown auth command")


def command_request(args: argparse.Namespace) -> None:
    method = args.method.upper()
    payload = read_json_body(args.body, args.body_file)
    if method in MUTATING_METHODS and not args.yes and not args.dry_run:
        raise XeroCliError("Write requests require --yes, or use --dry-run to inspect the request first.")
    config_path = Path(args.config).expanduser() if args.config else default_config_path()
    config = load_config(config_path)
    profile = get_profile(config, args.profile)
    result = api_request(
        profile,
        config,
        config_path,
        method,
        args.path,
        tenant_id=args.tenant,
        params=parse_query_pairs(args.param),
        payload=payload,
        idempotency_key=args.idempotency_key,
        dry_run=args.dry_run,
    )
    if args.out:
        Path(args.out).expanduser().write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        emit({"wrote": args.out}, True)
    else:
        emit(result, True)


def command_list(args: argparse.Namespace) -> None:
    config_path = Path(args.config).expanduser() if args.config else default_config_path()
    config = load_config(config_path)
    profile = get_profile(config, args.profile)
    records = fetch_resource_records(args, profile, config, config_path)
    write_records(records, args.out, args.format, args.json)


def command_report(args: argparse.Namespace) -> None:
    config_path = Path(args.config).expanduser() if args.config else default_config_path()
    config = load_config(config_path)
    profile = get_profile(config, args.profile)
    params = parse_query_pairs(args.param)
    for key, value in (
        ("fromDate", args.from_date),
        ("toDate", args.to_date),
        ("date", args.date),
        ("periods", args.periods),
        ("timeframe", args.timeframe),
    ):
        if value:
            params[key] = value
    payload = api_request(profile, config, config_path, "GET", REPORT_DEFS[args.report], tenant_id=args.tenant, params=params)
    if args.out:
        Path(args.out).expanduser().write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        emit({"wrote": args.out}, True)
    else:
        emit(payload, True)


def command_export(args: argparse.Namespace) -> None:
    args.all_pages = True
    args.resource = args.dataset
    command_list(args)


def command_snapshot(args: argparse.Namespace) -> None:
    config_path = Path(args.config).expanduser() if args.config else default_config_path()
    config = load_config(config_path)
    profile = get_profile(config, args.profile)

    def make_args(resource: str) -> argparse.Namespace:
        return argparse.Namespace(
            resource=resource,
            where=None,
            from_date=args.from_date,
            to_date=args.to_date,
            status=None,
            order=None,
            page=1,
            max_pages=args.max_pages,
            all_pages=True,
            param=[],
            tenant=args.tenant,
        )

    organisation = api_request(profile, config, config_path, "GET", "Organisation", tenant_id=args.tenant)
    invoices = fetch_resource_records(make_args("invoices"), profile, config, config_path)
    accounts = fetch_resource_records(make_args("accounts"), profile, config, config_path)
    bank_transactions = fetch_resource_records(make_args("bank-transactions"), profile, config, config_path)
    analysis = analyze_invoices(invoices)
    snapshot = {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "profile": args.profile,
        "tenant_id": active_tenant_id(profile, args.tenant),
        "filters": {"from": args.from_date, "to": args.to_date},
        "data": {
            "Organisation": organisation.get("Organisations", organisation) if isinstance(organisation, dict) else organisation,
            "Accounts": accounts,
            "Invoices": invoices,
            "BankTransactions": bank_transactions,
        },
        "analysis": analysis,
    }
    if args.out:
        Path(args.out).expanduser().write_text(json.dumps(snapshot, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        emit({"wrote": args.out, "invoices": len(invoices), "bank_transactions": len(bank_transactions)}, True)
    else:
        emit(snapshot, True)


def command_analyze(args: argparse.Namespace) -> None:
    invoices, existing = load_analysis_input(args.input)
    analysis = existing or analyze_invoices(invoices)
    if args.out:
        Path(args.out).expanduser().write_text(json.dumps(analysis, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        emit({"wrote": args.out}, True)
    elif args.json:
        emit(analysis, True)
    else:
        print(render_human_analysis(analysis))


def command_chart(args: argparse.Namespace) -> None:
    invoices, existing = load_analysis_input(args.input)
    analysis = existing or analyze_invoices(invoices)
    series = metric_series(analysis, args.metric)
    svg = svg_chart(series, f"Xero monthly {args.metric}")
    if args.out:
        Path(args.out).expanduser().write_text(svg, encoding="utf-8")
        emit({"wrote": args.out, "points": len(series)}, True)
    else:
        print(svg)


def command_template(args: argparse.Namespace) -> None:
    if args.kind == "contact":
        payload = contact_template()
    elif args.kind == "sales-invoice":
        payload = invoice_template("ACCREC")
    elif args.kind == "bill":
        payload = invoice_template("ACCPAY")
    elif args.kind == "payment":
        payload = payment_template()
    else:
        raise XeroCliError(f"Unknown template kind: {args.kind}")
    emit(payload, True)


def build_parser() -> argparse.ArgumentParser:
    def add_common_options(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--profile", default=argparse.SUPPRESS, help="Local Xero profile name")
        command_parser.add_argument("--config", default=argparse.SUPPRESS, help="Path to local Xero config JSON")
        command_parser.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Emit JSON when a command has a human-readable default",
        )

    parser = argparse.ArgumentParser(description="Codex helper for Xero OAuth, Accounting API access, exports, and analysis.")
    parser.add_argument("--profile", default="default", help="Local Xero profile name")
    parser.add_argument("--config", help="Path to local Xero config JSON")
    parser.add_argument("--json", action="store_true", help="Emit JSON when a command has a human-readable default")
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth = subparsers.add_parser("auth", help="Configure and manage Xero OAuth connections")
    add_common_options(auth)
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    configure = auth_sub.add_parser("configure", help="Store app client settings without logging in")
    add_common_options(configure)
    configure.add_argument("--client-id", required=True)
    configure.add_argument("--redirect-uri", default="http://localhost:45009/callback")
    configure.add_argument("--client-secret-env", help="Environment variable containing a confidential app secret")
    configure.add_argument("--scope", action="append", help="OAuth scope string. Can be repeated.")
    configure.add_argument("--write-scopes", action="store_true", help="Add write scopes for guarded write operations")
    configure.set_defaults(func=command_auth)

    login = auth_sub.add_parser("login", help="Run OAuth 2.0 authorization code + PKCE login")
    add_common_options(login)
    login.add_argument("--client-id")
    login.add_argument("--redirect-uri", default="http://localhost:45009/callback")
    login.add_argument("--client-secret-env", help="Environment variable containing a confidential app secret")
    login.add_argument("--scope", action="append", help="OAuth scope string. Can be repeated.")
    login.add_argument("--write-scopes", action="store_true", help="Add write scopes for guarded write operations")
    login.add_argument("--timeout", type=int, default=180)
    login.add_argument("--no-browser", action="store_true")
    login.set_defaults(func=command_auth)

    for name in ("refresh", "tenants", "status"):
        sub = auth_sub.add_parser(name)
        add_common_options(sub)
        sub.set_defaults(func=command_auth)
    select = auth_sub.add_parser("select", help="Select an active Xero tenant by ID or exact name")
    add_common_options(select)
    select.add_argument("tenant")
    select.set_defaults(func=command_auth)
    disconnect = auth_sub.add_parser("disconnect", help="Remove local tokens for a profile")
    add_common_options(disconnect)
    disconnect.add_argument("--yes", action="store_true")
    disconnect.set_defaults(func=command_auth)

    request = subparsers.add_parser("request", help="Call a Xero Accounting API endpoint")
    add_common_options(request)
    request.add_argument("method", choices=["GET", "POST", "PUT", "PATCH", "DELETE", "get", "post", "put", "patch", "delete"])
    request.add_argument("path", help="Accounting API path, e.g. Invoices or /Contacts")
    request.add_argument("--tenant")
    request.add_argument("--param", action="append", help="Query parameter as key=value. Can be repeated.")
    request.add_argument("--body", help="JSON request body")
    request.add_argument("--body-file", help="Path to JSON request body")
    request.add_argument("--idempotency-key")
    request.add_argument("--dry-run", action="store_true")
    request.add_argument("--yes", action="store_true", help="Confirm a write request")
    request.add_argument("--out")
    request.set_defaults(func=command_request)

    listing = subparsers.add_parser("list", help="List a common Xero Accounting API resource")
    add_common_options(listing)
    listing.add_argument("resource", choices=sorted(RESOURCE_DEFS))
    listing.add_argument("--tenant")
    listing.add_argument("--where")
    listing.add_argument("--order")
    listing.add_argument("--from", dest="from_date")
    listing.add_argument("--to", dest="to_date")
    listing.add_argument("--status")
    listing.add_argument("--param", action="append")
    listing.add_argument("--page", type=int)
    listing.add_argument("--all-pages", action="store_true")
    listing.add_argument("--max-pages", type=int, default=100)
    listing.add_argument("--format", choices=["json", "csv"], default="json")
    listing.add_argument("--out")
    listing.set_defaults(func=command_list)

    report = subparsers.add_parser("report", help="Fetch a common Xero report")
    add_common_options(report)
    report.add_argument("report", choices=sorted(REPORT_DEFS))
    report.add_argument("--tenant")
    report.add_argument("--from", dest="from_date")
    report.add_argument("--to", dest="to_date")
    report.add_argument("--date")
    report.add_argument("--periods")
    report.add_argument("--timeframe")
    report.add_argument("--param", action="append")
    report.add_argument("--out")
    report.set_defaults(func=command_report)

    export = subparsers.add_parser("export", help="Export a resource to JSON or CSV")
    add_common_options(export)
    export.add_argument("dataset", choices=sorted(RESOURCE_DEFS))
    export.add_argument("--tenant")
    export.add_argument("--where")
    export.add_argument("--order")
    export.add_argument("--from", dest="from_date")
    export.add_argument("--to", dest="to_date")
    export.add_argument("--status")
    export.add_argument("--param", action="append")
    export.add_argument("--page", type=int)
    export.add_argument("--max-pages", type=int, default=100)
    export.add_argument("--format", choices=["json", "csv"], default="json")
    export.add_argument("--out", required=True)
    export.set_defaults(func=command_export)

    snapshot = subparsers.add_parser("snapshot", help="Fetch organisation, accounts, invoices, bank transactions, and analysis")
    add_common_options(snapshot)
    snapshot.add_argument("--tenant")
    snapshot.add_argument("--from", dest="from_date")
    snapshot.add_argument("--to", dest="to_date")
    snapshot.add_argument("--max-pages", type=int, default=100)
    snapshot.add_argument("--out")
    snapshot.set_defaults(func=command_snapshot)

    analyze = subparsers.add_parser("analyze", help="Analyze exported invoices or a snapshot")
    add_common_options(analyze)
    analyze.add_argument("--input", required=True)
    analyze.add_argument("--out")
    analyze.set_defaults(func=command_analyze)

    chart = subparsers.add_parser("chart", help="Generate an SVG chart from exported invoices or a snapshot")
    add_common_options(chart)
    chart.add_argument("--input", required=True)
    chart.add_argument("--metric", choices=["sales", "bills", "net"], default="sales")
    chart.add_argument("--out")
    chart.set_defaults(func=command_chart)

    template = subparsers.add_parser("template", help="Print safe starter payloads for common write operations")
    add_common_options(template)
    template.add_argument("kind", choices=["contact", "sales-invoice", "bill", "payment"])
    template.set_defaults(func=command_template)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except XeroCliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
