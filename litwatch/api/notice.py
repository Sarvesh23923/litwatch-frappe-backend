# apps/litwatch/litwatch/api/notice.py

import frappe
from frappe import _
from frappe.utils import getdate, nowdate, date_diff


# ---------------------------------------------------------------------------
# 1.1 GET /clients/:clientKey/notices
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["GET"])
def get_client_notices(client_key=None, risk=None, status=None, bucket=None, sort="due_date asc"):
    """
    Returns all litigation notices for one client, with optional filters.

    Query params:
      client_key - Required. Client key, e.g. ACME001.
      risk       - Optional. critical | high | medium | low
      status     - Optional. Notice status (no whitelist).
      bucket     - Optional. overdue | due7 | due30
      sort       - Optional. Default "due_date asc" (== dueInDays ascending).
    """

    if not client_key:
        frappe.throw(_("client_key is required"))

    if not frappe.db.exists("Client", client_key):
        frappe.throw(
            _("Client {0} not found").format(client_key),
            frappe.DoesNotExistError,
        )

    allowed_risk = ["critical", "high", "medium", "low"]
    if risk and risk not in allowed_risk:
        frappe.throw(_("Invalid risk value: {0}. Must be one of {1}").format(risk, allowed_risk))

    allowed_bucket = ["overdue", "due7", "due30"]
    if bucket and bucket not in allowed_bucket:
        frappe.throw(_("Invalid bucket value: {0}. Must be one of {1}").format(bucket, allowed_bucket))

    allowed_sort = [
        "due_date asc", "due_date desc",
        "amount asc", "amount desc",
        "filed_date asc", "filed_date desc",
    ]
    if sort not in allowed_sort:
        frappe.throw(_("Invalid sort value: {0}. Must be one of {1}").format(sort, allowed_sort))

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

    notices = frappe.get_list(
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
            "consultant",
        ],
        order_by=sort,
    )

    for n in notices:
        n["dueInDays"] = date_diff(n["due_date"], today) if n.get("due_date") else None
        n.pop("due_date", None)

        if n.get("consultant"):
            c = frappe.db.get_value(
                "Consultant",
                n["consultant"],
                ["initials", "consultant_name", "color"],
                as_dict=True,
            )
            n["consultant"] = (
                {"initials": c.initials, "name": c.consultant_name, "color": c.color}
                if c else None
            )

    return notices

# ---------------------------------------------------------------------------
# 1.2 GET /clients/:clientKey/notices/:id
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["GET"])
def get_client_notice_detail(client_key=None, id=None):
    """
    Returns full detail for a single litigation notice belonging to a client,
    including its timeline of events.

    Path/query params:
      client_key - Required. Client key, e.g. ACME001.
      id         - Required. Notice id/docname, e.g. N-2041.
    """

    if not client_key:
        frappe.throw(_("client_key is required"))

    if not id:
        frappe.throw(_("id is required"))

    if not frappe.db.exists("Client", client_key):
        frappe.throw(
            _("Client {0} not found").format(client_key),
            frappe.DoesNotExistError,
        )

    if not frappe.db.exists("Litigation Notice", id):
        frappe.throw(
            _("Notice {0} not found").format(id),
            frappe.DoesNotExistError,
        )

    notice = frappe.get_doc("Litigation Notice", id)

    # --- Ensure the notice actually belongs to this client ---
    if notice.client != client_key:
        frappe.throw(
            _("Notice {0} does not belong to client {1}").format(id, client_key),
            frappe.PermissionError,
        )

    today = getdate(nowdate())

    nd = notice.get("notice_data")
    import json as _json
    while isinstance(nd, str):
        try:
            nd = _json.loads(nd)
        except Exception:
            nd = {}
            break
    if not isinstance(nd, dict):
        nd = {}

    result = {
        "id": notice.name,
        "gstin": notice.gstin,
        "section": notice.section,
        "type": notice.notice_type,
        "amount": notice.amount,
        "risk": notice.risk,
        "status": notice.status,
        "dueInDays": date_diff(notice.due_date, today) if notice.due_date else None,
        "sourcePdfUrl": notice.source_pdf_url,
        "extractedByAi": bool(notice.get("extracted_by_ai")),
        "noticeData": nd,
    }

    # --- Timeline ---
    # ASSUMPTION: a child table field called "timeline" on Litigation Notice,
    # with child rows having "at" (datetime) and "event" (text) fields.
    # Adjust the child doctype/fieldnames below to match your actual schema.
    timeline = []
    for row in notice.get("timeline") or []:
        timeline.append({
            "at": row.get("at"),
            "event": row.get("event"),
        })

    result["timeline"] = timeline

    return result


    # ---------------------------------------------------------------------------
# 1.3 GET /clients/:clientKey/notices/stats
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["GET"])
def get_client_notice_stats(client_key=None):
    """
    Returns KPI stats for a client's notices — used in Command Hub's
    KPI row (total / critical / demand / overdue / due-soon tiles).

    Query params:
      client_key - Required. Client key, e.g. ACME001.
    """

    if not client_key:
        frappe.throw(_("client_key is required"))

    if not frappe.db.exists("Client", client_key):
        frappe.throw(
            _("Client {0} not found").format(client_key),
            frappe.DoesNotExistError,
        )

    today = getdate(nowdate())
    due_soon_end = frappe.utils.add_days(today, 7)  # ASSUMPTION: "due soon" == due7 bucket

    total = frappe.db.count("Litigation Notice", filters={"client": client_key})

    critical = frappe.db.count(
        "Litigation Notice",
        filters={"client": client_key, "risk": "critical"},
    )

    total_demand = frappe.db.get_value(
        "Litigation Notice",
        filters={"client": client_key},
        fieldname="sum(amount)",
    ) or 0

    overdue = frappe.db.count(
        "Litigation Notice",
        filters={"client": client_key, "due_date": ["<", today]},
    )

    due_soon = frappe.db.count(
        "Litigation Notice",
        filters={"client": client_key, "due_date": ["between", [today, due_soon_end]]},
    )

    return {
        "total": total,
        "critical": critical,
        "totalDemand": total_demand,
        "overdue": overdue,
        "dueSoon": due_soon,
    }

# ---------------------------------------------------------------------------
# 1.4 GET /clients/:clientKey/notices/trend
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["GET"])
def get_client_notice_trend(client_key=None):
    """
    Returns monthly notice intake trend (received vs resolved) for the
    trailing 12 months — used in Command Hub's Notice Intake chart.

    Query params:
      client_key - Required. Client key, e.g. ACME001.
    """

    if not client_key:
        frappe.throw(_("client_key is required"))

    if not frappe.db.exists("Client", client_key):
        frappe.throw(
            _("Client {0} not found").format(client_key),
            frappe.DoesNotExistError,
        )

    today = getdate(nowdate())

    # Build list of the trailing 12 (year, month) tuples, oldest first
    months = []
    y, m = today.year, today.month
    for _i in range(12):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months.reverse()

    received = []
    resolved = []

    for (yr, mo) in months:
        month_start = getdate(f"{yr}-{mo:02d}-01")
        month_end = frappe.utils.get_last_day(month_start)

        # ASSUMPTION: "received" = notices filed in that month (filed_date)
        received_count = frappe.db.count(
            "Litigation Notice",
            filters={
                "client": client_key,
                "filed_date": ["between", [month_start, month_end]],
            },
        )

        # ASSUMPTION: "resolved" = status == "Resolved", counted by a
        # "resolved_date" field in that month. Adjust field name if
        # your doctype tracks resolution differently.
        
        resolved_count = frappe.db.count(
            "Litigation Notice",
            filters={
                "client": client_key,
                "status": "Resolved",
                "resolved_on": ["between", [month_start, month_end]],
            },
        )

        received.append(received_count)
        resolved.append(resolved_count)

    return {
        "received": received,
        "resolved": resolved,
    }


# ---------------------------------------------------------------------------
# 1.5 GET /clients/:clientKey/locations
# ---------------------------------------------------------------------------
RISK_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
RISK_TONE = {"critical": "red", "high": "red", "medium": "amber", "low": "green"}


@frappe.whitelist(methods=["GET"])
def get_client_locations(client_key=None):
    """
    Returns state-wise aggregated notice data for a client — used in
    Command Hub's heat map, State Coverage page, and StateModal.

    Query params:
      client_key - Required. Client key, e.g. ACME001.
    """

    if not client_key:
        frappe.throw(_("client_key is required"))

    if not frappe.db.exists("Client", client_key):
        frappe.throw(
            _("Client {0} not found").format(client_key),
            frappe.DoesNotExistError,
        )

    notices = frappe.get_list(
        "Litigation Notice",
        filters={"client": client_key},
        fields=["state", "city", "gstin", "amount", "risk", "consultant"],
    )

    # --- Group by state ---
    grouped = {}
    for n in notices:
        state = n.get("state")
        if not state:
            continue
        g = grouped.setdefault(state, {
            "name": state,
            "city": n.get("city"),
            "gstin": n.get("gstin"),
            "count": 0,
            "demand": 0,
            "risk": None,
            "consultant": n.get("consultant"),
        })
        g["count"] += 1
        g["demand"] += n.get("amount") or 0

        # Highest-severity risk wins for the state's overall risk
        if n.get("risk") and (
            g["risk"] is None or RISK_RANK.get(n["risk"], 0) > RISK_RANK.get(g["risk"], 0)
        ):
            g["risk"] = n["risk"]

    # --- Enrich each state group ---
    result = []
    for state, g in grouped.items():
        # State code lookup
        code = frappe.db.get_value("State", state, "state_code")  # ASSUMPTION: doctype/field name

        # Consultant details for whichever consultant we captured
        consultant = None
        if g["consultant"]:
            c = frappe.db.get_value(
                "Consultant",
                g["consultant"],
                ["initials", "consultant_name"],
                as_dict=True,
            )
            if c:
                consultant = {"initials": c.initials, "name": c.consultant_name}

        result.append({
            "name": g["name"],
            "code": code,
            "city": g["city"],
            "gstin": g["gstin"],
            "count": g["count"],
            "demand": g["demand"],
            "risk": g["risk"],
            "tone": RISK_TONE.get(g["risk"], "gray"),
            "consultant": consultant,
        })

    return result


# ---------------------------------------------------------------------------
# 1.6 POST /notices/:id/draft-response
# ---------------------------------------------------------------------------
@frappe.whitelist(methods=["POST"])
def draft_notice_response(id=None):
    """
    Triggers AI generation of a draft response for a notice. Generates a
    draft id, stores it on the notice, and returns it immediately.

    Path param:
      id - Required. Notice id/docname, e.g. N-2041.
    """

    if not id:
        frappe.throw(_("id is required"))

    if not frappe.db.exists("Litigation Notice", id):
        frappe.throw(
            _("Notice {0} not found").format(id),
            frappe.DoesNotExistError,
        )

    draft_id = f"D-{frappe.generate_hash(length=4).upper()}"

    frappe.db.set_value("Litigation Notice", id, "draft_id", draft_id)
    frappe.db.commit()

    frappe.enqueue(
        "litwatch.api.notice.generate_draft_content",
        queue="default",
        draft_id=draft_id,
        notice_id=id,
        enqueue_after_commit=True,
    )

    frappe.local.response.http_status_code = 202

    return {"draftId": draft_id, "status": "queued"}


def generate_draft_content(draft_id, notice_id):
    """
    Background job: calls the AI service to generate the actual draft
    response text for the notice.
    """
    try:
        notice = frappe.get_doc("Litigation Notice", notice_id)

        # ASSUMPTION: your actual AI-drafting logic goes here.
        draft_text = _call_ai_drafting_service(notice)

        # ASSUMPTION: storing generated text needs a real destination field —
        # frappe.db.set_value("Litigation Notice", notice_id, "<field>", draft_text)

    except Exception:
        frappe.log_error(title="Draft generation failed", message=frappe.get_traceback())


def _call_ai_drafting_service(notice):
    """Placeholder — wire this up to your actual AI drafting integration."""
    raise NotImplementedError("Wire up your AI drafting service call here")


@frappe.whitelist(methods=["POST"])
def create_notice(
    client_key=None,
    gstin=None,
    section=None,
    notice_type=None,
    label=None,
    state=None,
    city=None,
    fy=None,
    amount=None,
    filed_date=None,
    consultant=None,
    risk=None,
    status=None,
    due_date=None,
    notice_data=None,
):
    """
    Creates a new litigation notice, with an optional uploaded PDF attached
    and extracted via AI. Used by NewNoticeModal ("New Notice" button) and
    Litigation Dashboard's "Add Notice" button.

    multipart/form-data fields:
      client_key  - Required. Client key, e.g. ACME001.
      gstin       - Optional.
      section     - Optional.
      notice_type - Optional. Link to Notice Type.
      label       - Optional.
      state       - Optional.
      city        - Optional.
      fy          - Optional.
      amount      - Optional.
      filed_date  - Optional.
      consultant  - Optional. Link to Consultant.
      risk        - Optional. critical | high | medium | low
      status      - Optional. Defaults to "Under review".
      due_date    - Optional.
      notice_data - Optional. JSON object (or JSON string) with the full
                    structured extraction.
      noticeData  - Optional. camelCase alias for notice_data (frontend compat).
      file        - Optional. Binary PDF upload.
    """
    import json

    # --- Normalize form_dict camelCase → snake_case ---
    fd = frappe.form_dict or {}

    if not client_key:
        client_key = fd.get("clientKey") or fd.get("client_key")

    if notice_data in (None, "", "{}"):
        notice_data = fd.get("noticeData") or fd.get("notice_data") or None

    # --- Cast empty strings to None so Link/Date fields stay NULL in DB ---
    def _blank_to_none(v):
        return None if v == "" else v

    gstin       = _blank_to_none(gstin)
    section     = _blank_to_none(section)
    notice_type = _blank_to_none(notice_type)
    label       = _blank_to_none(label)
    state       = _blank_to_none(state)
    city        = _blank_to_none(city)
    fy          = _blank_to_none(fy)
    filed_date  = _blank_to_none(filed_date)
    consultant  = _blank_to_none(consultant)
    risk        = _blank_to_none(risk)
    due_date    = _blank_to_none(due_date)

    # --- Normalize notice_data: unwrap string layers until dict ---
    if notice_data is None:
        notice_data = {}

    while isinstance(notice_data, str):
        try:
            parsed = json.loads(notice_data)
        except Exception:
            frappe.throw(_("notice_data must be valid JSON"))
        if parsed == notice_data:
            break
        notice_data = parsed

    if not isinstance(notice_data, dict):
        frappe.throw(_("notice_data must be a JSON object"))

    # --- Required client ---
    if not client_key:
        frappe.throw(_("clientKey is required"))

    if not frappe.db.exists("Client", client_key):
        frappe.throw(
            _("Client {0} not found").format(client_key),
            frappe.DoesNotExistError,
        )

    # --- Validate risk ---
    allowed_risk = ["critical", "high", "medium", "low"]
    if risk and risk not in allowed_risk:
        frappe.throw(_("Invalid risk value: {0}. Must be one of {1}").format(risk, allowed_risk))

    # --- Derive flat fields from notice_data if not explicitly passed ---
    taxpayer     = (notice_data.get("taxpayer") or {})
    demand       = (notice_data.get("demand") or {})
    notice_block = (notice_data.get("notice") or {})
    doc_ident    = (notice_data.get("document_identity") or {})
    jurisdiction = (notice_data.get("jurisdiction_and_authority") or {})
    provisions   = (notice_data.get("legal_basis") or {}).get("provisions") or []

    gstin       = gstin       or (taxpayer.get("gstin") or {}).get("value")
    state       = state       or jurisdiction.get("state")
    city        = city        or jurisdiction.get("division") or jurisdiction.get("circle")
    fy          = fy          or (notice_block.get("financial_years") or [None])[0]
    amount      = amount      or (demand.get("total_demand") or {}).get("amount")
    filed_date  = filed_date  or (notice_block.get("issue_date") or {}).get("value")
    due_date    = due_date    or (notice_block.get("explicit_reply_due_date") or {}).get("value")
    section     = section     or (provisions[0].get("section") if provisions else None)
    notice_type = notice_type or (doc_ident.get("notice_form") or {}).get("value")

    # --- Create the notice ---
    notice_id = f"N-{frappe.generate_hash(length=4).upper()}"

    notice = frappe.get_doc({
        "doctype": "Litigation Notice",
        "notice_id": notice_id,
        "client": client_key,
        "gstin": gstin,
        "section": section,
        "notice_type": notice_type,
        "label": label,
        "state": state,
        "city": city,
        "fy": fy,
        "amount": amount,
        "filed_date": filed_date,
        "consultant": consultant,
        "risk": risk,
        "status": status or "Under review",
        "due_date": due_date,
        "extracted_by_ai": 0,
        "notice_data": notice_data,
    })
    notice.insert(ignore_permissions=True)

    # --- Handle uploaded file, if any ---
    extracted_by_ai = False
    uploaded = frappe.request.files.get("file") if frappe.request else None

    if uploaded:
        # Save the file
        saved_file = frappe.get_doc({
            "doctype": "File",
            "attached_to_doctype": "Litigation Notice",
            "attached_to_name": notice.name,
            "file_name": uploaded.filename or f"{notice.name}.pdf",
            "content": uploaded.stream.read(),
            "is_private": 1,
        })
        saved_file.save(ignore_permissions=True)

        notice.db_set("source_pdf_url", saved_file.file_url)

        # --- Run AI extraction synchronously (so the response includes it) ---
        frappe.db.commit()
        extract_notice_from_pdf(notice_id=notice.name, file_url=saved_file.file_url)
        extracted_by_ai = True

    # --- If structured data was passed in (no PDF), mark as AI-extracted ---
    if notice_data and not extracted_by_ai:
        extracted_by_ai = True
        notice.db_set("extracted_by_ai", 1)

    frappe.db.commit()

    # --- Build response with nulls preserved ---
    response_data = build_consolidated_response(notice, extracted_by_ai)

    def _clean(d):
        if isinstance(d, dict):
            return {k: _clean(v) for k, v in d.items()}
        if isinstance(d, list):
            return [_clean(i) for i in d]
        return "" if d is None else d

    response_data = _clean(response_data)

    frappe.response["message"] = response_data
    frappe.response["http_status_code"] = 201
    return


def build_consolidated_response(notice, extracted_by_ai=None):
    """
    Returns the full consolidated notice — flat summary fields + notice_data.
    """
    import json

    try:
        notice.reload()
    except Exception:
        pass

    nd = notice.get("notice_data")
    while isinstance(nd, str):
        try:
            nd = json.loads(nd)
        except Exception:
            nd = {}
            break
    if not isinstance(nd, dict):
        nd = {}

    if extracted_by_ai is None:
        extracted_by_ai = bool(notice.get("extracted_by_ai"))

    return {
        "id": notice.name,
        "client": notice.get("client"),
        "gstin": notice.get("gstin"),
        "section": notice.get("section"),
        "noticeType": notice.get("notice_type"),
        "label": notice.get("label"),
        "state": notice.get("state"),
        "city": notice.get("city"),
        "fy": notice.get("fy"),
        "amount": notice.get("amount"),
        "filedDate": str(notice.filed_date) if notice.get("filed_date") else None,
        "consultant": notice.get("consultant"),
        "risk": notice.get("risk"),
        "status": notice.get("status"),
        "dueDate": str(notice.due_date) if notice.get("due_date") else None,
        "sourcePdfUrl": notice.get("source_pdf_url"),
        "extractedByAi": extracted_by_ai,
        "resolvedOn": str(notice.resolved_on) if notice.get("resolved_on") else None,
        "noticeData": nd,
    }





def _call_ai_drafting_service(notice):
    """Placeholder — wire this up to your actual AI drafting integration."""
    raise NotImplementedError("Wire up your AI drafting service call here")




def extract_notice_from_pdf(notice_id, file_url):
    """
    Background job: runs after create_notice commits, when a PDF was uploaded.

    Loads the PDF, sends it to the AI extraction service, and writes back:
      - notice.notice_data (structured JSON)
      - flat fields (gstin, section, state, city, fy, amount, filed_date,
        due_date, notice_type) — only if currently empty
      - extracted_by_ai = 1

    Any failure is logged to Error Log; the notice itself remains saved.
    """
    import frappe
    import json
    import traceback

    try:
        notice = frappe.get_doc("Litigation Notice", notice_id)

        # 1. Call the AI service — must return a dict matching notice_data schema
        extracted = _call_ai_extraction_service(notice_id, file_url)

        if not extracted or not isinstance(extracted, dict):
            frappe.log_error(
                title="Notice extraction — empty result",
                message=f"notice_id={notice_id} file_url={file_url} result={extracted!r}",
            )
            return

        # 2. Persist the structured JSON
        notice.notice_data = extracted
        notice.extracted_by_ai = 1

        # 3. Fill flat fields — only if currently empty (don't overwrite user input)
        def _fill(fieldname, new_value):
            if new_value in (None, "", "0", 0):
                return
            current = notice.get(fieldname)
            if current in (None, "", "0", 0):
                notice.set(fieldname, new_value)

        taxpayer     = extracted.get("taxpayer") or {}
        demand       = extracted.get("demand") or {}
        notice_block = extracted.get("notice") or {}
        doc_ident    = extracted.get("document_identity") or {}
        jurisdiction = extracted.get("jurisdiction_and_authority") or {}
        provisions   = (extracted.get("legal_basis") or {}).get("provisions") or []

        _fill("gstin",       (taxpayer.get("gstin") or {}).get("value"))
        _fill("state",       jurisdiction.get("state"))
        _fill("city",        jurisdiction.get("division") or jurisdiction.get("circle"))
        _fill("fy",          (notice_block.get("financial_years") or [None])[0])
        _fill("amount",      (demand.get("total_demand") or {}).get("amount"))
        _fill("filed_date",  (notice_block.get("issue_date") or {}).get("value"))
        _fill("due_date",    (notice_block.get("explicit_reply_due_date") or {}).get("value"))
        _fill("section",     (provisions[0].get("section") if provisions else None))
        _fill("notice_type", (doc_ident.get("notice_form") or {}).get("value"))

        notice.save(ignore_permissions=True)
        frappe.db.commit()

        frappe.logger("litwatch").info(
            f"[extract_notice_from_pdf] OK notice={notice_id} keys={list(extracted.keys())}"
        )

    except Exception:
        frappe.db.rollback()
        frappe.log_error(
            title="Notice extraction failed",
            message=f"notice_id={notice_id}\nfile_url={file_url}\n\n{traceback.format_exc()}",
        )


EXTRACTION_PROMPT = """You are a GST notice extraction engine. Given page images of a scanned GST notice document, extract structured data and return ONLY a JSON object (no markdown, no explanation, no code fences) with this exact top-level shape:

{
  "portal_metadata": {...},
  "document_identity": {...},
  "taxpayer": {...},
  "notice": {...},
  "jurisdiction_and_authority": {...},
  "legal_basis": {...},
  "demand": {...},
  "issues": [...],
  "hearing": {...},
  "extensions": [...],
  "related_proceedings": {...},
  "document_contents": {...},
  "artifacts": {...},
  "review": {...}
}

For every extracted field with a value, wrap it as {"value": ..., "origin": "pdf_extracted", "confidence": <0-1>, "citation": {"source_document": <filename>, "page": <int>, "region": <string>}}. Use "origin": "ai_structured_from_pdf" for fields you infer/summarize rather than directly transcribe. Use null for anything not found. Follow field names and nesting exactly."""


def _call_ai_extraction_service(notice_id, file_url):
    import frappe, requests, json, base64
    from pdf2image import convert_from_bytes

    # 1. Load the file
    file_doc = frappe.get_doc("File", {"file_url": file_url})
    pdf_bytes = file_doc.get_content()
    if isinstance(pdf_bytes, str):
        pdf_bytes = pdf_bytes.encode("latin-1")

    # 2. Convert PDF pages to images (GPT-4o vision needs images, not raw PDF)
    pages = convert_from_bytes(pdf_bytes, dpi=150)

    image_blocks = []
    for page in pages:
        import io
        buf = io.BytesIO()
        page.save(buf, format="PNG")
        page_b64 = base64.b64encode(buf.getvalue()).decode()
        image_blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{page_b64}"},
        })

    # 3. Call the AI provider
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {frappe.conf.openai_api_key}"},
        json={
            "model": "gpt-4o",
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": EXTRACTION_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Extract the notice data. Source document filename: {file_doc.file_name}"},
                    *image_blocks,
                ]},
            ],
        },
        timeout=180,
    )
    result = response.json()

    if "choices" not in result:
        frappe.log_error(title="OpenAI extraction API error", message=json.dumps(result))
        raise Exception(f"OpenAI API error: {result.get('error', result)}")

    # 4. Parse and return (persistence handled by caller: extract_notice_from_pdf)
    notice_data = json.loads(result["choices"][0]["message"]["content"])
    return notice_data



def generate_notice_draft(notice_id):
    """
    Generate a response text for the notice.
    """
    try:
        notice = frappe.get_doc("Litigation Notice", notice_id)
        draft_text = _call_ai_drafting_service(notice)

        if draft_text:
            notice.db_set("draft_id", frappe.generate_hash(length=10))
            # If you have a field for the draft body, set it here:
            # notice.db_set("<draft_body_field>", draft_text)
            frappe.db.commit()

        return draft_text

    except Exception:
        frappe.log_error(
            title="Draft generation failed",
            message=frappe.get_traceback(),
        )






@frappe.whitelist(methods=["POST"])
def approve_and_send_notice(id=None, draftId=None, editedBody=None):
    """
    Approves a drafted notice response and marks the notice as Filed.

    Accepts `id` from (priority order):
      1. Function arg
      2. JSON body:     { "id": "N-09C4", ... }
      3. Query string:  ?id=N-09C4
      4. Raw query string
      5. frappe.form_dict

    Returns (raw, no "message" wrapper):
      { "notice": { "id": "<docname>", "status": "Filed" } }
    """
    from urllib.parse import parse_qs

    req = frappe.request

    body = {}
    if req and req.data:
        try:
            body = req.get_json(silent=True) or {}
        except Exception:
            body = {}

    args = dict(req.args or {}) if req else {}

    raw_qs = {}
    if req and req.query_string:
        try:
            qs_str = req.query_string
            if isinstance(qs_str, bytes):
                qs_str = qs_str.decode("utf-8", "ignore")
            parsed = parse_qs(qs_str)
            raw_qs = {k: v[0] for k, v in parsed.items() if v}
        except Exception:
            raw_qs = {}

    fd = dict(frappe.form_dict or {})

    # --- Resolve id ---
    if not id:
        id = body.get("id") or args.get("id") or raw_qs.get("id") or fd.get("id")

    # --- Resolve draftId ---
    if draftId is None:
        draftId = (
            body.get("draftId")
            or args.get("draftId")
            or raw_qs.get("draftId")
            or fd.get("draftId")
        )

    # --- Resolve editedBody ---
    if editedBody is None:
        for src in (body, args, raw_qs, fd):
            if "editedBody" in src:
                editedBody = src.get("editedBody")
                break

    # --- Validation ---
    if not id:
        frappe.throw(_("id is required"))

    if not frappe.db.exists("Litigation Notice", id):
        frappe.throw(
            _("Notice {0} not found").format(id),
            frappe.DoesNotExistError,
        )

    current_draft = frappe.db.get_value("Litigation Notice", id, "draft_id")

    if draftId and current_draft and current_draft != draftId:
        frappe.throw(
            _("Draft ID mismatch: expected {0}, got {1}").format(current_draft, draftId)
        )

    # --- Persist ---
    updates = {"status": "Filed"}

    if editedBody:
        updates["response_body"] = editedBody
        # ^ Confirm this field exists on Litigation Notice. If not, add it or
        #   comment this line out.

    frappe.db.set_value("Litigation Notice", id, updates)
    frappe.db.commit()

    # --- Return RAW JSON (no "message" wrapper) ---
    frappe.local.response.update({
        "notice": {
            "id": id,
            "status": "Filed",
        }
    })
    frappe.local.response.http_status_code = 200
    return

@frappe.whitelist(methods=["POST"])
def approve_and_send_route(id=None, **kwargs):
    """
    Route-style wrapper — reads id from URL path, draftId/editedBody from JSON body.
    Use for POST /notices/:id/approve-and-send (REST-style URLs).
    """
    data = frappe.request.get_json(silent=True) or {}
    return approve_and_send_notice(
        id=id,
        draftId=data.get("draftId"),
        editedBody=data.get("editedBody"),
    )

@frappe.whitelist(methods=["GET"])
def export_notice_register(client_key=None, status=None, format=None):
    """
    Export a client's notice register as XLSX or CSV.

    Used by: Notice Register page "Export" button — /notice-register

    Query params:
      client_key - Required. Client key, e.g. ACME001.
      status     - Optional. Filter by status (e.g. "Under review", "Filed").
      format     - Optional. "xlsx" (default) or "csv".

    Returns: binary file download.
    """
    import io
    from frappe.utils.xlsxutils import make_xlsx

    # --- Resolve client_key from query string if Frappe didn't bind it ---
    if not client_key and frappe.request:
        client_key = (
            frappe.request.args.get("client_key")
            or frappe.request.args.get("clientKey")
            or frappe.form_dict.get("client_key")
            or frappe.form_dict.get("clientKey")
        )

    # --- Resolve format / status similarly ---
    if not format and frappe.request:
        format = frappe.request.args.get("format") or frappe.form_dict.get("format")

    if not status and frappe.request:
        status = frappe.request.args.get("status") or frappe.form_dict.get("status")

    # --- Validation ---
    if not client_key:
        frappe.throw(_("client_key is required"))

    if not frappe.db.exists("Client", client_key):
        frappe.throw(
            _("Client {0} not found").format(client_key),
            frappe.DoesNotExistError,
        )

    fmt = (format or "xlsx").lower().strip()
    if fmt not in ("xlsx", "csv"):
        frappe.throw(
            _("Invalid format value: {0}. Must be one of ['xlsx', 'csv']").format(fmt)
        )

    # --- Fetch ---
    filters = {"client": client_key}
    if status:
        filters["status"] = status

    notices = frappe.get_all(
        "Litigation Notice",
        filters=filters,
        fields=[
            "notice_id", "gstin", "section", "notice_type", "label",
            "state", "city", "fy", "amount", "filed_date",
            "risk", "status", "due_date", "consultant",
        ],
        order_by="due_date asc",
        limit_page_length=0,
    )

    headers = [
        "Notice ID", "GSTIN", "Section", "Type", "Label",
        "State", "City", "FY", "Amount", "Filed Date",
        "Risk", "Status", "Due Date", "Consultant",
    ]

    rows = [headers]
    for n in notices:
        rows.append([
            n.notice_id, n.gstin, n.section, n.notice_type, n.label,
            n.state, n.city, n.fy, n.amount, n.filed_date,
            n.risk, n.status, n.due_date, n.consultant,
        ])

    # --- Output ---
    if fmt == "csv":
        import csv

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerows(rows)

        frappe.response["result"] = buffer.getvalue()
        frappe.response["type"] = "csv"
        frappe.response["doctype"] = "csv"
        return

    # XLSX
    xlsx_data = make_xlsx(rows, "Notice Register")
    frappe.response["filename"] = "notice-register.xlsx"
    frappe.response["filecontent"] = xlsx_data.getvalue()
    frappe.response["type"] = "binary"
    return