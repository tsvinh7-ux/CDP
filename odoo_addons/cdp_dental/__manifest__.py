{
    "name": "CDP Dental (Dental ERP connector)",
    "summary": "Phát sự kiện CDP (lịch hẹn, kế hoạch điều trị, thủ thuật, trả góp) từ model của Dental ERP bằng cấu hình",
    "version": "17.0.0.1.0",
    "category": "Marketing",
    "author": "DMAG",
    "license": "OPL-1",
    "depends": ["cdp_base", "base_automation"],
    "data": [
        "security/ir.model.access.csv",
        "views/cdp_event_rule_views.xml",
    ],
    "installable": True,
}
