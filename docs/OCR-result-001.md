# OCR Result 001

**Source file:** `1PPCHB0_ID25003864_20250715_20250716_145036.pdf`  
**Prompt version:** `invoice_prompt.yaml` id=7_6, structure_prompt_version=4  
**Extracted by:** Manual (Claude baseline, pre-iteration)

---

## JSON Output

```json
[
  {
    "docType": "invoice",
    "invoiceType": "Commercial Invoice",
    "nameOfInvoice": "E-INVOICE",
    "invoiceNumber": "ID25003864",
    "invoiceDate": "2025-07-15",
    "page": [1],
    "currency": "MYR",
    "totalNetAmount": 67487.10,
    "totalAmount": 67487.10,
    "totalTaxAmount": 0.00,
    "billToName": "PP CHIN HIN SDN BHD (CO NO.334885H)",
    "billToComposite": "NO.1243,6333, JALAN PERMATANG JANGGUS, PERMATANG PAUH, 13500 BUTTERWORTH, PENANG",
    "billToCountry": "Malaysia",
    "billToCountryCode": "MY",
    "billToTaxIdentificationNumber": "C5884056070",
    "billFromName": "ANN JOO STEEL BERHAD",
    "billFromComposite": "Lot 1236, Prai Industrial Estate, 13600 Prai, Penang, Malaysia",
    "billFromCountry": "Malaysia",
    "billFromCountryCode": "MY",
    "billFromTaxIdentificationNumber": "C855273080",
    "billFromBusinessRegistrationNumber": "199101000340 (460935-M)",
    "purchaseOrderNumber": "P1 516799",
    "deliveryOrderNumber": "LTA2507140039",
    "salesOrderNumber": "OA2507080038",
    "detailOfGoodsOrServices": [
      {
        "articleName": "HR Ribbed Weldable Reinforcing Steel B500B, 10.0mm × 12.000M",
        "description": "138 PCS, 11 BDL",
        "quantity": 11.242,
        "unitOfMeasure": "MT",
        "unitPrice": 2250.00,
        "netAmount": 25294.50,
        "taxRate": "0%",
        "tax": 0.00,
        "grossAmount": 25294.50
      },
      {
        "articleName": "HR Ribbed Weldable Reinforcing Steel B500B, 12.0mm × 12.000M",
        "description": "96 PCS, 15 BDL",
        "quantity": 15.345,
        "unitOfMeasure": "MT",
        "unitPrice": 2200.00,
        "netAmount": 33759.00,
        "taxRate": "0%",
        "tax": 0.00,
        "grossAmount": 33759.00
      },
      {
        "articleName": "HR Ribbed Weldable Reinforcing Steel B500B, 16.0mm × 12.000M",
        "description": "54 PCS, 2 BDL",
        "quantity": 2.048,
        "unitOfMeasure": "MT",
        "unitPrice": 2100.00,
        "netAmount": 4300.80,
        "taxRate": "0%",
        "tax": 0.00,
        "grossAmount": 4300.80
      },
      {
        "articleName": "HR Ribbed Weldable Reinforcing Steel B500B, 20.0mm × 12.000M",
        "description": "34 PCS, 2 BDL",
        "quantity": 2.016,
        "unitOfMeasure": "MT",
        "unitPrice": 2050.00,
        "netAmount": 4132.80,
        "taxRate": "0%",
        "tax": 0.00,
        "grossAmount": 4132.80
      }
    ]
  }
]
```

---

## Extraction Notes

| 字段 | 处理说明 |
|------|---------|
| `invoiceType` | 票面标题为 "E-INVOICE"，e-Invoice Type=31，无 SST/Tax Invoice 标识 → `Commercial Invoice` |
| `invoiceNumber` | e-Invoice Code = ID25003864 |
| `currency` | MYR；数字格式：`,` 千分位、`.` 小数点（东南亚 MYR 标准） |
| `totalAmount` | PDF 版面对齐导致 "Total Including Tax" 文字提取为 0.00，实际 = Total Excluding Tax（税额为 0）= 67,487.10 |
| `billToTaxIdentificationNumber` | C5884056070（C + 10 位，符合马来西亚 TIN 格式）；买方唯一 TIN |
| `billFromBusinessRegistrationNumber` | 从公司抬头读取 199101000340 (460935-M) |
| `purchaseOrderNumber` | Buyer's Order No = P1 516799 |
| `deliveryOrderNumber` | D.O. No. = LTA2507140039 |
| `salesOrderNumber` | AJSB C.O. No. = OA2507080038（供应商内部销售单号） |
| `quantity / unitOfMeasure` | 数量列分 BDL / +/- / MT 三子列；只有 MT × unitPrice = Subtotal（11.242 × 2250 = 25,294.50 ✓），计费单位为 MT |
| `detailOfTaxSummary` | 所有行税额均为 0.00，字段省略 |
| `originalInvoiceReferences` | 非 Credit Note，Original Invoice Ref No. = NA，字段省略 |
