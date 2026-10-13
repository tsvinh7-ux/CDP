{
    "name": "CDP Marketing Bridge",
    "summary": "Dùng phân khúc/trigger CDP trong Email Marketing và Marketing Automation (chủ phòng khám)",
    "version": "17.0.0.1.0",
    "category": "Marketing",
    "author": "DMAG",
    "license": "OPL-1",
    "depends": ["cdp_base", "mass_mailing"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/cdp_marketing_views.xml",
    ],
    "installable": True,
}
