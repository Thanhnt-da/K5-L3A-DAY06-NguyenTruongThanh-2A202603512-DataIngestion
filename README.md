# Day 06 · Data Ingestion Engineering — Nguyễn Trường Thành (2A202603512)

Pipeline biến sách *Introductory Sociology 3e* (OpenStax, CC BY 4.0) thành kho tri thức truy xuất được bằng **PostgreSQL + pgvector** và embedding **OpenAI `text-embedding-3-small`**, rồi so sánh **7 chiến lược dữ liệu** (A–F, Z) trên cùng một benchmark 24 câu.

- Báo cáo cá nhân: [report/individual/2A202603512_NguyenTruongThanh.md](report/individual/2A202603512_NguyenTruongThanh.md)
- Thông tin học viên: [STUDENT.md](STUDENT.md) · Nhật ký thí nghiệm: [artifacts/experiment_log.csv](artifacts/experiment_log.csv)

## Kết quả chính

| Strategy | Mô tả | Dev Hit@1 | Dev Hit@3 | Test Hit@1 | Test Hit@3 |
|---|---|---|---|---|---|
| A | cố định 500 ký tự, overlap 50 | 3/15 | 4/15 | 2/5 | 2/5 |
| B | theo đoạn/heading, tối đa 1000 | 10/15 | 14/15 | 3/5 | 5/5 |
| C | B + lọc `chapter` | 11/15 | 14/15 | 4/5 | 5/5 |
| D | theo đoạn, tối đa 500 | 7/15 | 10/15 | 4/5 | 4/5 |
| E | cố định 500, overlap 150 | 8/15 | 8/15 | 4/5 | 5/5 |
| F | B + hybrid (vector + full-text, RRF) | 11/15 | 14/15 | 3/5 | 5/5 |
| **Z** (chọn trên dev) | theo đoạn trong mục, nối qua trang | 11/15 | 14/15 | 3/5 | 5/5 |

Câu probe vắt qua trang (z01–z04): Z 4/4 Hit@3, A–F 0/4. Phân tích đầy đủ, nhãn thủ công và hạn chế nằm trong báo cáo.

## Cấu trúc

```text
data/manifest.json              # nguồn, giấy phép, link tải PDF, trang được tính/loại, số ký tự
benchmark/questions.jsonl       # 24 câu: 15 dev / 5 test / 4 probe; README.md mô tả định dạng
artifacts/experiment_log.csv    # cấu hình, giả thuyết, kết quả A-F, Z và lý do chọn
starter/
  docker-compose.yml, init.sql  # PostgreSQL 16 + pgvector 0.8.7 (cổng local 55433)
  requirements.txt              # dependencies đã ghim phiên bản
  src/                          # ingestion, chunking, embeddings, store, evaluation, audit, run
  scripts/                      # check_setup, verify_metadata, apply_round2_labels, audit_summary
  results/                      # ingest report, log, trace dev/test/probe, summary, audit/
report/individual/              # báo cáo cá nhân
```

## Tái lập

Yêu cầu: Docker Desktop (WSL 2 trên Windows), Python 3.11–3.14, OpenAI API key. Chạy từ thư mục `starter/`.

1. **Lấy dữ liệu:** tải PDF từ https://commons.libretexts.org/book/socialsci-164413 (nút *Download PDF*), lưu thành `data/Introductory Sociology 3e.pdf` ở thư mục gốc repo. So khớp SHA-256 trong `data/manifest.json`. PDF không được commit.
2. **Môi trường:**
   ```bash
   cd starter
   python -m venv .venv
   .venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
   python -m pip install -r requirements.txt
   Copy-Item .env.example .env          # macOS/Linux: cp .env.example .env
   ```
   Điền `OPENAI_API_KEY` và `EMBEDDING_MODEL=text-embedding-3-small` trong `.env` (không commit file này).
3. **Database:** mở Docker Desktop, rồi
   ```bash
   docker compose -p day6_student_starter up -d --wait
   python scripts/check_setup.py        # kỳ vọng: Setup OK
   ```
4. **Pipeline và benchmark:**
   ```bash
   python -m src.run ingest                         # đọc PDF, chunk, embed, lưu 7 chiến lược (chạy lại không nhân đôi)
   python -m src.run check-benchmark                # kiểm chứng evidence trên trang gold (không gọi API)
   python -m src.run tune-hybrid                    # chọn trọng số F chỉ trên dev
   python -m src.run benchmark --split dev
   python -m src.run benchmark --split test --chosen Z
   python -m src.run benchmark --split probe
   ```
5. **Audit:**
   ```bash
   python -m src.run audit-sheets --round 1 --tied C,F,Z   # coherence + grounding dev (Excel)
   python -m src.run audit-sheets --round 2                # metadata + grounding test (Excel)
   python scripts/verify_metadata.py                       # kiểm tra metadata độc lập bằng pypdf
   python scripts/audit_summary.py                         # bảng tổng hợp + audit_labels.csv
   ```
   Lệnh tạo workbook không ghi đè file đã có nhãn trừ khi thêm `--overwrite`.

Chi phí embedding toàn bộ ≈ $0.005 (khoảng 240 nghìn token). Phiên bản thư viện và thời điểm chạy được ghi trong `starter/results/ingest_report.json` và `summary_*.json`.

## Sử dụng AI

Bài làm có sự hỗ trợ của Claude (Anthropic, Claude Code) để viết code, soạn nháp giả thuyết và báo cáo. Nhãn grounding trên tập test do Claude chấm như một LLM judge có ghi phương pháp; nguồn của từng nhãn nằm trong `starter/results/audit/audit_labels.csv`. Chi tiết trong báo cáo cá nhân.

## Đề bài

Đề bài gốc và hướng dẫn: [GUIDE.md](GUIDE.md) · [BENCHMARK.md](BENCHMARK.md) · [RUBRIC.md](RUBRIC.md) · [RULES.md](RULES.md) · [SUBMISSION.md](SUBMISSION.md) · [CHECKPOINTS.md](CHECKPOINTS.md) · [starter/README.md](starter/README.md)
