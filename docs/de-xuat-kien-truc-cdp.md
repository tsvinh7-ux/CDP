# Đề xuất kiến trúc CDP: add-on cho Dental ERP (Odoo 17) và DMAG

> Phiên bản: 0.6 · Ngày: 13/10/2026
>
> Lịch sử thay đổi:
> - **0.1**: module Odoo hay platform riêng.
> - **0.2**: bối cảnh DMAG.
> - **0.3**: CDP là add-on của Dental ERP.
> - **0.4**:
>   - **Chốt quyết định kiến trúc** kèm ước tính tải.
>   - Bỏ engine "Lite" chạy trong Odoo.
>   - Marketing Automation do Bizapp tự viết.
>   - Code thuộc DMAG.
>   - Web bán khóa học là eLearning trên Odoo của DMAG.
>   - Thêm data contract v1 (xem [`data-contract-v1.md`](data-contract-v1.md)).
> - **0.5**:
>   - Thêm nhật ký quyết định (mục 0).
>   - Thêm mô hình triển khai SaaS / Dedicated (mục 3.8).
>   - Thêm checklist điều khoản hợp đồng với Bizapp (xem [`dieu-khoan-hop-dong-bizapp.md`](dieu-khoan-hop-dong-bizapp.md)).
> - **0.6**:
>   - Video: Google Drive, YouTube, NAS. Thanh toán: VNPay, SePay. Marketing Automation nhắm **chủ phòng khám**.
>   - Đã dựng **bản khung chạy được** (CDP Core + `cdp_base`/`cdp_website`/`cdp_education` trên Odoo 17), xem [`huong-dan-chay-demo.md`](huong-dan-chay-demo.md).

## 0. Nhật ký quyết định

| # | Quyết định | Ngày |
|---|-----------|------|
| D1 | CDP Core chạy riêng, multi-tenant; Odoo chỉ cài add-on kết nối + giao diện | 13/10/2026 |
| D2 | Không có engine tính toán trong Odoo; các gói Lite/Pro/AI là cờ tính năng trên Core | 13/10/2026 |
| D3 | **DMAG sở hữu toàn bộ sản phẩm/dịch vụ**: Dental ERP, Marketing Automation, CDP Core, các module `cdp_*`, SDK và tài liệu | 13/10/2026 |
| D4 | Marketing Automation do Bizapp tự viết lại, mô phỏng tính năng bản Enterprise, không dùng mã Enterprise. Ghi thành điều khoản hợp đồng | 13/10/2026 |
| D5 | Dental ERP + CDP **mặc định SaaS do DMAG host**; có gói **Dedicated** (cài server riêng của khách) với giá riêng, cao hơn | 13/10/2026 |
| D6 | Website bán khóa học của DMAG là eLearning trên Odoo; tracking SDK gửi thẳng tới Core | 13/10/2026 |
| D7 | Đồng bộ Odoo → Core bằng outbox (không dùng CDC làm cơ chế chính); dữ liệu CDP trong Odoo lưu ở model `cdp.profile` riêng | 13/10/2026 |
| D8 | Marketing Automation nhắm **chủ phòng khám**. CDP tổng hợp tín hiệu của nhân sự lên cấp phòng khám rồi gắn vào hồ sơ chủ phòng khám (ví dụ "nhân sự đã học Cơ bản, chưa học Nâng cao") | 13/10/2026 |
| D9 | Video NAS nên phát bằng link file trực tiếp (thẻ `<video>`) để đo được tiến độ xem; Google Drive chỉ đo được mở video và thời gian học | 13/10/2026 |

## 1. Quyết định kiến trúc (đã chốt)

**CDP Core chạy riêng, là dịch vụ độc lập và multi-tenant. Odoo chỉ cài module kết nối và giao diện (add-on).**

| Thành phần | Chạy ở đâu | Làm gì |
|-----------|-----------|-------|
| **CDP Core** | Hạ tầng riêng của DMAG (cloud tại VN) | Thu thập sự kiện, hợp nhất danh tính, hồ sơ, trait, AI, phân khúc, tích hợp Zalo/Facebook/TikTok, metering |
| **Add-on Odoo** (`cdp_base`, `cdp_education`, `cdp_dental`, `cdp_marketing_bridge`) | Trong Odoo DMAG và từng Dental ERP | Gửi sự kiện (outbox), nhận phân khúc/trait, giao diện 360 + phân khúc + kích hoạt, nối vào Marketing Automation, gắn tracking SDK vào website |

Người dùng vẫn làm việc hoàn toàn trong Odoo. Odoo **không** lưu sự kiện hành vi và **không** chạy tính toán phân khúc.

### 1.1. Vì sao: ước tính tải nếu chạy CDP trong Odoo

Giả định cho DMAG (cần đối chiếu số liệu thật ở giai đoạn 0):

| Nguồn | Giả định | Sự kiện/tháng |
|------|---------|--------------|
| Duyệt web/eLearning | 150–300k phiên/tháng × ~20 sự kiện | 3–6 triệu |
| Theo dõi tiến độ video (heartbeat mỗi 30 giây) | 10k người học × 10 giờ/tháng × 120 nhịp/giờ | ~12 triệu |
| Nghiệp vụ (đơn, ghi danh, hoàn thành bài, MA, Zalo) | | 0,1–0,5 triệu |
| **Tổng** | | **~15–20 triệu/tháng, ~200 triệu/năm** |
| Đỉnh (webinar, mở bán khóa) | 2.000 người online × 1 sự kiện/10 giây | **~200 sự kiện/giây** |

Nếu đổ lượng này vào Odoo:
- **Worker**: Odoo xử lý mỗi request bằng một worker, thường cấu hình 9–17 worker. Một request ghi sự kiện qua ORM mất 20–50 ms. Ở mức 200 sự kiện/giây, riêng tracking đã chiếm 4–10 worker, tức **gần nửa server**, tranh chỗ với học viên đang học và đang thanh toán.
- **Database**: khoảng 200 triệu dòng/năm trong PostgreSQL của Odoo. Backup/restore do Bizapp vận hành chậm dần, vacuum và index nặng, database phình từ vài GB lên hàng trăm GB.
- **Phân khúc**: truy vấn hành vi trên hàng chục triệu dòng mất từ vài giây đến vài phút trên PostgreSQL row-store. ClickHouse làm cùng việc dưới 1 giây, dữ liệu nén chỉ còn vài GB.
- **Dental ERP SaaS**: nhiều phòng khám chạy chung server Odoo, nên tải tracking của tất cả cộng dồn lên cùng hạ tầng ERP.

Ngược lại, phần còn lại trong Odoo rất nhẹ:
- Outbox nghiệp vụ: vài nghìn dòng/ngày.
- Bảng hồ sơ CDP: 150k dòng, cập nhật theo lô ban đêm hoặc theo delta.
- Giao diện gọi API Core khi người dùng mở hồ sơ.

### 1.2. Bỏ engine "CDP Lite" chạy trong Odoo

Bản 0.3 đề xuất gói Lite tính toán ngay trong Odoo. **Tôi bỏ phương án này**, vì nó buộc phải duy trì hai engine phân khúc/trait song song (một trong Odoo, một trong Core), gấp đôi chi phí phát triển và kiểm thử.

Thay vào đó, **các gói là cờ tính năng (feature flag) trên Core**:

| Gói | Tính năng |
|----|----------|
| **Lite** | Chỉ dữ liệu ERP: hồ sơ 360, RFM/LTV, nhắc tái khám, phân khúc cho Marketing Automation |
| **Pro** | + tracking web, Zalo OA/ZNS, Facebook/TikTok lead + CAPI + Audiences, đo doanh thu theo quảng cáo, trigger real-time |
| **AI** | + dự báo no-show, khả năng chấp nhận điều trị, gợi ý khóa/dịch vụ tiếp theo, LLM tạo phân khúc bằng tiếng Việt |

Phí tin nhắn ZNS/SMS tính theo lượt (credit), giống mô hình IAP của Odoo.

Ngoại lệ duy nhất là phòng khám cài on-premise không có internet. Trường hợp này hiếm, nên không thiết kế cho nó ở giai đoạn đầu.

---

## 2. CDP "chuẩn concept": 7 năng lực

| # | Năng lực | DMAG (eLearning) | Phòng khám (Dental ERP) |
|---|---------|-----------------|------------------------|
| 1 | **Ingestion** | Website/eLearning Odoo (SDK), đơn hàng, ghi danh, tiến độ học, chứng chỉ, Zalo, Facebook | Lịch hẹn, kế hoạch điều trị, thủ thuật, thanh toán/trả góp, landing page, Zalo, Facebook/TikTok lead |
| 2 | **Identity resolution** | Khách vô danh trên web ↔ tài khoản portal ↔ SĐT ↔ email ↔ Zalo ↔ `res.partner` | Bệnh nhân ↔ người thân/hộ gia đình ↔ lead quảng cáo ↔ Zalo; hợp nhất giữa các chi nhánh |
| 3 | **Unified profile** | Người học + phòng khám nơi làm việc | Bệnh nhân + hộ gia đình |
| 4 | **Traits & AI** | RFM, CLV, engagement học tập, lead score, khóa gợi ý | LTV, no-show, chấp nhận điều trị, đến hạn tái khám |
| 5 | **Segmentation** | Động, batch và real-time | Như DMAG |
| 6 | **Activation** | Marketing Automation (Bizapp), ZNS, Facebook CAPI/Audiences, CRM | Như DMAG, cộng nhắc lịch, tái khám, khảo sát |
| 7 | **Governance** | Luật BVDLCN 91/2025/QH15 | Thêm yêu cầu cho dữ liệu sức khỏe (nhạy cảm) |

---

## 3. Kiến trúc chi tiết

```
 ┌──────────── Odoo DMAG / Dental ERP (mỗi tenant) ───────────┐
 │ Website + eLearning ── JS SDK ─────────────────┐            │
 │ Nghiệp vụ (ORM) ── cdp_outbox ── job gửi lô ─┐ │            │
 │ cdp.profile / cdp.segment ◄── API nhận ◄──┐  │ │            │
 │ Marketing Automation ◄── cdp_marketing_bridge│ │            │
 └────────────────────────────────────────────┼──┼─┼────────────┘
                                              │  │ │  HTTPS (HMAC)
                                              │  ▼ ▼
 ┌──────────────────────── CDP Core ─────────┼────────────────────┐
 │ Collector API ─► Redpanda ─► Identity svc ─► PostgreSQL (profile)│
 │                         └──► ClickHouse (event) ─► dbt traits     │
 │ Segmentation ─► Activation: Odoo · ZNS · FB/TikTok · Webhook ──┘ │
 │ Console vận hành · Metering/billing · Cách ly tenant (RLS)       │
 └──────────────────────────────────────────────────────────────────┘
```

### 3.1. Bộ module Odoo

| Module | Phụ thuộc | Vai trò |
|-------|----------|--------|
| `cdp_base` | `base`, `mail`, `website` | Outbox + job gửi, đăng ký tenant/API key, mapping cấu hình được, model `cdp.profile` và `cdp.segment`, API nhận dữ liệu từ Core, gắn JS SDK vào website, widget 360 |
| `cdp_education` | `cdp_base`, `website_slides`, `website_sale`, `survey` | Sự kiện eLearning (ghi danh, tiến độ, hoàn thành, chứng chỉ), giỏ hàng/đơn khóa học, tab 360 người học |
| `cdp_dental` | `cdp_base`, các module Dental ERP | Sự kiện lịch hẹn, kế hoạch điều trị, thủ thuật, thanh toán; tab Patient 360 |
| `cdp_marketing_bridge` | `cdp_base`, module MA của Bizapp | Cho phép chiến dịch MA lọc theo phân khúc CDP và bắt đầu từ trigger CDP; gửi kết quả MA về Core |

### 3.2. Lưu dữ liệu CDP trong Odoo: model riêng, không ghi vào `res.partner`

Dữ liệu từ Core lưu vào model riêng `cdp.profile` (quan hệ 1–1 với `res.partner`), **không** thêm cột `x_cdp_*` vào `res.partner`. Lý do:
- Ghi vào `res.partner` làm đổi `write_date`. Khi đó cơ chế đối soát/outbox coi là partner thay đổi và gửi lại sang Core, tạo **vòng lặp đồng bộ**.
- Tránh kích hoạt các field compute, rule tự động và tracking chatter mà Bizapp/Dental ERP gắn trên `res.partner`. Cập nhật 150k hồ sơ có thể sinh 150k tin nhắn chatter.
- Bảng `res.partner` là bảng "nóng" nhất. Ghi hàng loạt dễ gây lock khi lễ tân/sales đang thao tác.

`res.partner` chỉ có field quan hệ (`cdp_profile_id`) và các field `related`/`search` (`cdp_segment_ids`, `cdp_rfm_segment`, `cdp_ltv`…). Nhờ vậy domain filter của Marketing Automation, CRM và báo cáo vẫn dùng được như field bình thường.

### 3.3. Đồng bộ Odoo → Core

- **Outbox trong cùng transaction**: hook vào method nghiệp vụ, ghi `cdp.outbox`. Rollback thì không phát sự kiện; commit thì chắc chắn gửi được.
- **Job gửi theo lô** (cron mỗi phút, hoặc `queue_job` nếu Bizapp đã dùng): HTTPS + HMAC, retry lũy thừa, idempotency theo `event_id`.
- **Snapshot thực thể** (`entity.upserted`) cho các model trong mapping, kèm **sự kiện nghiệp vụ** (`order.paid`, `course.completed`…).
- **Backfill** khi kích hoạt tenant; **đối soát hằng đêm** theo `write_date`.
- Chi tiết định dạng: [`data-contract-v1.md`](data-contract-v1.md).

### 3.4. Đồng bộ Core → Odoo

- Core gọi API của `cdp_base`, xác thực bằng Odoo API key của một user kỹ thuật + HMAC. Ba loại dữ liệu:
  - cập nhật hồ sơ/trait theo lô (chỉ phần thay đổi);
  - thay đổi thành viên phân khúc (thêm/bớt);
  - trigger real-time.
- Ghi bằng SQL/ORM theo lô 500–1000 bản ghi, trong context tắt tracking (`tracking_disable`, `mail_notrack`) và không chạm `res.partner`.

### 3.5. Phối hợp với Marketing Automation của Bizapp

MA do Bizapp tự viết mô phỏng bản Enterprise. Bản Enterprise chọn đối tượng chiến dịch bằng **domain filter trên một model** và có các hoạt động/trigger theo thời gian hoặc sự kiện. Nếu MA của Bizapp giữ cấu trúc tương tự, `cdp_marketing_bridge` chỉ cần:
1. **Đối tượng theo phân khúc**: chiến dịch lọc `res.partner` bằng domain `[('cdp_segment_ids.key', '=', 'checkout_abandoned_24h')]`. MA không phải sửa gì.
2. **Bắt đầu chiến dịch theo trigger CDP**: thêm loại trigger "Sự kiện CDP". Khi Core báo `trigger.fired`, bridge thêm partner vào chiến dịch tương ứng ngay lập tức, không chờ chu kỳ quét.
3. **Kết quả MA về Core**: gửi, mở, click, hủy đăng ký, hoàn thành chiến dịch được đưa vào outbox.

**Một orchestrator cho mỗi kênh**:
- MA của Odoo lo chuỗi email/SMS/ZNS (nếu MA hỗ trợ ZNS).
- Core lo quảng cáo (Facebook/TikTok) và các trigger real-time mà MA không có.
- Core quản lý giới hạn tần suất liên lạc chung.

### 3.6. Tracking trên website/eLearning Odoo của DMAG

- Module `cdp_website` chèn JS SDK vào layout website. SDK **gửi thẳng tới Core** (domain riêng, ví dụ `t.dmag.vn`), **không** gửi qua Odoo, nên Odoo không chịu tải.
- Người dùng đã đăng nhập portal: template chèn `partner_id` đã ký (HMAC, có hạn) để SDK gọi `identify`. Lịch sử duyệt web khi còn vô danh được gắn vào hồ sơ.
- Sự kiện phía client:
  - xem trang, xem khóa, xem bài học;
  - video theo nguồn DMAG đang dùng:
    - **YouTube**: tiến độ theo mốc 25/50/75/100% qua YouTube iframe API;
    - **NAS**: nếu phát bằng thẻ `<video>` với link file trực tiếp (mp4/HLS) thì đo tiến độ đầy đủ. Nếu nhúng trình phát web của NAS (iframe) thì không đo được. **Khuyến nghị phát video NAS bằng link file trực tiếp**;
    - **Google Drive**: iframe preview không có API, chỉ ghi nhận "mở video" và thời gian ở lại bài học. Hoàn thành bài vẫn lấy chính xác từ Odoo (`slide.slide.partner`);
  - tìm kiếm, thêm vào giỏ, bắt đầu checkout.
- Sự kiện phía server (qua outbox, chính xác hơn client):
  - giỏ hàng (`sale.order._cart_update`) và thanh toán qua **VNPay** và **SePay** (`payment.transaction`):
    - `order.placed`: khách đã chọn VNPay hoặc SePay nhưng tiền chưa về;
    - `order.paid`: VNPay IPN hoặc webhook SePay xác nhận đã nhận tiền;
    - phân khúc "đặt đơn, chưa thanh toán" dùng để nhắc khách bỏ dở VNPay hoặc chưa chuyển khoản SePay;
  - ghi danh (`slide.channel.partner`), hoàn thành bài (`slide.slide.partner`);
  - chứng chỉ (`survey.user_input` đạt);
  - đăng ký tài khoản (`res.users` portal).
- Ghi nhận UTM/fbclid/zclid ở phiên đầu để gán nguồn cho đơn hàng (attribution) và gửi Facebook Conversions API.
- Có thể tắt tracking lượt xem trang mặc định của Odoo (`website.visitor`/`website.track`) để giảm ghi vào DB Odoo, vì CDP đã đảm nhiệm. Cần thống nhất với Bizapp.

### 3.7. CDP Core

- **Công nghệ**:
  - FastAPI (Python 3.12), Redpanda, ClickHouse, PostgreSQL 16 (RLS);
  - dbt-clickhouse, Dagster;
  - React + TypeScript cho Console, OWL cho UI trong Odoo.
- **Cách ly tenant**:
  - `tenant_id` trên mọi bản ghi, PostgreSQL RLS, bắt buộc filter tenant trong ClickHouse;
  - khóa mã hóa riêng cho từng tenant;
  - **không ghép danh tính chéo tenant**;
  - chuỗi phòng khám lớn có thể có cluster riêng.
- **Hạ tầng giai đoạn đầu**: 2–3 máy chủ khoảng 8 vCPU / 32 GB tại cloud Việt Nam, Docker Compose. Chuyển Kubernetes khi có từ khoảng 5–10 phòng khám.

---

### 3.8. Mô hình triển khai: SaaS và Dedicated

|  | **SaaS (mặc định)** | **Dedicated (theo yêu cầu, giá cao)** |
|--|--------------------|---------------------------------------|
| Odoo / Dental ERP | DMAG host, mỗi khách một database | Cài trên server của khách hoặc server riêng do DMAG quản lý |
| CDP Core | Dùng chung cụm multi-tenant của DMAG | **Tùy chọn A**: vẫn kết nối Core SaaS (rẻ hơn, đủ tính năng). **Tùy chọn B**: cài Core single-tenant cạnh Odoo của khách |
| Cập nhật | DMAG deploy liên tục | Theo bản phát hành có version; cập nhật theo lịch hợp đồng hoặc qua kênh cập nhật từ xa |
| Dữ liệu | Tại hạ tầng DMAG (trong nước) | Nằm hoàn toàn tại khách (tùy chọn B). Đây là điểm bán hàng với chuỗi lớn khó tính về dữ liệu bệnh nhân |
| AI dùng chung | Có (dữ liệu ẩn danh, theo hợp đồng) | Chỉ mô hình đóng gói sẵn, không đóng góp dữ liệu |
| Tích hợp Zalo/Facebook/TikTok | Có | Server phải ra được internet tới các API này |

Yêu cầu thiết kế để gói Dedicated không tốn thêm công phát triển:
- **Một codebase, hai cấu hình.** Core chạy được ở chế độ single-tenant chỉ bằng cấu hình. Đóng gói bằng Docker Compose (khách nhỏ) và Helm chart (khách lớn).
- **Chỉ dùng thành phần mã nguồn mở tự host được** (PostgreSQL, ClickHouse, Redpanda, MinIO). Không phụ thuộc dịch vụ managed của một nhà cung cấp cloud cụ thể.
- **License key / kích hoạt** cho bản Dedicated: kiểm soát số chi nhánh, gói tính năng, hạn hỗ trợ.
- **Bảo vệ sở hữu trí tuệ** khi mã nằm trên server khách:
  - Mã Python của module Odoo không che giấu được thực sự, nên dựa vào **hợp đồng license (EULA)**: cấm sao chép, cấm bán lại, cấm reverse-engineer.
  - Core có thể giao dưới dạng container image thay vì mã nguồn.
- **Nghĩa vụ giấy phép mã nguồn mở** chỉ thực sự phát sinh khi giao phần mềm cho khách: LGPL (lõi Odoo đã sửa), AGPL (nếu dùng module OCA giấy phép AGPL). Cần danh mục giấy phép (mục 6).

## 4. Tenant DMAG: use-case ưu tiên

| # | Use-case | Kích hoạt | KPI |
|---|---------|----------|-----|
| 1 | Thu hồi giỏ hàng/checkout bỏ dở | Trigger CDP → MA (ZNS/email) | % đơn thu hồi |
| 2 | Doanh thu theo chiến dịch Facebook + Conversions API | Dashboard, CAPI | ROAS, CPA thực |
| 3 | Loại người đã mua khỏi quảng cáo; lookalike từ khách CLV cao | Facebook Custom Audiences | Chi phí quảng cáo lãng phí |
| 4 | Gợi ý khóa tiếp theo theo lộ trình | MA | Tỷ lệ mua lại |
| 5 | Kéo người học dở quay lại | MA (ZNS/email) | % hoàn thành khóa |
| 6 | Lead score cho khóa hands-on giá cao → sales | CRM | Tỷ lệ chốt |
| 7 | Xem theo phòng khám: nhiều nhân sự đi học → chào gói doanh nghiệp **và Dental ERP** | CRM | Pipeline Dental ERP |

MVP: use-case 1–3.

## 5. Tenant phòng khám: use-case

1. Nhắc tái khám định kỳ (cạo vôi 6 tháng, tái khám niềng, bảo hành implant).
2. Nhắc lịch hẹn + dự báo no-show.
3. Theo dõi kế hoạch điều trị chưa chốt.
4. **Đo doanh thu thực theo nguồn quảng cáo** (lead → lịch hẹn → điều trị → thanh toán). Đây là điểm bán hàng mạnh nhất.
5. Lead quảng cáo vào CRM + chấm điểm + phân cho tư vấn viên.
6. Kích hoạt lại bệnh nhân lâu không quay lại.
7. Khảo sát hài lòng/NPS, mời đánh giá Google.
8. Giới thiệu theo hộ gia đình.
9. Hợp nhất bệnh nhân giữa các chi nhánh.

**Tối thiểu hóa dữ liệu**: CDP chỉ nhận nhóm thủ thuật, ngày, giá trị, trạng thái, chi nhánh, nguồn khách. **Không nhận** ghi chú lâm sàng, sơ đồ răng, phim X-quang, chẩn đoán chi tiết.

---

## 6. Pháp lý, bản quyền & sở hữu

- **DMAG sở hữu toàn bộ sản phẩm** (D3): Dental ERP, Marketing Automation, CDP Core, các module `cdp_*`, SDK, tài liệu. Các điều khoản cần có trong hợp đồng với Bizapp được liệt kê tại [`dieu-khoan-hop-dong-bizapp.md`](dieu-khoan-hop-dong-bizapp.md).
- **Marketing Automation "mô phỏng bản Enterprise"** (D4):
  - Viết lại tính năng tương tự là hợp lệ. Tính năng và ý tưởng không được bảo hộ bản quyền.
  - **Không được** sao chép hay chỉnh sửa mã nguồn, giao diện, tài nguyên của Odoo Enterprise (giấy phép OEEL).
  - Ghi thành cam kết + bồi thường trong hợp đồng.
- **Giấy phép mã nguồn mở**:
  - Odoo Community dùng **LGPLv3**: module riêng của DMAG được dùng giấy phép độc quyền.
  - Nếu **sửa trực tiếp lõi Odoo** và giao phần mềm cho khách (gói Dedicated), phải công bố mã của phần sửa đó.
  - **Module OCA dùng AGPLv3** (nhiều module OCA dùng giấy phép này): AGPL áp dụng cả khi chỉ cung cấp qua mạng (SaaS). Module của DMAG phụ thuộc trực tiếp vào chúng có thể bị yêu cầu công bố mã. Do đó Bizapp phải cung cấp **danh mục mọi module bên thứ ba kèm giấy phép**, và tránh hoặc cách ly module AGPL khỏi các module thương mại.
- **Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15** (hiệu lực 01/01/2026):
  - Consent theo mục đích; quyền truy cập/xóa/rút đồng ý; lưu trữ trong nước; hồ sơ đánh giá tác động.
  - Với phòng khám: dữ liệu sức khỏe là **dữ liệu nhạy cảm**. Phòng khám là bên kiểm soát, DMAG là bên xử lý, cần **DPA** với từng phòng khám.
  - Mặc định không nhắm quảng cáo theo loại điều trị.

> Cần luật sư rà soát hợp đồng với Bizapp và mẫu DPA trước khi bán cho phòng khám đầu tiên.

---

## 7. Lộ trình

| Giai đoạn | Thời gian | Phạm vi | Kết quả nghiệm thu |
|----------|----------|--------|-------------------|
| **0. Khảo sát** | 2 tuần | Với Bizapp: rà model eLearning/sale/MA thực tế, số liệu traffic, chốt data contract v1, hợp đồng IP | Data contract ký duyệt; số liệu tải thật |
| **1. MVP DMAG** | 8–10 tuần | Core (multi-tenant, 1 tenant), `cdp_base`, `cdp_education`, `cdp_marketing_bridge`, SDK website, identity, 360, RFM/CLV, phân khúc → MA, Facebook CAPI/Audiences, consent | Use-case DMAG 1–3 chạy production |
| **2. Đa kênh** | 6–8 tuần | Zalo OA/ZNS, Facebook Lead Ads, lead score → CRM, dashboard attribution, xem theo phòng khám | Use-case DMAG 4–7 |
| **3. Dental add-on** | 8–10 tuần | `cdp_dental`, use-case phòng khám 1–5, gói Lite/Pro, metering, Console tenant | Pilot 2–3 phòng khám |
| **4. Thương mại hóa** | liên tục | AI, LLM segment builder, use-case 6–9, Kubernetes, onboarding tự động | Bán đại trà |

**Nhân sự:**
- 1 tech lead;
- 2–3 backend Python (1 người kiêm data);
- 1 frontend (OWL + React);
- 1 Odoo developer (in-house hoặc Bizapp theo spec);
- 1 BA/PM;
- QA bán thời gian.

---

## 8. Rủi ro

| Rủi ro | Giảm thiểu |
|-------|-----------|
| Rủi ro bản quyền từ module "như Enterprise" | Cam kết + bồi thường trong hợp đồng; review mã khi nhận bàn giao |
| Phụ thuộc Bizapp khi thay đổi Odoo | Data contract có version; module mỏng; DMAG sở hữu code nên có thể chuyển đội phát triển |
| Vòng lặp đồng bộ / làm nặng `res.partner` | Model `cdp.profile` riêng, tắt tracking khi ghi lô (mục 3.2) |
| Rò rỉ dữ liệu chéo tenant | RLS, test tự động cách ly tenant, pentest trước pilot |
| Dữ liệu sức khỏe & quảng cáo | Tối thiểu hóa dữ liệu, DPA, mặc định không target theo điều trị |
| Gửi tin chồng chéo MA ↔ Core | Một orchestrator mỗi kênh, giới hạn tần suất chung |
| Ước tính tải sai | Đo số liệu thật ở giai đoạn 0; Core scale ngang được |
| Module AGPL lẫn vào sản phẩm thương mại | Danh mục giấy phép do Bizapp cung cấp; kiểm tra tự động trường `license` trong manifest ở CI |
| Bản Dedicated bị sao chép / phân mảnh phiên bản | EULA + license key; một codebase, phát hành có version, giới hạn số phiên bản được hỗ trợ đồng thời |

---

## 9. Câu hỏi còn mở

Câu hỏi kỹ thuật cho Bizapp (trả lời trong workshop):
1. Có dùng `queue_job` (OCA) không? Có sửa trực tiếp mã lõi Odoo không? Danh sách module bên thứ ba kèm giấy phép.
2. MA của Bizapp: đối tượng là **chủ phòng khám** (đã xác nhận). Cần biết: lọc bằng domain filter hay model riêng? Hỗ trợ kênh nào (email/SMS/ZNS)? Có trigger theo sự kiện không?
3. Mã kỹ thuật (`code`) của module thanh toán VNPay và SePay của Bizapp. SePay xác nhận qua webhook vào `payment.transaction` hay ghi thẳng vào sổ ngân hàng?
4. Video NAS: phát bằng link file trực tiếp hay trình phát web của NAS?
5. Số liệu thật: traffic/tháng, số đơn/tháng, số học viên đang học.

## 10. Bước tiếp theo

1. Gửi tài liệu này + [`data-contract-v1.md`](data-contract-v1.md) cho Bizapp, workshop 1 buổi để xác nhận model thực tế.
2. **Đưa checklist [`dieu-khoan-hop-dong-bizapp.md`](dieu-khoan-hop-dong-bizapp.md) cho luật sư** để soạn/bổ sung hợp đồng với Bizapp, trước khi Bizapp bắt đầu viết module mới.
3. Dựng khung repo:
   - `core/`;
   - `odoo_addons/cdp_base`, `cdp_education`, `cdp_marketing_bridge`;
   - `sdk/web/`.

   Sau đó bắt đầu giai đoạn 1.
