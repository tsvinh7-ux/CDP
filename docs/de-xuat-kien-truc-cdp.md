# Đề xuất kiến trúc CDP: add-on cho Dental ERP (Odoo 17) và DMAG

> Phiên bản: 0.3 · Ngày: 13/10/2026
>
> Lịch sử thay đổi:
> - **0.1**: phân tích module Odoo hay platform riêng.
> - **0.2**: bối cảnh DMAG (đào tạo nha khoa, ~100–150k nhân sự nha khoa, kênh web + Zalo + Facebook, Odoo do Bizapp customize đã có Marketing Automation).
> - **0.3**: DMAG sẽ **bán Dental ERP trên nền Odoo cho phòng khám**, CDP trở thành **add-on bán kèm**. Bản này thay thế giả định ở 0.2 là "phòng khám không dùng Odoo".

## 1. Tóm tắt kết luận

Từ trải nghiệm của khách hàng, **CDP là một add-on của Odoo**: phòng khám mua, cài vào Dental ERP, bật lên, và dùng ngay trong giao diện Odoo, không phải mở phần mềm khác.

Bên dưới, phần xử lý nặng chạy trên **một dịch vụ CDP Core trung tâm, multi-tenant, do DMAG vận hành**: tracking, hợp nhất danh tính, AI, tích hợp Zalo/Facebook, tính toán phân khúc.

Mô hình này giống cách chính Odoo làm các dịch vụ IAP (SMS, số hóa hóa đơn bằng OCR, làm giàu thông tin lead): module trong Odoo chỉ là giao diện + connector, còn "bộ não" chạy tập trung ở cloud và tính phí theo gói hoặc theo lượt dùng.

```
 ┌───────────────────── Odoo của từng khách hàng ─────────────────────┐
 │  DMAG (đào tạo)    Phòng khám A    Phòng khám B    Chuỗi C (3 CN)   │
 │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ │
 │  │ cdp_connector │ │ cdp_connector │ │ cdp_connector │ │ cdp_connector │ │  ← add-on Odoo
 │  │ + education   │ │ + dental      │ │ + dental      │ │ + dental      │ │    (UI, outbox, kích hoạt)
 │  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘ └──────┬───────┘ │
 └─────────┼────────────────┼────────────────┼────────────────┼─────────┘
           └────────────────┴───── HTTPS ────┴────────────────┘
                                    ▼
           ┌──────────────────────────────────────────────────┐
           │ CDP Core (SaaS, multi-tenant, DMAG vận hành)       │
           │ tracking · identity · profile · traits · AI ·      │
           │ phân khúc · Zalo/Facebook/ads · billing theo tenant│
           └──────────────────────────────────────────────────┘
```

**Vì sao không làm CDP thuần trong module Odoo, dù khách hàng đều dùng Odoo?**

Khi bán cho N phòng khám, mọi điểm yếu của "CDP trong Odoo" bị nhân lên N lần:

| Vấn đề | Module thuần (cài vào từng Odoo) | Add-on + CDP Core (khuyến nghị) |
|-------|----------------------------------|----------------------------------|
| Tích hợp Zalo ZNS, Facebook CAPI/Audiences, TikTok | Mỗi instance tự gọi API, tự xử lý token, rate limit; API đổi thì phải cập nhật N nơi | Làm một lần ở Core, mọi khách dùng chung |
| Tracking web (event volume) | Ghi vào DB Odoo của phòng khám, làm chậm ERP lễ tân/thu ngân đang dùng | Ghi vào ClickHouse của Core, ERP không bị ảnh hưởng |
| AI (dự báo no-show, gợi ý điều trị tiếp theo, rời bỏ) | Mỗi phòng khám ít dữ liệu, mô hình yếu; khó triển khai và cập nhật mô hình | Huấn luyện tập trung (trên dữ liệu đã ẩn danh, có thỏa thuận), cập nhật một lần |
| Nâng cấp & sửa lỗi | Phải nâng cấp module trên N database | Phần lớn logic nằm ở Core, deploy một lần; module mỏng ít thay đổi |
| Bảo vệ sở hữu trí tuệ | Mã Python của module nằm trên server khách (nếu on-premise), dễ bị sao chép | Lõi giá trị nằm ở Core, không giao mã nguồn |
| Mô hình doanh thu | Bán license một lần, khó thu theo lượt dùng | Thuê bao theo tháng + tính phí theo tin nhắn/hồ sơ (như IAP) |
| Trải nghiệm trong Odoo | Tốt | Tốt như nhau: UI vẫn là OWL trong Odoo |

### Hai gói sản phẩm gợi ý

- **CDP Lite** (chạy hoàn toàn trong Odoo, không cần Core): Patient/Customer 360 từ dữ liệu ERP, RFM, danh sách nhắc tái khám, phân khúc cơ bản dùng cho Marketing Automation có sẵn. Dùng làm gói tặng kèm hoặc giá thấp, và là cửa vào để upsell.
- **CDP Pro** (kết nối CDP Core): tracking web/landing page, hợp nhất danh tính đa kênh, Zalo OA/ZNS, Facebook/TikTok lead + CAPI + Custom Audiences, đo doanh thu theo chiến dịch quảng cáo, AI, trigger real-time.

Lite và Pro dùng **cùng một module**, Pro chỉ là bật kết nối Core. Định nghĩa phân khúc lưu cùng một định dạng, để khi khách nâng cấp lên Pro thì không phải làm lại.

---

## 2. CDP "chuẩn concept": 7 năng lực

| # | Năng lực | DMAG (đào tạo) | Phòng khám (Dental ERP) |
|---|---------|---------------|------------------------|
| 1 | **Ingestion** | Đơn hàng, ghi danh, tiến độ học, web, Zalo, Facebook | Lịch hẹn, kế hoạch điều trị, thủ thuật đã làm, thanh toán/trả góp, web/landing page, Zalo, Facebook/TikTok lead |
| 2 | **Identity resolution** | Web vô danh ↔ SĐT ↔ email ↔ Zalo ↔ `res.partner` | Bệnh nhân ↔ người giám hộ/gia đình ↔ lead quảng cáo ↔ Zalo, hợp nhất giữa các chi nhánh |
| 3 | **Unified profile** | Người học + phòng khám nơi họ làm việc | Bệnh nhân + hộ gia đình |
| 4 | **Traits & AI** | RFM, CLV, engagement học tập, lead score, gợi ý khóa tiếp theo | LTV bệnh nhân, xác suất no-show, xác suất chấp nhận kế hoạch điều trị, đến hạn tái khám |
| 5 | **Segmentation** | Động, batch và real-time | Như DMAG |
| 6 | **Activation** | Marketing Automation của Odoo, ZNS, Facebook CAPI/Audiences, CRM | Như DMAG, cộng thêm nhắc lịch hẹn, nhắc tái khám, khảo sát hài lòng |
| 7 | **Governance & Privacy** | Luật BVDLCN 91/2025/QH15 | **Dữ liệu sức khỏe là dữ liệu nhạy cảm**: yêu cầu cao hơn (mục 6) |

---

## 3. Kiến trúc

### 3.1. Bộ module Odoo (add-on)

| Module | Vai trò |
|-------|--------|
| `cdp_base` | Lõi dùng chung: outbox, gửi sự kiện, đăng ký tenant với Core (API key), cấu hình mapping, model `cdp.segment`, field `x_cdp_*`, chế độ Lite. Không phụ thuộc ngành. |
| `cdp_dental` | Mapping cho model của Dental ERP (bệnh nhân, lịch hẹn, kế hoạch điều trị, thủ thuật, trả góp), trait và phân khúc mẫu cho nha khoa, tab Patient 360. |
| `cdp_education` | Mapping cho DMAG (khóa học, ghi danh, tiến độ, chứng chỉ), tab Customer 360. |
| `cdp_marketing_bridge` | Nối phân khúc/trigger của CDP vào Marketing Automation của Bizapp. Tách riêng vì phụ thuộc module của Bizapp. |

Nguyên tắc: **module mỏng, ít override**, chủ yếu thêm mới. Nhờ vậy khi Dental ERP nâng cấp Odoo 18/19, phần phải migrate là nhỏ.

### 3.2. Đồng bộ Odoo → Core: outbox

- Hook vào method nghiệp vụ (xác nhận lịch hẹn, check-in, hoàn thành thủ thuật, chấp nhận kế hoạch điều trị, thanh toán, ghi danh…). Mỗi sự kiện được ghi vào bảng `cdp_outbox` **trong cùng transaction**: không mất sự kiện, không phát sự kiện của giao dịch bị rollback.
- Job nền gửi theo lô qua HTTPS, ký HMAC, có retry và idempotency key. Nếu Core gián đoạn, sự kiện xếp hàng trong Odoo, ERP vẫn chạy bình thường.
- **Backfill** dữ liệu lịch sử khi kích hoạt. **Đối soát hằng đêm** theo `write_date` để bắt thay đổi ngoài ORM.
- **Không dùng CDC (Debezium) làm cơ chế chính.** Với N database của khách, việc mở replication slot cho từng DB vừa phức tạp, vừa có rủi ro đầy ổ đĩa khi Core dừng lâu.

### 3.3. Đồng bộ Core → Odoo

- Core đẩy theo lô qua API của `cdp_base`: thành viên phân khúc, trait (`x_cdp_*`), lead score, gợi ý.
- **Một orchestrator cho mỗi kênh**:
  - Marketing Automation của Odoo: chuỗi chăm sóc email/SMS.
  - Core: trigger real-time theo hành vi web, Facebook/TikTok, và ZNS nếu MA chưa hỗ trợ Zalo.
  - Core quản lý **giới hạn tần suất liên lạc** chung để bệnh nhân/khách không bị nhắn dồn dập.
- Kết quả từ MA (gửi, mở, click, hủy đăng ký) chảy ngược về Core qua outbox.

### 3.4. Giao diện

- **Trong Odoo (OWL)**: tab 360, danh sách phân khúc + builder đơn giản, màn hình kích hoạt (gửi phân khúc sang MA/ZNS/Facebook), dashboard tóm tắt. Đây là nơi 95% người dùng làm việc.
- **Phân tích nâng cao** (attribution, cohort, funnel): trang web của Core nhúng vào Odoo bằng iframe với SSO token ngắn hạn, để không phải viết UI phức tạp hai lần.
- **CDP Console** (React): cho đội vận hành DMAG quản lý tenant, gói, quota, giám sát đồng bộ.

### 3.5. CDP Core

```
 Outbox từ Odoo · JS SDK web/landing · Zalo OA webhook · Facebook/TikTok Lead
                                  │
                                  ▼
          Collector API (FastAPI): xác thực tenant, chuẩn hóa, quota
                                  │
                                  ▼
                 Redpanda (Kafka API), key theo tenant
                 ┌────────────────┴────────────────┐
                 ▼                                 ▼
        Identity service                     ClickHouse (event store)
   (identity graph theo tenant)              partition theo tenant
                 └────────────────┬────────────────┘
                                  ▼  dbt: trait, RFM, LTV, no-show, engagement
              PostgreSQL: profile, consent, phân khúc, cấu hình (RLS theo tenant)
                                  │
                                  ▼
   Activation: Odoo của tenant · Zalo ZNS · Facebook CAPI/Audiences · TikTok · Webhook
   Billing/metering: số hồ sơ, số tin ZNS/SMS, số lượt đồng bộ audience
```

**Cách ly tenant** (bắt buộc vì có dữ liệu sức khỏe):
- `tenant_id` trên mọi bản ghi; PostgreSQL Row-Level Security; mọi truy vấn ClickHouse đều bắt buộc có filter tenant.
- Khóa mã hóa riêng cho từng tenant với trường nhạy cảm.
- **Không bao giờ ghép danh tính chéo tenant.** Bệnh nhân của phòng khám A không được xuất hiện trong phân khúc của phòng khám B, kể cả khi trùng SĐT.
- Khách lớn (chuỗi phòng khám) có thể được tách DB/cluster riêng.

### 3.6. Công nghệ

| Lớp | Đề xuất |
|----|--------|
| Backend Core | Python 3.12 + FastAPI (cùng ngôn ngữ với Odoo) |
| Hàng đợi | Redpanda |
| Event store | ClickHouse |
| Profile/config | PostgreSQL 16 (RLS) |
| Mô hình hóa | dbt-clickhouse; lập lịch bằng Dagster |
| Web SDK | Thư viện mã nguồn mở tương thích Segment spec |
| UI | OWL (trong Odoo), React + TypeScript (Console, analytics nhúng) |
| AI | LightGBM/scikit-learn (no-show, chấp nhận điều trị, lead score); LLM cho "tạo phân khúc bằng tiếng Việt" và tóm tắt hồ sơ cho lễ tân/tư vấn viên |
| Hạ tầng | Cloud tại Việt Nam do DMAG làm chủ. Docker Compose khi chỉ có DMAG, Kubernetes khi có từ khoảng 5–10 phòng khám |

Ước tính giai đoạn đầu (DMAG + vài phòng khám pilot): 2–3 máy chủ khoảng 8 vCPU / 32 GB.

---

## 4. Tenant đầu tiên: DMAG (đào tạo)

**Mô hình B2B2C**: phòng khám (account) → nhân sự nha khoa (người học) → khóa học/ghi danh.
- **Xem theo phòng khám**: "Phòng khám X có 6 người học khóa cơ bản, chưa ai học nâng cao" → chào gói đào tạo doanh nghiệp, và **chào luôn Dental ERP + CDP**. CDP của DMAG trở thành công cụ tìm khách hàng cho chính sản phẩm Dental ERP.
- Định danh mạnh: tài khoản web = `partner_id`, số chứng chỉ hành nghề (nếu thu thập, có consent), email/SĐT cá nhân. **Không auto-merge theo SĐT phòng khám dùng chung.**
- Nếu khóa học được công nhận giờ đào tạo liên tục (Luật Khám bệnh, chữa bệnh 2023 yêu cầu cập nhật kiến thức để gia hạn giấy phép hành nghề), CDP có thể theo dõi số giờ tích lũy và nhắc người học. *(Cần xác minh quy định chi tiết.)*

**Tracking plan chính:**
- Web: `course_viewed`, `added_to_cart`, `checkout_started`, `signed_up`.
- Thương mại: `order_paid`, `refund_issued`.
- Học tập: `enrolled`, `lesson_completed`, `course_completed`, `certificate_issued`, `webinar_attended`.
- Zalo: `oa_followed`, `zns_clicked`.
- Facebook: `lead_form_submitted`, cùng UTM/fbclid khi vào web.

**Use-case ưu tiên cho MVP:**
1. Thu hồi checkout bỏ dở (ZNS/email qua MA).
2. Đo doanh thu theo chiến dịch Facebook + gửi Conversions API.
3. Loại người đã mua khỏi quảng cáo; lookalike từ nhóm CLV cao.

**Giai đoạn sau:** gợi ý khóa tiếp theo, kéo người học dở quay lại, lead score cho khóa hands-on giá cao, bán gói doanh nghiệp.

## 5. Tenant phòng khám (add-on cho Dental ERP)

**Mô hình dữ liệu**: hộ gia đình → bệnh nhân → lịch hẹn / kế hoạch điều trị / thủ thuật / thanh toán–trả góp; chuỗi phòng khám có nhiều chi nhánh.

**CDP chỉ lấy dữ liệu tối thiểu cần cho marketing/CSKH** (nguyên tắc tối thiểu hóa dữ liệu):
- Lấy: nhóm thủ thuật (cạo vôi, trám, implant, niềng, tẩy trắng…), ngày, giá trị, trạng thái lịch hẹn và kế hoạch điều trị, chi nhánh, nguồn khách.
- **Không lấy**: ghi chú lâm sàng, sơ đồ răng chi tiết, phim X-quang, chẩn đoán chi tiết.

| # | Use-case | Giá trị cho phòng khám |
|---|---------|----------------------|
| 1 | **Nhắc tái khám định kỳ** (cạo vôi 6 tháng, tái khám niềng, kiểm tra/bảo hành implant) qua ZNS | Doanh thu lặp lại, ít tốn quảng cáo |
| 2 | **Nhắc lịch hẹn + dự báo no-show** → gọi xác nhận nhóm rủi ro cao | Giảm ghế trống |
| 3 | **Kế hoạch điều trị chưa chốt** → nhắc tư vấn viên theo dõi, gửi nội dung phù hợp | Tăng tỷ lệ chấp nhận điều trị (nguồn doanh thu lớn nhất bị bỏ sót) |
| 4 | **Đo doanh thu thực theo nguồn quảng cáo** (Facebook/TikTok/Zalo → lead → lịch hẹn → điều trị → thanh toán) | Phòng khám chi nhiều cho quảng cáo nhưng hiếm khi biết ROAS thật: **đây là điểm bán hàng mạnh nhất** |
| 5 | **Lead quảng cáo vào thẳng CRM** + chấm điểm + phân cho tư vấn viên, đo thời gian phản hồi | Tăng tỷ lệ chuyển lead → lịch hẹn |
| 6 | **Kích hoạt lại bệnh nhân lâu không quay lại** | Doanh thu từ tệp khách sẵn có |
| 7 | **Khảo sát hài lòng/NPS sau điều trị**, mời đánh giá Google | Uy tín, kênh giới thiệu |
| 8 | **Giới thiệu bạn bè/người thân** (theo hộ gia đình) | Khách mới chi phí thấp |
| 9 | **Hợp nhất bệnh nhân giữa các chi nhánh** của chuỗi | Hồ sơ không trùng lặp, báo cáo đúng |

**Định giá gợi ý (để thảo luận):**
- Lite: tặng kèm hoặc giá thấp trong gói Dental ERP.
- Pro: thuê bao theo chi nhánh/tháng, theo bậc số hồ sơ.
- Tin nhắn ZNS/SMS: tính theo lượt (mua credit, như IAP), có biên lợi nhuận.

---

## 6. Pháp lý & bảo mật

- Luật Bảo vệ dữ liệu cá nhân **91/2025/QH15** (hiệu lực 01/01/2026). **Dữ liệu sức khỏe của bệnh nhân là dữ liệu cá nhân nhạy cảm.**
- **Vai trò:** phòng khám là bên kiểm soát dữ liệu, DMAG/CDP Core là bên xử lý dữ liệu. Cần **thỏa thuận xử lý dữ liệu (DPA)** trong hợp đồng với mỗi phòng khám.
- **Consent tại phòng khám**: form đồng ý trong Dental ERP lúc đăng ký/check-in, theo mục đích (nhắc lịch: chăm sóc; marketing; quảng cáo). Rút đồng ý thì phải dừng trên mọi kênh.
- **Quảng cáo với dữ liệu sức khỏe**: đẩy danh sách bệnh nhân lên Facebook/TikTok (kể cả đã băm) cần cân nhắc kỹ, vì có thể bị coi là tiết lộ tình trạng điều trị. Hai nền tảng này cũng có chính sách riêng hạn chế dùng dữ liệu sức khỏe nhạy cảm. Mặc định: **chỉ dùng loại trừ (exclusion) và lookalike từ tệp đã đồng ý; không target theo loại điều trị.**
- Mã hóa, phân quyền, audit log, lưu trữ trong nước, quyền xóa/truy cập. Cần hồ sơ đánh giá tác động cho Core.
- Dữ liệu tổng hợp dùng huấn luyện AI chung: chỉ dùng dữ liệu đã ẩn danh, và phải ghi rõ trong hợp đồng.

> Cần luật sư xác nhận khung pháp lý trước khi bán cho phòng khám đầu tiên.

---

## 7. Lộ trình

| Giai đoạn | Thời gian (ước tính) | Phạm vi |
|----------|-------------------|--------|
| **0. Khảo sát** | 2 tuần | Với Bizapp: model của Odoo DMAG và Dental ERP, cách MA lọc đối tượng, mô hình triển khai Dental ERP; chốt data contract v1; chốt sở hữu IP |
| **1. Core + DMAG (MVP)** | 8–10 tuần | `cdp_base` + `cdp_education`, Core multi-tenant (dù mới 1 tenant), web SDK, identity, profile 360, RFM/CLV, phân khúc → MA, Facebook CAPI/Audiences, consent. Use-case DMAG 1–3 |
| **2. Đa kênh + Lite** | 6–8 tuần | Zalo OA/ZNS, Facebook Lead Ads, lead score → CRM, dashboard attribution, chế độ CDP Lite chạy độc lập trong Odoo |
| **3. Dental add-on + pilot** | 8–10 tuần | `cdp_dental`, use-case phòng khám 1–5, metering/billing, Console quản lý tenant; pilot 2–3 phòng khám là khách của DMAG |
| **4. Thương mại hóa** | liên tục | AI (no-show, chấp nhận điều trị), LLM segment builder, use-case 6–9, Kubernetes, quy trình onboarding tự động |

**Nhân sự gợi ý:**
- 1 tech lead;
- 2–3 backend Python (1 người kiêm data/dbt);
- 1 frontend (OWL + React);
- 1 Odoo developer (in-house hoặc Bizapp, theo spec của team CDP);
- 1 BA/PM có hiểu biết vận hành phòng khám;
- QA bán thời gian.

---

## 8. Rủi ro

| Rủi ro | Giảm thiểu |
|-------|-----------|
| **Sở hữu trí tuệ Dental ERP & CDP giữa DMAG và Bizapp** | Hợp đồng rõ ràng trước khi code: ai sở hữu module, ai được bán, chia doanh thu. Repo CDP do DMAG làm chủ |
| **Nguồn gốc các module "như Enterprise" của Bizapp** (vd. Marketing Automation). Nếu port từ mã nguồn Odoo Enterprise (giấy phép OEEL) thì **bán lại cho nhiều phòng khám là rủi ro pháp lý lớn** | Yêu cầu Bizapp xác nhận bằng văn bản là module tự phát triển. CDP chỉ phụ thuộc MA qua module cầu nối `cdp_marketing_bridge` |
| Dental ERP chưa hoàn thiện khi CDP sẵn sàng | Làm DMAG trước (giai đoạn 1–2). `cdp_dental` dựa trên data contract, phát triển song song với Dental ERP |
| Rò rỉ dữ liệu chéo tenant | RLS, test tự động cho cách ly tenant, pentest trước khi pilot |
| Dữ liệu sức khỏe & quảng cáo | Tối thiểu hóa dữ liệu, mặc định không target theo điều trị, DPA với từng phòng khám |
| Hai hệ thống cùng gửi tin | Một orchestrator mỗi kênh + giới hạn tần suất chung |
| Phình phạm vi | Đi theo use-case có KPI, mỗi giai đoạn có tiêu chí nghiệm thu |

---

## 9. Câu hỏi cần làm rõ

1. **Dental ERP triển khai thế nào**: SaaS do DMAG/Bizapp host (multi-database) hay cài on-premise tại phòng khám? Điều này quyết định cách đóng gói add-on và mức bảo vệ IP.
2. **Sở hữu Dental ERP**: DMAG sở hữu, Bizapp sở hữu, hay đồng sở hữu? CDP do ai phát triển?
3. Dental ERP đã có những phân hệ nào (lịch hẹn, kế hoạch điều trị, sơ đồ răng, trả góp, kho vật tư, hoa hồng bác sĩ)? Đã có thiết kế dữ liệu chưa?
4. Web bán khóa học của DMAG là Odoo Website/eLearning hay nền tảng khác?
5. Marketing Automation của Bizapp hỗ trợ kênh nào (email, SMS, ZNS)? Chọn đối tượng bằng cách nào? Có trigger từ webhook không?
6. Mục tiêu số phòng khám trong 12–24 tháng (để định cỡ hạ tầng và đội ngũ)?

## 10. Bước tiếp theo

1. Chốt câu hỏi 1–2 (mô hình triển khai và IP). Hai câu này ảnh hưởng trực tiếp đến kiến trúc và hợp đồng.
2. Viết **data contract v1**: định dạng sự kiện outbox, API Core ↔ Odoo, danh mục sự kiện chung + education + dental.
3. Dựng khung repo:
   - `core/`: FastAPI + PostgreSQL + ClickHouse + Redpanda;
   - `odoo_addons/`: `cdp_base`, `cdp_education`, `cdp_dental`, `cdp_marketing_bridge`;
   - `sdk/web/`;
   - `docs/`.
