"""Browser part of the demo (Playwright).

    python browser.py journey   # real visitor on the Odoo eLearning site (SDK → CDP Core)
    python browser.py screens   # screenshots of Odoo back-office + CDP console
"""
import os
import sys
import xmlrpc.client

from playwright.sync_api import sync_playwright

ODOO = os.environ.get("ODOO_URL", "http://localhost:8069")
CORE = os.environ.get("CDP_CORE_URL", "http://localhost:8000")
TOKEN = os.environ.get("CDP_ADMIN_TOKEN", "demo-admin")
DB = os.environ.get("ODOO_DB", "dmag")
OUT = os.environ.get("DEMO_OUT", os.path.join(os.path.dirname(__file__), "..", "..", "docs", "demo"))


def odoo_rpc(model, method, *args, **kw):
    common = xmlrpc.client.ServerProxy(f"{ODOO}/xmlrpc/2/common")
    uid = common.authenticate(DB, "admin", "admin", {})
    obj = xmlrpc.client.ServerProxy(f"{ODOO}/xmlrpc/2/object")
    return obj.execute_kw(DB, uid, "admin", model, method, list(args), kw)


def course_id(name):
    return odoo_rpc("slide.channel", "search", [("name", "=", name)])[0]


def journey():
    adv, basic = course_id("Implant Nâng cao"), course_id("Implant Cơ bản")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="vi-VN")
        page = ctx.new_page()
        page.on("console", lambda m: print("  [browser]", m.text) if "[cdp]" in m.text else None)

        print("1. Khách ẩn danh vào từ quảng cáo Facebook, xem khóa Implant Nâng cao 3 lần")
        for i in range(3):
            page.goto(f"{ODOO}/slides/{adv}?utm_source=facebook&utm_medium=cpc&utm_campaign=implant_nang_cao_t10"
                      if i == 0 else f"{ODOO}/slides/{adv}")
            page.wait_for_timeout(2500)
        page.goto(f"{ODOO}/slides/{basic}")
        page.wait_for_timeout(2500)
        print("   anonymousId =", page.evaluate("window.cdp && window.cdp.anonymousId()"))

        print("2. Khách đăng nhập: là BS. Trần Minh, chủ Nha khoa An Phúc")
        page.goto(f"{ODOO}/web/login")
        page.fill("input[name=login]", "minh@anphuc.vn")
        page.fill("input[name=password]", "demo1234")
        page.click("form.oe_login_form button[type=submit]")
        page.wait_for_load_state("networkidle")

        print("3. Quay lại khóa Nâng cao và bấm 'Add to Cart' nhưng không thanh toán")
        page.goto(f"{ODOO}/slides/{adv}")
        page.wait_for_timeout(2500)
        page.click("#add_to_cart")
        page.wait_for_timeout(3000)
        page.goto(f"{ODOO}/shop/cart")
        page.wait_for_timeout(3000)
        os.makedirs(OUT, exist_ok=True)
        page.screenshot(path=os.path.join(OUT, "01-website-gio-hang.png"))
        browser.close()


def screens():
    minh = odoo_rpc("res.partner", "search", [("email", "=", "minh@anphuc.vn")])[0]
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="vi-VN")
        page = ctx.new_page()
        page.goto(f"{ODOO}/web/login")
        page.fill("input[name=login]", "admin")
        page.fill("input[name=password]", "admin")
        page.click("form.oe_login_form button[type=submit]")
        page.wait_for_selector(".o_main_navbar")  # the back-office keeps a long-polling connection open

        page.goto(f"{ODOO}/web#id={minh}&model=res.partner&view_type=form")
        page.wait_for_selector("a[name=cdp_360], a:has-text('CDP 360')")
        page.click("a:has-text('CDP 360')")
        page.wait_for_timeout(1500)
        page.screenshot(path=os.path.join(OUT, "02-odoo-ho-so-cdp-360.png"), full_page=True)

        for action, name in (("cdp_base.action_cdp_trigger_log", "03-odoo-trigger.png"),
                             ("cdp_base.action_cdp_segment", "04-odoo-phan-khuc.png")):
            page.goto(f"{ODOO}/web#action={action}")
            page.wait_for_timeout(2500)
            page.screenshot(path=os.path.join(OUT, name), full_page=True)

        # Same domain Marketing Automation would use: owners whose staff did the basic course only.
        page.goto(f"{ODOO}/web#action=cdp_base.action_cdp_segment")
        page.wait_for_timeout(1500)
        page.click("td:has-text('Chủ PK: nhân sự đã học Cơ bản')")
        page.wait_for_timeout(1500)
        page.click("button[name=action_view_partners]")
        page.wait_for_timeout(2000)
        page.screenshot(path=os.path.join(OUT, "05-odoo-loc-theo-phan-khuc.png"), full_page=True)

        page.goto(f"{CORE}/console?token={TOKEN}&tenant=dmag")
        page.wait_for_timeout(2500)
        page.click("#profiles tr.click:has-text('Trần Minh')")
        page.wait_for_timeout(1500)
        page.screenshot(path=os.path.join(OUT, "06-cdp-console.png"), full_page=True)
        browser.close()
    print("Screenshots in", os.path.abspath(OUT))


if __name__ == "__main__":
    {"journey": journey, "screens": screens}[sys.argv[1] if len(sys.argv) > 1 else "journey"]()
