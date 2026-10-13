# DMAG CDP

Customer Data Platform của DMAG: dịch vụ CDP Core multi-tenant cùng các add-on Odoo 17 cho website/eLearning của DMAG và Dental ERP.

## Tài liệu

| Tài liệu | Nội dung |
|---------|---------|
| [docs/de-xuat-kien-truc-cdp.md](docs/de-xuat-kien-truc-cdp.md) | Đề xuất kiến trúc, nhật ký quyết định, lộ trình |
| [docs/data-contract-v1.md](docs/data-contract-v1.md) | Hợp đồng dữ liệu Odoo ↔ CDP Core (outbox, API, danh mục sự kiện) |
| [docs/dieu-khoan-hop-dong-bizapp.md](docs/dieu-khoan-hop-dong-bizapp.md) | Checklist điều khoản hợp đồng với Bizapp (sở hữu trí tuệ, giấy phép, dữ liệu) |

## Cấu trúc dự kiến

```
core/            CDP Core (FastAPI, PostgreSQL, ClickHouse, Redpanda)
odoo_addons/     cdp_base, cdp_education, cdp_dental, cdp_marketing_bridge (Odoo 17)
sdk/web/         JS SDK tracking cho website
docs/            Tài liệu thiết kế
```
