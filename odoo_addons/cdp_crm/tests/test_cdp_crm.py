from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCrmTouchpoints(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("cdp.enabled", "1")
        cls.partner = cls.env["res.partner"].create({"name": "BS. Lan", "email": "lan@nhakhoa.vn"})
        cls.stages = cls.env["crm.stage"].search([], order="sequence")

    def events(self, event_type):
        return self.env["cdp.outbox"].search([("event_type", "=", event_type)])

    def test_lead_without_partner_uses_its_contact_details(self):
        lead = self.env["crm.lead"].create({"name": "Form website", "email_from": "Minh@X.vn", "phone": "0912 000 111",
                                            "description": "ghi chú nội bộ"})
        ev = self.events("lead.created").filtered(lambda e: e.res_id == lead.id)
        self.assertEqual(len(ev), 1)
        subject = ev.payload["subject"]
        self.assertEqual(subject["model"], "crm.lead")
        self.assertEqual(subject["identifiers"]["email"], "minh@x.vn")
        self.assertEqual(subject["identifiers"]["phone"], "+84912000111")
        self.assertNotIn("description", ev.payload["properties"])

    def test_stage_won_lost_and_activity(self):
        lead = self.env["crm.lead"].create({"name": "Implant", "partner_id": self.partner.id, "type": "opportunity",
                                            "stage_id": self.stages[0].id})
        lead.write({"stage_id": self.stages[1].id})
        ev = self.events("lead.stage_changed").filtered(lambda e: e.res_id == lead.id)
        self.assertEqual(ev.payload["properties"]["stage"], self.stages[1].name)
        self.assertEqual(ev.payload["properties"]["previous_stage"], self.stages[0].name)
        self.assertEqual(ev.payload["subject"]["model"], "res.partner")
        lead.action_set_won()
        self.assertTrue(self.events("lead.won").filtered(lambda e: e.res_id == lead.id))
        reason = self.env["crm.lost.reason"].create({"name": "Giá cao"})
        other = self.env["crm.lead"].create({"name": "Chỉnh nha", "partner_id": self.partner.id})
        other.action_set_lost(lost_reason_id=reason.id)
        lost = self.events("lead.lost").filtered(lambda e: e.res_id == other.id)
        self.assertEqual(lost.payload["properties"]["lost_reason"], "Giá cao")

        call = self.env.ref("mail.mail_activity_data_call")
        act = lead.activity_schedule(activity_type_id=call.id, summary="Tư vấn Implant")
        act.action_feedback(feedback="khách hẹn tuần sau")
        done = self.events("sales.activity_done").filtered(lambda e: e.payload["properties"].get("lead_id") == lead.id)
        self.assertEqual(len(done), 1)
        self.assertEqual(done.payload["properties"]["summary"], "Tư vấn Implant")
        self.assertNotIn("khách hẹn", str(done.payload))
