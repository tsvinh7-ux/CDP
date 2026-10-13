"""CDP Core side of the demo (admin API):

    python core_steps.py destinations   # Facebook CAPI/Audiences + Zalo ZNS pointed at the MOCK providers
    python core_steps.py zalo           # signed Zalo OA webhooks: Minh follows the OA and shares his phone
    python core_steps.py dental         # second tenant (dental clinic) fed with Dental ERP-style events
    python core_steps.py summary        # what was sent to the (mock) providers
"""
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx

CORE = os.environ.get("CDP_CORE_URL", "http://localhost:8000")
MOCK = os.environ.get("MOCK_URL", "http://localhost:9000")
H = {"X-Admin-Token": os.environ.get("CDP_ADMIN_TOKEN", "demo-admin")}
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "core"))
from cdp_core.security import signed_headers  # noqa: E402


def destinations():
    dests = [
        {"type": "facebook_capi", "name": "Meta Pixel DMAG (mock)",
         "config": {"pixel_id": "PX_DMAG", "graph_url": f"{MOCK}/graph"}, "secrets": {"access_token": "demo-token"}},
        {"type": "facebook_audience", "name": "Meta Custom Audiences (mock)",
         "config": {"ad_account_id": "act_DMAG", "segments": ["clinic_owners", "high_value"],
                    "name_prefix": "DMAG CDP", "graph_url": f"{MOCK}/graph"},
         "secrets": {"access_token": "demo-token"}},
        {"type": "zalo_zns", "name": "Zalo OA DMAG (mock)",
         "config": {"app_id": "APP_DMAG", "oa_id": "OA_DMAG", "mode": "development",
                    "oauth_url": f"{MOCK}/zalo/oauth", "zns_url": f"{MOCK}/zalo/zns",
                    "templates": {
                        "owner_staff_basic_no_advanced": {"template_id": "ZNS_OWNER_ADV", "purpose": "marketing",
                            "data": {"ten_khach": "name", "phong_kham": "clinic.name",
                                     "khoa_goi_y": "clinic_next_course.name"}},
                        "checkout_abandoned": {"template_id": "ZNS_CART", "purpose": "marketing",
                            "data": {"ten_khach": "name", "khoa_hoc": "open_cart.items.0.name",
                                     "so_tien": "open_cart.amount"}},
                        "awaiting_transfer": {"template_id": "ZNS_PAYMENT", "purpose": "care",
                            "data": {"ten_khach": "name", "ma_don": "awaiting_payment.0.order_name",
                                     "so_tien": "awaiting_payment.0.amount"}},
                    }},
         "secrets": {"secret_key": "demo-app-secret", "refresh_token": "demo-refresh", "oa_secret_key": "demo-oa-secret"}},
    ]
    for d in dests:
        r = httpx.post(f"{CORE}/v1/admin/destinations?tenant=dmag", headers=H, json=d)
        r.raise_for_status()
        print("destination", r.json()["id"], d["name"])


def zalo():
    def send(body):
        raw = json.dumps(body).encode()
        sig = "mac=" + hashlib.sha256(b"APP_DMAG" + raw + str(body["timestamp"]).encode() + b"demo-oa-secret").hexdigest()
        r = httpx.post(f"{CORE}/v1/webhooks/zalo/dmag", content=raw, headers={"X-ZEvent-Signature": sig})
        print(body["event_name"], r.status_code, r.json())
    ts = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    send({"app_id": "APP_DMAG", "event_name": "follow", "timestamp": ts, "follower": {"id": "zalo-user-minh"}})
    send({"app_id": "APP_DMAG", "event_name": "user_submit_info", "timestamp": str(int(ts) + 1),
          "sender": {"id": "zalo-user-minh"}, "info": {"name": "Trần Minh", "phone": "0901111111"}})


def dental():
    tenant = {"id": "pk_anphuc", "name": "Nha khoa An Phúc (Dental ERP)", "secret": "pk-secret", "write_key": "wk_pk",
              "settings": {"vertical": "dental"}}
    httpx.post(f"{CORE}/v1/admin/tenants", headers=H, json=tenant)
    now = datetime.now(timezone.utc)

    def ev(event_type, pid, props, at=None, kind="event", obj=None):
        m = {"spec_version": "1.0", "event_id": str(uuid.uuid4()), "kind": kind, "event_type": event_type,
             "occurred_at": (at or now).isoformat(), "properties": props}
        if kind == "event":
            m["subject"] = {"model": "res.partner", "id": pid}
        if obj:
            m["object"] = obj
        return m

    def patient(pid, name, phone):
        return ev("entity.upserted", None, {"fields": {"name": name, "mobile": phone}}, kind="entity.upserted",
                  obj={"model": "res.partner", "id": pid})

    msgs = [
        patient(1, "Chị Nguyễn Thị Mai", "0905111222"), patient(2, "Anh Lê Văn Nam", "0905333444"),
        patient(3, "Bé Trần An (phụ huynh: Chị Hạnh)", "0905555666"),
        # Mai: scaling 7 months ago, nothing booked → recall due.
        ev("procedure.completed", 1, {"service_group": "scaling", "amount": 400000, "branch_id": 1}, now - timedelta(days=215)),
        # Nam: implant plan proposed 5 days ago, not accepted; missed an appointment; next one tomorrow.
        ev("treatment_plan.proposed", 2, {"plan_id": 21, "amount": 32000000, "service_groups": ["implant"]}, now - timedelta(days=5)),
        ev("appointment.booked", 2, {"appointment_id": 31, "scheduled_at": (now - timedelta(days=20)).isoformat()}, now - timedelta(days=25)),
        ev("appointment.no_show", 2, {"appointment_id": 31}, now - timedelta(days=20)),
        ev("appointment.booked", 2, {"appointment_id": 32, "scheduled_at": (now + timedelta(days=1)).isoformat(),
                                     "service_group": "implant_consult"}, now - timedelta(days=2)),
        # An: orthodontics with an overdue instalment.
        ev("treatment_plan.accepted", 3, {"plan_id": 41, "amount": 45000000, "service_groups": ["ortho"]}, now - timedelta(days=90)),
        ev("procedure.completed", 3, {"service_group": "ortho", "amount": 15000000}, now - timedelta(days=60)),
        ev("installment.overdue", 3, {"plan_id": 41, "due_date": (now - timedelta(days=10)).date().isoformat(), "amount": 5000000}),
    ]
    body = json.dumps({"messages": msgs}).encode()
    r = httpx.post(f"{CORE}/v1/batch", content=body, headers=signed_headers("pk_anphuc", "pk-secret", body))
    print("dental batch", r.status_code, len(r.json()["accepted"]), "accepted")
    httpx.post(f"{CORE}/v1/admin/run-jobs?tenant=pk_anphuc", headers=H, timeout=60)
    for p in httpx.get(f"{CORE}/v1/admin/profiles?tenant=pk_anphuc", headers=H).json():
        print(f"  {p['name']:<36} {p['segments']}")


def summary():
    calls = httpx.get(f"{MOCK}/log").json()
    for c in calls:
        body = c["body"]
        if c["provider"] == "facebook_capi":
            detail = ", ".join(f"{e['event_name']} {e.get('custom_data', {}).get('value', '')}" for e in body.get("data", []))
        elif c["provider"] == "facebook_audience":
            detail = body.get("name") or f"{c['method']} {len((body.get('payload') or {}).get('data') or [])} người (hash SHA-256)"
        elif c["provider"] == "zalo_zns":
            detail = f"{body.get('template_id')} → {body.get('phone')} {body.get('template_data')}"
        else:
            detail = "làm mới access token"
        print(f"  [{c['at']}] {c['provider']:<18} {detail}")
    out = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "demo", "mock-provider-calls.json")
    with open(out, "w") as f:
        json.dump(calls, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    {"destinations": destinations, "zalo": zalo, "dental": dental, "summary": summary}[sys.argv[1]]()
