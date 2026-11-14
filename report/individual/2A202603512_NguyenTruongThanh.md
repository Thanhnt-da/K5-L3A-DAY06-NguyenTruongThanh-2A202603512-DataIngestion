# Báo cáo cá nhân — Day 6

- **Họ và tên:** Nguyễn Trường Thành
- **MSSV:** 2A202603512
- **Repo:** https://github.com/Thanhnt-da/K5-L3A-DAY06-NguyenTruongThanh-2A202603512-DataIngestion

> **Sử dụng AI:** Bài làm có sự hỗ trợ của Claude (Anthropic, Claude Code) để giải thích, viết code pipeline, soạn nháp giả thuyết và báo cáo. Tôi đã đọc, chạy và kiểm tra kết quả. Do hết thời gian, **nhãn grounding trên tập test do Claude chấm (LLM judge)**, một phần nhãn metadata do script kiểm tra. Nguồn của từng nhãn được ghi rõ ở mục Kết quả và trong `starter/results/audit/audit_labels.csv`.

## Dữ liệu và pipeline

- **Chủ đề, nguồn/quyền, số trang và quy tắc tính trang:** Sách *Introductory Sociology 3e* (OpenStax, phân phối qua LibreTexts, https://commons.libretexts.org/book/socialsci-164413), giấy phép **CC BY 4.0**, truy cập 2026-11-13. File PDF có 752 trang vật lý (SHA-256 ghi trong `data/manifest.json`); PDF **không commit** vào repo, manifest ghi link tải. Dùng chương 9–12 (trang PDF 269–410). Một trang được tính là nội dung nếu **không** phải trang tiêu đề/mục lục chương, **không** thuộc phần cuối chương (Key Terms, Section Summary, Section Quiz, Short Answer, Further Research, References) và có **≥200 ký tự** sau khi bỏ footer, nhãn "Learning Objectives" và dòng license. Kết quả: **85 trang nội dung** (ch.9: 19, ch.10: 16, ch.11: 26, ch.12: 24), 57 trang bị loại (49 trang cuối chương, 4 trang tiêu đề, 4 trang gần rỗng). Danh sách trang được tính/loại và số ký tự từng trang nằm trong `data/manifest.json`.
- **Chuẩn hóa, lỗi và giới hạn:** pypdf ở chế độ mặc định tách từ sai trên file này ("th e", "Kin gsley"), nên dùng **PyMuPDF 2.1** đọc theo text block (≈1 block/đoạn). Chuẩn hóa Unicode **NFKC**, bỏ soft hyphen, gộp khoảng trắng trong một block, giữ ranh giới đoạn bằng dòng trống. Không có trang lỗi đọc; trang loại được ghi lý do trong `starter/results/ingestion_log.json`. Giới hạn: caption hình có thể chen giữa đoạn văn; một số đoạn bị ngắt qua trang (xử lý riêng ở chiến lược Z).
- **Embedding, vector store, metric, lệnh chạy và chi phí:** OpenAI `text-embedding-3-small`, **1536 chiều**, SDK `openai 3.50.0`; query và document mã hóa **giống nhau** (API không có prefix). Vector store **PostgreSQL 16.16 + pgvector 0.8.7** (Docker, `starter/docker-compose.yml`), một bảng `chunks` cho mọi chiến lược, khóa `(strategy, chunk_id)`, metric **cosine** (`<=>`), tìm kiếm **exact** (không có ANN index). Có bảng `embedding_cache` theo hash nội dung để không trả tiền hai lần cho cùng một đoạn. Lệnh (từ `starter/`): `python -m src.run ingest`, `check-benchmark`, `tune-hybrid`, `benchmark --split dev|test|probe`. Chi phí embedding thực tế: lần ingest A–C **117.590 token ≈ $0.0024** (36,7 s), lần ingest D/E/F/Z **123.064 token ≈ $0.0025** (45,9 s); câu hỏi test/probe thêm 223 token. Tổng ≈ **$0.005** (giá giả định $0.02/1M token, cần đối chiếu bảng giá OpenAI). Nạp lại toàn bộ: số dòng không đổi, 0 dòng bị xóa, 0 token mới → không nhân đôi.
- **Metadata bắt buộc, trường lọc, nguồn giá trị và lý do:** mỗi chunk có `document_id`, `source`, `page` (trang PDF vật lý) và thêm `printed_page`, `chapter` (int), `section` (vd "9.5"), `section_title`, `page_end`. Giá trị lấy từ **footer in trên mỗi trang** dạng `275 (9.5.1)` (trang in 275, chương 9, mục 9.5) và heading `9.5: Theoretical Perspectives…`, không suy đoán. Trường lọc là `chapter`: các lý thuyết (functionalism, conflict theory, symbolic interactionism) xuất hiện ở cả 4 chương, nên lọc chương có giá trị phân biệt thật.

## Cấu hình và benchmark

- **A / B / C (và D, E, F, Z):**

  | | Chunking | Kích thước | Overlap | Tìm kiếm | So sánh với |
  |---|---|---|---|---|---|
  | A | cố định theo ký tự, từng trang | 500 | 50 | vector | baseline |
  | B | theo đoạn/heading, từng trang | tối đa 1000 | 0 | vector | A |
  | C | **giống hệt B** (dùng lại vector) | tối đa 1000 | 0 | vector + lọc `chapter` khi câu hỏi có filter | B |
  | D | theo đoạn/heading, từng trang | tối đa 500 | 0 | vector | A (cùng kích thước), B (cùng quy tắc) |
  | E | cố định theo ký tự | 500 | 150 | vector | A |
  | F | giống B | tối đa 1000 | 0 | hybrid: vector + full-text PostgreSQL, RRF, trọng số vector 0,75 (chọn trên dev) | B |
  | Z | theo đoạn trong cả **mục**, được nối qua trang | tối đa 1000 | 0 | vector | B (metadata dạng khoảng trang) |

  **B–C chỉ khác đúng một yếu tố:** bộ lọc `chapter`. Quy tắc B: heading ngắn luôn đi cùng đoạn sau; đoạn dài tách tại cuối câu; mảnh <100 ký tự gộp vào chunk bên cạnh (nên max thực tế là 1092). Z nối lại 37 đoạn bị ngắt trang; 46/412 chunk trải 2 trang.
- **Benchmark:** 24 câu tự viết (`benchmark/questions.jsonl`): **15 dev / 5 test** chia trước khi chạy, cộng **4 câu probe** (z01–z04) có evidence vắt qua ranh giới trang, báo cáo riêng (ban đầu nằm trong dev/test; được chuyển sang probe trước khi chạy vì A–F không thể chứa trọn evidence, sẽ làm lệch so sánh). Loại câu: 8 trực tiếp, 7 diễn đạt lại, 6 điều kiện, 3 filter. **3 câu filter** (q05 `chapter=9`, q10 `chapter=10` ở dev; q20 `chapter=12` ở test); filter là ngữ cảnh giả định có trước khi truy xuất, không lấy từ gold. Evidence được kiểm chứng tự động bằng `check-benchmark`: mỗi `evidence_quote` phải nằm trong văn bản đã chuẩn hóa của trang gold (so khớp bỏ dấu câu/khoảng trắng), trang gold phải là trang nội dung và nằm trong filter. q10 có `alternative_gold`.
- **Tiêu chí chọn (chốt trước mọi lần chạy, ghi trong `artifacts/experiment_log.csv`):** Evidence Hit@3 dev cao nhất → Hit@1 dev → grounding thủ công → ít chunk / chi phí thấp hơn.

## Kết quả

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian | Chi phí (token embedding) |
|---|---|---|---|---|---|---|
| A | dev / test | 3/15 / 2/5 | 4/15 / 2/5 | 665 | 3,9 s* | 61.803 (≈$0.0012) |
| B | dev / test | 10/15 / 3/5 | 14/15 / 5/5 | 427 | 2,1 s* | 55.787 (≈$0.0011) |
| C | dev / test | 11/15 / 4/5 | 14/15 / 5/5 | 427 | 1,9 s* | 0 (dùng lại B) |
| D | dev / test | 7/15 / 4/5 | 10/15 / 4/5 | 773 | 3,3 s* | 48.131 (≈$0.0010) |
| E | dev / test | 8/15 / 4/5 | 8/15 / 5/5 | 819 | 3,6 s* | 64.813 (≈$0.0013) |
| F | dev / test | 11/15 / 3/5 | 14/15 / 5/5 | 427 | 1,9 s* | 0 (dùng lại B) |
| **Z (chọn)** | dev / test | 11/15 / 3/5 | 14/15 / 5/5 | 412 | 1,9 s* | 10.120 (≈$0.0002; chỉ 67 chunk mới, 345 chunk có nội dung đã có trong cache) |
| Z | probe | 3/4 | **4/4** | | | |
| A–F | probe | 0/4 | 0/4 | | | |

\* Thời gian nạp lại (đã cache embedding, gồm chunking + ghi DB). Thời gian ingest lần đầu chỉ đo theo đợt: A–C 36,7 s, D/E/F/Z 45,9 s. Trên dev, mọi chiến lược đều lấy đúng **trang** gold trong top-3 (15/15); khác biệt nằm ở việc evidence có nằm trọn trong một chunk không.

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N, test top-3) | Filter tuân thủ (dev + test, n/N) | Grounding test 0/1/2 và TB /2 |
|---|---|---|---|---|
| A | 1,30 (10) | 15/15 | 2/3 | 0/2/3 — 1,60 |
| B | 1,90 (10) | 15/15 | 0/3 | 0/0/5 — 2,00 |
| C | 1,90 (10) | 15/15 | **3/3** | 0/0/5 — 2,00 |
| D | 1,20 (10) | 15/15 | 1/3 | 1/0/4 — 1,60 |
| E | 1,10 (10) | 15/15 | 1/3 | 0/0/5 — 2,00 |
| F | 1,90 (10) | 15/15 | 0/3 | 0/0/5 — 2,00 |
| Z | 1,90 (10) | **14/15** (met061: khoảng trang rộng hơn văn bản) | 0/3 | 0/0/5 — 2,00 |

Grounding dev (tiebreak, nhãn của tác giả): C, F, Z đều 2,00/2 (15/15 câu đạt 2).

**Đường dẫn trace, nhãn và cách chọn mẫu:**
- Trace top-3 đầy đủ: `starter/results/traces_dev.jsonl`, `traces_test.jsonl`, `traces_probe.jsonl`; bảng tổng hợp `summary_*.json/csv`; trọng số hybrid `hybrid_tuning.json`.
- Nhãn: `starter/results/audit/audit_workbook_round1.xlsx`, `audit_workbook_round2.xlsx`, tổng hợp `audit_summary.json`, mọi nhãn kèm nguồn `audit_labels.csv`.
- **Nguồn nhãn:** coherence và grounding dev — **tác giả**; metadata `page_ok` — tác giả (66) + script (1); `chapter_ok`/`section_ok` — script `starter/scripts/verify_metadata.py` (đọc lại PDF bằng pypdf, độc lập với PyMuPDF dùng khi ingest); grounding test — **Claude làm LLM judge** (phương pháp và lý do từng nhãn trong `claude_grounding_labels.json`).
- Mẫu coherence: 10 vị trí chọn **cơ học** (trang ở vị trí round(i·85/10) trong 85 trang nội dung, câu đầu ≥60 ký tự của đoạn đầu ≥150 ký tự), ghi trong `coherence_anchors.json`. Hàng trùng nội dung (C=B, F=B, phần lớn Z=B) dùng lại nhãn và được đánh dấu.

## Phân tích và kết luận

- **A–B:** Hit@3 dev tăng từ 4/15 lên 14/15, coherence từ 1,30 lên 1,90. Phân tích "reachable": chỉ **5/15** evidence nằm trọn trong *bất kỳ* chunk A nào — 10/11 lần A trượt là **không thể đúng do cách cắt** (evidence dài 140–356 ký tự bị cửa sổ 500/50 cắt ngang), chỉ q06 là lỗi truy xuất thật. D tách hai yếu tố mà A–B thay cùng lúc: cùng kích thước 500, chỉ đổi quy tắc cắt (A→D) Hit@3 4→10; cùng quy tắc, tăng kích thước (D→B) 10→14. E (overlap 150) cải thiện A 4→8 nhưng coherence thấp nhất (1,10).
- **B–C:** chỉ khác filter. Filter tuân thủ 0/3 → 3/3; Hit@1 dev 10→11, test 3→4; không đổi ở câu không có filter, đúng giả thuyết.
- **Ví dụ sai 1 — q10** (dev, "(chương 10) Relative poverty được định nghĩa thế nào?", gold p310): B xếp hạng 1 là định nghĩa relative poverty **của chương 9 (p283)** — một định nghĩa khác; C với `chapter=10` đưa p310 lên hạng 1.
- **Ví dụ sai 2 — q20** (test, chương 12, Engels/proletariat, gold p391): B, F, Z xếp hai chunk Marx của chương 9 (p288) trên đoạn Engels; D không lấy được đoạn Engels nào (grounding 0); A có đoạn Engels nhưng bị cắt đầu ("le of the proletariat…", grounding 1).
- **Ví dụ sai 3 — q08** (dev, semi-peripheral nations, gold p306): mọi chiến lược đều lấy p306 ở hạng 3 nhưng là chunk Wallerstein khác, không chứa evidence → lỗi truy xuất thật, không phải lỗi chunking.
- **Hit@3 vs grounding:** q19/A có Hit@3 = 0 (evidence bị chia giữa hai cửa sổ) nhưng đọc chung chunk 2+3 vẫn đủ trả lời → grounding 2. q08 cũng vậy ở dev. Đây là lý do Hit@3 không thay được chấm thủ công.
- **Chiến lược chọn: Z.** Trên dev, B/C/F/Z hòa Hit@3 (14/15); C/F/Z hòa Hit@1 (11/15) và grounding (2,00); tiêu chí cuối (ít chunk) chọn Z (412 so với 427). Trên test Z giữ 5/5 Hit@3 (Hit@1 3/5), và trên probe là chiến lược duy nhất lấy được evidence vắt trang (4/4 so với 0/4). **Đánh đổi:** biên quyết định rất nhỏ (15 chunk); Z đánh đổi truy vết một trang lấy khoảng trang; C tốt hơn Z ở Hit@1 test (4/5) nhờ filter. **Điều kiện áp dụng:** Z có lợi khi bằng chứng hay nằm ở ranh giới trang; nếu người dùng cung cấp chương, nên kết hợp với filter như C (Z + filter chưa được đo).
- **Hạn chế:**
  - Test chỉ 5 câu (1 câu = 20 điểm %); D và E đạt 4/5 Hit@1 trên test nhưng yếu trên dev → dao động do mẫu nhỏ, không kết luận chúng tốt hơn. Chương 12 có 1 câu dev nhưng 3 câu test (phân bổ lệch).
  - **Lỗi metadata của Z phát hiện khi audit (sau khi chạy test, không sửa để giữ tính độc lập của test):** 13/46 chunk Z hai trang có khoảng trang rộng hơn văn bản (vd q18, p341–342 nhưng văn bản chỉ ở p342), do heading hộp ở cuối trang không có dấu chấm bị coi là câu dở dang, và các phần tách của một đoạn đã nối kế thừa cả khoảng trang. Hit@1/Hit@3 không bị thổi phồng vì hit vẫn yêu cầu evidence nằm trong văn bản chunk; chỉ độ chính xác truy vết bị ảnh hưởng. Trong top-3 test, lỗi này xuất hiện ở met061 (Z, q18): tác giả ban đầu đánh ✅, sau khi xem kết quả script đã đổi thành ❌, nên metadata Z là 14/15.
  - Nhãn: tác giả vừa thiết kế vừa chấm (nguy cơ thiên lệch); grounding test do LLM chấm, chưa có người chấm độc lập; trọng số F chọn trên dev nên kết quả dev của F lạc quan.
  - Thời gian chỉ đo theo đợt ingest; chi phí dựa trên giá giả định.
- **Nếu làm lại / mở rộng:** sửa quy tắc nối trang của Z (coi block ngắn kiểu heading là hoàn chỉnh, mỗi phần tách giữ trang riêng) và đo lại như một biến thể có nhãn rõ; thử Z + filter và contextual header (gắn đường dẫn chương/mục vào chunk); tăng tập test (≥15 câu, cân bằng chương) và có người chấm thứ hai; thêm câu không có đáp án để đo khả năng từ chối.
