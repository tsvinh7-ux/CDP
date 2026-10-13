from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMarketingBridge(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("cdp.enabled", "0")
        Partner = cls.env["res.partner"]
        cls.owner = Partner.create({"name": "BS. Minh", "email": "minh@anphuc.vn"})
        cls.assistant = Partner.create({"name": "Lê Thu", "email": "thu@anphuc.vn"})
        cls.no_consent = Partner.create({"name": "BS. Hòa", "email": "hoa@hoabinh.vn"})
        cls.env["cdp.profile"]._cdp_upsert([
            {"partner_id": cls.owner.id, "segments": ["owners_basic"],
             "traits": {"job_role": "clinic_owner", "consent": {"marketing": True}}},
            {"partner_id": cls.assistant.id, "segments": ["owners_basic"],
             "traits": {"job_role": "assistant", "consent": {"marketing": True}}},
            {"partner_id": cls.no_consent.id, "segments": ["owners_basic"],
             "traits": {"job_role": "clinic_owner", "consent": {"marketing": False}}},
        ])
        cls.segment = cls.env["cdp.segment"].search([("key", "=", "owners_basic")])
        cls.mlist = cls.env["mailing.list"].create({"name": "CDP – chủ PK"})

    def test_mailing_targets_only_consenting_members(self):
        action = self.segment.action_create_mailing()
        mailing = self.env["mailing.mailing"].browse(action["res_id"])
        recipients = self.env["res.partner"].search(eval(mailing.mailing_domain))
        self.assertEqual(recipients, self.owner | self.assistant)

    def test_segment_synced_to_mailing_list(self):
        self.segment.mailing_list_id = self.mlist
        self.segment._sync_mailing_list()
        self.assertEqual(set(self.mlist.contact_ids.mapped("email")), {"minh@anphuc.vn", "thu@anphuc.vn"})
        # Leaving the segment removes the contact from the list.
        self.env["cdp.profile"]._cdp_upsert([{"partner_id": self.assistant.id, "segments": [], "traits": {}}])
        self.assertEqual(self.mlist.contact_ids.mapped("email"), ["minh@anphuc.vn"])

    def test_trigger_rules_for_owners(self):
        self.env["cdp.trigger.rule"].create([
            {"segment_id": self.segment.id, "action": "mailing_list", "mailing_list_id": self.mlist.id,
             "only_role": "clinic_owner"},
            {"segment_id": self.segment.id, "action": "activity", "activity_summary": "Gọi chủ PK"},
        ])
        Log = self.env["cdp.trigger.log"]
        owner_log = Log.create({"trigger_id": "t1", "key": "owners_basic", "segment_id": self.segment.id,
                                "partner_id": self.owner.id, "data": {}})
        owner_log._dispatch()
        self.assertEqual(owner_log.state, "done")
        self.assertIn("mailing.contact", owner_log.result)
        self.assertIn("minh@anphuc.vn", self.mlist.contact_ids.mapped("email"))
        assistant_log = Log.create({"trigger_id": "t2", "key": "owners_basic", "segment_id": self.segment.id,
                                    "partner_id": self.assistant.id, "data": {}})
        assistant_log._dispatch()
        self.assertIn("bỏ qua (vai trò)", assistant_log.result)
        activity = self.env["mail.activity"].search([("res_id", "=", self.assistant.id),
                                                     ("res_model", "=", "res.partner")])
        self.assertEqual(activity.summary, "Gọi chủ PK")

    def test_ma_adapter_reports_missing_model(self):
        rule = self.env["cdp.trigger.rule"].create({"segment_id": self.segment.id, "action": "ma_campaign",
                                                    "ma_campaign_id": 7, "ma_participant_model": "x.not.installed"})
        log = self.env["cdp.trigger.log"].create({"trigger_id": "t3", "key": "owners_basic",
                                                  "segment_id": self.segment.id, "partner_id": self.owner.id})
        log._dispatch()
        self.assertIn("chưa cài model x.not.installed", log.result)

    def test_ma_adapter_enrolls_participant(self):
        # Any model with campaign/record fields works; mailing.trace has neither, so use a
        # stand-in model with the same shape as Odoo Enterprise's marketing.participant.
        rule = self.env["cdp.trigger.rule"].create({
            "segment_id": self.segment.id, "action": "ma_campaign", "ma_campaign_id": self.mlist.id,
            "ma_participant_model": "mailing.subscription", "ma_campaign_field": "list_id",
            "ma_record_field": "contact_id"})
        contact = self.env["mailing.contact"].create({"name": "x", "email": "x@x.vn"})
        result = rule._do_ma(self.env["res.partner"].browse(contact.id))
        self.assertTrue(result.startswith("mailing.subscription,"))
        self.assertEqual(rule._do_ma(self.env["res.partner"].browse(contact.id)), "ma_campaign: đã có trong chiến dịch")
