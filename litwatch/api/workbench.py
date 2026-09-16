# apps/litwatch/litwatch/api/workbench.py

import json
import frappe
from frappe import _
from frappe.utils import date_diff, nowdate


@frappe.whitelist(methods=["GET"])
def get_matter_workbench(client_key=None, id=None):
    """
    GET /clients/:clientKey/matters/:id/workbench

    Returns everything the Notice Workbench page needs for one matter.
    Covers all 7 stages in a single response.
    """

    # ─────────────────────────────────────────────
    # 1. Validate inputs
    # ─────────────────────────────────────────────
    if not client_key:
        frappe.throw(_("client_key is required"))
    if not id:
        frappe.throw(_("id is required"))

    # ─────────────────────────────────────────────
    # 2. Resolve client (by client_code, fall back to doc name)
    # ─────────────────────────────────────────────
    client_name = frappe.db.get_value("Client", {"client_code": client_key}, "name")
    if not client_name and frappe.db.exists("Client", client_key):
        client_name = client_key

    if not client_name:
        frappe.throw(
            _("Client {0} not found").format(client_key),
            frappe.DoesNotExistError,
        )

    client_display = frappe.db.get_value("Client", client_name, "client_name")

    # ─────────────────────────────────────────────
    # 3. Resolve matter (by doc name or matter_id)
    # ─────────────────────────────────────────────
    if frappe.db.exists("Workbench Matter", id):
        matter_name = id
    else:
        matter_name = frappe.db.get_value("Workbench Matter", {"matter_id": id}, "name")

    if not matter_name:
        frappe.throw(
            _("Matter {0} not found for client {1}").format(id, client_key),
            frappe.DoesNotExistError,
        )

    matter = frappe.get_doc("Workbench Matter", matter_name)
    matter.load_from_db()

    if matter.client != client_name:
        frappe.throw(
            _("Matter {0} not found for client {1}").format(id, client_key),
            frappe.DoesNotExistError,
        )

    matter.check_permission("read")

    # ─────────────────────────────────────────────
    # 4. daysLeft
    # ─────────────────────────────────────────────
    days_left = None
    if matter.due_date:
        days_left = date_diff(matter.due_date, nowdate())

    # ─────────────────────────────────────────────
    # 5. docs[]
    # ─────────────────────────────────────────────
    docs = []
    for d in (matter.docs or []):
        docs.append({
            "documentId": d.document_id or d.name,
            "name": d.document_name or d.name,
            "role": d.role or "main",
            "docType": d.doc_type or "Notice",
            "pages": d.pages or 0,
        })

    # ─────────────────────────────────────────────
    # 6. fields[] — from matter.fields
    # ─────────────────────────────────────────────
    has_check_field = bool(frappe.get_meta("Workbench Extraction Field").get_field("check"))

    fields = []
    for ef in (matter.fields or []):
        entry = {
            "label": ef.field_label,
            "value": ef.field_value,
            "confidence": ef.ocr_confidence,
            "source": ef.source or "",
        }
        if has_check_field and getattr(ef, "check", None):
            entry["check"] = ef.check
        if ef.needs_fix:
            entry["needsFix"] = True
            entry["note"] = ef.note or ""
        fields.append(entry)

    # ─────────────────────────────────────────────
    # 7. Validation result — query by matter
    # ─────────────────────────────────────────────
    issues = []
    risk = None

    vr_name = frappe.db.get_value(
        "Workbench Validation Result",
        {"matter": matter.name},
        "name",
    )

    if vr_name:
        vr = frappe.get_doc("Workbench Validation Result", vr_name)
        vr.load_from_db()

        issues = [
            {
                "category": i.category,
                "amount": i.amount,
                "text": i.text,
            }
            for i in (vr.issues or [])
        ]

        factors = []
        for f in (vr.factors or []):
            pts = f.points or 0
            factors.append({
                "label": f.factor_label,
                "detail": f.detail,
                "points": "+{0}".format(pts) if pts >= 0 else str(pts),
            })

        risk = {
            "score": vr.score,
            "band": vr.band,
            "factors": factors,
        }

    # ─────────────────────────────────────────────
    # 8. analysis{}
    # ─────────────────────────────────────────────
    analysis = None
    if matter.analysis:
        a = matter.analysis[0]
        a.load_from_db()   # force child hydration for findings/history/prior_matters

        try:
            columns = json.loads(a.columns) if a.columns else []
        except Exception:
            columns = []

        try:
            rows = json.loads(a.rows) if a.rows else []
        except Exception:
            rows = []

        findings = [
            {"tone": fi.tone, "lead": fi.lead, "text": fi.text}
            for fi in (a.findings or [])
        ]

        history = [
            {"ok": bool(h.ok), "text": h.text}
            for h in (a.history or [])
        ]

        prior_matters = []
        for pm in (a.prior_matters or []):
            section = pm.section or ""
            status = pm.status or ""
            text = " — ".join(x for x in [section, status] if x)
            prior_matters.append({
                "id": pm.notice_id,
                "text": text,
            })

        analysis = {
            "source": a.source,
            "title": a.title,
            "columns": columns,
            "rows": rows,
            "findings": findings,
            "history": history,
            "priorMatters": prior_matters,
            "conclusion": a.conclusion,
        }

    # ─────────────────────────────────────────────
    # 9. draft[] — sections + paragraphs
    # ─────────────────────────────────────────────
    draft = []
    for d in (matter.draft or []):
        d.load_from_db()   # force child hydration for paragraphs
        draft.append({
            "heading": d.heading or "",
            "paragraphs": [
                {"text": p.text or "", "gap": bool(p.gap)}
                for p in (d.paragraphs or [])
            ],
        })

    # ─────────────────────────────────────────────
    # 10. Response
    # ─────────────────────────────────────────────
    return {
        "id": matter.matter_id or matter.name,
        "clientKey": client_key,
        "clientName": client_display,
        "form": matter.form,
        "provision": matter.provision,
        "replyForm": matter.reply_form,
        "windowDays": matter.window,
        "gstin": matter.gstin,
        "arn": matter.arn,
        "alertCode": matter.alert_code,
        "issued": str(matter.issued_date) if matter.issued_date else None,
        "served": str(matter.served_date) if matter.served_date else None,
        "due": str(matter.due_date) if matter.due_date else None,
        "daysLeft": days_left,
        "taxPeriod": matter.tax_period,
        "stage": matter.stage,
        "confidence": (matter.confidence or 0) / 100.0,
        "demand": {
            "tax": matter.demand_tax or 0,
            "interest": matter.demand_interest or 0,
            "penalty": matter.demand_penalty or 0,
        },
        "docs": docs,
        "fields": fields,
        "issues": issues,
        "risk": risk,
        "analysis": analysis,
        "draft": draft,
    }