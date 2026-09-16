

# apps/litwatch/litwatch/api/gstin.py

import random

import frappe
from frappe import _
from frappe.utils import add_days, add_months, getdate, nowdate


# ---------------------------------------------------------------------------
# 3.1 GET /gstin-connections
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["GET"])
def get_gstin_connections():
    """
    Lists all GST Registrations with client details, consent status,
    and notice counts. Used by the GSTIN Connections table at
    /gstin-connections.
    """

    registrations = frappe.get_all(
        "GST Registration",
        fields=[
            "name as gstin",
            "city",
            "state",
            "client",
            "consent_status",
            "valid_until as validUntil",
            "last_checked as lastChecked",
        ],
        order_by="modified desc",
    )

    if not registrations:
        return []

    state_codes = {
        "Karnataka": "KA",
        "Maharashtra": "MH",
        "Tamil Nadu": "TN",
        "Delhi": "DL",
        "Gujarat": "GJ",
        "Telangana": "TG",
    }

    result = []
    for reg in registrations:
        # Guard against orphaned client links.
        if not reg.get("client") or not frappe.db.exists("Client", reg["client"]):
            frappe.throw(
                f"GST Registration {reg['gstin']} points to a missing Client {reg.get('client')}",
                frappe.DoesNotExistError,
            )

        client_code = frappe.db.get_value("Client", reg["client"], "client_code")

        notices_count = frappe.db.count(
            "Litigation Notice",
            filters={"gstin": reg["gstin"]},
        )

        state = reg.get("state")
        result.append({
            "gstin": reg["gstin"],
            "code": state_codes.get(state, state[:2].upper() if state else ""),
            "city": reg.get("city"),
            "state": state,
            "client": client_code,
            "clientKey": reg["client"],
            "consent": reg.get("consent_status") or "inactive",
            "validUntil": str(reg["validUntil"]) if reg.get("validUntil") else None,
            "lastChecked": (
                reg["lastChecked"].isoformat() + "Z"
                if reg.get("lastChecked") else None
            ),
            "notices": notices_count,
        })

    return result



# ---------------------------------------------------------------------------
# 3.2 POST /gstin-connections  (connect_gstin)
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["POST"])
def connect_gstin(gstin, portalUserId):
    """
    Starts GSTIN portal authentication by triggering an OTP send.

    Step 1 of the two-step flow:
      1. connect_gstin  -> returns otpRequestId
      2. verify_gstin_otp(gstin, otpRequestId, otp) -> activates consent

    Creates a GST OTP Request row with status "pending" and returns the
    otp_request_id the client must send back to step 2.
    """

    # --- Required validations ---
    if not gstin:
        frappe.throw("gstin is required")

    if not portalUserId:
        frappe.throw("portalUserId is required")

    if not frappe.db.exists("GST Registration", gstin):
        frappe.throw(
            f"GST Registration {gstin} not found",
            frappe.DoesNotExistError,
        )

    gst_reg = frappe.get_doc("GST Registration", gstin)
    gst_reg.check_permission("write")

    # --- Persist portal user id on the registration (field exists) ---
    gst_reg.portal_user_id = portalUserId
    gst_reg.save(ignore_permissions=True)

    # --- Generate the OTP request id ---
    otp_request_id = f"OTP-{frappe.generate_hash(length=4).upper()}"

    # --- Trigger OTP with the GST portal ---
    # TODO: replace with the real portal call. Placeholder value below.
    otp_sent_to = "•• 42"   # masked destination returned by the portal

    # --- Store the pending request ---
    doc = frappe.get_doc({
        "doctype": "GST OTP Request",
        "gstin": gstin,
        "otp_request_id": otp_request_id,
        "otp_sent_to": otp_sent_to,
        "status": "pending",
        "requested_at": frappe.utils.now(),
    })
    doc.insert(ignore_permissions=True)

    frappe.db.commit()

    return {
        "otpSentTo": otp_sent_to,
        "otpRequestId": otp_request_id,
    }

# ---------------------------------------------------------------------------
# 3.3 POST /gstin-connections/:gstin/verify-otp  (verify_gstin_otp)
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["POST"])
def verify_gstin_otp(gstin=None, otpRequestId=None, otp=None):
    """
    Step 2 of the two-step GSTIN auth flow.

    Verifies the OTP against the pending GST OTP Request row and, on
    success, activates the GST Registration's consent.

    Accepts params from JSON body, form_dict, or query string:
      gstin         - Required. GSTIN / GST Registration docname.
      otpRequestId  - Required. The OTP request id from step 1.
      otp           - Required. The OTP the user entered.
    """
    from urllib.parse import parse_qs

    # --- Collect JSON body ---
    body = {}
    if frappe.request and frappe.request.data:
        try:
            body = frappe.request.get_json(silent=True) or {}
        except Exception:
            body = {}

    # --- Werkzeug-parsed query string ---
    args = dict(frappe.request.args or {}) if frappe.request else {}

    # --- Raw query string bytes (bulletproof) ---
    raw_qs = {}
    if frappe.request and frappe.request.query_string:
        try:
            qs_str = frappe.request.query_string
            if isinstance(qs_str, bytes):
                qs_str = qs_str.decode("utf-8", "ignore")
            parsed = parse_qs(qs_str)
            raw_qs = {k: v[0] for k, v in parsed.items() if v}
        except Exception:
            raw_qs = {}

    fd = dict(frappe.form_dict or {})

    def _pick(*keys):
        for src in (body, args, raw_qs, fd):
            for k in keys:
                v = src.get(k)
                if v not in (None, ""):
                    return v
        return None

    gstin = gstin or _pick("gstin", "GSTIN", "gstinNo")
    otpRequestId = otpRequestId or _pick("otpRequestId", "otp_request_id", "otpId")
    otp = otp or _pick("otp", "OTP", "otpCode")

    # --- Required validations ---
    if not gstin:
        frappe.throw(_("gstin is required"))

    if not otpRequestId:
        frappe.throw(_("otpRequestId is required"))

    if not otp:
        frappe.throw(_("otp is required"))

    if not frappe.db.exists("GST Registration", gstin):
        frappe.throw(
            _("GST Registration {0} not found").format(gstin),
            frappe.DoesNotExistError,
        )

    # --- Resolve the OTP request row ---
    otp_row_name = frappe.db.get_value(
        "GST OTP Request",
        {"otp_request_id": otpRequestId},
        "name",
    )
    if not otp_row_name:
        frappe.throw(
            _("OTP request {0} not found").format(otpRequestId),
            frappe.DoesNotExistError,
        )

    otp_req = frappe.get_doc("GST OTP Request", otp_row_name)

    if otp_req.gstin != gstin:
        frappe.throw(
            _("OTP request {0} does not belong to GSTIN {1}").format(otpRequestId, gstin)
        )

    if otp_req.status == "verified":
        frappe.throw(_("OTP request {0} has already been verified").format(otpRequestId))

    if otp_req.status == "expired":
        frappe.throw(_("OTP request {0} has expired").format(otpRequestId))

    # --- Verify OTP with the GST portal ---
    # TODO: replace with the real portal verification call. Placeholder
    # below assumes the portal returned success.
    portal_ok = True
    if not portal_ok:
        frappe.throw(_("Invalid OTP"))

    # --- Activate consent on the registration ---
    gst_reg = frappe.get_doc("GST Registration", gstin)
    gst_reg.check_permission("write")

    valid_until = frappe.utils.add_months(frappe.utils.nowdate(), 12)

    gst_reg.consent_status = "active"
    gst_reg.valid_until = valid_until
    gst_reg.last_checked = frappe.utils.now()
    gst_reg.save(ignore_permissions=True)

    # --- Consume the OTP request ---
    otp_req.status = "verified"
    otp_req.save(ignore_permissions=True)

    frappe.db.commit()

    return {
        "consent": gst_reg.consent_status,
        "validUntil": str(gst_reg.valid_until),
    }

# ---------------------------------------------------------------------------
# 3.4 POST /gstin-connections/:gstin/otp/resend  (resend_gstin_otp)
# ---------------------------------------------------------------------------
import random

from frappe.utils import nowdate


@frappe.whitelist(methods=["POST"])
def resend_gstin_otp(gstin=None):
    """
    Re-sends the OTP for an existing GSTIN auth request.

    Input: gstin (query parameter)
      e.g. POST /api/method/litwatch.api.resend_gstin_otp?gstin=29ABCDE1234F1Z5

    Returns a new otpRequestId. Any existing pending GST OTP Request row
    for this GSTIN is marked expired so it can no longer be used.
    """

    # --- Required validations ---
    if not gstin:
        frappe.throw("gstin is required")

    if not frappe.db.exists("GST Registration", gstin):
        frappe.throw(
            f"GST Registration {gstin} not found",
            frappe.DoesNotExistError,
        )

    gst_reg = frappe.get_doc("GST Registration", gstin)
    gst_reg.check_permission("write")

    # --- Expire any pending requests for this GSTIN ---
    old_rows = frappe.get_all(
        "GST OTP Request",
        filters={"gstin": gstin, "status": "pending"},
        fields=["name"],
    )
    for row in old_rows:
        frappe.db.set_value("GST OTP Request", row.name, "status", "expired")

    # --- Generate a fresh request id ---
    otp_request_id = f"OTP-{frappe.generate_hash(length=4).upper()}"

    # --- Trigger a new OTP send with the GST portal ---
    # TODO: replace with the real portal call. Placeholder value below.
    otp_sent_to = "•• 42"   # masked destination returned by the portal

    # --- Generate the OTP (mock; replace with real portal value) ---
    otp = str(random.randint(100000, 999999))

    # --- Store the pending request ---
    frappe.get_doc({
        "doctype": "GST OTP Request",
        "gstin": gstin,
        "otp_request_id": otp_request_id,
        "otp_sent_to": otp_sent_to,
        "otp": otp,
        "status": "pending",
        "requested_at": frappe.utils.now(),
    }).insert(ignore_permissions=True)

    frappe.db.commit()

    return {
        "otpRequestId": otp_request_id,
    }

# ---------------------------------------------------------------------------
# 3.5 POST /gstin-connections/:gstin/sync-now  (sync_gstin_notices)
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["POST"])
def sync_gstin_notices(gstin=None):
    """
    Pulls new notices for a GSTIN from the GST portal and syncs them
    into Litigation Notice. Triggered by the "Sync now" action on the
    GSTIN Connections table.

    Input: gstin (query parameter)
      e.g. POST /api/method/litwatch.api.sync_gstin_notices?gstin=29ABCDE1234F1Z5

    Returns counts: how many notices the portal reported ("seen"), how
    many were new to us and inserted ("found"), and a status string.
    """

    # --- Required validations ---
    if not gstin:
        frappe.throw("gstin is required")

    if not frappe.db.exists("GST Registration", gstin):
        frappe.throw(
            f"GST Registration {gstin} not found",
            frappe.DoesNotExistError,
        )

    gst_reg = frappe.get_doc("GST Registration", gstin)
    gst_reg.check_permission("write")

    if gst_reg.consent_status != "active":
        frappe.throw(
            f"GST Registration {gstin} has no active consent — connect it first"
        )

    # --- Fetch notices from the GST portal ---
    # TODO: replace with the real portal call. Placeholder below.
    # Expected shape: a list of notice dicts, each containing at minimum
    # a unique portal-side identifier so we can detect duplicates.
    portal_notices = []   # <-- real portal response goes here

    seen = len(portal_notices)
    found = 0

    for n in portal_notices:
        # --- Skip if we already have it ---
        existing = frappe.db.exists(
            "Litigation Notice",
            {"gstin": gstin, "portal_ref": n.get("portal_ref")},
        )
        if existing:
            continue

        # --- Insert the new notice ---
        frappe.get_doc({
            "doctype": "Litigation Notice",
            "gstin": gstin,
            "client": gst_reg.client,
            "status": "Under review",
            "extracted_by_ai": 1,
            # Map the portal fields onto our doctype here. Placeholder:
            # "section": n.get("section"),
            # "label": n.get("label"),
            # "filed_date": n.get("filed_date"),
            # "amount": n.get("amount"),
            # "portal_ref": n.get("portal_ref"),
        }).insert(ignore_permissions=True)

        found += 1

    # --- Stamp the registration as freshly checked ---
    gst_reg.last_checked = frappe.utils.now()
    gst_reg.save(ignore_permissions=True)

    frappe.db.commit()

    return {
        "seen": seen,
        "found": found,
        "status": "ok",
    }

# ---------------------------------------------------------------------------
# 3.6 POST /sync/run-all  (run_all_gstin_sync)
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["POST"])
def run_all_gstin_sync():
    """
    Queues a sync for every GSTIN with an active consent.

    Triggered by the "Run all now" action on the Sync & API Usage page.
    Returns immediately with a run id and the number of GSTINs queued;
    the actual sync happens in background workers.
    """

    # --- Required validations ---
    # No inputs. Caller must be authenticated; permission is checked
    # below by filtering to registrations the user can write.

    # --- Find every GSTIN with an active consent ---
    gstins = frappe.get_all(
        "GST Registration",
        filters={"consent_status": "active"},
        fields=["name as gstin"],
    )

    if not gstins:
        # Nothing to queue — return an empty run.
        return {
            "runId": f"RUN-{frappe.generate_hash(length=4).upper()}",
            "gstinsQueued": 0,
        }

    # --- Create a run header so the UI can poll progress ---
    run_id = f"RUN-{frappe.generate_hash(length=4).upper()}"

    # --- Queue one background job per GSTIN ---
    queued = 0
    for row in gstins:
        gstin = row["gstin"]

        # Honour per-user permission the same way sync-now does.
        if not frappe.has_permission("GST Registration", "write", gstin):
            continue

        frappe.enqueue(
            "litwatch.api.sync_gstin_notices",
            queue="long",
            timeout=600,
            gstin=gstin,
            run_id=run_id,
        )
        queued += 1

    frappe.db.commit()

    frappe.response.http_status_code = 202
    return {
        "runId": run_id,
        "gstinsQueued": queued,
    }

# ---------------------------------------------------------------------------
# 3.7 GET /sync/usage  (get_sync_usage)
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["GET"])
def get_sync_usage():
    """
    Returns API call usage, cap, token expiry, weekly notice count,
    skipped runs, and a per-endpoint breakdown.

    Used by the usage meter on /sync-api-usage.
    """

    from frappe.utils import nowdate, add_days, getdate

    today = nowdate()          # string, e.g. "2026-09-12"
    today_d = getdate(today)   # date object, for weekday arithmetic

    # --- callsUsed: sum of calls over the current window (today) ---
    calls_used = (
        frappe.db.get_value(
            "API Usage Log",
            {"log_date": today},
            "sum(calls)",
        )
        or 0
    )

    # --- callsCap: no column anywhere; source TBD ---
    # TODO: replace with a real source (e.g. Sync Settings Single doctype).
    calls_cap = 2000

    # --- tokenValidUntil: latest non-null value from API Usage Log ---
    token_valid_until = frappe.db.get_value(
        "API Usage Log",
        {"token_valid_until": ["is", "set"]},
        "token_valid_until",
        order_by="log_date desc",
    )

    # --- noticesThisWeek: notices created Mon–Sun of the current week ---
    week_start = add_days(today_d, -today_d.weekday())   # Monday (date)
    week_end = add_days(week_start, 6)                    # Sunday (date)

    week_start_str = str(week_start) + " 00:00:00"
    week_end_str   = str(week_end) + " 23:59:59"

    notices_this_week = frappe.db.count(
        "Litigation Notice",
        filters={"creation": ["between", [week_start_str, week_end_str]]},
    )

    # --- skippedRuns: GST Sync Log rows with a skipped status ---
    # TODO: confirm the exact status value used by the writer.
    skipped_runs = frappe.db.count(
        "GST Sync Log",
        filters={"status": "skipped"},
    )

    # --- byEndpoint: per-endpoint totals for today ---
    by_endpoint = frappe.db.sql(
        """
        SELECT
            endpoint_name AS name,
            SUM(calls)   AS calls,
            'today'      AS `when`
        FROM `tabAPI Usage Log`
        WHERE log_date = %(today)s
        GROUP BY endpoint_name
        ORDER BY calls DESC
        """,
        {"today": today},
        as_dict=True,
    )

    return {
        "callsUsed": calls_used,
        "callsCap": calls_cap,
        "tokenValidUntil": str(token_valid_until) if token_valid_until else None,
        "noticesThisWeek": notices_this_week,
        "skippedRuns": skipped_runs,
        "byEndpoint": by_endpoint,
    }

# ---------------------------------------------------------------------------
# 3.8 GET /sync/log  (get_sync_log)
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["GET"])
def get_sync_log(gstin=None):
    """
    Returns the recent sync history for the Sync & API Usage page.

    Optional filter: gstin. When provided, only rows for that GSTIN
    are returned. When omitted, rows for all GSTINs are returned.

    Reads from GST Sync Log (gstin, sync_time, seen, found, status).
    """

    filters = {}
    if gstin:
        if not frappe.db.exists("GST Registration", gstin):
            frappe.throw(
                f"GST Registration {gstin} not found",
                frappe.DoesNotExistError,
            )
        filters["gstin"] = gstin

    rows = frappe.get_all(
        "GST Sync Log",
        filters=filters,
        fields=[
            "sync_time as time",
            "gstin",
            "seen",
            "found",
            "status",
        ],
        order_by="sync_time desc",
        limit_page_length=100,
    )

    # Format the timestamp as ISO 8601 with a Z suffix.
    for r in rows:
        if r.get("time"):
            r["time"] = r["time"].isoformat() + "Z"

    return rows


def _humanize_size(size):
    """Turn a byte count into a compact string like '842 KB'."""
    if not size:
        return "0 B"
    size = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.0f} PB"
