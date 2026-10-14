"""
Business Workflow Agent: service-request triage.
Run:  python agent.py          (runs the 5 demo/test cases)
      python agent.py --json   (prints the final mock-system state)

Pipeline per request: CLASSIFY -> VALIDATE -> LOOKUP -> APPLY RULES ->
UPDATE mock system -> VERIFY (re-read) -> LOG. Anything missing, conflicting,
risky or unverifiable is ROUTED TO A HUMAN instead of guessed.
"""
import json, re, sys
from datetime import date

TODAY = date(2026, 10, 14)

# ---------- Business rules (edit these, not the code) ----------
RULES = {
    "refund_auto_limit": 100.00,      # refunds up to this are auto-approved
    "refund_window_days": 30,         # order must be this recent
    "manager_keywords": ["lawyer", "legal", "sue", "chargeback", "fraud", "press"],
}

# ---------- Mock system of record ----------
DB = {
    "orders": {
        "A100": {"customer": "Priya", "total": 45.00,  "date": "2026-10-05", "status": "delivered"},
        "A101": {"customer": "Marcus", "total": 240.00, "date": "2026-10-08", "status": "delivered"},
        "A102": {"customer": "Lena", "total": 80.00,  "date": "2026-10-12", "status": "processing"},
        "A103": {"customer": "Omar", "total": 60.00,  "date": "2026-08-01", "status": "delivered"},
    },
    "tickets": {},
    "audit_log": [],
}

# ---------- Tools (the agent's permitted actions) ----------
def lookup_order(order_id):
    return DB["orders"].get(order_id)

def update_ticket(tid, status, assignee, note):
    DB["tickets"][tid] = {"status": status, "assignee": assignee, "note": note}

def read_ticket(tid):
    return DB["tickets"].get(tid)

def log(tid, step, detail):
    DB["audit_log"].append({"ticket": tid, "step": step, "detail": detail})

# ---------- Agent steps ----------
def classify(text):
    t = text.lower()
    cats = []
    if re.search(r"refund|money back|charged twice", t): cats.append("refund")
    if re.search(r"change (my )?address|wrong address|ship to", t): cats.append("address_change")
    if re.search(r"cancel", t): cats.append("cancel")
    if re.search(r"not working|error|can't log in|cannot log in|outage", t): cats.append("technical")
    return cats

def decide(req):
    """Returns (status, assignee, reason). Never guesses on missing/conflicting info."""
    text = req["text"]
    # Rule 1: legal/risk language always goes to a manager
    hit = [k for k in RULES["manager_keywords"] if k in text.lower()]
    if hit:
        return "escalated", "manager", f"Risk keyword(s) {hit}"

    cats = classify(text)
    if not cats:
        return "escalated", "human_triage", "Could not classify request"
    # Rule 2: conflicting intents (e.g. cancel + address change) need a human
    if len(cats) > 1 and set(cats) != {"refund", "cancel"}:
        return "escalated", "human_triage", f"Conflicting intents: {cats}"
    cat = cats[0]

    if cat == "technical":
        return "routed", "support_team", "Technical issue -> support queue"

    # Order-based categories need an order id
    oid = req.get("order_id")
    if not oid:
        return "needs_info", "human_triage", "Missing order ID; request more info from customer"
    order = lookup_order(oid)
    if not order:
        return "escalated", "human_triage", f"Order {oid} not found"
    if order["customer"].lower() != req["customer"].lower():
        return "escalated", "manager", f"Order {oid} does not belong to {req['customer']}"

    if cat == "address_change":
        if order["status"] == "processing":
            return "resolved", "agent", "Address change approved (order not yet shipped)"
        return "escalated", "human_triage", f"Order already {order['status']}; cannot change address"

    if cat in ("refund", "cancel"):
        age = (TODAY - date.fromisoformat(order["date"])).days
        if age > RULES["refund_window_days"]:
            return "escalated", "manager", f"Order is {age} days old (> {RULES['refund_window_days']}-day window)"
        if order["total"] > RULES["refund_auto_limit"]:
            return "escalated", "manager", f"Amount ${order['total']:.2f} exceeds auto limit ${RULES['refund_auto_limit']:.0f}"
        return "resolved", "agent", f"Refund of ${order['total']:.2f} approved (within limit and window)"

    return "escalated", "human_triage", "No matching procedure"

def handle(req):
    tid = req["id"]
    log(tid, "received", req["text"])
    status, assignee, reason = decide(req)
    log(tid, "decision", f"{status} -> {assignee}: {reason}")
    update_ticket(tid, status, assignee, reason)
    # VERIFY: re-read the system and confirm the write actually landed
    saved = read_ticket(tid)
    ok = saved == {"status": status, "assignee": assignee, "note": reason}
    log(tid, "verify", "OK" if ok else "MISMATCH")
    if not ok:  # recover safely: flag for a human instead of continuing
        update_ticket(tid, "escalated", "human_triage", "Verification failed; manual check")
    return {"ticket": tid, "status": status, "assignee": assignee, "reason": reason, "verified": ok}

# ---------- Test cases ----------
TESTS = [
    # 1. Normal: small refund inside window -> auto-resolved
    ({"id": "T1", "customer": "Priya", "order_id": "A100",
      "text": "I'd like a refund for my order, it arrived damaged."}, "resolved"),
    # 2. Missing info: no order ID -> asks human
    ({"id": "T2", "customer": "Lena", "order_id": None,
      "text": "Please change my address, I moved last week."}, "needs_info"),
    # 3. Exception: refund over limit -> manager
    ({"id": "T3", "customer": "Marcus", "order_id": "A101",
      "text": "I want my money back for this order."}, "escalated"),
    # 4. Conflicting instructions -> human
    ({"id": "T4", "customer": "Lena", "order_id": "A102",
      "text": "Cancel my order but also ship to my new address."}, "escalated"),
    # 5. Risk language -> manager
    ({"id": "T5", "customer": "Omar", "order_id": "A103",
      "text": "Refund me now or I will call my lawyer."}, "escalated"),
]

if __name__ == "__main__":
    passed = 0
    for req, expected in TESTS:
        r = handle(req)
        good = r["status"] == expected and r["verified"]
        passed += good
        print(f"[{'PASS' if good else 'FAIL'}] {r['ticket']}: {r['status']:<10} -> {r['assignee']:<13} | {r['reason']}")
    print(f"\n{passed}/{len(TESTS)} tests passed; {len(DB['audit_log'])} audit entries logged.")
    if "--json" in sys.argv:
        print(json.dumps(DB["tickets"], indent=2))
