import frappe
from frappe.utils import getdate, date_diff, nowdate

# ---------------------------------------------------------------------------
# GET /notices  (get_all_notices)
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["GET"])
def get_all_notices(client=None, limit_start=0, limit_page_length=200):
    """
    Returns litigation notices across all clients, or one client.

    Query parameters (all optional):
      client            - Client key, e.g. ACME001. Filters to one client.
      limit_start       - Offset for paging. Default 0.
      limit_page_length - Page size. Default 200, max 500.
    """

    # --- Normalise paging params ---
    try:
        limit_start = int(limit_start)
    except (TypeError, ValueError):
        limit_start = 0
    if limit_start < 0:
        limit_start = 0

    try:
        limit_page_length = int(limit_page_length)
    except (TypeError, ValueError):
        limit_page_length = 200
    if limit_page_length < 1:
        limit_page_length = 1
    if limit_page_length > 500:
        limit_page_length = 500

    # --- Validate client if supplied ---
    filters = {}
    if client:
        if not frappe.db.exists("Client", client):
            frappe.throw(
                f"Client {client} not found",
                frappe.DoesNotExistError,
            )
        filters["client"] = client

    # --- Fetch notices ---
    rows = frappe.get_list(
        "Litigation Notice",
        filters=filters,
        fields=[
            "name as id",
            "notice_id",
            "client",
            "gstin",
            "section",
            "notice_type",
            "label",
            "state",
            "city",
            "fy",
            "amount",
            "filed_date",
            "risk",
            "status",
            "due_date",
            "source_pdf_url",
            "extracted_by_ai",
            "resolved_on",
        ],
        order_by="creation desc",
        limit_start=limit_start,
        limit_page_length=limit_page_length,
    )

    return rows

@frappe.whitelist()
def get_client_notices(client_key, risk=None, status=None, bucket=None, sort="due_date asc"):
    filters = {"client": client_key}

    if risk:
        filters["risk"] = risk
    if status:
        filters["status"] = status

    today = getdate(nowdate())

    if bucket == "overdue":
        filters["due_date"] = ["<", today]
    elif bucket == "due7":
        filters["due_date"] = ["between", [today, frappe.utils.add_days(today, 7)]]
    elif bucket == "due30":
        filters["due_date"] = ["between", [today, frappe.utils.add_days(today, 30)]]

    notices = frappe.get_all(
        "Litigation Notice",
        filters=filters,
        fields=[
            "name as id",
            "gstin",
            "section",
            "notice_type as type",
            "label",
            "state",
            "city",
            "fy",
            "amount",
            "filed_date as filed",
            "risk",
            "status",
            "due_date",
            "consultant"
        ],
        order_by=sort
    )

    for n in notices:
        # compute dueInDays from due_date
        if n.get("due_date"):
            n["dueInDays"] = date_diff(n["due_date"], today)
        else:
            n["dueInDays"] = None
        del n["due_date"]

        # attach consultant object
        if n.get("consultant"):
            c = frappe.db.get_value(
                "Consultant", n["consultant"], ["initials", "consultant_name", "color"], as_dict=True
            )
            if c:
                n["consultant"] = {"initials": c.initials, "name": c.consultant_name, "color": c.color}

    return notices

@frappe.whitelist()
def get_client_notice_detail(client_key, id):
    notice = frappe.db.get_value(
        "Litigation Notice",
        {"name": id, "client": client_key},
        [
            "name as id",
            "gstin",
            "section",
            "notice_type as type",
            "label",
            "state",
            "city",
            "fy",
            "amount",
            "filed_date as filed",
            "risk",
            "status",
            "due_date",
            "consultant",
            "source_pdf_url",
            "extracted_by_ai",
            "resolved_on"
        ],
        as_dict=True
    )

    if not notice:
        frappe.throw(f"Notice {id} not found for client {client_key}", frappe.DoesNotExistError)

    # compute dueInDays
    if notice.get("due_date"):
        notice["dueInDays"] = date_diff(notice["due_date"], getdate(nowdate()))
    else:
        notice["dueInDays"] = None
    del notice["due_date"]

    # attach consultant object
    if notice.get("consultant"):
        c = frappe.db.get_value(
            "Consultant", notice["consultant"], ["initials", "consultant_name", "color"], as_dict=True
        )
        if c:
            notice["consultant"] = {"initials": c.initials, "name": c.consultant_name, "color": c.color}

    # attach timeline child table rows
    timeline = frappe.get_all(
        "Notice Timeline",  # confirm this is the correct child doctype name
        filters={"parent": id},
        fields=["*"],
        order_by="idx asc"
    )
    notice["timeline"] = timeline

    return notice

@frappe.whitelist()
def get_client_notice_stats(client_key):
    today = getdate(nowdate())
    due_soon_cutoff = frappe.utils.add_days(today, 7)  # adjust window if "due soon" means something else

    total = frappe.db.count("Litigation Notice", filters={"client": client_key})

    critical = frappe.db.count(
        "Litigation Notice",
        filters={"client": client_key, "risk": "critical"}
    )

    total_demand = frappe.db.get_value(
        "Litigation Notice",
        {"client": client_key},
        "sum(amount)"
    ) or 0

    overdue = frappe.db.count(
        "Litigation Notice",
        filters={"client": client_key, "due_date": ["<", today], "status": ["!=", "Resolved"]}
    )

    due_soon = frappe.db.count(
        "Litigation Notice",
        filters={
            "client": client_key,
            "due_date": ["between", [today, due_soon_cutoff]],
            "status": ["!=", "Resolved"]
        }
    )

    return {
        "total": total,
        "critical": critical,
        "totalDemand": total_demand,
        "overdue": overdue,
        "dueSoon": due_soon
    }

@frappe.whitelist()
def get_client_notice_trend(client_key):
    from frappe.utils import add_months, get_first_day, get_last_day

    today = getdate(nowdate())
    months = []
    for i in range(11, -1, -1):  # last 12 months, oldest to newest
        month_date = add_months(today, -i)
        months.append({
            "start": get_first_day(month_date),
            "end": get_last_day(month_date)
        })

    received = []
    resolved = []

    for m in months:
        received_count = frappe.db.count(
            "Litigation Notice",
            filters={
                "client": client_key,
                "filed_date": ["between", [m["start"], m["end"]]]
            }
        )
        received.append(received_count)

        resolved_count = frappe.db.count(
            "Litigation Notice",
            filters={
                "client": client_key,
                "resolved_on": ["between", [m["start"], m["end"]]]
            }
        )
        resolved.append(resolved_count)

    return {
        "received": received,
        "resolved": resolved
    }

@frappe.whitelist()
def get_client_locations(client_key):
    notices = frappe.get_all(
        "Litigation Notice",
        filters={"client": client_key},
        fields=["state", "city", "gstin", "amount", "risk", "consultant"]
    )

    if not notices:
        return []

    grouped = {}
    for n in notices:
        state = n.get("state")
        if not state:
            continue
        if state not in grouped:
            grouped[state] = {
                "name": state,
                "city": n.get("city"),
                "gstin": n.get("gstin"),
                "count": 0,
                "demand": 0,
                "risk_counts": {"critical": 0, "high": 0, "medium": 0, "low": 0},
                "consultants": {}
            }

        g = grouped[state]
        g["count"] += 1
        g["demand"] += n.get("amount") or 0

        risk = n.get("risk")
        if risk in g["risk_counts"]:
            g["risk_counts"][risk] += 1

        consultant = n.get("consultant")
        if consultant:
            g["consultants"][consultant] = g["consultants"].get(consultant, 0) + 1

    # state code lookup — adjust if you have a proper State doctype/mapping
    state_codes = {
        "Karnataka": "KA", "Maharashtra": "MH", "Tamil Nadu": "TN",
        "Delhi": "DL", "Gujarat": "GJ", "Telangana": "TG"
        # extend as needed
    }

    risk_to_tone = {
        "critical": "red", "high": "red", "medium": "amber", "low": "green"
    }

    result = []
    for state, g in grouped.items():
        # determine dominant risk (highest severity present)
        dominant_risk = "low"
        for level in ["critical", "high", "medium", "low"]:
            if g["risk_counts"][level] > 0:
                dominant_risk = level
                break

        # pick most frequent consultant for this state
        top_consultant_id = None
        if g["consultants"]:
            top_consultant_id = max(g["consultants"], key=g["consultants"].get)

        consultant_obj = None
        if top_consultant_id:
            c = frappe.db.get_value(
                "Consultant", top_consultant_id, ["initials", "consultant_name"], as_dict=True
            )
            if c:
                consultant_obj = {"initials": c.initials, "name": c.consultant_name}

        result.append({
            "name": state,
            "code": state_codes.get(state, state[:2].upper()),
            "city": g["city"],
            "gstin": g["gstin"],
            "count": g["count"],
            "demand": g["demand"],
            "risk": dominant_risk,
            "tone": risk_to_tone.get(dominant_risk, "green"),
            "consultant": consultant_obj
        })

    return result

@frappe.whitelist(methods=["POST"])
def generate_draft_response(id):
    if not frappe.db.exists("Litigation Notice", id):
        frappe.throw(f"Notice {id} not found", frappe.DoesNotExistError)

    draft_id = f"D-{frappe.generate_hash(length=4).upper()}"

    frappe.db.set_value("Litigation Notice", id, "draft_id", draft_id)
    frappe.db.commit()

    frappe.response.http_status_code = 202
    return {"draftId": draft_id, "status": "queued"}

# @frappe.whitelist(methods=["POST"])
# def approve_and_send_notice(id, draftId=None, editedBody=None):
#     if not frappe.db.exists("Litigation Notice", id):
#         frappe.throw(f"Notice {id} not found", frappe.DoesNotExistError)

#     # Optional: verify the draftId matches what's stored, so you can't approve a stale/wrong draft
#     current_draft = frappe.db.get_value("Litigation Notice", id, "draft_id")
#     if draftId and current_draft != draftId:
#         frappe.throw(f"Draft ID mismatch: expected {current_draft}, got {draftId}")

#     # If the user edited the draft body, store it — adjust fieldname to wherever body text should live
#     if editedBody:
#         frappe.db.set_value("Litigation Notice", id, "response_body", editedBody)
#         # ^ confirm this fieldname exists — if not, we need to add it or use a different field

#     frappe.db.set_value("Litigation Notice", id, "status", "Filed")
#     frappe.db.commit()

#     return {
#         "notice": {
#             "id": id,
#             "status": "Filed"
#         }
#     }

@frappe.whitelist(methods=["GET"])
def export_notice_register(client_key, status=None, format="xlsx"):
    import io
    from frappe.utils.xlsxutils import make_xlsx

    filters = {"client": client_key}
    if status:
        filters["status"] = status

    notices = frappe.get_all(
        "Litigation Notice",
        filters=filters,
        fields=[
            "notice_id", "gstin", "section", "notice_type", "label",
            "state", "city", "fy", "amount", "filed_date",
            "risk", "status", "due_date", "consultant"
        ],
        order_by="due_date asc"
    )

    headers = [
        "Notice ID", "GSTIN", "Section", "Type", "Label",
        "State", "City", "FY", "Amount", "Filed Date",
        "Risk", "Status", "Due Date", "Consultant"
    ]

    rows = [headers]
    for n in notices:
        rows.append([
            n.notice_id, n.gstin, n.section, n.notice_type, n.label,
            n.state, n.city, n.fy, n.amount, n.filed_date,
            n.risk, n.status, n.due_date, n.consultant
        ])

    if format == "csv":
        import csv
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerows(rows)
        frappe.response["result"] = buffer.getvalue()
        frappe.response["type"] = "csv"
        frappe.response["doctype"] = "csv"
    else:
        xlsx_data = make_xlsx(rows, "Notice Register")
        frappe.response["filename"] = "notice-register.xlsx"
        frappe.response["filecontent"] = xlsx_data.getvalue()
        frappe.response["type"] = "binary"

@frappe.whitelist(methods=["POST"])
def create_notice():
    client_key = frappe.form_dict.get("clientKey")
    gstin = frappe.form_dict.get("gstin")

    if not client_key:
        frappe.throw("clientKey is required")
    if not frappe.db.exists("Client", client_key):
        frappe.throw(f"Client {client_key} not found")
    if gstin and not frappe.db.exists("GST Registration", gstin):
        frappe.throw(f"GST Registration {gstin} not found — create it first")

    notice_id = f"N-{frappe.generate_hash(length=4).upper()}"

    notice = frappe.get_doc({
        "doctype": "Litigation Notice",
        "notice_id": notice_id,
        "client": client_key,
        "gstin": gstin,
        "status": "Under review",
        "extracted_by_ai": 1
    })
    notice.insert(ignore_permissions=True)

    uploaded_file_url = None
    if "file" in frappe.request.files:
        from frappe.utils.file_manager import save_file
        file_obj = frappe.request.files["file"]
        _file = save_file(
            fname=file_obj.filename,
            content=file_obj.stream.read(),
            dt="Litigation Notice",
            dn=notice.name,
            is_private=1
        )
        uploaded_file_url = _file.file_url
        notice.db_set("source_pdf_url", uploaded_file_url)

    frappe.db.commit()

    frappe.response.http_status_code = 201
    return {
        "id": notice.name,
        "gstin": notice.gstin,
        "status": notice.status,
        "extractedByAi": bool(notice.extracted_by_ai)
    }

@frappe.whitelist(methods=["GET"])
def get_clients():
    from frappe.utils import add_months, get_first_day, get_last_day, getdate, nowdate

    raw_clients = frappe.get_all(
        "Client",
        fields=["name", "client_name", "short_name", "pan", "sector"]
    )

    # rename fields in Python instead of aliasing reserved words in SQL
    clients = []
    for rc in raw_clients:
        clients.append({
            "key": rc["name"],
            "name": rc["client_name"],
            "shortName": rc["short_name"],
            "pan": rc["pan"],
            "sector": rc["sector"]
        })

    today = getdate(nowdate())
    months = []
    for i in range(11, -1, -1):
        month_date = add_months(today, -i)
        months.append({"start": get_first_day(month_date), "end": get_last_day(month_date)})

    for c in clients:
        client_key = c["key"]

        c["notices"] = frappe.db.count("Litigation Notice", filters={"client": client_key})

        c["demand"] = frappe.db.get_value(
            "Litigation Notice", {"client": client_key}, "sum(amount)"
        ) or 0

        c["overdue"] = frappe.db.count(
            "Litigation Notice",
            filters={"client": client_key, "due_date": ["<", today], "status": ["!=", "Resolved"]}
        )

        registrations = frappe.get_all(
            "GST Registration",
            filters={"client": client_key},
            fields=["gstin", "state", "consent_status as consent"]
        )
        c["registrations"] = registrations

        received = []
        resolved = []
        for m in months:
            received.append(frappe.db.count(
                "Litigation Notice",
                filters={"client": client_key, "filed_date": ["between", [m["start"], m["end"]]]}
            ))
            resolved.append(frappe.db.count(
                "Litigation Notice",
                filters={"client": client_key, "resolved_on": ["between", [m["start"], m["end"]]]}
            ))
        c["trendData"] = received
        c["resolvedData"] = resolved

    return clients

@frappe.whitelist(methods=["POST"])
def create_client(name, pan=None, sector=None, firstGstin=None):
    # Auto-generate client_code from short name initials (e.g. "Meridian Apparel Sourcing Ltd" -> "MASL")
    words = name.split()
    initials = "".join(w[0].upper() for w in words if w[0].isalpha())
    client_code = initials

    # Ensure uniqueness — append a number if this code already exists
    suffix = 1
    base_code = client_code
    while frappe.db.exists("Client", client_code):
        suffix += 1
        client_code = f"{base_code}{suffix}"

    # Derive firm_profile — using the logged-in user's default firm, or a fallback
    firm_profile = frappe.defaults.get_user_default("firm_profile")
    if not firm_profile:
        # fallback: pick the first existing firm profile value used by another client, or leave blank
        existing = frappe.db.get_value("Client", {"firm_profile": ["!=", ""]}, "firm_profile")
        firm_profile = existing or "LitWatch Law Firm"  # adjust default as appropriate

    client = frappe.get_doc({
        "doctype": "Client",
        "client_code": client_code,
        "client_name": name,
        "short_name": client_code,
        "firm_profile": firm_profile,
        "pan": pan,
        "sector": sector,
        "status": "Active"
    })
    client.insert(ignore_permissions=True)

    # Create the first GST Registration if provided
    if firstGstin:
        if not frappe.db.exists("GST Registration", firstGstin):
            frappe.get_doc({
                "doctype": "GST Registration",
                "gstin": firstGstin,
                "client": client.name
            }).insert(ignore_permissions=True)

    frappe.db.commit()

    frappe.response.http_status_code = 201
    return {
        "key": client.name,
        "name": client.client_name,
        "notices": 0,
        "registrations": []
    }

@frappe.whitelist(methods=["PATCH", "POST"])
def set_active_client(clientKey):
    if not frappe.db.exists("Client", clientKey):
        frappe.throw(f"Client {clientKey} not found")

    frappe.defaults.set_user_default("active_client", clientKey)

    return {"clientKey": clientKey}


@frappe.whitelist(methods=["GET"])
def get_active_client():
    active_client = frappe.defaults.get_user_default("active_client")
    return {"clientKey": active_client}

@frappe.whitelist(methods=["GET"])
def get_client_pipeline():
    clients = frappe.get_all(
        "Client",
        filters={"onboarding_stage": ["!=", "Complete"]},
        fields=["client_name", "sector", "onboarding_stage", "onboarding_date", "name"]
    )

    result = []
    for c in clients:
        gstin_count = frappe.db.count("GST Registration", filters={"client": c["name"]})
        result.append({
            "name": c["client_name"],
            "sector": c["sector"],
            "stage": c["onboarding_stage"],
            "gstins": gstin_count,
            "when": c["onboarding_date"]
        })

    return result

@frappe.whitelist(methods=["PATCH", "POST"])
def update_client_pipeline_stage(id, stage):
    if not frappe.db.exists("Client", id):
        frappe.throw(f"Client {id} not found")

    frappe.db.set_value("Client", id, "onboarding_stage", stage)
    frappe.db.commit()

    client_name = frappe.db.get_value("Client", id, "client_name")

    return {
        "name": client_name,
        "stage": stage
    }









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
def verify_gstin_otp(gstin, otpRequestId, otp):
    """
    Step 2 of the two-step GSTIN auth flow.

    Verifies the OTP against the pending GST OTP Request row and, on
    success, activates the GST Registration's consent.

    Prerequisite: connect_gstin (step 1) must have written a
    GST OTP Request row with status="pending" for this otpRequestId.
    """

    # --- Required validations ---
    if not gstin:
        frappe.throw("gstin is required")

    if not otpRequestId:
        frappe.throw("otpRequestId is required")

    if not otp:
        frappe.throw("otp is required")

    if not frappe.db.exists("GST Registration", gstin):
        frappe.throw(
            f"GST Registration {gstin} not found",
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
            f"OTP request {otpRequestId} not found",
            frappe.DoesNotExistError,
        )

    otp_req = frappe.get_doc("GST OTP Request", otp_row_name)

    if otp_req.gstin != gstin:
        frappe.throw(
            f"OTP request {otpRequestId} does not belong to GSTIN {gstin}"
        )

    if otp_req.status == "verified":
        frappe.throw(f"OTP request {otpRequestId} has already been verified")

    if otp_req.status == "expired":
        frappe.throw(f"OTP request {otpRequestId} has expired")

    # --- Verify OTP with the GST portal ---
    # TODO: replace with the real portal verification call. Placeholder
    # below assumes the portal returned success.
    portal_ok = True
    if not portal_ok:
        frappe.throw("Invalid OTP")

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


# ---------------------------------------------------------------------------
# 4.2 POST /documents  (upload_document)
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["POST"])
def upload_document():
    """
    Uploads a document and attaches it to a client (and optionally a matter).

    Triggered by the Upload modal on /documents.

    Body: multipart/form-data with:
      file      - the binary file (required)
      clientKey - Client key, e.g. "ACME001" (required)
      role      - "main" | "supp" (required)
      type      - "Notice" | "Annexure" | "Filed reply" |
                  "Acknowledgement" | "Evidence" (required)
      matter    - Matter id (optional, Link to Matter doctype)
    """

    from frappe.utils.file_manager import save_file

    # --- Read form fields ---
    client_key = frappe.form_dict.get("clientKey")
    matter = frappe.form_dict.get("matter")
    role = frappe.form_dict.get("role")
    doc_type = frappe.form_dict.get("type")

    # --- Required validations ---
    if not client_key:
        frappe.throw("clientKey is required")

    if not frappe.db.exists("Client", client_key):
        frappe.throw(
            f"Client {client_key} not found",
            frappe.DoesNotExistError,
        )

    allowed_roles = ("main", "supp")
    if not role:
        frappe.throw("role is required")
    if role not in allowed_roles:
        frappe.throw(
            f"Invalid role value: {role}. Must be one of {list(allowed_roles)}"
        )

    allowed_types = (
        "Notice", "Annexure", "Filed reply",
        "Acknowledgement", "Evidence",
    )
    if not doc_type:
        frappe.throw("type is required")
    if doc_type not in allowed_types:
        frappe.throw(
            f"Invalid type value: {doc_type}. Must be one of {list(allowed_types)}"
        )

    # Optional matter link — validate it exists if provided
    if matter and not frappe.db.exists("Matter", matter):
        frappe.throw(
            f"Matter {matter} not found",
            frappe.DoesNotExistError,
        )

    file_obj = frappe.request.files.get("file")
    if not file_obj:
        frappe.throw("file is required")

    content = file_obj.stream.read()

    # --- 1. Save the File record unattached ---
    # dt=None and dn=None are required: Frappe rejects a File whose
    # attached_to_doctype is set but attached_to_name is None.
    _file = save_file(
        fname=file_obj.filename,
        content=content,
        dt=None,
        dn=None,
        is_private=1,
    )

    # --- 2. Create the Client Document row with the file URL ---
    # "file" is a mandatory field on Client Document, so it must be set
    # at insert time. The Client Document row cannot be created before
    # the File exists.
    doc = frappe.get_doc({
        "doctype": "Client Document",
        "file": _file.file_url,
        "file_name": file_obj.filename,
        "client": client_key,
        "matter": matter,
        "role": role,
        "document_type": doc_type,
        "file_size": len(content),
        "upload_date": frappe.utils.now(),
    })
    doc.insert(ignore_permissions=True)

    # --- 3. Attach the File to the new Client Document row ---
    # Use db_set, not _file.save(). Inserting the Client Document updates
    # the File record underneath us, which makes Frappe's optimistic-lock
    # check fail with TimestampMismatchError on save().
    frappe.db.set_value(
        "File",
        _file.name,
        {
            "attached_to_doctype": "Client Document",
            "attached_to_name": doc.name,
        },
    )

    frappe.db.commit()

    frappe.response.http_status_code = 201
    return {
        "name": doc.file_name,
        "matter": doc.matter,
        "role": doc.role,
        "type": doc.document_type,
        "size": _humanize_size(doc.file_size),
    }


def _humanize_size(size):
    """Turn a byte count into a compact string like '310 KB'."""
    if not size:
        return "0 B"
    size = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.0f} PB"

# ---------------------------------------------------------------------------
# 4.3 GET /documents/:id  (download_document)
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["GET"])
def download_document(id=None):
    """
    Streams the binary file for a Client Document.

    Used by the Documents row's "view / download" action and by the
    Notice Workbench document viewer pane.

    Input: id (query parameter) — the Client Document name.
      e.g. GET /api/method/litwatch.api.download_document?id=<doc-name>

    Returns the raw file bytes, not JSON.
    """

    # --- Required validations ---
    if not id:
        frappe.throw("id is required")

    if not frappe.db.exists("Client Document", id):
        frappe.throw(
            f"Client Document {id} not found",
            frappe.DoesNotExistError,
        )

    doc = frappe.get_doc("Client Document", id)
    doc.check_permission("read")

    if not doc.file:
        frappe.throw(
            f"Client Document {id} has no file attached",
            frappe.DoesNotExistError,
        )

    # --- Look up the File record by URL ---
    file_doc = frappe.db.get_value(
        "File",
        {"file_url": doc.file},
        ["name", "file_name", "file_url"],
        as_dict=True,
    )
    if not file_doc:
        frappe.throw(
            f"File for Client Document {id} not found",
            frappe.DoesNotExistError,
        )

    # --- Return the binary ---
    from frappe.utils.file_manager import get_file

    file_name, content = get_file(file_doc.file_url)

    frappe.local.response.filename = file_name
    frappe.local.response.filecontent = content
    frappe.local.response.type = "download"


# ---------------------------------------------------------------------------
# 4.1 GET /documents  (get_documents)
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["GET"])
def get_documents(client=None, role=None, type=None):
    """
    Returns documents in the Documents library, plus storage usage.

    Used by the Documents library table and the storage-used KPI at
    /documents.

    Filters (all optional):
      client - Client key, e.g. ACME001
      role   - "main" or "supp"
      type   - "Notice" | "Annexure" | "Filed reply" |
               "Acknowledgement" | "Evidence"
    """

    # --- Validate the filters if supplied ---
    if client:
        if not frappe.db.exists("Client", client):
            frappe.throw(
                f"Client {client} not found",
                frappe.DoesNotExistError,
            )

    allowed_roles = ("main", "supp")
    if role and role not in allowed_roles:
        frappe.throw(
            f"Invalid role value: {role}. Must be one of {list(allowed_roles)}"
        )

    allowed_types = (
        "Notice", "Annexure", "Filed reply",
        "Acknowledgement", "Evidence",
    )
    if type and type not in allowed_types:
        frappe.throw(
            f"Invalid type value: {type}. Must be one of {list(allowed_types)}"
        )

    # --- Build filters ---
    filters = {}
    if client:
        filters["client"] = client
    if role:
        filters["role"] = role
    if type:
        filters["document_type"] = type

    # --- Fetch document rows ---
    rows = frappe.get_all(
        "Client Document",
        filters=filters,
        fields=[
            "name",                # <-- Client Document.name (the id)
            "file_name",           # <-- display filename
            "matter",
            "client",
            "role",
            "document_type",
            "file_size",
            "upload_date",
        ],
        order_by="upload_date desc",
        limit_page_length=200,
    )

    # --- Shape the response ---
    items = []
    for r in rows:
        items.append({
            "id": r.get("name"),          # <-- NEW: Client Document.name, for 4.3
            "name": r.get("file_name"),   # display filename
            "matter": r.get("matter"),
            "client": r.get("client"),
            "role": r.get("role"),
            "type": r.get("document_type"),
            "size": _humanize_size(r.get("file_size")),
            "date": str(r["upload_date"]) if r.get("upload_date") else None,
        })

    # --- Storage usage (account-wide, not filtered) ---
    storage_used = (
        frappe.db.get_value("Client Document", {}, "sum(file_size)")
        or 0
    )
    # TODO: replace with the real source for the cap.
    storage_cap = 10737418240   # 10 GiB

    return {
        "items": items,
        "storageUsed": int(storage_used),
        "storageCap": storage_cap,
    }


# ---------------------------------------------------------------------------
# 4.4 DELETE /documents/:id  (delete_document)
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["DELETE", "POST"])
def delete_document(id=None):
    """
    Deletes a Client Document and its underlying File.

    Used by the row delete action on /documents.

    Input: id (query parameter) — the Client Document name.
      e.g. DELETE /api/method/litwatch.api.delete_document?id=c4vesbi6i2

    Returns 204 No Content with an empty body.
    """

    # --- Required validations ---
    if not id:
        frappe.throw("id is required")

    if not frappe.db.exists("Client Document", id):
        frappe.throw(
            f"Client Document {id} not found",
            frappe.DoesNotExistError,
        )

    doc = frappe.get_doc("Client Document", id)
    doc.check_permission("delete")

    # --- Delete the File record first (if any) ---
    # Look up by attached_to_name, not by file_url. The upload handler
    # sets attached_to_doctype / attached_to_name reliably; file_url can
    # differ by formatting and cause the lookup to miss silently.
    file_name = frappe.db.get_value(
        "File",
        {
            "attached_to_doctype": "Client Document",
            "attached_to_name": id,
        },
        "name",
    )
    if file_name:
        frappe.delete_doc(
            "File",
            file_name,
            ignore_permissions=True,
            force=True,
        )

    # --- Delete the Client Document row ---
    frappe.delete_doc("Client Document", id, ignore_permissions=True, force=True)

    frappe.db.commit()

    frappe.local.response.http_status_code = 204
    frappe.local.response["type"] = "json"
    frappe.local.response["message"] = None


# ---------------------------------------------------------------------------
# 5.1 GET /filings  (get_filings)
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["GET"])
def get_filings(client=None):
    """
    Returns filings for the Filing & Ack page.

    Used by the "Approved but unfiled" and "Filed this month" lists at
    /filing-ack.

    Optional filter: client (Client name). When provided, only filings
    for that client are returned.
    """

    # --- Validate the filter if supplied ---
    filters = {}
    if client:
        if not frappe.db.exists("Client", client):
            frappe.throw(
                f"Client {client} not found",
                frappe.DoesNotExistError,
            )
        filters["client"] = client

    # --- Fetch filing rows ---
    rows = frappe.get_all(
        "Filing",
        filters=filters,
        fields=[
            "filing_id",
            "client",
            "filing_type",
            "reply_status",
            "status",
            "arn",
            "filed_date",
            "ack_file",
            "filed_by",
        ],
        order_by="filed_date desc",
    )

    if not rows:
        return []

    # --- Resolve consultant display names once ---
    consultant_ids = {r["filed_by"] for r in rows if r.get("filed_by")}
    consultant_names = {}
    for cid in consultant_ids:
        name = frappe.db.get_value("Consultant", cid, "consultant_name")
        if name:
            consultant_names[cid] = name

    # --- Shape the response ---
    result = []
    for r in rows:
        result.append({
            "id": r.get("filing_id"),
            "client": r.get("client"),
            "type": r.get("filing_type"),
            "reply": r.get("reply_status"),
            "status": r.get("status"),
            "arn": r.get("arn"),
            "filed": str(r["filed_date"]) if r.get("filed_date") else None,
            "ack": r.get("ack_file"),
            "by": consultant_names.get(r.get("filed_by")),
        })

    return result

# ---------------------------------------------------------------------------
# 5.1 GET /filings  (get_filings)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 5.2 PATCH /filings/:id/record  (record_filing)
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["PATCH", "POST"])
def record_filing(id=None):
    """
    Records a filing: sets ARN, filing date, and acknowledgement file,
    flips the filing to "filed", and closes the matter when every
    filing for the same notice AND client is filed.

    Used by the FilingModal's "Record filing" action at /filing-ack.

    Input:
      id        (query parameter) - the filing_id, e.g. "F-TEST01"
      arn       (form field)      - the acknowledgement reference number
      filedDate (form field)      - YYYY-MM-DD
      ackFile   (form file)       - the acknowledgement PDF
    """

    from frappe.utils.file_manager import save_file

    # --- Required validations ---
    if not id:
        frappe.throw("id is required")

    filing_doc_name = frappe.db.get_value("Filing", {"filing_id": id}, "name")
    if not filing_doc_name:
        frappe.throw(
            f"Filing {id} not found",
            frappe.DoesNotExistError,
        )

    arn = frappe.form_dict.get("arn")
    filed_date = frappe.form_dict.get("filedDate")

    if not arn:
        frappe.throw("arn is required")

    if not filed_date:
        frappe.throw("filedDate is required")

    file_obj = frappe.request.files.get("ackFile")
    if not file_obj:
        frappe.throw("ackFile is required")

    doc = frappe.get_doc("Filing", filing_doc_name)
    doc.check_permission("write")

    # --- Save the ack file (same pattern as 4.2) ---
    content = file_obj.stream.read()
    _file = save_file(
        fname=file_obj.filename,
        content=content,
        dt=None,
        dn=None,
        is_private=1,
    )

    # --- Update the filing fields ---
    doc.arn = arn
    doc.filed_date = filed_date
    doc.ack_file = _file.file_url
    doc.status = "filed"
    doc.reply_status = "Filed"

    # --- Decide whether the matter is now closed ---
    # The rule: the matter is closed when every filing for the SAME
    # notice AND the SAME client is filed. Filings on the same notice
    # belonging to other clients do not affect this client's matter.
    #
    # The current filing is excluded from the sibling query (in the
    # filter) because its status has already been set to "filed" in
    # memory; its persisted value is still the pre-update status.
    sibling_statuses = frappe.get_all(
        "Filing",
        filters={
            "notice": doc.notice,
            "client": doc.client,
            "name": ["!=", doc.name],
        },
        fields=["status"],
        pluck="status",
    )
    all_filed = all(s == "filed" for s in sibling_statuses)
    doc.matter_closed = 1 if all_filed else 0

    doc.save(ignore_permissions=True)

    # --- Attach the file to the filing doc ---
    frappe.db.set_value(
        "File",
        _file.name,
        {
            "attached_to_doctype": "Filing",
            "attached_to_name": doc.name,
        },
    )

    frappe.db.commit()

    return {
        "id": doc.filing_id,
        "status": doc.status,
        "arn": doc.arn,
        "matterClosed": bool(doc.matter_closed),
    }

# ---------------------------------------------------------------------------
# 6.1 GET /review-queue  (get_review_queue)
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["GET"])
def get_review_queue(client=None, limit_start=0, limit_page_length=200):
    """
    Returns review-queue items for the Review Queue page.

    Optional filters:
      client            - Client name. Filters to items for that client.
      limit_start       - Offset for paging. Default 0.
      limit_page_length - Page size. Default 200, max 500.
    """

    # --- Normalise and validate query params ---
    if client is not None:
        client = str(client).strip()
        if client == "":
            client = None

    try:
        limit_start = int(limit_start)
    except (TypeError, ValueError):
        limit_start = 0
    if limit_start < 0:
        limit_start = 0

    try:
        limit_page_length = int(limit_page_length)
    except (TypeError, ValueError):
        limit_page_length = 200
    if limit_page_length < 1:
        limit_page_length = 1
    if limit_page_length > 500:
        limit_page_length = 500

    # --- Validate the client filter if supplied ---
    filters = {}
    if client:
        if not frappe.db.exists("Client", client):
            frappe.throw(
                f"Client {client} not found",
                frappe.DoesNotExistError,
            )
        if not frappe.has_permission("Client", "read", client):
            frappe.throw(
                f"Not permitted to read Client {client}",
                frappe.PermissionError,
            )
        filters["client"] = client

    # --- Fetch review-queue rows ---
    rows = frappe.get_list(
        "Review Queue Item",
        filters=filters,
        fields=[
            "name",
            "review_id",
            "client",
            "review_type",
            "section",
            "gstin",
            "demand",
            "due_date",
            "confidence",
            "urgent",
            "why",
        ],
        order_by="due_date asc",
        limit_start=limit_start,
        limit_page_length=limit_page_length,
    )

    if not rows:
        return []

    # --- Fetch all flags for these parents in one query ---
    parent_names = [r["name"] for r in rows]
    flag_rows = frappe.get_all(
        "Review Flag",
        filters={"parent": ["in", parent_names]},
        fields=["parent", "flag_label", "detail"],
        order_by="idx asc",
    )

    # Group flags by parent
    flags_by_parent = {}
    for f in flag_rows:
        flags_by_parent.setdefault(f["parent"], []).append({
            "label": f.get("flag_label"),
            "detail": f.get("detail"),
        })

    # --- Shape the response ---
    result = []
    for r in rows:
        result.append({
            "id": r.get("review_id"),
            "client": r.get("client"),
            "type": r.get("review_type"),
            "section": r.get("section"),
            "gstin": r.get("gstin"),
            "demand": r.get("demand"),
            "due": str(r["due_date"]) if r.get("due_date") else None,
            "confidence": int(r["confidence"]) if r.get("confidence") is not None else None,
            "urgent": bool(r.get("urgent")),
            "flags": flags_by_parent.get(r["name"], []),
            "why": r.get("why"),
        })

    return result


# ---------------------------------------------------------------------------
# 3.1 GET /clients/:clientKey/matters/:id/workbench
#     (get_matter_workbench)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GET /clients/:clientKey/matters  (Litigation Spine)
# URL: GET /api/method/litwatch.api.get_client_matters
# ---------------------------------------------------------------------------
import json as _json

FLOW_VALUES = ["scrutiny", "demand", "appeal", "registration", "detention", "nonfiler"]
STATUS_VALUES = ["all", "open", "urgent", "closed"]


@frappe.whitelist(methods=["GET"])
def get_client_matters(client_key=None, flow=None, status=None):
    """
    Returns all litigation matters (cases) for a client, powering the
    Litigation Spine page's timeline cards.
    """
    if not client_key:
        frappe.throw(frappe._("client_key is required"))

    is_all_clients = client_key.lower() == "all"

    if not is_all_clients and not frappe.db.exists("Client", client_key):
        frappe.throw(frappe._("Client {0} not found").format(client_key))

    if flow and flow not in FLOW_VALUES:
        frappe.throw(frappe._("Invalid flow value: {0}. Must be one of {1}").format(flow, FLOW_VALUES))

    if status and status not in STATUS_VALUES:
        frappe.throw(frappe._("Invalid status value: {0}. Must be one of {1}").format(status, STATUS_VALUES))

    filters = {}
    if not is_all_clients:
        filters["client"] = client_key

    cases = frappe.get_list(
        "Litigation Case",
        filters=filters,
        fields=[
            "name", "case_id", "client", "notice", "flow", "section",
            "demand", "current_step", "state", "step_dates", "deadline",
            "action", "next_action", "closed", "critical",
        ],
    )

    today = getdate(nowdate())
    result = []

    for c in cases:
        client_name = frappe.db.get_value("Client", c["client"], "client_name")

        deadline_val = c.get("deadline")
        if deadline_val:
            days_left = date_diff(deadline_val, today)
            deadline_out = str(deadline_val)
        else:
            days_left = None
            deadline_out = "—"

        try:
            dates = _json.loads(c.get("step_dates") or "[]")
        except Exception:
            dates = []

        matter = {
            "id": c["notice"],
            "clientKey": c["client"],
            "clientName": client_name,
            "flow": c["flow"],
            "section": c["section"],
            "demand": c.get("demand") or 0,
            "at": c.get("current_step") or 0,
            "state": c["state"],
            "dates": dates,
            "deadline": deadline_out,
            "daysLeft": days_left,
            "action": c.get("action"),
            "next": c.get("next_action"),
            "closed": bool(c.get("closed")),
            "critical": bool(c.get("critical")),
        }

        if status and status != "all":
            if status == "open" and matter["closed"]:
                continue
            if status == "closed" and not matter["closed"]:
                continue
            if status == "urgent" and not (matter["daysLeft"] is not None and matter["daysLeft"] <= 7):
                continue

        result.append(matter)

    return result
