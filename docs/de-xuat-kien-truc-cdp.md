# Đề xuất kiến trúc CDP cho DMAG, tích hợp Odoo 17 Community (bản Bizapp customize)

> Phiên bản: 0.2 · Ngày: 13/10/2026
> Thay đổi so với 0.1: cập nhật theo bối cảnh DMAG (đào tạo nha khoa, ~100–150k nhân sự nha khoa, bán chủ lực qua web + Zalo, Facebook dẫn traffic), Odoo đã có Marketing Automation do Bizapp customize, Odoo chạy trên server của Bizapp, định hướng sau này thương mại hóa CDP cho phòng khám nha khoa và các ngành khác.

## 1. Tóm tắt kết luận

**Xây CDP thành một platform riêng, do DMAG sở hữu, kết nối với Odoo qua một module connector mỏng.** Bối cảnh mới làm khuyến nghị này rõ ràng hơn:

1. **Định hướng thành sản phẩm.** Khách hàng tương lai (phòng khám nha khoa, các bên khác) phần lớn **không dùng Odoo**. Nếu CDP là module Odoo, sản phẩm sẽ không bán được cho họ. Odoo phải là *một nguồn dữ liệu trong nhiều nguồn*, không phải nền tảng chứa CDP.
2. **Odoo đã có Marketing Automation (Bizapp customize).** CDP **không xây lại** journey/email/SMS automation ở giai đoạn đầu. CDP tập trung vào thứ Odoo không làm tốt: tracking hành vi web, hợp nhất danh tính, chấm điểm, phân khúc. Kết quả đẩy vào Odoo để Marketing Automation hiện có dùng.
3. **Quy mô 100–150k hồ sơ là vừa phải.** Không cần hạ tầng "big data" ở giai đoạn đầu. MVP chạy được trên 2 máy chủ, nhưng nên thiết kế sẵn để mở rộng và phục vụ nhiều khách hàng (multi-tenant).
4. **Odoo chạy trên server của Bizapp.** Cơ chế đồng bộ chính nên là **đẩy sự kiện qua HTTPS từ module connector** (outbox pattern). Đọc trực tiếp WAL của PostgreSQL (CDC) không nên làm cơ chế chính, vì đó là kết nối cấp database xuyên hai tổ chức và có rủi ro làm đầy ổ đĩa server Odoo (xem mục 5.1).

---

## 2. CDP "chuẩn concept" — 7 năng lực

| # | Năng lực | Với DMAG cụ thể là |
|---|----------|------------------|
| 1 | **Ingestion** | Đơn hàng, ghi danh, tiến độ học từ Odoo; hành vi trên web (xem khóa, video, giỏ hàng, checkout); Zalo OA/ZNS; Facebook (UTM, lead form) |
| 2 | **Identity resolution** | Ghép khách vô danh trên web + SĐT + email + Zalo ID + Facebook lead + `res.partner` thành một người |
| 3 | **Unified profile** | Hồ sơ 360° của **người học** và của **phòng khám** họ làm việc (mô hình B2B2C) |
| 4 | **Computed traits & AI** | RFM, CLV, mức độ học (engagement), lộ trình khóa học, lead score cho khóa offline giá cao, gợi ý khóa tiếp theo |
| 5 | **Segmentation** | Phân khúc động, tự cập nhật, batch và real-time |
| 6 | **Activation** | Đẩy phân khúc vào Odoo Marketing Automation, Zalo ZNS, Facebook Custom Audiences + Conversions API, CRM của sales |
| 7 | **Governance & Privacy** | Consent, xóa/truy cập dữ liệu theo **Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15 (hiệu lực 01/01/2026)** |

Kiến trúc theo hướng **Composable / Warehouse-native CDP**: dữ liệu nằm trong kho do DMAG làm chủ (ClickHouse + PostgreSQL), các khối thu thập, mô hình hóa và kích hoạt ghép được với nhau.

---

## 3. Vì sao không làm CDP thuần trong Odoo (cập nhật)

| Lý do | Giải thích |
|-------|-----------|
| **Chặn đường thương mại hóa** | Phòng khám nha khoa dùng phần mềm quản lý phòng khám riêng, rất ít dùng Odoo. CDP trong Odoo thì không bán được cho họ. Nếu bắt họ cài Odoo chỉ để dùng CDP thì không khả thi. |
| **Không kiểm soát hạ tầng** | Odoo chạy trên server của Bizapp. Đặt CDP (dữ liệu tracking lớn, job tính toán nặng) vào đó thì hiệu năng ERP, backup và bảo mật đều phụ thuộc bên thứ ba. |
| **Sở hữu trí tuệ** | Code CDP nằm trong codebase Odoo của Bizapp thì ranh giới sở hữu khó tách bạch khi bán ra ngoài. |
| **Khối lượng sự kiện web** | Tracking web sinh ra hàng triệu event/tháng. Ghi qua ORM vào DB Odoo sẽ làm phình DB và chậm hệ thống bán hàng. |
| **Truy vấn phân tích** | Phân khúc theo hành vi ("xem khóa Implant ≥ 3 lần trong 14 ngày, chưa mua, đã mua khóa cơ bản") là truy vấn OLAP. Columnar store (ClickHouse) nhanh hơn PostgreSQL + ORM hàng chục lần. |
| **Phụ thuộc nâng cấp** | Mỗi lần Odoo lên 18/19 hoặc Bizapp thay đổi customize thì một khối CDP lớn phải migrate theo. |

**Phần nào vẫn nên nằm trong Odoo:** Marketing Automation (đã có), CRM/sales pipeline, đơn hàng, hóa đơn, ghi danh. CDP **làm giàu** cho các phần này chứ không thay thế.

---

## 4. Mô hình dữ liệu đặc thù DMAG: B2B2C nha khoa

```
  Phòng khám (Account)  ──1:n──  Nhân sự nha khoa (Person)  ──n:n──  Khóa học / Ghi danh
   - quy mô, khu vực             - vai trò: bác sĩ, phụ tá,          - online / offline / hands-on
   - số nhân sự đã học             kỹ thuật viên, chủ phòng khám      - tiến độ, điểm, chứng chỉ
   - tổng chi tiêu               - chuyên môn quan tâm (implant,
   - người ra quyết định           chỉnh nha, nội nha, thẩm mỹ…)
```

- Odoo đã có sẵn quan hệ công ty–liên hệ (`res.partner.parent_id`). CDP dùng quan hệ này làm gốc, đồng thời tự suy luận thêm (cùng email domain, cùng địa chỉ, người học tự khai nơi làm việc).
- **Account-level view** rất có giá trị cho B2B: "Phòng khám X có 6 người đã học khóa cơ bản, chưa ai học nâng cao" → sales chào gói đào tạo doanh nghiệp.
- **Định danh mạnh của nhân sự y tế:** số chứng chỉ/giấy phép hành nghề, nếu DMAG có thu thập để cấp chứng nhận. Đây là khóa ghép danh tính rất tin cậy. Tuy nhiên là dữ liệu cá nhân nên cần consent và mã hóa.
- **Đào tạo liên tục:** theo Luật Khám bệnh, chữa bệnh 2023, giấy phép hành nghề phải gia hạn định kỳ và yêu cầu cập nhật kiến thức y khoa liên tục. Nếu khóa học của DMAG được công nhận giờ đào tạo liên tục, CDP có thể theo dõi số giờ tích lũy và nhắc người học. *(Cần xác minh quy định chi tiết và tư cách cấp giờ đào tạo của DMAG.)*

### Tracking plan (sự kiện cốt lõi)

| Nhóm | Sự kiện |
|------|--------|
| Web | `page_viewed`, `course_viewed`, `video_preview_played`, `course_searched`, `added_to_cart`, `checkout_started`, `signed_up`, `logged_in`, `form_submitted` |
| Thương mại (từ Odoo) | `order_created`, `order_paid`, `order_cancelled`, `refund_issued`, `coupon_applied` |
| Học tập | `enrolled`, `lesson_started`, `lesson_completed`, `quiz_passed`, `course_completed`, `certificate_issued`, `webinar_registered`, `webinar_attended`, `offline_class_checked_in` |
| Zalo | `oa_followed`, `oa_unfollowed`, `oa_message_received`, `zns_sent`, `zns_delivered`, `zns_clicked` |
| Facebook | `lead_form_submitted`, `ad_click` (từ UTM/fbclid khi vào web) |
| CRM / Marketing (từ Odoo) | `lead_created`, `lead_stage_changed`, `email_opened`, `email_clicked`, `campaign_enrolled` |

---

## 5. Kiến trúc đề xuất

```
 ┌──────────────── NGUỒN ────────────────┐
 │ Web DMAG (JS SDK)  Odoo 17 (Bizapp)    │
 │ Zalo OA webhook    Facebook Lead/Ads   │
 └───┬───────────────┬───────────────┬────┘
     │ track/identify │ outbox events │ webhooks
     ▼                ▼  (HTTPS+HMAC) ▼
 ┌──────────────────────────────────────────┐
 │ Collector API (FastAPI) — xác thực, chuẩn hóa, gắn tenant_id │
 └────────────────────┬─────────────────────┘
                      ▼
          Redpanda (1 node, Kafka API)
          ┌───────────┴───────────┐
          ▼                       ▼
 ┌──────────────────┐   ┌─────────────────────┐
 │ Identity service  │   │ ClickHouse           │
 │ (identity graph,  │   │ event store, phân   │
 │ merge/unmerge)    │   │ tích hành vi         │
 └────────┬─────────┘   └──────────┬──────────┘
          ▼                        ▼  dbt (traits, RFM, CLV, engagement)
 ┌──────────────────────────────────────────┐
 │ PostgreSQL: profile, account (phòng khám),│
 │ traits, consent, segment membership       │
 └────────┬─────────────────────────────────┘
          ▼
 ┌──────────────────────────────────────────┐
 │ Segmentation + Activation                 │
 │ → Odoo (segment → MA / CRM / tag)         │
 │ → Facebook Custom Audiences + CAPI        │
 │ → Zalo ZNS (trigger real-time)            │
 │ → Webhook chung (cho khách hàng tương lai) │
 └──────────────────────────────────────────┘
  UI: CDP Admin (React) · tab "Customer 360" (OWL) trong Odoo
```

### 5.1. Đồng bộ Odoo → CDP

**Cơ chế chính: outbox events từ module connector `dmag_cdp_connector`** (Bizapp hỗ trợ cài):

- Hook vào các method nghiệp vụ (xác nhận đơn, thanh toán, ghi danh, hoàn thành bài học, chuyển stage lead, cập nhật `res.partner`…). Mỗi sự kiện được ghi vào bảng `cdp_outbox` **trong cùng transaction**: transaction rollback thì không phát sự kiện, commit thì chắc chắn không mất.
- Một job nền (cron hoặc `queue_job` của OCA nếu Bizapp đã dùng) gửi theo lô qua HTTPS, ký HMAC, có retry + idempotency key. CDP tạm ngừng thì sự kiện xếp hàng trong Odoo, không mất.
- Field custom của Bizapp: connector có **mapping cấu hình được** (chọn model/field nào gửi đi) để không phải sửa code khi Bizapp thêm field.

**Cơ chế bổ trợ:**
- **Backfill lần đầu** toàn bộ khách hàng, đơn hàng, ghi danh lịch sử qua JSON-RPC/XML-RPC hoặc export từ replica.
- **Đối soát (reconciliation) hằng đêm**: so số lượng và checksum theo `write_date` để bắt các thay đổi đi ngoài ORM (SQL trực tiếp, script import).

**Vì sao CDC (Debezium) không làm cơ chế chính:** CDC cần bật `wal_level=logical`, mở kết nối replication từ hạ tầng DMAG vào DB trên server Bizapp, và tạo *replication slot*. Nếu phía CDP dừng lâu, slot giữ lại WAL và **có thể làm đầy ổ đĩa, kéo sập Odoo**. Với hệ thống do bên thứ ba vận hành, rủi ro này không đáng so với lợi ích. Có thể xem xét lại sau nếu Bizapp đồng ý và có giám sát slot.

### 5.2. Đồng bộ CDP → Odoo và phối hợp với Marketing Automation của Bizapp

Nguyên tắc: **một hệ thống điều phối hành trình (orchestrator) cho mỗi kênh**, tránh hai hệ thống cùng gửi tin cho một khách.

| Việc | Ai làm |
|-----|-------|
| Email / SMS / chuỗi chăm sóc dài ngày | **Odoo Marketing Automation** (đã có) |
| Phân khúc, điểm số, thuộc tính hành vi | **CDP** đẩy vào Odoo |
| Trigger real-time theo hành vi web (bỏ giỏ hàng, xem khóa nhiều lần) | CDP phát hiện → gọi Odoo (connector API hoặc trigger *On webhook* của automation rule) để MA chạy kịch bản, **hoặc** CDP gửi ZNS trực tiếp nếu MA chưa hỗ trợ Zalo |
| Quảng cáo Facebook (audience, conversion) | **CDP** (Odoo không làm phần này tốt) |
| Lead cho sales | CDP chấm điểm → tạo/cập nhật `crm.lead` + activity trong Odoo |

Cụ thể trong Odoo, connector cung cấp:
- Model `cdp.segment` và quan hệ thành viên với `res.partner`. **Cần khảo sát cách MA của Bizapp lọc đối tượng** (domain trên `res.partner`, mailing list hay model riêng) để chọn cách đẩy phân khúc mà MA dùng được ngay, không phải sửa MA.
- Field `x_cdp_*` trên `res.partner`: CDP ID, RFM, CLV, engagement score, lead score, khóa gợi ý tiếp theo, ngày cập nhật.
- Tab **Customer 360** trên form khách hàng (widget OWL gọi API CDP): timeline hành vi web/Zalo/học tập, phòng khám, điểm số.
- Kết quả từ MA (gửi, mở, click, hủy đăng ký) chảy ngược về CDP qua outbox để hồ sơ đầy đủ.

### 5.3. Web và Facebook: phần mang lại giá trị nhanh nhất

- **JS SDK** trên web DMAG (tương thích Segment spec: `page`, `track`, `identify`), cookie first-party, gửi về domain của DMAG (vd. `t.dmag.vn`) để ít bị chặn.
- Ghi nhận **UTM / fbclid / zalo click id** ở phiên đầu → gán nguồn cho đơn hàng → **báo cáo doanh thu theo chiến dịch Facebook** (attribution).
- **Facebook Conversions API (server-side)**: CDP gửi `Purchase`, `Lead`, `InitiateCheckout` kèm dữ liệu băm SHA-256 và consent → tối ưu quảng cáo tốt hơn pixel (không bị chặn bởi iOS/adblock).
- **Custom Audiences**: loại người đã mua khỏi quảng cáo khóa đó; lookalike từ nhóm CLV cao; retarget nhóm xem khóa nhưng chưa mua.

### 5.4. Zalo

- **Zalo OA**: nhận webhook (follow, tin nhắn), ghép Zalo user ID với SĐT khi người dùng chia sẻ số hoặc qua form có consent.
- **ZNS**: gửi theo SĐT với template đã duyệt (xác nhận đơn, nhắc lịch học, nhắc học dở, nhắc bỏ giỏ hàng nếu template được duyệt cho mục đích này).
- Nếu MA của Bizapp đã tích hợp Zalo thì CDP chỉ cung cấp trigger và phân khúc, không gửi trực tiếp.

### 5.5. Identity resolution

- **Chuẩn hóa SĐT** về `+84…`, xử lý đầu số 11 số cũ, loại SĐT rác/SĐT tổng đài phòng khám (một SĐT phòng khám gắn với nhiều người là chuyện thường gặp trong ngành này: **không được auto-merge theo SĐT dùng chung**).
- **Email**: lowercase, xử lý alias Gmail.
- **Thứ tự tin cậy**: tài khoản đăng nhập web = `partner_id` > số chứng chỉ hành nghề > email đã xác minh > SĐT cá nhân > Zalo ID > Facebook lead > cookie.
- **Anonymous → known**: khi khách vô danh đăng nhập hoặc điền form, toàn bộ lịch sử duyệt web trước đó được gắn vào hồ sơ.
- **Merge/unmerge có lịch sử**, giới hạn kích thước cụm. CDP **chỉ đề xuất** gộp partner trùng trong Odoo, con người duyệt.

### 5.6. Thiết kế sẵn cho thương mại hóa (multi-tenant-ready)

Làm cho DMAG trước, nhưng **không viết code chỉ chạy được cho DMAG**:

- Mọi bảng/event có `tenant_id`. PostgreSQL dùng Row-Level Security, ClickHouse partition/filter theo tenant.
- **Connector là plugin**: Odoo là connector đầu tiên. Sau này thêm connector cho phần mềm quản lý phòng khám, Haravan/Sapo, Google Sheets, webhook chung…
- **Tracking plan và trait theo template ngành**: "Đào tạo" (DMAG), "Phòng khám nha khoa" (lịch hẹn, điều trị, tái khám, nhắc cạo vôi 6 tháng…).
- Cấu hình (mapping field, luật ghép danh tính, phân khúc) lưu dạng dữ liệu, không hard-code.
- **Lưu ý cho giai đoạn phòng khám**: dữ liệu bệnh nhân là **dữ liệu sức khỏe, thuộc nhóm dữ liệu cá nhân nhạy cảm** theo luật. Yêu cầu bảo vệ cao hơn hẳn (mã hóa, cô lập tenant, đánh giá tác động, lưu trữ trong nước). Không đưa vào MVP, nhưng kiến trúc phải chừa chỗ.

Không làm ngay: billing, self-service onboarding, marketplace connector. Làm khi đã có khách hàng thứ hai.

### 5.7. Tuân thủ Luật Bảo vệ dữ liệu cá nhân (91/2025/QH15)

- Consent theo mục đích (marketing, quảng cáo, phân tích), lưu thời điểm, kênh, bằng chứng; banner/cookie consent trên web.
- Hỗ trợ truy cập, chỉnh sửa, rút đồng ý, xóa dữ liệu. Lệnh xóa lan sang event store, Odoo và audience đã đẩy lên Facebook.
- Chỉ gửi dữ liệu băm cho nền tảng quảng cáo, chỉ với khách đã đồng ý.
- Phân quyền, audit log, mã hóa trường nhạy cảm. Lưu trữ tại Việt Nam.
- Chuẩn bị hồ sơ đánh giá tác động xử lý dữ liệu cá nhân.

> Phần pháp lý cần pháp chế/luật sư xác nhận. Tài liệu chỉ nêu yêu cầu kỹ thuật.

---

## 6. Công nghệ (tinh gọn cho quy mô hiện tại)

| Lớp | Đề xuất | Ghi chú |
|----|--------|--------|
| Backend | **Python 3.12 + FastAPI** | Cùng ngôn ngữ với Odoo, dễ phối hợp với Bizapp |
| Hàng đợi sự kiện | **Redpanda 1 node** | API Kafka, sau này scale không phải viết lại |
| Event store | **ClickHouse 1 node** | 150k hồ sơ, vài triệu–vài chục triệu event/tháng là dư sức |
| Profile store | **PostgreSQL 16** | Hồ sơ, identity graph, consent, cấu hình, RLS cho multi-tenant |
| Mô hình hóa | **dbt** (dbt-clickhouse) | RFM, CLV, engagement, attribution bằng SQL có test |
| Lập lịch | **Dagster** (hoặc APScheduler ở MVP) | |
| Web SDK | Thư viện mã nguồn mở tương thích Segment spec | Không tự viết SDK từ đầu |
| Giao diện | **React + TypeScript**; **OWL** cho tab trong Odoo | |
| AI | LightGBM/scikit-learn (lead score, gợi ý khóa, rời bỏ); LLM cho "tạo phân khúc bằng tiếng Việt" và tóm tắt hồ sơ cho sales | Giai đoạn 3 |
| Hạ tầng | Docker Compose trên cloud tại VN, do DMAG làm chủ | Kubernetes khi có tenant thứ 2–3 |

**Hạ tầng MVP ước tính**: 2 máy chủ, mỗi máy khoảng 8 vCPU / 16–32 GB RAM / SSD 500 GB:
- (1) ClickHouse + Redpanda;
- (2) PostgreSQL + API + workers + UI.

Kèm backup sang object storage.

---

## 7. Use-case ưu tiên cho DMAG

| # | Use-case | Dữ liệu cần | Kênh kích hoạt | KPI |
|---|---------|------------|---------------|-----|
| 1 | **Thu hồi giỏ hàng / checkout bỏ dở** | Web tracking + đơn Odoo | ZNS / email qua MA | % đơn thu hồi |
| 2 | **Đo doanh thu theo chiến dịch Facebook** + gửi CAPI | UTM/fbclid + đơn đã thanh toán | Dashboard, Facebook CAPI | ROAS, CPA thực |
| 3 | **Loại người đã mua khỏi quảng cáo; lookalike từ khách CLV cao** | Đơn hàng, CLV | Facebook Custom Audiences | Giảm chi phí quảng cáo lãng phí |
| 4 | **Gợi ý khóa tiếp theo theo lộ trình** (cơ bản → nâng cao → hands-on) | Lịch sử ghi danh, hoàn thành | MA (email/Zalo) | Tỷ lệ mua lại, CLV |
| 5 | **Kéo người học dở quay lại** | Tiến độ học | ZNS / email | % hoàn thành khóa |
| 6 | **Lead score cho khóa offline/hands-on giá cao** → giao sales | Hành vi web + lịch sử mua + phòng khám | Odoo CRM (lead + activity) | Tỷ lệ chốt, thời gian phản hồi |
| 7 | **Bán gói doanh nghiệp cho phòng khám** | Account view (nhiều nhân sự cùng phòng khám) | Odoo CRM | Doanh thu B2B |

MVP nên chốt 1, 2, 3 (giá trị nhanh, dữ liệu sẵn), sau đó mở rộng 4–7.

---

## 8. Lộ trình triển khai

| Giai đoạn | Thời gian (ước tính) | Phạm vi | Kết quả |
|----------|-------------------|--------|---------|
| **0. Khảo sát với Bizapp** | 2 tuần | Liệt kê model/field liên quan (website, eLearning, sale, CRM, MA custom, Zalo nếu có); MA lọc đối tượng thế nào; chất lượng dữ liệu khách; tracking plan; thỏa thuận phối hợp & sở hữu code | Data map, tracking plan, backlog |
| **1. MVP** | 8–10 tuần | Connector Odoo (outbox + backfill + reverse sync), web SDK, identity (web ↔ partner ↔ SĐT/email), profile 360 + phòng khám, RFM/CLV, phân khúc batch, đẩy phân khúc vào MA, Facebook CAPI + Custom Audiences, consent | Use-case 1–3 chạy thật |
| **2. Đa kênh & học tập** | 6–8 tuần | Zalo OA/ZNS, Facebook Lead Ads, engagement học tập, lead score → CRM, dashboard attribution, trigger real-time, tab Customer 360 | Use-case 4–7 |
| **3. Thông minh & sản phẩm hóa** | 8–12 tuần | Gợi ý khóa/dự báo rời bỏ, LLM segment builder, hoàn thiện multi-tenant, connector thứ 2 (phần mềm phòng khám), pilot 1–2 phòng khám là khách DMAG | Bản thương mại đầu tiên |

**Nhân sự gợi ý (giai đoạn 1–2)**:
- 1 tech lead (kiêm kiến trúc);
- 2 backend Python (1 người kiêm data/dbt);
- 1 frontend;
- 1 Odoo developer phía Bizapp (bán thời gian, làm connector theo spec của team CDP);
- 1 BA/PM nắm marketing DMAG.

---

## 9. Rủi ro & cách giảm thiểu

| Rủi ro | Giảm thiểu |
|-------|-----------|
| Phụ thuộc Bizapp cho connector và mọi thay đổi Odoo | Connector mỏng, spec do team CDP viết, mapping cấu hình được; thỏa thuận SLA phối hợp |
| **Nguồn gốc module Marketing Automation của Bizapp**: nếu được port từ mã nguồn Odoo Enterprise thì có rủi ro bản quyền (Enterprise dùng giấy phép OEEL) | Xác nhận với Bizapp là module tự phát triển. Điều này cũng quan trọng khi thương mại hóa vì CDP không nên phụ thuộc vào module này |
| Sở hữu trí tuệ CDP khi bán ra ngoài | Code CDP thuộc DMAG/đơn vị phát triển; connector Odoo tách repo, ghi rõ trong hợp đồng với Bizapp |
| Ghép nhầm danh tính do SĐT phòng khám dùng chung | Không auto-merge theo SĐT dùng chung, giới hạn cụm, có unmerge |
| Dữ liệu khách trong Odoo trùng/bẩn | Pipeline chuẩn hóa + báo cáo chất lượng; CDP đề xuất gộp, con người duyệt |
| Hai hệ thống cùng gửi tin cho một khách | Quy tắc "mỗi kênh một orchestrator" (mục 5.2), tần suất liên lạc tối đa (frequency cap) do CDP quản lý |
| Phình phạm vi do muốn làm sản phẩm ngay | Multi-tenant-ready nhưng chỉ triển khai 1 tenant đến hết giai đoạn 2 |
| Pháp lý dữ liệu cá nhân (đặc biệt dữ liệu bệnh nhân ở giai đoạn phòng khám) | Consent & xóa dữ liệu có ngay từ MVP; dữ liệu sức khỏe chỉ đưa vào sau khi có đánh giá tác động riêng |

---

## 10. Câu hỏi còn mở (cần làm rõ ở giai đoạn 0)

1. Web bán khóa học của DMAG là **Odoo Website/eLearning** hay nền tảng khác (WordPress, LMS riêng)? Thanh toán qua cổng nào? Phần này quyết định cách gắn tracking và lấy dữ liệu học tập.
2. Marketing Automation của Bizapp hỗ trợ những kênh nào (email, SMS, Zalo ZNS)? Chọn đối tượng bằng domain filter, mailing list hay model riêng? Có trigger từ webhook không?
3. DMAG đã có Zalo OA xác thực và template ZNS chưa? Đang dùng Facebook Pixel/CAPI chưa?
4. Lượng truy cập web hằng tháng (để ước lượng event) và số đơn/tháng?
5. DMAG có thu thập số chứng chỉ hành nghề / nơi làm việc của người học không? Khóa học có được công nhận giờ đào tạo liên tục không?
6. Đội phát triển CDP: DMAG tự tuyển, thuê ngoài, hay Bizapp làm? Ảnh hưởng trực tiếp đến sở hữu code khi thương mại hóa.

---

## 11. Bước tiếp theo

1. Gửi tài liệu này + danh sách câu hỏi mục 10 cho Bizapp, tổ chức 1 buổi workshop kỹ thuật.
2. Dựng khung repo:
   - `platform/`: FastAPI + PostgreSQL + ClickHouse + Redpanda, docker-compose;
   - `connectors/odoo/dmag_cdp_connector/`: module Odoo 17;
   - `sdk/web/`;
   - `docs/`: tracking plan, data contract với Bizapp.
3. Viết **data contract** (định dạng sự kiện outbox, API reverse sync) để Bizapp làm connector song song với team CDP.
