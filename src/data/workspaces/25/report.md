# VNM2.pdf — Document Profile & Analysis

## What Matters Most

1. **This is an e-seal page, not data** — VNM2.pdf contains a single-page Vietnamese electronic company seal (`Con dấu số`), used for authenticating official documents issued by **Vinamilk**.
2. **Digitally signed on April 29, 2026** by *Công Ty Cổ Phần Sữa Việt Nam* (Vinamilk / VNM), Vietnam's largest dairy producer headquartered in Ho Chi Minh City.
3. **The seal has been retired** — the page header states **"ĐÓNG RÚT"** (Withdrawn/Closed), meaning this specific electronic seal is no longer active for new document signing.

## Page Dimensions & Layout

| Dimension | Value |
|---|---|
| Page Size | A4 Landscape (842 × 595 pts) |
| Images Embedded | 2 JPEG images |
| Fonts Used | Helvetica, ZapfDingbats, MyriadPro-Regular |

<p align="center"><img src="/api/v1/files/proxy-object?path=vnm2_analysis.png" alt="Document profile chart" width="80%"></p>

## Content Breakdown

### Visible Text Elements

| Position | Content | Meaning |
|---|---|---|
| Top-left | **ĐÓNG RÚT — THỦ DẦU MỘT — BÌNH DƯƠNG** | Retired seal, Binh Duong province |
| Center-right | **CÔNG TY CỔ PHẦN SỮA VIỆT NAM** | Legal entity name (Vinamilk) |
| Overlay stamp | Digitally signed: 2026-04-29 17:18:59 UTC+7 | Timestamp of last use/signing |

### Security & Technical Properties

- **PDF Version:** 1.6 (produced by iText 2.1.7)
- **Encryption:** Standard object-level encryption enabled
- **AcroForm Fields:** Present but inactive (legacy form fields)
- **File size:** ~222 KB (dominated by embedded JPEGs)

## Key Takeaways

- This file is a **corporate authentication artifact**, not a transactional or analytical dataset
- The electronic seal was last used on **April 29, 2026** and has since been **withdrawn** ("ĐÓNG RÚT")
- No quantitative data, financial figures, or operational metrics are embedded in this document
- The digital signature can be verified at [Vietnam's national e-seal registry](https://congthongtin.dchquocgia.gov.vn/)

## Recommended Next Steps

- If you need transactional data, financial reports, or operational metrics from Vinamilk, request their audited financial statements, annual reports, or SEC filings instead
- To verify this seal's validity for ongoing business, confirm whether Vinamilk has issued a replacement electronic seal via the Vietnam National Platform

## Methods & Limitations

Analysis performed using `PyMuPDF` (fitz) for PDF structure inspection and `matplotlib` for visualization. Only text metadata, font tables, image dimensions, and encryption flags were extracted. The actual visual content (the rendered seal graphics) was not decoded beyond dimension analysis.
