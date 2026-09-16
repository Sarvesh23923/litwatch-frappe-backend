# ---------------------------------------------------------------------------
# GET /clients/:clientKey/matters  (Litigation Spine)
# URL: GET /api/method/litwatch.api.get_client_matters
# ---------------------------------------------------------------------------
import json as _json
import frappe
from frappe import _
from frappe.utils import getdate, nowdate, date_diff

FLOW_VALUES = ["scrutiny", "demand", "appeal", "registration", "detention", "nonfiler"]
STATUS_VALUES = ["all", "open", "urgent", "closed"]


@frappe.whitelist(methods=["GET"])
def get_client_matters(client_key=None, flow=None, status=None):
    """
    Returns all litigation matters (cases) for a client, powering the
    Litigation Spine page's timeline cards.

    Query params:
      client_key - Required. Client key, e.g. ACME001 (or portfolio key for "all clients").
      flow       - Optional. scrutiny | demand | appeal | registration | detention | nonfiler
      status     - Optional. all | open | urgent | closed
    """

    if not client_key:
        frappe.throw(_("client_key is required"))

    # ASSUMPTION: "all" is the portfolio key for "all clients" — confirm
    # this matches whatever useActiveClient() actually sends.
    is_all_clients = client_key.lower() == "all"

    if not is_all_clients and not frappe.db.exists("Client", client_key):
        # Plain frappe.throw() (no exc class) → HTTP 417, matching spec.
        frappe.throw(_("Client {0} not found").format(client_key))

    if flow and flow not in FLOW_VALUES:
        frappe.throw(_("Invalid flow value: {0}. Must be one of {1}").format(flow, FLOW_VALUES))

    if status and status not in STATUS_VALUES:
        frappe.throw(_("Invalid status value: {0}. Must be one of {1}").format(status, STATUS_VALUES))

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
            "id": c["notice"],  # per doc §6.2: one matter = one notice → id is the notice's real id
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