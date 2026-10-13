import hashlib
import hmac
import json
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from odoo.tests import HttpCase, TransactionCase, tagged

from ..models.tools import normalize_phone, sign


class CdpCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        for key, value in {"cdp.enabled": "1", "cdp.core_url": "http://cdp.test", "cdp.tenant_id": "dmag",
                           "cdp.secret": "s3cret"}.items():
            icp.set_param(key, value)
        cls.Outbox = cls.env["cdp.outbox"]

    def flush_precommit(self):
        self.env.flush_all()
        self.env.cr.precommit.run()


@tagged("post_install", "-at_install")
class TestOutbox(CdpCase):

    def test_writes_are_collapsed_into_one_snapshot(self):
        partner = self.env["res.partner"].create({"name": "BS. Lan", "phone": "0901234567"})
        partner.write({"email": "lan@example.com"})
        partner.write({"city": "Hà Nội"})
        partner.write({"comment": "not mapped"})
        self.flush_precommit()
        rows = self.Outbox.search([("res_model", "=", "res.partner"), ("res_id", "=", partner.id)])
        self.assertEqual(len(rows), 1)
        fields = rows.payload["properties"]["fields"]
        self.assertEqual(fields["email"], "lan@example.com")
        self.assertEqual(fields["city"], "Hà Nội")
        self.assertNotIn("comment", fields)

    def test_disabled_means_no_outbox(self):
        self.env["ir.config_parameter"].sudo().set_param("cdp.enabled", "0")
        self.env["res.partner"].create({"name": "X"})
        self.flush_precommit()
        self.assertFalse(self.Outbox.search([("res_model", "=", "res.partner")]))

    def test_business_event_subject(self):
        partner = self.env["res.partner"].create({"name": "A", "mobile": "0168 812 3456", "email": "A@X.VN"})
        row = self.Outbox._emit("order.paid", partner, None, {"amount": 1000})
        subject = row.payload["subject"]
        self.assertEqual(subject["identifiers"], {"phone": "+84388123456", "email": "a@x.vn"})
        public = self.env.ref("base.public_partner")
        self.assertFalse(self.Outbox._emit("cart.updated", public))

    def test_send_batch_marks_rows(self):
        partner = self.env["res.partner"].create({"name": "A"})
        row = self.Outbox._emit("order.paid", partner)

        class Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"accepted": [row.event_id], "duplicates": [], "rejected": []}

        with patch("odoo.addons.cdp_base.models.cdp_outbox.requests.post", return_value=Resp()) as post:
            self.Outbox._send_batch()
        sent = json.loads(post.call_args.kwargs["data"])
        self.assertIn(row.event_id, [m["event_id"] for m in sent["messages"]])
        self.assertEqual(row.state, "sent")

    def test_profile_upsert_does_not_touch_partner(self):
        partner = self.env["res.partner"].create({"name": "Chủ PK"})
        self.flush_precommit()
        write_date = partner.write_date
        unknown = self.env["cdp.profile"]._cdp_upsert([
            {"partner_id": partner.id, "cdp_id": "prf_1", "segments": ["clinic_owners"],
             "traits": {"rfm_segment": "champion", "total_spent": 12000000,
                        "timeline": [{"at": "2026-10-13T08:00:00+00:00", "label": "Xem khóa Implant"}]}},
            {"partner_id": 999999999, "segments": []},
        ])
        self.assertEqual(unknown, [999999999])
        found = self.env["res.partner"].search([("cdp_segment_ids.key", "=", "clinic_owners")])
        self.assertEqual(found, partner)
        self.assertEqual(partner.cdp_rfm_segment, "champion")
        self.assertIn("Implant", partner.cdp_timeline_html)
        self.assertIn("<table", partner.cdp_timeline_html)
        self.assertNotIn("&lt;", partner.cdp_timeline_html)
        self.assertEqual(partner.write_date, write_date)

    def test_trigger_creates_activity(self):
        partner = self.env["res.partner"].create({"name": "Chủ PK"})
        log = self.env["cdp.trigger.log"].create({
            "trigger_id": "trg_1", "key": "checkout_abandoned", "partner_id": partner.id,
            "data": {"segment_name": "Bỏ giỏ hàng", "open_cart": {"amount": 4500000}},
        })
        log._dispatch()
        self.assertEqual(log.state, "done")
        activity = self.env["mail.activity"].search([("res_model", "=", "res.partner"), ("res_id", "=", partner.id)])
        self.assertIn("Bỏ giỏ hàng", activity.note)

    def test_consent_changes_are_sent(self):
        partner = self.env["res.partner"].create({"name": "BS. Lan", "email": "lan@example.com"})
        self.env["cdp.consent"]._set(partner, "ads", True, channel="form", evidence="F-12")
        self.env["cdp.consent"]._set(partner, "ads", False, channel="phone")
        rows = self.Outbox.search([("event_type", "=", "consent.updated")], order="id")
        self.assertEqual([r.payload["properties"]["granted"] for r in rows], [True, False])
        self.assertEqual(rows[0].payload["properties"]["evidence_ref"], "F-12")

    def test_email_unsubscribe_withdraws_marketing(self):
        partner = self.env["res.partner"].create({"name": "BS. Lan", "email": "Lan@Example.com"})
        self.env["mail.blacklist"]._add("lan@example.com")
        consent = partner.cdp_consent_ids.filtered(lambda c: c.purpose == "marketing")
        self.assertFalse(consent.granted)
        self.assertEqual(consent.channel, "email")

    def test_erasure_request_excludes_partner(self):
        partner = self.env["res.partner"].create({"name": "BS. Lan", "email": "lan@example.com"})
        self.env["cdp.profile"]._cdp_upsert([{"partner_id": partner.id, "segments": [], "traits": {}}])
        partner.action_cdp_request_erasure()
        self.assertTrue(partner.cdp_excluded)
        self.assertFalse(partner.cdp_profile_ids)
        self.assertTrue(self.Outbox.search([("event_type", "=", "privacy.erasure_requested")]))
        # Nothing about this person leaves Odoo any more.
        self.flush_precommit()
        before = self.Outbox.search_count([])
        partner.write({"city": "Huế"})
        self.Outbox._emit("order.paid", partner)
        self.flush_precommit()
        self.assertEqual(self.Outbox.search_count([]), before)

    def test_erased_tombstone_from_core_clears_copy(self):
        partner = self.env["res.partner"].create({"name": "BS. Lan"})
        self.env["cdp.profile"]._cdp_upsert([{"partner_id": partner.id, "segments": ["x"], "traits": {}}])
        self.env["cdp.profile"]._cdp_upsert([{"partner_id": partner.id, "segments": [], "traits": {"erased": True}}])
        self.assertFalse(partner.cdp_profile_ids)
        self.assertTrue(partner.cdp_excluded)

    def test_internal_users_are_not_customers(self):
        admin_partner = self.env.ref("base.user_admin").partner_id
        admin_partner.write({"city": "Hà Nội"})
        self.flush_precommit()
        self.assertFalse(self.Outbox.search([("res_model", "=", "res.partner"), ("res_id", "=", admin_partner.id)]))
        self.assertFalse(self.Outbox._emit("order.paid", admin_partner))

    def test_phone_normalisation(self):
        self.assertEqual(normalize_phone("84 90 123 4567"), "+84901234567")
        self.assertIsNone(normalize_phone("0000000000"))


@tagged("post_install", "-at_install")
class TestReceiver(HttpCase):

    def setUp(self):
        super().setUp()
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("cdp.tenant_id", "dmag")
        icp.set_param("cdp.secret", "s3cret")

    def _post(self, path, payload, secret="s3cret"):
        body = json.dumps(payload).encode()
        ts = int(time.time())
        headers = {"Content-Type": "application/json", "X-CDP-Tenant": "dmag",
                   "X-CDP-Timestamp": str(ts), "X-CDP-Signature": sign(secret, ts, body)}
        return self.url_open(path, data=body, headers=headers)

    def test_signature_required(self):
        resp = self._post("/cdp/v1/profiles", {"profiles": []}, secret="wrong")
        self.assertEqual(resp.status_code, 401)

    def test_trigger_is_idempotent(self):
        partner = self.env["res.partner"].create({"name": "Chủ PK"})
        payload = {"trigger_id": "trg_42", "key": "hot_leads", "partner_id": partner.id, "data": {}}
        self.assertEqual(self._post("/cdp/v1/triggers", payload).json()["status"], "done")
        self.assertEqual(self._post("/cdp/v1/triggers", payload).json()["status"], "duplicate")


@tagged("post_install", "-at_install")
class TestJourneyEmbed(CdpCase):

    def test_journey_embed_link_and_prediction_summary(self):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("cdp.public_url", "https://cdp.example.vn/")
        icp.set_param("cdp.tenant_id", "dmag")
        icp.set_param("cdp.secret", "s3cret")
        partner = self.env["res.partner"].create({"name": "BS. Nam"})
        self.assertFalse(partner.cdp_journey_html)  # no CDP profile yet
        self.env["cdp.profile"]._cdp_upsert([{"partner_id": partner.id, "kind": "person", "traits": {
            "purchase_probability_30d": 0.41,
            "predicted_products": [{"item": "Implant Nâng cao", "probability": 0.37}],
            "next_touchpoints": [{"label": "Xem khóa học", "expected_in_days": 3.5, "probability": 0.6}]}}])
        partner.invalidate_recordset()
        html = str(partner.cdp_journey_html)
        self.assertIn("https://cdp.example.vn/embed/journey?tenant=dmag&amp;partner_id=%d" % partner.id, html)
        url = partner._cdp_journey_url()
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        expected = hmac.new(b"s3cret", f"dmag.{partner.id}.{q['exp']}".encode(), hashlib.sha256).hexdigest()
        self.assertEqual(q["sig"], expected)
        summary = str(partner.cdp_summary_html)
        self.assertIn("Implant Nâng cao (37%)", summary)
        self.assertIn("41%", summary)
