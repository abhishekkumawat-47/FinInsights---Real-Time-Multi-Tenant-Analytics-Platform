#!/usr/bin/env python3
"""
JavaBank User Lookup Script
===========================
Connects to the JavaBank Supabase PostgreSQL database and retrieves
comprehensive user information by email address.

Usage:
    python javabank_user_lookup.py "user@example.com"

Requirements:
    pip install psycopg2-binary tabulate
"""

import sys
import os
import io
import json
from datetime import datetime

# Fix Windows console encoding to support Unicode/emoji output
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("❌ psycopg2 is not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

try:
    from tabulate import tabulate
except ImportError:
    tabulate = None
    print("⚠️  tabulate not installed (pip install tabulate). Output will use basic formatting.\n")


# ─────────────────────────────────────────────────────────────
# Database connection configuration (Supabase / JavaBank)
# ─────────────────────────────────────────────────────────────
# You can override these via environment variables if you prefer.
DB_HOST = os.environ.get("JAVABANK_DB_HOST", "aws-1-ap-south-1.pooler.supabase.com")
DB_PORT = int(os.environ.get("JAVABANK_DB_PORT", "5432"))
DB_NAME = os.environ.get("JAVABANK_DB_NAME", "postgres")
DB_USER = os.environ.get("JAVABANK_DB_USER", "postgres.vveddtextpaviyunihwh")
DB_PASS = os.environ.get("JAVABANK_DB_PASS", "abhishekkumawat")


def get_connection():
    """Create and return a database connection."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        sslmode="require",
        connect_timeout=15,
    )


def fmt_json(data):
    """Pretty-format JSON data."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return data
    return json.dumps(data, indent=2, default=str)


def fmt_date(dt):
    """Format datetime nicely."""
    if dt is None:
        return "—"
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return str(dt)


def print_section(title: str):
    """Print a styled section header."""
    width = 70
    print(f"\n{'═' * width}")
    print(f"  {title}")
    print(f"{'═' * width}")


def print_table(headers, rows):
    """Print a table using tabulate or fallback."""
    if tabulate:
        print(tabulate(rows, headers=headers, tablefmt="fancy_grid", numalign="right"))
    else:
        # Simple fallback
        col_widths = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=0)) for i, h in enumerate(headers)]
        header_line = " | ".join(str(h).ljust(w) for h, w in zip(headers, col_widths))
        print(header_line)
        print("-+-".join("-" * w for w in col_widths))
        for row in rows:
            print(" | ".join(str(c).ljust(w) for c, w in zip(row, col_widths)))


def lookup_user(email: str):
    """Main lookup function — fetches all relevant data for a user email."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ── 1. Customer profile ──────────────────────────────────
    print_section(f"🔍  Looking up: {email}")

    cur.execute(
        """
        SELECT c.*, t.name AS tenant_name, t.ifscprefix, t.branchcode
        FROM "Customer" c
        LEFT JOIN "Tenant" t ON c.tenantid = t.id
        WHERE c.email = %s
        """,
        (email,),
    )
    customer = cur.fetchone()

    if not customer:
        print(f"\n  ❌  No customer found with email: {email}")
        cur.close()
        conn.close()
        return {
            "email": email,
            "found": False,
            "promoted": False,
            "already_admin": False,
        }

    customer_id = customer["id"]
    old_role = customer["role"]

    # ── Promote user to ADMIN ────────────────────────────────
    if old_role != "ADMIN":
        cur.execute(
            """UPDATE "Customer" SET role = 'ADMIN' WHERE id = %s""",
            (customer_id,),
        )
        conn.commit()
        print(f"\n  🔄  Role updated: {old_role} → ADMIN")
        promoted = True
    else:
        print(f"\n  ✅  User is already ADMIN")
        promoted = False

    print_section("👤  Customer Profile")
    profile_rows = [
        ["Name", customer["name"]],
        ["Email", customer["email"]],
        ["Phone", customer["phone"]],
        ["Customer Type", customer["customertype"]],
        ["Role", f"ADMIN (was: {old_role})" if old_role != "ADMIN" else "ADMIN"],
        ["Date of Birth", fmt_date(customer["dateofbirth"])],
        ["PAN", customer["pan"]],
        ["KYC Status", customer["kycstatus"]],
        ["KYC Completed At", fmt_date(customer.get("kyccompletedat"))],
        ["Last Login", fmt_date(customer.get("lastlogin"))],
        ["Tenant", f"{customer['tenant_name']} ({customer["tenantid"]})"],
    ]
    print_table(["Field", "Value"], profile_rows)

    # Address
    if customer.get("address"):
        print(f"\n  📍 Address: {fmt_json(customer['address'])}")

    # Settings
    if customer.get("settingconfig"):
        print(f"\n  ⚙️  Settings: {fmt_json(customer["settingconfig"])}")

    # ── 2. Accounts ──────────────────────────────────────────
    cur.execute(
        """
        SELECT accno, accounttype, balance, status, createdon, updatedon
        FROM "Account"
        WHERE customerid = %s
        ORDER BY createdon DESC
        """,
        (customer_id,),
    )
    accounts = cur.fetchall()

    print_section(f"🏦  Accounts ({len(accounts)})")
    if accounts:
        acc_rows = [
            [a["accno"], a["accounttype"], f"₹{a['balance']:,.2f}", "✅" if a["status"] else "❌", fmt_date(a["createdon"])]
            for a in accounts
        ]
        print_table(["Account No", "Type", "Balance", "Active", "Created"], acc_rows)
    else:
        print("  No accounts found.")

    # ── 3. Recent Transactions (last 20) ─────────────────────
    acc_nos = [a["accno"] for a in accounts]
    if acc_nos:
        cur.execute(
            """
            SELECT t.id, t.transactiontype, t.senderaccno, t.receiveraccno,
                   t.amount, t.status, t.category, t.channel, t.description, t.timestamp
            FROM "Transaction" t
            WHERE t.senderaccno = ANY(%s) OR t.receiveraccno = ANY(%s)
            ORDER BY t.timestamp DESC
            LIMIT 20
            """,
            (acc_nos, acc_nos),
        )
        transactions = cur.fetchall()

        print_section(f"💸  Recent Transactions ({len(transactions)})")
        if transactions:
            txn_rows = [
                [
                    t["transactiontype"],
                    t["senderaccno"][:12] + "…" if len(t["senderaccno"]) > 12 else t["senderaccno"],
                    t["receiveraccno"][:12] + "…" if len(t["receiveraccno"]) > 12 else t["receiveraccno"],
                    f"₹{t['amount']:,.2f}",
                    t["status"],
                    t["category"],
                    t["channel"],
                    fmt_date(t["timestamp"]),
                ]
                for t in transactions
            ]
            print_table(
                ["Type", "From", "To", "Amount", "Status", "Category", "Channel", "Time"],
                txn_rows,
            )
        else:
            print("  No transactions found.")

    # ── 4. Loans ─────────────────────────────────────────────
    if acc_nos:
        cur.execute(
            """
            SELECT l.id, l.loantype, l.principalamount, l.interestrate, l.interestamount,
                   l.term, l.dueamount, l.status, l.startdate, l.enddate
            FROM "Loan" l
            WHERE l.accno = ANY(%s)
            ORDER BY l.startdate DESC
            """,
            (acc_nos,),
        )
        loans = cur.fetchall()

        print_section(f"📋  Loans ({len(loans)})")
        if loans:
            loan_rows = [
                [
                    lo["loantype"],
                    f"₹{lo["principalamount"]:,.2f}",
                    f"{lo["interestrate"]}%",
                    f"₹{lo["dueamount"]:,.2f}",
                    f"{lo['term']} mo",
                    "✅" if lo["status"] else "❌",
                    fmt_date(lo["startdate"]),
                    fmt_date(lo["enddate"]),
                ]
                for lo in loans
            ]
            print_table(
                ["Type", "Principal", "Rate", "Due", "Term", "Active", "Start", "End"],
                loan_rows,
            )
        else:
            print("  No loans found.")

    # ── 5. Loan Applications ─────────────────────────────────
    cur.execute(
        """
        SELECT la.loantype, la.principalamount, la.term, la.interestrate,
               la.status, la.kycstep, la.notes, la.createdon
        FROM "LoanApplication" la
        WHERE la.customerid = %s
        ORDER BY la.createdon DESC
        """,
        (customer_id,),
    )
    loan_apps = cur.fetchall()

    print_section(f"📝  Loan Applications ({len(loan_apps)})")
    if loan_apps:
        app_rows = [
            [
                la["loantype"],
                f"₹{la["principalamount"]:,.2f}",
                f"{la["interestrate"]}%",
                f"{la['term']} mo",
                la["status"],
                la["kycstep"],
                fmt_date(la["createdon"]),
            ]
            for la in loan_apps
        ]
        print_table(["Type", "Amount", "Rate", "Term", "Status", "KYC Step", "Applied On"], app_rows)
    else:
        print("  No loan applications found.")

    # ── 6. Payees ────────────────────────────────────────────
    cur.execute(
        """
        SELECT p.name, p.payeeaccno, p.payeeifsc, p.payeetype
        FROM "Payee" p
        WHERE p.payercustomerid = %s
        """,
        (customer_id,),
    )
    payees = cur.fetchall()

    print_section(f"👥  Registered Payees ({len(payees)})")
    if payees:
        payee_rows = [[p["name"], p["payeeaccno"], p["payeeifsc"], p["payeetype"]] for p in payees]
        print_table(["Name", "Account No", "IFSC", "Type"], payee_rows)
    else:
        print("  No payees registered.")

    # ── 7. User Licenses ─────────────────────────────────────
    cur.execute(
        """
        SELECT ul.featureid, ul.amount, ul.expirydate, ul.active, ul.createdon
        FROM "UserLicense" ul
        WHERE ul.customerid = %s
        ORDER BY ul.createdon DESC
        """,
        (customer_id,),
    )
    licenses = cur.fetchall()

    print_section(f"🔑  Feature Licenses ({len(licenses)})")
    if licenses:
        lic_rows = [
            [li["featureid"], f"₹{li['amount']:,.2f}", "✅" if li["active"] else "❌", fmt_date(li["expirydate"]), fmt_date(li["createdon"])]
            for li in licenses
        ]
        print_table(["Feature", "Amount", "Active", "Expires", "Created"], lic_rows)
    else:
        print("  No feature licenses.")

    # ── 8. Recent Events (last 15) ───────────────────────────
    cur.execute(
        """
        SELECT e.eventname, e.tenantid, e.userid, e.metadata, e.timestamp
        FROM "Event" e
        WHERE e.customerid = %s
        ORDER BY e.timestamp DESC
        LIMIT 15
        """,
        (customer_id,),
    )
    events = cur.fetchall()

    print_section(f"📊  Recent Telemetry Events ({len(events)})")
    if events:
        event_rows = [
            [ev["eventname"], ev["tenantid"], ev["userid"][:16] + "…" if len(ev["userid"]) > 16 else ev["userid"], fmt_date(ev["timestamp"])]
            for ev in events
        ]
        print_table(["Event", "Tenant", "User ID", "Timestamp"], event_rows)
    else:
        print("  No events found.")

    # ── 9. Location History (last 10) ────────────────────────
    cur.execute(
        """
        SELECT ul.country, ul.city, ul.devicetype, ul.platform, ul.ip, ul.timestamp
        FROM "UserLocation" ul
        WHERE ul.customerid = %s
        ORDER BY ul.timestamp DESC
        LIMIT 10
        """,
        (customer_id,),
    )
    locations = cur.fetchall()

    print_section(f"🌍  Login Locations ({len(locations)})")
    if locations:
        loc_rows = [
            [lo.get("country", "—"), lo.get("city", "—"), lo.get("devicetype", "—"), lo.get("platform", "—"), lo.get("ip", "—"), fmt_date(lo["timestamp"])]
            for lo in locations
        ]
        print_table(["Country", "City", "Device", "Platform", "IP", "Time"], loc_rows)
    else:
        print("  No location data.")

    # ── Summary ──────────────────────────────────────────────
    total_balance = sum(a["balance"] for a in accounts)
    print_section("📈  Quick Summary")
    summary = [
        ["Total Accounts", len(accounts)],
        ["Combined Balance", f"₹{total_balance:,.2f}"],
        ["Total Transactions (shown)", len(transactions) if acc_nos else 0],
        ["Active Loans", sum(1 for lo in loans if lo["status"]) if acc_nos and loans else 0],
        ["Pending Applications", sum(1 for la in loan_apps if la["status"] in ("PENDING", "KYC_PENDING", "UNDER_REVIEW"))],
        ["Licensed Features", sum(1 for li in licenses if li["active"])],
        ["KYC Status", customer["kycstatus"]],
    ]
    print_table(["Metric", "Value"], summary)

    print(f"\n{'─' * 70}")
    print(f"  ✅  Lookup complete for {email}")
    print(f"{'─' * 70}\n")

    cur.close()
    conn.close()
    return {
        "email": email,
        "found": True,
        "promoted": promoted,
        "already_admin": not promoted,
    }


def list_tenant_admins(tenant: str):
    """Query database and return all users with ADMIN role for a tenant."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute(
            """
            SELECT email FROM "Customer"
            WHERE role = 'ADMIN' AND tenantid = (SELECT id FROM "Tenant" WHERE name = %s LIMIT 1)
            ORDER BY email
            """,
            (tenant,),
        )
        admins = [row["email"] for row in cur.fetchall()]
        return {"admins": admins}
    except Exception as e:
        return {"admins": [], "error": str(e)}
    finally:
        cur.close()
        conn.close()


def main():
    args = [arg.strip() for arg in sys.argv[1:] if arg.strip()]

    if not args:
        print("Usage:")
        print("  1. Lookup user: python javabank_user_lookup.py <email> [--json]")
        print('     Example: python javabank_user_lookup.py "omeshmehta03@gmail.com" --json')
        print("  2. List admins: python javabank_user_lookup.py --list-admins <tenant>")
        print('     Example: python javabank_user_lookup.py --list-admins jbank')
        sys.exit(1)

    # Handle --list-admins command
    if args[0] == "--list-admins":
        if len(args) < 2:
            print("Error: --list-admins requires a tenant name")
            print('Usage: python javabank_user_lookup.py --list-admins <jbank|obank>')
            sys.exit(1)

        tenant = args[1].lower().strip()
        if tenant not in ("jbank", "obank"):
            print(json.dumps({"admins": [], "error": f"Unknown tenant: {tenant}"}))
            sys.exit(1)

        try:
            print(f"\n🔗  Connecting to JavaBank Supabase database to list admins for {tenant}...")
            result = list_tenant_admins(tenant)
            print(json.dumps(result))
            sys.exit(0)
        except psycopg2.OperationalError as e:
            print(json.dumps({"admins": [], "error": str(e)}))
            sys.exit(1)
        except Exception as e:
            print(json.dumps({"admins": [], "error": str(e)}))
            sys.exit(1)

    # Handle email lookup (original behavior)
    json_mode = "--json" in args
    email_args = [arg for arg in args if arg != "--json"]

    if not email_args:
        print("Usage: python javabank_user_lookup.py <email> [--json]")
        print('Example: python javabank_user_lookup.py "omeshmehta03@gmail.com" --json')
        sys.exit(1)

    email = email_args[0]
    if not email or "@" not in email:
        if json_mode:
            print(json.dumps({"email": email, "found": False, "error": "invalid_email"}))
        else:
            print(f"❌ Invalid email address: {email}")
        sys.exit(1)

    if not json_mode:
        print(f"\n🔗  Connecting to JavaBank Supabase database...")
    try:
        result = lookup_user(email)
        if result is None:
            result = {
                "email": email,
                "found": False,
                "promoted": False,
                "already_admin": False,
            }
        if json_mode:
            print(json.dumps(result))
        sys.exit(0 if result.get("found") else 2)
    except psycopg2.OperationalError as e:
        if json_mode:
            print(json.dumps({"email": email, "found": False, "error": str(e)}))
        else:
            print(f"\n❌ Connection failed: {e}")
            print("   Check network/VPN and that the Supabase project is active.")
        sys.exit(1)
    except Exception as e:
        if json_mode:
            print(json.dumps({"email": email, "found": False, "error": str(e)}))
        else:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
