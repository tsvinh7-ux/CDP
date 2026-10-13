"""Demo steps executed inside `odoo-bin shell` (env is provided by the shell).

    DEMO_STEP=seed  odoo-bin shell -c odoo.conf -d dmag < odoo_steps.py
    DEMO_STEP=send  ...   # flush the CDP outbox to Core now (instead of waiting for the cron)
    DEMO_STEP=sepay_order ...
    DEMO_STEP=report ...
"""
import os

step = os.environ.get("DEMO_STEP", "seed")
CORE_URL = os.environ.get("CDP_CORE_URL", "http://localhost:8000")
SECRET = os.environ.get("CDP_SECRET", "demo-secret")


def seed():
    icp = env["ir.config_parameter"].sudo()
    for key, value in {
        "cdp.enabled": "1", "cdp.core_url": CORE_URL, "cdp.public_url": CORE_URL,
        "cdp.tenant_id": "dmag", "cdp.secret": SECRET, "cdp.write_key": "wk_dmag",
    }.items():
        icp.set_param(key, value)
    env.ref("base.user_admin").write({"password": "admin"})
    env.ref("base.main_company").write({"name": "DMAG Academy"})
    vnd = env.ref("base.VND")
    vnd.active = True
    env.ref("base.main_company").currency_id = vnd
    env["product.pricelist"].search([]).write({"currency_id": vnd.id})

    # Courses: level tags drive the clinic-owner segment.
    group = env["slide.channel.tag.group"].create({"name": "Cấp độ", "is_published": True})
    Tag = env["slide.channel.tag"]
    basic = Tag.create({"name": "Cơ bản", "group_id": group.id})
    advanced = Tag.create({"name": "Nâng cao", "group_id": group.id})
    implant = Tag.create({"name": "Implant", "group_id": group.id})
    ortho = Tag.create({"name": "Chỉnh nha", "group_id": group.id})

    def course(name, tags, price, lessons):
        product = env["product.product"].create({
            "name": name, "list_price": price, "detailed_type": "course",
            "website_published": True, "invoice_policy": "order",
        })
        channel = env["slide.channel"].create({
            "name": name, "enroll": "payment", "product_id": product.id,
            "tag_ids": [(6, 0, tags.ids)], "is_published": True,
            "description": f"Khóa học {name} dành cho bác sĩ và phụ tá nha khoa.",
        })
        env["slide.slide"].create([{
            "name": f"Bài {i}: {lesson}", "channel_id": channel.id, "slide_category": "article",
            "html_content": f"<p>{lesson}</p>", "is_published": True, "is_preview": i == 1,
        } for i, lesson in enumerate(lessons, 1)])
        return channel

    c_basic = course("Implant Cơ bản", basic | implant, 3_500_000,
                     ["Giải phẫu ứng dụng", "Lập kế hoạch điều trị", "Kỹ thuật cấy cơ bản"])
    c_adv = course("Implant Nâng cao", advanced | implant, 12_000_000,
                   ["Ghép xương", "Nâng xoang", "Implant tức thì"])
    c_ortho = course("Chỉnh nha Cơ bản", basic | ortho, 4_200_000,
                     ["Chẩn đoán", "Khí cụ tháo lắp"])

    # Payment providers as they will appear with Bizapp's modules.
    demo = env.ref("payment.payment_provider_demo")
    demo.write({"state": "test", "is_published": True})
    vnpay = demo.copy({"name": "VNPay (demo)", "state": "test", "is_published": True})
    sepay = demo.copy({"name": "SePay chuyển khoản (demo)", "state": "test", "is_published": True})

    Partner = env["res.partner"]
    portal = env.ref("base.group_portal")

    def clinic(name, city, phone):
        return Partner.create({"name": name, "is_company": True, "city": city, "phone": phone})

    def person(name, company, role, email, phone, login=False):
        p = Partner.create({"name": name, "parent_id": company.id, "dental_role": role,
                            "email": email, "mobile": phone})
        if login:
            env["res.users"].with_context(no_reset_password=True).create({
                "name": name, "login": email, "password": "demo1234", "partner_id": p.id,
                "groups_id": [(6, 0, [portal.id])],
            })
        return p

    an_phuc = clinic("Nha khoa An Phúc", "TP. Hồ Chí Minh", "028 3812 3456")
    minh = person("BS. Trần Minh", an_phuc, "clinic_owner", "minh@anphuc.vn", "0901 111 111", login=True)
    thu = person("Lê Thu", an_phuc, "assistant", "thu@anphuc.vn", "0902 222 222")
    hung = person("BS. Phạm Hùng", an_phuc, "dentist", "hung@anphuc.vn", "0903 333 333")
    # Receptionist registered with the owner's phone: must NOT be merged with him.
    person("Lễ tân An Phúc", an_phuc, "other", "letan@anphuc.vn", "0901111111")

    # Consent collected on the registration form (Luật 91/2025): Minh accepts marketing + ads.
    Consent = env["cdp.consent"]
    Consent._set(minh, "marketing", True, channel="form", evidence="Phiếu đăng ký lớp Implant 09/2026")
    Consent._set(minh, "ads", True, channel="form", evidence="Phiếu đăng ký lớp Implant 09/2026")
    Consent._set(thu, "marketing", True, channel="website", evidence="Checkbox khi mua khóa")

    hoa_binh = clinic("Nha khoa Hòa Bình", "Hà Nội", "024 3999 8888")
    hoa = person("BS. Nguyễn Hòa", hoa_binh, "clinic_owner", "hoa@hoabinh.vn", "0911 444 444")
    lan = person("BS. Đỗ Lan", hoa_binh, "dentist", "lan@hoabinh.vn", "0912 555 555")
    Consent._set(lan, "ads", True, channel="form", evidence="Phiếu khảo sát hội thảo")

    def paid_order(partner, channel, provider):
        order = env["sale.order"].create({
            "partner_id": partner.id, "website_id": env["website"].search([], limit=1).id,
            "order_line": [(0, 0, {"product_id": channel.product_id.id, "product_uom_qty": 1})],
        })
        tx = env["payment.transaction"].create({
            "provider_id": provider.id, "payment_method_id": env.ref("payment.payment_method_unknown").id,
            "amount": order.amount_total, "currency_id": order.currency_id.id,
            "partner_id": partner.id, "sale_order_ids": [(6, 0, order.ids)],
        })
        tx._set_done()
        order.with_context(send_email=False).action_confirm()  # enrols the buyer (website_sale_slides)
        return order

    def learn(partner, channel, n_lessons):
        for slide in channel.slide_ids.sorted("sequence")[:n_lessons]:
            env["slide.slide.partner"].create({"slide_id": slide.id, "partner_id": partner.id,
                                               "channel_id": channel.id, "completed": True})

    # An Phúc staff learned the BASIC implant course (paid via VNPay / SePay).
    paid_order(thu, c_basic, vnpay)
    learn(thu, c_basic, 3)          # completed
    paid_order(hung, c_basic, sepay)
    learn(hung, c_basic, 1)         # in progress
    # Hòa Bình already moved to the ADVANCED course.
    paid_order(lan, c_basic, vnpay)
    paid_order(lan, c_adv, vnpay)
    learn(lan, c_adv, 2)
    paid_order(hoa, c_ortho, vnpay)

    env.cr.commit()
    print("SEED OK", {"minh": minh.id, "hoa": hoa.id, "courses": [c_basic.id, c_adv.id, c_ortho.id],
                      "sepay_provider": sepay.id})


def send():
    outbox = env["cdp.outbox"]
    before = outbox.search_count([("state", "in", ("pending", "failed"))])
    outbox._cron_send()
    env.cr.commit()
    after = outbox.search_count([("state", "in", ("pending", "failed"))])
    print(f"OUTBOX sent {before - after} messages, {after} left, "
          f"{outbox.search_count([('state', '=', 'dead')])} dead")


def sepay_order():
    """Hòa (owner) orders the advanced course and picks SePay transfer, but has not paid yet."""
    hoa = env["res.partner"].search([("email", "=", "hoa@hoabinh.vn")], limit=1)
    channel = env["slide.channel"].search([("name", "=", "Implant Nâng cao")], limit=1)
    sepay = env["payment.provider"].search([("name", "ilike", "SePay")], limit=1)
    order = env["sale.order"].create({
        "partner_id": hoa.id, "website_id": env["website"].search([], limit=1).id,
        "order_line": [(0, 0, {"product_id": channel.product_id.id, "product_uom_qty": 1})],
    })
    env["payment.transaction"].create({
        "provider_id": sepay.id, "payment_method_id": env.ref("payment.payment_method_unknown").id,
        "amount": order.amount_total, "currency_id": order.currency_id.id,
        "partner_id": hoa.id, "sale_order_ids": [(6, 0, order.ids)],
    })  # order.placed is emitted on creation; SePay's webhook will later call _set_done()
    env.cr.commit()
    print("SEPAY ORDER", order.name, order.amount_total)


def rules():
    """Marketing bridge: what Odoo does when CDP reports segment entries (owners first)."""
    Segment = env["cdp.segment"]
    hot = Segment.search([("key", "=", "hot_leads")])
    owners = Segment.search([("key", "=", "owner_staff_basic_no_advanced")])
    hot_list = env["mailing.list"].create({"name": "CDP – Quan tâm cao"})
    owner_list = env["mailing.list"].create({"name": "CDP – Chủ PK: mời Implant Nâng cao cho đội ngũ"})
    owners.mailing_list_id = owner_list
    env["cdp.trigger.rule"].create([
        {"segment_id": hot.id, "action": "activity", "activity_summary": "Gọi tư vấn khóa khách đang quan tâm"},
        {"segment_id": hot.id, "action": "mailing_list", "mailing_list_id": hot_list.id},
        {"segment_id": owners.id, "action": "activity", "only_role": "clinic_owner",
         "activity_summary": "Chào gói Implant Nâng cao cho đội ngũ phòng khám"},
    ])
    owners._sync_mailing_list()
    env.cr.commit()
    print("RULES OK", {"owner_list": owner_list.contact_count, "hot_list": hot_list.id})


def report():
    for p in env["res.partner"].search([("cdp_profile_ids", "!=", False)], order="id"):
        print(f"{p.name:<22} segments={p.cdp_segment_ids.mapped('key')} rfm={p.cdp_rfm_segment} "
              f"spent={p.cdp_total_spent:,.0f}")
    for t in env["cdp.trigger.log"].search([], order="id"):
        print(f"TRIGGER {t.key:<32} → {t.partner_id.name:<16} {t.state} {t.result}")
    for ml in env["mailing.list"].search([("name", "like", "CDP –")]):
        print(f"MAILING LIST {ml.name}: {', '.join(ml.contact_ids.mapped('email'))}")


{"seed": seed, "send": send, "sepay_order": sepay_order, "rules": rules, "report": report}[step]()
