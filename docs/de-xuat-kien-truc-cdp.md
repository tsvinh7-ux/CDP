# Đề xuất kiến trúc CDP tích hợp Odoo 17 Community (bản Bizapp customize)

> Phiên bản: 0.1 (bản nháp để thảo luận) · Ngày: 13/10/2026

## 1. Tóm tắt kết luận

**Không nên xây CDP thuần là một module Odoo. Cũng không nên xây một platform tách rời hoàn toàn khỏi Odoo.**

Khuyến nghị: **CDP là một platform riêng (composable / warehouse-native) + một module Odoo mỏng làm "connector"** (`bizapp_cdp_connector`).

- **Platform CDP riêng**: thu thập sự kiện, hợp nhất danh tính (identity resolution), hồ sơ khách hàng hợp nhất (golden record), tính toán chỉ số (RFM, CLV, churn…), phân khúc, kích hoạt ra các kênh (Zalo ZNS, SMS, email, Facebook/Google/TikTok audiences).
- **Module Odoo mỏng**: đẩy sự kiện nghiệp vụ từ Odoo sang CDP (outbox pattern), nhận ngược lại phân khúc/điểm/nhãn từ CDP ghi vào `res.partner`, và hiển thị tab **Customer 360** ngay trên form khách hàng trong Odoo.

Người dùng nghiệp vụ vẫn "cảm thấy" CDP nằm trong Odoo, nhưng phần nặng (dữ liệu sự kiện, tính toán, real-time) chạy bên ngoài, không làm chậm ERP và không bị khóa vào vòng đời nâng cấp của Odoo/Bizapp.

---

## 2. CDP "chuẩn concept" là gì — tiêu chí để đánh giá

Theo định nghĩa của CDP Institute, một "Real CDP" phải có: *phần mềm đóng gói, tạo ra cơ sở dữ liệu khách hàng thống nhất, lưu trữ lâu dài, và các hệ thống khác truy cập được.* Một CDP hiện đại (2025–2026) thường gồm 7 năng lực:

| # | Năng lực | Mô tả ngắn |
|---|----------|-----------|
| 1 | **Ingestion** | Thu thập dữ liệu từ mọi nguồn: ERP (Odoo), web/app tracking, POS, sàn TMĐT, Zalo OA, Facebook, call center… cả batch và real-time |
| 2 | **Identity resolution** | Ghép các định danh (SĐT, email, Zalo ID, cookie/device ID, partner_id Odoo, mã thẻ thành viên) thành một con người duy nhất; hỗ trợ merge/unmerge |
| 3 | **Unified profile (Golden record)** | Hồ sơ 360°: thuộc tính, lịch sử giao dịch, hành vi, sự đồng ý (consent) — với quy tắc ưu tiên nguồn (survivorship) |
| 4 | **Computed traits & AI** | RFM, CLV, tần suất mua, ngành hàng yêu thích, xác suất rời bỏ, next-best-product |
| 5 | **Segmentation** | Phân khúc động (tự cập nhật), batch và real-time, builder cho marketer không cần SQL |
| 6 | **Activation / Orchestration** | Đẩy phân khúc ra kênh: Odoo, Zalo ZNS, SMS, email, ads audiences; journey tự động |
| 7 | **Governance & Privacy** | Consent, quyền xóa/truy cập dữ liệu, phân quyền, audit log — tuân thủ **Luật Bảo vệ dữ liệu cá nhân số 91/2025/QH15 (hiệu lực 01/01/2026)** |

Xu hướng "hiện đại" hiện nay là **Composable CDP / Warehouse-native CDP**: dữ liệu nằm trong kho dữ liệu của chính doanh nghiệp (ClickHouse/BigQuery/Postgres…), các thành phần (thu thập, mô hình hóa, kích hoạt) là các khối ghép được, thay vì một hộp đen đóng gói.

---

## 3. Phân tích: làm module Odoo có được không?

### 3.1. Làm được — nhưng chỉ ở mức "Customer 360 trong ERP"

Odoo 17 Community có sẵn nền tảng tốt để làm phần "hồ sơ khách hàng":
`res.partner`, `sale.order`, `pos.order`, `account.move`, `crm.lead`, `mass_mailing`, `base_automation` (có action *Send Webhook Notification* và trigger *On Webhook*), ORM, phân quyền, giao diện OWL. Nếu doanh nghiệp **chỉ có dữ liệu trong Odoo**, quy mô vài chục nghìn khách hàng, không tracking web/app, thì một module Odoo có thể làm: gộp trùng partner, tính RFM, gắn tag phân khúc, gửi email bằng mass_mailing.

**Nhưng đó chưa phải là CDP đúng nghĩa.**

### 3.2. Các giới hạn kỹ thuật khi nhồi CDP vào Odoo

| Vấn đề | Lý do |
|-------|-------|
| **Khối lượng sự kiện** | Tracking web/app, Zalo, ads sinh ra hàng triệu–hàng chục triệu event/tháng. Ghi vào PostgreSQL của Odoo qua ORM sẽ làm phình DB ERP, chậm backup, chậm toàn hệ thống bán hàng/kế toán. |
| **Mô hình worker của Odoo** | Odoo chạy worker đồng bộ (prefork), mỗi request chiếm 1 worker, có `limit_time_real`. Không phù hợp nhận luồng event liên tục, xử lý streaming, hay chạy job tính toán phân khúc trên hàng triệu dòng. |
| **Truy vấn phân tích** | Phân khúc kiểu "khách đã xem sản phẩm X ≥3 lần trong 7 ngày nhưng chưa mua, tổng chi tiêu 12 tháng > 5 triệu" là truy vấn OLAP; PostgreSQL row-store + ORM kém hơn rất nhiều so với columnar store (ClickHouse). |
| **Identity graph** | Hợp nhất danh tính là bài toán đồ thị (union-find, merge/unmerge có lịch sử). Làm trong ORM vừa phức tạp vừa dễ khóa bảng `res.partner` — bảng "nóng" nhất của Odoo. |
| **Gắn chặt vòng đời nâng cấp** | Khi lên Odoo 18/19, mọi module custom phải migrate. CDP trong Odoo = thêm một khối code lớn phải migrate cùng các customize của Bizapp. |
| **Phụ thuộc vào customize của Bizapp** | Nếu CDP nằm trong Odoo, mọi thay đổi của Bizapp (override model, field, view) đều có thể làm vỡ CDP và ngược lại. |
| **Thiếu sẵn có ở bản Community** | `marketing_automation`, `social marketing`, `sign`, Studio… là Enterprise. Bản Community không có journey/automation marketing. |
| **Đa nguồn** | CDP phải là "trung tâm" của nhiều nguồn; nếu đặt trong Odoo thì Odoo trở thành nơi hứng mọi dữ liệu ngoài ERP — sai vai trò. |

### 3.3. Ngược lại, platform riêng hoàn toàn (bỏ qua Odoo) cũng không ổn

- Nhân viên sales/CSKH làm việc trên Odoo; nếu họ phải mở thêm một ứng dụng khác mới xem được hồ sơ khách → tỷ lệ sử dụng thấp.
- Đồng bộ chỉ qua XML-RPC polling sẽ chậm, tốn tải Odoo, dễ sót dữ liệu.
- Kết quả phân khúc phải quay về Odoo (nhãn khách hàng, bảng giá, chương trình loyalty, mailing list) thì mới tạo ra giá trị kinh doanh.

→ Vì vậy giải pháp hợp lý là **hybrid**.

---

## 4. Ma trận so sánh 3 phương án

Thang điểm 1–5 (5 = tốt nhất).

| Tiêu chí | A. Module Odoo thuần | B. Platform riêng, tích hợp lỏng | **C. Platform riêng + connector Odoo (khuyến nghị)** |
|---|:-:|:-:|:-:|
| Đúng concept CDP (đa nguồn, identity, real-time) | 2 | 5 | **5** |
| Khả năng mở rộng (event volume) | 1 | 5 | **5** |
| Không ảnh hưởng hiệu năng ERP | 2 | 4 | **5** |
| Trải nghiệm người dùng Odoo | 5 | 2 | **4** |
| Đồng bộ tự động, gần real-time với Odoo | 5 (nội bộ) | 2 | **5** |
| Độc lập với nâng cấp Odoo/Bizapp | 1 | 5 | **4** |
| Chi phí & thời gian MVP | **5** | 2 | 3 |
| Chi phí vận hành hạ tầng | **5** | 3 | 3 |
| **Tổng** | 26 | 28 | **34** |

Phương án A chỉ nên chọn nếu: <50k khách hàng, chỉ dữ liệu từ Odoo, không có kênh online, ngân sách rất hạn chế. Ngay cả khi đó, nên thiết kế A sao cho sau này có thể "nhấc" ra thành C.

---

## 5. Kiến trúc đề xuất (Phương án C)

```
                        ┌───────────────────────────── NGUỒN DỮ LIỆU ─────────────────────────────┐
                        │ Odoo 17 (Bizapp)   Web/App SDK   Zalo OA   FB/TikTok   Shopee/Lazada   POS │
                        └───────┬───────────────┬────────────┬──────────┬─────────────┬────────────┘
                                │               │            │          │             │
              (1) CDC Debezium  │  (2) Outbox   │            ▼          ▼             ▼
              trên WAL Postgres │  events từ    │      ┌─────────────────────────────────────┐
                                │  connector    │      │  Collector API (chuẩn Segment spec)│
                                ▼               ▼      └──────────────────┬──────────────────┘
                        ┌──────────────────────────────────────────────────┴───────┐
                        │        Event bus: Redpanda/Kafka (hoặc NATS khi nhỏ)       │
                        └───────┬──────────────────────────┬────────────────────────┘
                                │                          │
                    ┌───────────▼───────────┐   ┌──────────▼──────────────┐
                    │ Identity Resolution    │   │ Raw/Event store          │
                    │ (identity graph,       │   │ ClickHouse + MinIO/S3    │
                    │  merge/unmerge rules)  │   │ (lưu toàn bộ lịch sử)   │
                    └───────────┬───────────┘   └──────────┬──────────────┘
                                │                          │  dbt models
                    ┌───────────▼──────────────────────────▼──────────────┐
                    │ Profile Store (PostgreSQL): golden record, traits,     │
                    │ consent, segment membership — API tra cứu < 50ms       │
                    └───────────┬───────────────────────────┬────────────┘
                                │                           │
                  ┌─────────────▼────────────┐   ┌──────────▼──────────────────┐
                  │ Segmentation engine       │   │ AI/ML: RFM, CLV, churn,     │
                  │ (batch + real-time rules) │   │ next-best-product, LLM      │
                  └─────────────┬────────────┘   │ "segment bằng tiếng Việt"   │
                                │                └─────────────────────────────┘
                  ┌─────────────▼─────────────────────────────────────────────┐
                  │ Activation: Odoo (tag, pricelist, loyalty, mailing list),   │
                  │ Zalo ZNS, SMS brandname, Email, FB/Google/TikTok audiences, │
                  │ Webhook; Journey orchestration (giai đoạn 3)               │
                  └────────────────────────────────────────────────────────────┘

  Giao diện: CDP Admin (React) cho marketing/data  +  tab "Customer 360" (OWL) nhúng trong Odoo
```

### 5.1. Đồng bộ Odoo → CDP (tự động, gần real-time)

Kết hợp hai cơ chế, mỗi cái giải quyết một việc:

1. **CDC (Change Data Capture) bằng Debezium trên PostgreSQL của Odoo**
   - Đọc WAL (logical replication), không sửa code Odoo, không tải thêm cho worker.
   - Bắt được **mọi field custom của Bizapp** (vì đó chỉ là các cột trong bảng), kể cả thay đổi do import, script, cron.
   - Yêu cầu: `wal_level=logical`, một replication slot, user chỉ đọc. Nên trỏ vào replica nếu có.
   - Dùng cho: `res_partner`, `sale_order(_line)`, `pos_order(_line)`, `account_move`, `crm_lead`, `product_*`, `loyalty_*`, các bảng custom của Bizapp.

2. **Outbox events từ module connector**
   - CDC cho biết "dòng nào đổi", nhưng không cho biết **ý nghĩa nghiệp vụ** ("đơn được xác nhận", "khách đổi điểm", "phiếu bảo hành được tạo").
   - Module `bizapp_cdp_connector` hook vào các method nghiệp vụ (`action_confirm`, `action_pos_order_paid`, …), ghi bản ghi vào bảng `cdp_outbox` **trong cùng transaction** → đảm bảo không mất sự kiện, không gửi sự kiện của transaction bị rollback.
   - Debezium (hoặc một job `queue_job` của OCA) đọc bảng outbox và đẩy lên event bus.

> Không dùng XML-RPC polling làm cơ chế chính: chậm, tốn worker Odoo, khó bắt bản ghi bị xóa. Chỉ dùng cho backfill lần đầu và đối soát (reconciliation) định kỳ.

### 5.2. Đồng bộ CDP → Odoo (reverse ETL)

- Module connector cung cấp model `cdp.segment`, `cdp.profile.trait` và các field `x_cdp_*` trên `res.partner` (CDP ID, RFM segment, CLV, churn score, ngày cập nhật).
- CDP gọi API của connector (REST controller có xác thực token + HMAC) để cập nhật theo lô (batch upsert), không ghi trực tiếp vào DB Odoo.
- Kích hoạt trong Odoo: gắn `category_id` (tag), thêm vào `mailing.list`, gán pricelist/chương trình loyalty, tạo activity cho sales ("khách VIP sắp rời bỏ — gọi lại").
- Tab **Customer 360** trên form khách hàng: widget OWL gọi API CDP (timeline hành vi, kênh, điểm số, phân khúc) — dữ liệu không cần copy vào Odoo.

### 5.3. Quyền sở hữu dữ liệu (Master data)

Cần thống nhất từ đầu để tránh ghi đè vòng lặp:

| Dữ liệu | Hệ thống gốc (system of record) |
|--------|-------------------------------|
| Đơn hàng, hóa đơn, công nợ, tồn kho | Odoo |
| Thông tin khách do sales/CSKH nhập | Odoo |
| Định danh hợp nhất, hành vi online, điểm số, phân khúc | CDP |
| Consent (đồng ý nhận tin/xử lý dữ liệu) | CDP (đồng bộ bản sao về Odoo) |

Quy tắc: CDP **không sửa** các field nghiệp vụ gốc của Odoo; chỉ ghi vào field `x_cdp_*` và các model `cdp.*`. Gộp trùng khách hàng trong Odoo (merge partner) là thao tác có kiểm duyệt của con người, CDP chỉ **đề xuất**.

### 5.4. Identity resolution — đặc thù Việt Nam

- **Chuẩn hóa SĐT**: về E.164 (`+84…`), xử lý đầu số cũ 11 số → 10 số (chuyển đổi 2018), loại bỏ khoảng trắng/dấu chấm, phát hiện SĐT "rác" (0000000000, số tổng đài, số nhân viên nhập thay khách).
- **Email**: lowercase, xử lý alias Gmail (dấu chấm, `+tag`).
- **Tên tiếng Việt**: chuẩn hóa Unicode NFC, bỏ dấu để so khớp mờ (chỉ dùng làm tín hiệu phụ, không dùng để merge tự động).
- **Định danh kênh**: Zalo user ID (theo OA), Facebook PSID (theo Page), ID khách trên sàn TMĐT, mã thẻ thành viên, `res.partner.id`, cookie/device ID.
- **Luật ghép**: deterministic trước (SĐT/email đã xác minh, Zalo ID), probabilistic sau (chỉ đề xuất, có ngưỡng tin cậy). Có giới hạn chống "siêu cụm" (một SĐT công ty gắn với hàng trăm người).
- **Merge/unmerge có lịch sử**: lưu identity graph dạng cạnh có nguồn & thời điểm, để có thể tách lại khi ghép sai.

### 5.5. Tuân thủ Luật Bảo vệ dữ liệu cá nhân (91/2025/QH15)

- Lưu **consent theo mục đích** (marketing, phân tích, chia sẻ cho đối tác quảng cáo) kèm thời điểm, kênh, bằng chứng.
- Hỗ trợ quyền của chủ thể dữ liệu: truy cập, chỉnh sửa, **rút lại đồng ý**, **xóa** — xóa phải lan sang event store và các audience đã đẩy ra ngoài.
- Khi đẩy audience lên Facebook/Google/TikTok: chỉ gửi dữ liệu đã băm (SHA-256), chỉ với khách đã đồng ý mục đích quảng cáo.
- Mã hóa dữ liệu nhạy cảm, phân quyền theo vai trò, audit log mọi truy cập hồ sơ.
- Lưu trữ dữ liệu trong nước (khuyến nghị hạ tầng tại VN hoặc on-premise) và chuẩn bị hồ sơ đánh giá tác động xử lý dữ liệu cá nhân.

> Phần pháp lý cần được bộ phận pháp chế/luật sư xác nhận; tài liệu này chỉ nêu các yêu cầu kỹ thuật cần chuẩn bị.

---

## 6. Công nghệ đề xuất

| Lớp | Đề xuất | Lý do |
|----|--------|------|
| Ngôn ngữ backend | **Python (FastAPI)** | Đội Odoo đã quen Python; dùng chung kỹ năng, thư viện. Có thể dùng Go cho collector khi lưu lượng rất lớn. |
| Event bus | **Redpanda** (tương thích Kafka) | Nhẹ hơn Kafka, 1 binary, dễ vận hành. Quy mô nhỏ có thể bắt đầu với NATS JetStream/Redis Streams. |
| CDC | **Debezium** (Kafka Connect hoặc Debezium Server) | Chuẩn công nghiệp cho PostgreSQL CDC. |
| Event/analytics store | **ClickHouse** | Columnar, cực nhanh cho phân khúc & phân tích hành vi, nén tốt, mã nguồn mở. |
| Profile store | **PostgreSQL** | Truy vấn hồ sơ theo ID nhanh, giao dịch an toàn cho identity graph. |
| Biến đổi dữ liệu | **dbt** | Mô hình hóa traits/RFM/CLV bằng SQL có version, test. |
| Orchestration | **Dagster** hoặc Airflow | Lập lịch job tính traits, đồng bộ audience. |
| Web/App SDK | Tương thích **Segment/RudderStack spec** (`identify`, `track`, `page`) | Có thể dùng SDK mã nguồn mở của RudderStack/Jitsu thay vì tự viết. |
| Giao diện | **React + TypeScript** (CDP Admin), **OWL** (tab trong Odoo) | |
| AI | scikit-learn/LightGBM cho churn/CLV; LLM cho "tạo phân khúc bằng tiếng Việt" và tóm tắt hồ sơ khách | |
| Triển khai | Docker Compose (MVP) → Kubernetes (khi scale) | |

**Có thể tái sử dụng mã nguồn mở thay vì tự viết từ đầu**: RudderStack / Jitsu (thu thập sự kiện), Snowplow (tracking nâng cao), Dittofeed (journey/messaging), Apache Unomi hoặc Tracardi (tham khảo mô hình profile). Khuyến nghị **tự xây phần lõi** (identity resolution, profile, segmentation, connector Odoo) vì đó là phần đặc thù nghiệp vụ & dữ liệu Việt Nam, và **dùng lại** phần thu thập sự kiện & gửi tin.

---

## 7. Lộ trình triển khai

| Giai đoạn | Thời gian (ước tính) | Phạm vi | Kết quả |
|----------|-------------------|--------|---------|
| **0. Khảo sát** | 2–3 tuần | Audit DB & customize của Bizapp, đánh giá chất lượng dữ liệu (trùng, SĐT sai), xác định use-case ưu tiên & KPI | Data map, danh sách bảng/field cần đồng bộ, backlog use-case |
| **1. MVP** | 8–10 tuần | Connector Odoo (outbox + reverse ETL), CDC Debezium, identity resolution deterministic, profile store, RFM/CLV cơ bản, phân khúc batch, đẩy tag/mailing list về Odoo, tab Customer 360 | Marketing dùng được phân khúc từ dữ liệu Odoo; sales thấy hồ sơ 360 |
| **2. Đa kênh** | 8–10 tuần | Web/App SDK, Zalo OA/ZNS, Facebook/Google/TikTok audiences, sàn TMĐT, consent management, ClickHouse event store | Hồ sơ hợp nhất online + offline; chạy chiến dịch đa kênh |
| **3. Thông minh & real-time** | 8–12 tuần | Phân khúc real-time, journey orchestration, mô hình churn/next-best-product, LLM segment builder, dashboard | CDP hoàn chỉnh theo chuẩn |

**Nhân sự gợi ý**: 1 Tech lead/kiến trúc sư, 2 backend (Python), 1 data engineer (ClickHouse/dbt), 1 frontend (React), 1 Odoo developer (phối hợp với Bizapp), 1 BA/PM, QA bán thời gian.

**Hạ tầng MVP**: khoảng 3–4 máy chủ ảo (8 vCPU/32GB cho ClickHouse, 4 vCPU/16GB cho Postgres + API, 4 vCPU/16GB cho Redpanda + Debezium), có thể gộp ở giai đoạn đầu.

---

## 8. Rủi ro & cách giảm thiểu

| Rủi ro | Giảm thiểu |
|-------|-----------|
| Không có quyền truy cập DB/code customize của Bizapp | Làm rõ ngay ở giai đoạn 0: quyền đọc DB (replica), quyền cài module connector, quy trình phối hợp khi Bizapp nâng cấp |
| Odoo host trên hạ tầng không cho bật logical replication | Dự phòng: chỉ dùng outbox + `queue_job`, chấp nhận độ trễ vài giây–vài phút |
| Dữ liệu khách trùng lặp/bẩn trong Odoo | Pipeline chuẩn hóa + báo cáo chất lượng dữ liệu; CDP đề xuất gộp, con người duyệt |
| Ghép nhầm danh tính | Chỉ auto-merge bằng định danh mạnh, có unmerge, giới hạn kích thước cụm |
| Phình phạm vi (scope creep) | Đi theo use-case có KPI (tỷ lệ quay lại, doanh thu từ chiến dịch), mỗi giai đoạn có tiêu chí nghiệm thu |
| Nâng cấp Odoo 17 → 18/19 | Connector mỏng, ít override; CDC dựa trên bảng nên chỉ cần cập nhật mapping |
| Rủi ro pháp lý về dữ liệu cá nhân | Thiết kế consent & xóa dữ liệu ngay từ MVP, không để "làm sau" |

---

## 9. Các câu hỏi cần làm rõ để chốt thiết kế

1. Quy mô: bao nhiêu khách hàng, bao nhiêu đơn/ngày, bao nhiêu cửa hàng/POS?
2. Kênh hiện có: website/app riêng? Zalo OA? Fanpage? Sàn TMĐT nào? Call center?
3. Odoo đang host ở đâu (on-premise / cloud VN / Bizapp quản lý)? Có quyền truy cập PostgreSQL và cài module không?
4. Bizapp đã customize những phân hệ nào (loyalty, bảo hành, CSKH, POS…)? Có tài liệu/mã nguồn không?
5. Use-case ưu tiên số 1: tăng mua lại? chăm sóc VIP? giảm rời bỏ? tối ưu quảng cáo?
6. Đội ngũ triển khai: in-house, thuê ngoài, hay phối hợp với Bizapp?
7. Yêu cầu hạ tầng: bắt buộc on-premise / lưu trữ trong nước?
8. CDP phục vụ một doanh nghiệp hay định hướng thành sản phẩm bán cho nhiều khách hàng Odoo (multi-tenant)? — câu này ảnh hưởng lớn đến kiến trúc.

---

## 10. Bước tiếp theo đề xuất

1. Trả lời các câu hỏi ở mục 9.
2. Chạy giai đoạn 0 (khảo sát dữ liệu & customize Bizapp).
3. Dựng khung repo: `cdp-platform/` (FastAPI + Postgres + ClickHouse + Redpanda, docker-compose) và `odoo_addons/bizapp_cdp_connector/` (module Odoo 17).
4. Chọn 1–2 use-case cho MVP và định nghĩa KPI nghiệm thu.
