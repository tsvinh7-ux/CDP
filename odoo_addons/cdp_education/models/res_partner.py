from odoo import fields, models

DENTAL_ROLES = [
    ("clinic_owner", "Chủ phòng khám"),
    ("dentist", "Bác sĩ nha khoa"),
    ("assistant", "Phụ tá"),
    ("technician", "Kỹ thuật viên labo"),
    ("manager", "Quản lý phòng khám"),
    ("student", "Sinh viên"),
    ("other", "Khác"),
]


class ResPartner(models.Model):
    _inherit = "res.partner"

    # If Bizapp already stores this on another field, map that field instead
    # (CDP Core reads `dental_role` or `job_role` from the partner snapshot).
    dental_role = fields.Selection(DENTAL_ROLES, string="Vai trò nha khoa", index=True)
