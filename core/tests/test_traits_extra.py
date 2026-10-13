from datetime import datetime, timedelta, timezone

from cdp_core.identity import find_by_partner
from cdp_core.jobs import create_tenant, refresh_tenant
from cdp_core.pipeline import ingest_odoo_batch
from cdp_core.traits import lead_score, recommend

from .conftest import msg, partner_msg

NOW = datetime.now(timezone.utc)
CATALOG = {
    "1": {"name": "Implant Cơ bản", "tags": ["Cơ bản", "Implant"]},
    "2": {"name": "Implant Nâng cao", "tags": ["Nâng cao", "Implant"]},
    "3": {"name": "Chỉnh nha Cơ bản", "tags": ["Cơ bản", "Chỉnh nha"]},
    "4": {"name": "Chỉnh nha Nâng cao", "tags": ["Nâng cao", "Chỉnh nha"], "published": False},
}


def test_recommend_next_level_same_topic():
    t = {"courses_enrolled": [{"id": "1"}], "courses_completed": ["1"]}
    rec = recommend(t, CATALOG, {})
    assert rec["next_best_course"]["name"] == "Implant Nâng cao"
    # Unpublished courses are never suggested.
    t = {"courses_enrolled": [{"id": "3"}]}
    assert recommend(t, CATALOG, {})["next_best_course"] is None


def test_recommend_for_clinic_owner():
    t = {"job_role": "clinic_owner", "courses_enrolled": [],
         "clinic": {"staff_courses": [{"id": "1", "name": "Implant Cơ bản"}]}}
    rec = recommend(t, CATALOG, {})
    assert rec["clinic_next_course"]["name"] == "Implant Nâng cao"
    assert rec["next_best_course"]["name"] == "Implant Cơ bản"  # what colleagues took


def test_lead_score_is_explainable():
    t = {"hot_courses": [{"id": "2", "views": 5}], "open_cart": {"amount": 1}, "job_role": "clinic_owner",
         "last_seen_at": NOW.isoformat(), "first_touch": {"medium": "cpc"}, "engagement_score": 50}
    score, reasons = lead_score(t, NOW)
    assert score == 25 + 4 + 25 + 10 + 10 + 5 + 10
    assert any("giỏ hàng" in r for r in reasons)


def test_owner_gets_clinic_recommendation_from_odoo_catalog(session, tenant):
    channel = lambda cid, name, tags: msg("entity.upserted", "entity.upserted", obj={"model": "slide.channel", "id": cid},
                                          properties={"fields": {"name": name, "tag_ids_names": tags, "is_published": True}})
    ingest_odoo_batch(session, tenant, [
        channel(1, "Implant Cơ bản", ["Cơ bản", "Implant"]), channel(2, "Implant Nâng cao", ["Nâng cao", "Implant"]),
        partner_msg(10, name="PK", is_company=True),
        partner_msg(11, name="Chủ", parent_id=10, dental_role="clinic_owner"),
        partner_msg(12, name="Phụ tá", parent_id=10, dental_role="assistant"),
        msg(subject={"model": "res.partner", "id": 12},
            properties={"course_id": 1, "course_name": "Implant Cơ bản", "course_tags": ["Cơ bản", "Implant"]}),
    ])
    owner = find_by_partner(session, tenant.id, 11)
    assert owner.traits["clinic_next_course"]["name"] == "Implant Nâng cao"
    assistant = find_by_partner(session, tenant.id, 12)
    assert assistant.traits["next_best_course"]["name"] == "Implant Nâng cao"


def test_dental_patient_traits_and_segments(session):
    clinic = create_tenant(session, "pk_anphuc", "Nha khoa An Phúc", "s", "wk_pk", None, {"vertical": "dental"})
    patient = {"model": "res.partner", "id": 1}
    seven_months_ago = NOW - timedelta(days=215)
    ingest_odoo_batch(session, clinic, [
        partner_msg(1, name="Bệnh nhân A", phone="0903000111"),
        msg(event_type="procedure.completed", subject=patient, occurred_at=seven_months_ago,
            properties={"service_group": "scaling", "amount": 300000, "branch_id": 1}),
        msg(event_type="appointment.booked", subject=patient, occurred_at=NOW - timedelta(days=40),
            properties={"appointment_id": 7, "scheduled_at": (NOW - timedelta(days=30)).isoformat()}),
        msg(event_type="appointment.no_show", subject=patient, occurred_at=NOW - timedelta(days=30),
            properties={"appointment_id": 7}),
        msg(event_type="treatment_plan.proposed", subject=patient, occurred_at=NOW - timedelta(days=5),
            properties={"plan_id": 3, "amount": 60000000, "service_groups": ["implant"]}),
        msg(event_type="installment.overdue", subject=patient,
            properties={"plan_id": 2, "due_date": "2026-10-01", "amount": 5000000}),
    ])
    p = find_by_partner(session, clinic.id, 1)
    t = p.traits
    assert t["recall_due"][0]["service_group"] == "scaling"
    assert t["no_show_rate"] == 1.0 and t["open_treatment_plans"][0]["plan_id"] == 3
    assert {"recall_due", "plan_not_accepted", "installment_overdue"} <= set(p.segment_keys)

    # Booking the next visit closes the recall and, with a no-show history, flags the appointment.
    ingest_odoo_batch(session, clinic, [msg(event_type="appointment.booked", subject=patient, properties={
        "appointment_id": 8, "scheduled_at": (NOW + timedelta(days=1)).isoformat(), "service_group": "scaling"})])
    refresh_tenant(session, clinic)
    p = find_by_partner(session, clinic.id, 1)
    assert "recall_due" not in p.segment_keys and "no_show_risk" in p.segment_keys
