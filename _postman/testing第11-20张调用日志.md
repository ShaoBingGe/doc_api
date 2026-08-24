# testing/ 第 11-20 张 · invoicase.cn 生产调用日志与比对

**工具**：Postman Collection + newman 6（`_postman/testing_11_20.postman_collection.json`）  
**环境**：生产 https://invoicase.cn　templateId=7（Chinkin MY）　qwen3-vl-plus  
**结果**：请求 11 个 · 断言 121 项 · 失败 0 项 · 用时 3m30s  
**样本**：`testing/` 按文件名排序第 11-20 张（全为单页；无人工 GT，抽 4 张对照原件核验）

> 本批已含前一轮的三项修复：多票据不截断、MAX_PAGES=16、超页提示。

---

## 记录 1 — 1. POST /base/oauth/token

### 请求

```
POST invoicasecn/base/oauth/token
Content-Type: application/json
Accept: */*
Cache-Control: no-cache
Postman-Token: 2fe2d300-ee65-4f3c-a799-c0e8e3b7151f
```

**请求体**：

```json
{
  "client_id": "TN_RACzZVvvVh7MHg2xT",
  "timestamp": "1787537286",
  "sign": "cf29e8089d2b6c3035366ee4b3986f06"
}
```

### 接口返回

HTTP 200 · 123ms

```json
{
  "errcode": "0000",
  "description": "操作成功",
  "access_token": "4f27c0cf...33a6",
  "token_type": "bearer",
  "expires_in": 129600
}
```

**断言**：1/1 通过

---

## 记录 2 — 2. EBM INV2507-296_20250715_103429

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=4f27c0cfb109...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: eca02d62-7473-42ff-8a6b-35e8173a3aff
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "668b0a066c1089496038376936c4375a",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "EBM INV2507-296_20250715_103429.pdf"
}
```

### 接口返回

HTTP 200 · 21370ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "668b0a066c1089496038376936c4375a",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Commercial Invoice",
          "invoiceNumber": "INV2507-296",
          "invoiceCode": "",
          "invoiceDate": "2025-07-02",
          "totalNetAmount": "5286.12",
          "totalAmount": "5286.12",
          "totalTaxAmount": "",
          "currency": "MYR",
          "page": [
            "1"
          ]
        },
        "billTo": {
          "billToName": "PP CHIN HIN SDN BHD",
          "billToComposite": "",
          "billToCity": "",
          "billToStateOrProvince": "",
          "billToCountry": "",
          "billToCountryCode": "",
          "billToFax": "",
          "billToPostalCode": "",
          "billToTelephone": "",
          "billToTaxIdentificationNumber": "",
          "billToRecipient": "",
          "billToBankAccount": "",
          "billToBankOfAccount": "",
          "billToEmail": ""
        },
        "billFrom": {
          "billFromName": "EBM MARKETING SDN BHD",
          "billFromComposite": "",
          "billFromCity": "",
          "billFromStateOrProvince": "",
          "billFromCountry": "",
          "billFromCountryCode": "",
          "billFromFax": "",
          "billFromPostalCode": "",
          "billFromTelephone": "",
          "billFromTaxIdentificationNumber": "",
          "billFromBusinessRegistrationNumber": "",
          "billFromBankAccount": "",
          "billFromBankOfAccount": "",
          "billFromEmail": ""
        },
        "bussiness": {
          "purchaseOrderNumber": "W1 515443",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "",
          "deliveryOrderNumber": "DO251644"
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "Net 60 days",
          "dueDate": "",
          "paymentCurrency": "",
          "exchangeRate": "",
          "paidAmount": ""
        }
      },
      "detail": {
        "detailOfGoodsOrServices": [
          {
            "articleID": "",
            "articleName": "",
            "description": "SAGAPAVAR 80MM- STD RED",
            "quantity": "121.52",
            "unitOfMeasure": "",
            "unitPrice": "43.5",
            "netAmount": "5286.12",
            "taxRate": "",
            "tax": "",
            "grossAmount": "",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "WOODEN PALLET",
            "quantity": "10",
            "unitOfMeasure": "",
            "unitPrice": "0.0",
            "netAmount": "0.0",
            "taxRate": "",
            "tax": "",
            "grossAmount": "",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          }
        ],
        "detailOfTaxSummary": [],
        "originalInvoiceReferences": []
      }
    }
  ],
  "traceId": "1bff2c559100c937",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 记录 3 — 3. EBM INV2507-297_20250715_103428

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=4f27c0cfb109...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: e36b0333-b931-4dc1-a940-3f1748628a03
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "e863c2d956836d4fe8062bfc7fafc29a",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "EBM INV2507-297_20250715_103428.pdf"
}
```

### 接口返回

HTTP 200 · 18795ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "e863c2d956836d4fe8062bfc7fafc29a",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Commercial Invoice",
          "invoiceNumber": "INV2507-297",
          "invoiceCode": "",
          "invoiceDate": "2025-07-10",
          "totalNetAmount": "5286.12",
          "totalAmount": "5286.12",
          "totalTaxAmount": "",
          "currency": "MYR",
          "page": [
            "1"
          ]
        },
        "billTo": {
          "billToName": "PP CHIN HIN SDN BHD",
          "billToComposite": "",
          "billToCity": "",
          "billToStateOrProvince": "",
          "billToCountry": "",
          "billToCountryCode": "",
          "billToFax": "",
          "billToPostalCode": "",
          "billToTelephone": "",
          "billToTaxIdentificationNumber": "",
          "billToRecipient": "",
          "billToBankAccount": "",
          "billToBankOfAccount": "",
          "billToEmail": ""
        },
        "billFrom": {
          "billFromName": "EBM MARKETING SDN BHD",
          "billFromComposite": "",
          "billFromCity": "",
          "billFromStateOrProvince": "",
          "billFromCountry": "",
          "billFromCountryCode": "",
          "billFromFax": "",
          "billFromPostalCode": "",
          "billFromTelephone": "",
          "billFromTaxIdentificationNumber": "",
          "billFromBusinessRegistrationNumber": "",
          "billFromBankAccount": "",
          "billFromBankOfAccount": "",
          "billFromEmail": ""
        },
        "bussiness": {
          "purchaseOrderNumber": "W1 516808",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "",
          "deliveryOrderNumber": "DO251706"
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "Net 60 days",
          "dueDate": "",
          "paymentCurrency": "",
          "exchangeRate": "",
          "paidAmount": ""
        }
      },
      "detail": {
        "detailOfGoodsOrServices": [
          {
            "articleID": "",
            "articleName": "",
            "description": "SAGAPAVAR 80MM - STD RED",
            "quantity": "121.52",
            "unitOfMeasure": "",
            "unitPrice": "43.5",
            "netAmount": "5286.12",
            "taxRate": "",
            "tax": "",
            "grossAmount": "",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "WOODEN PALLET",
            "quantity": "10",
            "unitOfMeasure": "",
            "unitPrice": "0.0",
            "netAmount": "0.0",
            "taxRate": "",
            "tax": "",
            "grossAmount": "",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          }
        ],
        "detailOfTaxSummary": [],
        "originalInvoiceReferences": []
      }
    }
  ],
  "traceId": "a4e3619bd4cdeb4a",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 记录 4 — 4. ES248690.PDF_20250716_161011

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=4f27c0cfb109...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: 2436c074-a818-44d1-9c39-9e04071393c9
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "8ce8c5a849ca4187227ea7bd0f1c939e",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "ES248690.PDF_20250716_161011.pdf"
}
```

### 接口返回

HTTP 200 · 16070ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "8ce8c5a849ca4187227ea7bd0f1c939e",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Commercial Invoice",
          "invoiceNumber": "ES248690",
          "invoiceCode": "",
          "invoiceDate": "2025-07-15",
          "totalNetAmount": "2665.0",
          "totalAmount": "2665.0",
          "totalTaxAmount": "",
          "currency": "MYR",
          "page": [
            "1"
          ]
        },
        "billTo": {
          "billToName": "PP CHIN HIN SDN BHD",
          "billToComposite": "",
          "billToCity": "",
          "billToStateOrProvince": "",
          "billToCountry": "",
          "billToCountryCode": "",
          "billToFax": "",
          "billToPostalCode": "",
          "billToTelephone": "",
          "billToTaxIdentificationNumber": "",
          "billToRecipient": "",
          "billToBankAccount": "",
          "billToBankOfAccount": "",
          "billToEmail": ""
        },
        "billFrom": {
          "billFromName": "ES ENG SOON TRADING SDN BHD",
          "billFromComposite": "",
          "billFromCity": "",
          "billFromStateOrProvince": "",
          "billFromCountry": "",
          "billFromCountryCode": "",
          "billFromFax": "",
          "billFromPostalCode": "",
          "billFromTelephone": "",
          "billFromTaxIdentificationNumber": "",
          "billFromBusinessRegistrationNumber": "",
          "billFromBankAccount": "",
          "billFromBankOfAccount": "",
          "billFromEmail": ""
        },
        "bussiness": {
          "purchaseOrderNumber": "W1 517462",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "",
          "deliveryOrderNumber": ""
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "60 DAYS",
          "dueDate": "",
          "paymentCurrency": "",
          "exchangeRate": "",
          "paidAmount": ""
        }
      },
      "detail": {
        "detailOfGoodsOrServices": [
          {
            "articleID": "",
            "articleName": "",
            "description": "WBP PLYWOOD 4 X 8 X 25MM",
            "quantity": "27",
            "unitOfMeasure": "",
            "unitPrice": "95.0",
            "netAmount": "2565.0",
            "taxRate": "",
            "tax": "",
            "grossAmount": "",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "TRANSPORT CHARGES",
            "quantity": "1",
            "unitOfMeasure": "",
            "unitPrice": "100.0",
            "netAmount": "100.0",
            "taxRate": "",
            "tax": "",
            "grossAmount": "",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          }
        ],
        "detailOfTaxSummary": [],
        "originalInvoiceReferences": []
      }
    }
  ],
  "traceId": "6377c6c39ff31143",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 记录 5 — 5. F-SAL60 Invoice_20250715_215239

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=4f27c0cfb109...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: 34938388-995e-4b4f-a026-aa8a567658ac
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "41f5daf2bc47c0597c76a42d081760fa",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "F-SAL60 Invoice_20250715_215239.pdf"
}
```

### 接口返回

HTTP 200 · 20735ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "41f5daf2bc47c0597c76a42d081760fa",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Commercial Invoice",
          "invoiceNumber": "IV-65266",
          "invoiceCode": "",
          "invoiceDate": "2025-05-31",
          "totalNetAmount": "2810.0",
          "totalAmount": "2810.0",
          "totalTaxAmount": "0.0",
          "currency": "MYR",
          "page": [
            "1"
          ]
        },
        "billTo": {
          "billToName": "PP CHIN HIN SDN BHD - PERAK",
          "billToComposite": "PLOT 11-LOT 300812, JALAN PUSING TAMAN PERINDUSTRIAN PERABOT, NEGERI PERAK 31550, PUSING, PERAK",
          "billToCity": "",
          "billToStateOrProvince": "",
          "billToCountry": "",
          "billToCountryCode": "",
          "billToFax": "",
          "billToPostalCode": "",
          "billToTelephone": "",
          "billToTaxIdentificationNumber": "199501005689",
          "billToRecipient": "",
          "billToBankAccount": "",
          "billToBankOfAccount": "",
          "billToEmail": ""
        },
        "billFrom": {
          "billFromName": "VIVO RAISER SDN. BHD.",
          "billFromComposite": "9 & 10, JLN PKNK 3/7, KAWASAN PERUSAHAAN SUNGAI PETANI 08000 SUNGAI PETANI, KEDAH, MALAYSIA",
          "billFromCity": "",
          "billFromStateOrProvince": "",
          "billFromCountry": "",
          "billFromCountryCode": "",
          "billFromFax": "",
          "billFromPostalCode": "",
          "billFromTelephone": "04-4102148",
          "billFromTaxIdentificationNumber": "C920807P",
          "billFromBusinessRegistrationNumber": "920807-P",
          "billFromBankAccount": "",
          "billFromBankOfAccount": "",
          "billFromEmail": "sales@shijiru.com"
        },
        "bussiness": {
          "purchaseOrderNumber": "A1 512814 R30/5",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "",
          "deliveryOrderNumber": "DO-76834"
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "60 Days",
          "dueDate": "2025-07-30",
          "paymentCurrency": "",
          "exchangeRate": "",
          "paidAmount": ""
        }
      },
      "detail": {
        "detailOfGoodsOrServices": [
          {
            "articleID": "",
            "articleName": "",
            "description": "TPS Bonding Agent Premium (20kg)",
            "quantity": "5.0",
            "unitOfMeasure": "",
            "unitPrice": "100.0",
            "netAmount": "500.0",
            "taxRate": "",
            "tax": "",
            "grossAmount": "500.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "TECHNOBOND White Tile Grout 838 25kg",
            "quantity": "70.0",
            "unitOfMeasure": "",
            "unitPrice": "17.0",
            "netAmount": "1190.0",
            "taxRate": "",
            "tax": "",
            "grossAmount": "1190.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "TECHNOBOND Superbond 163 C2TE 40kg",
            "quantity": "80.0",
            "unitOfMeasure": "",
            "unitPrice": "14.0",
            "netAmount": "1120.0",
            "taxRate": "",
            "tax": "",
            "grossAmount": "1120.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          }
        ],
        "detailOfTaxSummary": [],
        "originalInvoiceReferences": []
      }
    }
  ],
  "traceId": "9c23e99c818ab0f3",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 记录 6 — 6. HOE GUAN INV L0013052_20250716_123224

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=4f27c0cfb109...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: a3279fb2-0192-49ab-9ac9-94019a07f6a5
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "66be26f3fc105d38a7e7786cf14116b9",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "HOE GUAN INV L0013052_20250716_123224.pdf"
}
```

### 接口返回

HTTP 200 · 19981ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "66be26f3fc105d38a7e7786cf14116b9",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Tax Invoice",
          "invoiceNumber": "L0013052",
          "invoiceCode": "",
          "invoiceDate": "2025-06-20",
          "totalNetAmount": "10780.0",
          "totalAmount": "10780.0",
          "totalTaxAmount": "0.0",
          "currency": "MYR",
          "page": [
            "1"
          ]
        },
        "billTo": {
          "billToName": "PP CHIN HIN SDN BHD",
          "billToComposite": "NO 90A,JLN SUTERA TANJUNG 8/4, TMN SUTERA UTAMA,81300 SKUDAI,JOHOR",
          "billToCity": "",
          "billToStateOrProvince": "",
          "billToCountry": "",
          "billToCountryCode": "",
          "billToFax": "",
          "billToPostalCode": "",
          "billToTelephone": "",
          "billToTaxIdentificationNumber": "C5884056070",
          "billToRecipient": "",
          "billToBankAccount": "",
          "billToBankOfAccount": "",
          "billToEmail": ""
        },
        "billFrom": {
          "billFromName": "HOE GUAN BRICKWORKS SDN BHD",
          "billFromComposite": "11-12 JALAN TENGKU MAHKOTA ISMAIL, 86000 KLUANG, JOHOR",
          "billFromCity": "",
          "billFromStateOrProvince": "",
          "billFromCountry": "",
          "billFromCountryCode": "",
          "billFromFax": "",
          "billFromPostalCode": "",
          "billFromTelephone": "",
          "billFromTaxIdentificationNumber": "J12-1608-2100/217",
          "billFromBusinessRegistrationNumber": "13984-K",
          "billFromBankAccount": "",
          "billFromBankOfAccount": "",
          "billFromEmail": ""
        },
        "bussiness": {
          "purchaseOrderNumber": "PO Ni 513699",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "00000290",
          "deliveryOrderNumber": ""
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "",
          "dueDate": "2025-08-19",
          "paymentCurrency": "",
          "exchangeRate": "1.0",
          "paidAmount": ""
        }
      },
      "detail": {
        "detailOfGoodsOrServices": [
          {
            "articleID": "",
            "articleName": "",
            "description": "COMMON CLAY BRICK-20/06 SOLID(20PLTS X 700PCS)-L0040529-JMC 9729",
            "quantity": "14000.0",
            "unitOfMeasure": "",
            "unitPrice": "0.44",
            "netAmount": "6160.0",
            "taxRate": "0%",
            "tax": "0.0",
            "grossAmount": "6160.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "COMMON CLAY BRICK-20/06 SOLID(15PLTS X 700PCS)-L0040530-JQM 9729",
            "quantity": "10500.0",
            "unitOfMeasure": "",
            "unitPrice": "0.44",
            "netAmount": "4620.0",
            "taxRate": "0%",
            "tax": "0.0",
            "grossAmount": "4620.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          }
        ],
        "detailOfTaxSummary": [
          {
            "taxCategory": "SST",
            "taxRate": "0%",
            "netTaxableAmount": "10780.0",
            "tax": "0.0"
          }
        ],
        "originalInvoiceReferences": []
      }
    }
  ],
  "traceId": "598024314af30e00",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 记录 7 — 7. HOE GUAN INV L0013052_20250716_135213

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=4f27c0cfb109...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: 36d9d4e7-e88e-4396-a905-e2d2db4e82f5
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "66be26f3fc105d38a7e7786cf14116b9",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "HOE GUAN INV L0013052_20250716_135213.pdf"
}
```

### 接口返回

HTTP 200 · 18571ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "66be26f3fc105d38a7e7786cf14116b9",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Commercial Invoice",
          "invoiceNumber": "L0013052",
          "invoiceCode": "",
          "invoiceDate": "2025-06-20",
          "totalNetAmount": "10780.0",
          "totalAmount": "10780.0",
          "totalTaxAmount": "0.0",
          "currency": "MYR",
          "page": [
            "1"
          ]
        },
        "billTo": {
          "billToName": "PP CHIN HIN SDN BHD",
          "billToComposite": "NO 90A,JLN SUTERA TANJUNG 8/4, TMN SUTERA UTAMA,81300 SKUDAI,JOHOR",
          "billToCity": "",
          "billToStateOrProvince": "",
          "billToCountry": "",
          "billToCountryCode": "",
          "billToFax": "",
          "billToPostalCode": "",
          "billToTelephone": "",
          "billToTaxIdentificationNumber": "C5884056070",
          "billToRecipient": "",
          "billToBankAccount": "",
          "billToBankOfAccount": "",
          "billToEmail": ""
        },
        "billFrom": {
          "billFromName": "HOE GUAN BRICKWORKS SDN BHD",
          "billFromComposite": "11-12 JALAN TENGKU MAHKOTA ISMAIL, 86000 KLUANG, JOHOR",
          "billFromCity": "",
          "billFromStateOrProvince": "",
          "billFromCountry": "",
          "billFromCountryCode": "",
          "billFromFax": "",
          "billFromPostalCode": "",
          "billFromTelephone": "",
          "billFromTaxIdentificationNumber": "J12-1608-2100/217",
          "billFromBusinessRegistrationNumber": "13984-K",
          "billFromBankAccount": "",
          "billFromBankOfAccount": "",
          "billFromEmail": ""
        },
        "bussiness": {
          "purchaseOrderNumber": "PO Ni 513699",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "00000290",
          "deliveryOrderNumber": ""
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "",
          "dueDate": "2025-08-19",
          "paymentCurrency": "",
          "exchangeRate": "1.0",
          "paidAmount": ""
        }
      },
      "detail": {
        "detailOfGoodsOrServices": [
          {
            "articleID": "",
            "articleName": "",
            "description": "COMMON CLAY BRICK-20/06\nSOLID(20PLTS X 700PCS)-L0040529-JMC 9729",
            "quantity": "14000.0",
            "unitOfMeasure": "",
            "unitPrice": "0.44",
            "netAmount": "6160.0",
            "taxRate": "0%",
            "tax": "0.0",
            "grossAmount": "6160.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "COMMON CLAY BRICK-20/06\nSOLID(15PLTS X 700PCS)-L0040530-JQM 9729",
            "quantity": "10500.0",
            "unitOfMeasure": "",
            "unitPrice": "0.44",
            "netAmount": "4620.0",
            "taxRate": "0%",
            "tax": "0.0",
            "grossAmount": "4620.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          }
        ],
        "detailOfTaxSummary": [
          {
            "taxCategory": "SST",
            "taxRate": "0%",
            "netTaxableAmount": "10780.0",
            "tax": "0.0"
          }
        ],
        "originalInvoiceReferences": []
      }
    }
  ],
  "traceId": "508dd0bd8f7b02a0",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 记录 8 — 8. HOE GUAN INV L0013084_20250716_123225

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=4f27c0cfb109...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: 630a0aa3-8e51-433f-8bb8-bdb21baa41a7
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "060d48f3c59db8c7e148e0543a91801f",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "HOE GUAN INV L0013084_20250716_123225.pdf"
}
```

### 接口返回

HTTP 200 · 31345ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "060d48f3c59db8c7e148e0543a91801f",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Tax Invoice",
          "invoiceNumber": "L0018084",
          "invoiceCode": "",
          "invoiceDate": "2025-06-30",
          "totalNetAmount": "43120.0",
          "totalAmount": "43120.0",
          "totalTaxAmount": "0.0",
          "currency": "MYR",
          "page": [
            "1"
          ]
        },
        "billTo": {
          "billToName": "PP CHIN HIN SDN BHD",
          "billToComposite": "NO 90A,JLN SUTERA TANJUNG 8-4, TMN SUTERA UTAMA,81300 SKUDAI,JOHOR",
          "billToCity": "",
          "billToStateOrProvince": "",
          "billToCountry": "",
          "billToCountryCode": "",
          "billToFax": "",
          "billToPostalCode": "",
          "billToTelephone": "",
          "billToTaxIdentificationNumber": "199501005689-334885-H",
          "billToRecipient": "",
          "billToBankAccount": "",
          "billToBankOfAccount": "",
          "billToEmail": ""
        },
        "billFrom": {
          "billFromName": "HOE GUAN BRICK WORKS SDN BHD",
          "billFromComposite": "11-12 JALAN TENGKU MAHKOTA ISMAIL, 86000 KLUANG, JOHOR",
          "billFromCity": "",
          "billFromStateOrProvince": "",
          "billFromCountry": "",
          "billFromCountryCode": "",
          "billFromFax": "",
          "billFromPostalCode": "",
          "billFromTelephone": "",
          "billFromTaxIdentificationNumber": "J12-1808-21007217",
          "billFromBusinessRegistrationNumber": "13984-K",
          "billFromBankAccount": "",
          "billFromBankOfAccount": "",
          "billFromEmail": ""
        },
        "bussiness": {
          "purchaseOrderNumber": "PON1 511447",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "00000286",
          "deliveryOrderNumber": ""
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "",
          "dueDate": "2025-08-29",
          "paymentCurrency": "",
          "exchangeRate": "",
          "paidAmount": ""
        }
      },
      "detail": {
        "detailOfGoodsOrServices": [
          {
            "articleID": "",
            "articleName": "",
            "description": "COMMON CLAY BRICK-30 06",
            "quantity": "14000.0",
            "unitOfMeasure": "",
            "unitPrice": "0.44",
            "netAmount": "6160.0",
            "taxRate": "0%",
            "tax": "0.0",
            "grossAmount": "6160.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "SOLID(20PLTS X 700PCS)-L0040574-JFY 7659 COMMON CLAY BRICK-30 06",
            "quantity": "14000.0",
            "unitOfMeasure": "",
            "unitPrice": "0.44",
            "netAmount": "6160.0",
            "taxRate": "0%",
            "tax": "0.0",
            "grossAmount": "6160.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "SOLID(20PLTS X 700PCS)-L0040575-JXL 9729 COMMON CLAY BRICK-30 06",
            "quantity": "14000.0",
            "unitOfMeasure": "",
            "unitPrice": "0.44",
            "netAmount": "6160.0",
            "taxRate": "0%",
            "tax": "0.0",
            "grossAmount": "6160.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "SOLID(20PLTS X 700PCS)-L0040576-BFG 9151 COMMON CLAY BRICK-30 06",
            "quantity": "14000.0",
            "unitOfMeasure": "",
            "unitPrice": "0.44",
            "netAmount": "6160.0",
            "taxRate": "0%",
            "tax": "0.0",
            "grossAmount": "6160.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "SOLID(20PLTS X 700PCS)-L0040577-JTH 9729 COMMON CLAY BRICK-30 06",
            "quantity": "14000.0",
            "unitOfMeasure": "",
            "unitPrice": "0.44",
            "netAmount": "6160.0",
            "taxRate": "0%",
            "tax": "0.0",
            "grossAmount": "6160.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "SOLID(20PLTS X 700PCS)-L0040578-JTN 9729 COMMON CLAY BRICK-30 06",
            "quantity": "14000.0",
            "unitOfMeasure": "",
            "unitPrice": "0.44",
            "netAmount": "6160.0",
            "taxRate": "0%",
            "tax": "0.0",
            "grossAmount": "6160.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "SOLID(20PLTS X 700PCS)-L0040579-JFY 7659 COMMON CLAY BRICK-30 06",
            "quantity": "14000.0",
            "unitOfMeasure": "",
            "unitPrice": "0.44",
            "netAmount": "6160.0",
            "taxRate": "0%",
            "tax": "0.0",
            "grossAmount": "6160.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          }
        ],
        "detailOfTaxSummary": [
          {
            "taxCategory": "SST",
            "taxRate": "0%",
            "netTaxableAmount": "43120.0",
            "tax": "0.0"
          }
        ],
        "originalInvoiceReferences": []
      }
    }
  ],
  "traceId": "47d15141fc62ddfb",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 记录 9 — 9. I-GN2507-0123_20250715_215241

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=4f27c0cfb109...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: 17fa3826-e9d5-4415-82e1-a1e93306e467
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "8b3ec13a9cce1f6458ace7b69ff6a5b1",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "I-GN2507-0123_20250715_215241.pdf"
}
```

### 接口返回

HTTP 200 · 21403ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "8b3ec13a9cce1f6458ace7b69ff6a5b1",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Commercial Invoice",
          "invoiceNumber": "I-GN2507-0123",
          "invoiceCode": "",
          "invoiceDate": "2025-07-05",
          "totalNetAmount": "1150.0",
          "totalAmount": "1150.0",
          "totalTaxAmount": "0.0",
          "currency": "MYR",
          "page": [
            "1"
          ]
        },
        "billTo": {
          "billToName": "PP CHIN HIN SDN.BHD - KUALA LUMPUR",
          "billToComposite": "A-1-9,PUSAT PERDAGANGAN KUCHAI, NO.2,JALAN 1/127 OFF JALAN KUCHAI LAMA, 58200 KUALA LUMPUR.",
          "billToCity": "",
          "billToStateOrProvince": "",
          "billToCountry": "",
          "billToCountryCode": "",
          "billToFax": "03-79817575",
          "billToPostalCode": "",
          "billToTelephone": "03-79817878",
          "billToTaxIdentificationNumber": "",
          "billToRecipient": "",
          "billToBankAccount": "",
          "billToBankOfAccount": "",
          "billToEmail": ""
        },
        "billFrom": {
          "billFromName": "KST KEAN SENG TRADING SDN. BHD.",
          "billFromComposite": "Lot 7103, Kampung Sungai Kunyit, 08300 Gurun, Kedah Darul Aman, Malaysia.",
          "billFromCity": "",
          "billFromStateOrProvince": "",
          "billFromCountry": "",
          "billFromCountryCode": "",
          "billFromFax": "",
          "billFromPostalCode": "",
          "billFromTelephone": "+6019-409 4655, +6012-408 8708",
          "billFromTaxIdentificationNumber": "",
          "billFromBusinessRegistrationNumber": "200701018630 (776644-P)",
          "billFromBankAccount": "",
          "billFromBankOfAccount": "",
          "billFromEmail": "kstkeanseng6655@gmail.com"
        },
        "bussiness": {
          "purchaseOrderNumber": "PO K2 516251",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "DO-GN2507-0123",
          "deliveryOrderNumber": ""
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "Net 60 days",
          "dueDate": "2025-09-03",
          "paymentCurrency": "",
          "exchangeRate": "",
          "paidAmount": ""
        }
      },
      "detail": {
        "detailOfGoodsOrServices": [
          {
            "articleID": "",
            "articleName": "",
            "description": "",
            "quantity": "50",
            "unitOfMeasure": "PCS",
            "unitPrice": "23.0",
            "netAmount": "1150.0",
            "taxRate": "",
            "tax": "0.0",
            "grossAmount": "1150.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          }
        ],
        "detailOfTaxSummary": [
          {
            "taxCategory": "SST",
            "taxRate": "0%",
            "netTaxableAmount": "0.0",
            "tax": "0.0"
          }
        ],
        "originalInvoiceReferences": []
      }
    }
  ],
  "traceId": "f689138cc4a5beef",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 记录 10 — 10. I-GN2507-0144_20250715_215342

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=4f27c0cfb109...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: 626a41f7-800f-4c62-a1b1-7d9c8066b592
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "38e8b4c75f03880e8dfdd3e6467187f3",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "I-GN2507-0144_20250715_215342.pdf"
}
```

### 接口返回

HTTP 200 · 20999ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "38e8b4c75f03880e8dfdd3e6467187f3",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Commercial Invoice",
          "invoiceNumber": "I-GN2507-0144",
          "invoiceCode": "",
          "invoiceDate": "2025-07-07",
          "totalNetAmount": "1200.0",
          "totalAmount": "1200.0",
          "totalTaxAmount": "0.0",
          "currency": "MYR",
          "page": [
            "1"
          ]
        },
        "billTo": {
          "billToName": "PP CHIN HIN SDN.BHD - KUALA LUMPUR",
          "billToComposite": "A-1-9,PUSAT PERDAGANGAN KUCHAI, NO.2,JALAN 1/127 OFF JALAN KUCHAI LAMA, 58200 KUALA LUMPUR.",
          "billToCity": "",
          "billToStateOrProvince": "",
          "billToCountry": "",
          "billToCountryCode": "",
          "billToFax": "03-79817575",
          "billToPostalCode": "",
          "billToTelephone": "03-79817878",
          "billToTaxIdentificationNumber": "",
          "billToRecipient": "",
          "billToBankAccount": "",
          "billToBankOfAccount": "",
          "billToEmail": ""
        },
        "billFrom": {
          "billFromName": "KST KEAN SENG TRADING SDN. BHD.",
          "billFromComposite": "Lot 7103, Kampung Sungai Kunyit, 08300 Gurun, Kedah Darul Aman, Malaysia.",
          "billFromCity": "",
          "billFromStateOrProvince": "",
          "billFromCountry": "",
          "billFromCountryCode": "",
          "billFromFax": "",
          "billFromPostalCode": "",
          "billFromTelephone": "+6019-409 4655, +6012-408 8708",
          "billFromTaxIdentificationNumber": "",
          "billFromBusinessRegistrationNumber": "200701018630 (776644-P)",
          "billFromBankAccount": "",
          "billFromBankOfAccount": "",
          "billFromEmail": "kstkeanseng6655@gmail.com"
        },
        "bussiness": {
          "purchaseOrderNumber": "PO K2 516227",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "",
          "deliveryOrderNumber": "DO-GN2507-0144"
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "Net 60 days",
          "dueDate": "2025-09-05",
          "paymentCurrency": "",
          "exchangeRate": "",
          "paidAmount": ""
        }
      },
      "detail": {
        "detailOfGoodsOrServices": [
          {
            "articleID": "",
            "articleName": "",
            "description": "BRC ROLL 65 2MX30M (8 WIRE)",
            "quantity": "10.0",
            "unitOfMeasure": "ROLLS",
            "unitPrice": "120.0",
            "netAmount": "1200.0",
            "taxRate": "",
            "tax": "0.0",
            "grossAmount": "1200.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          }
        ],
        "detailOfTaxSummary": [
          {
            "taxCategory": "SST",
            "taxRate": "",
            "netTaxableAmount": "0.0",
            "tax": "0.0"
          }
        ],
        "originalInvoiceReferences": []
      }
    }
  ],
  "traceId": "c1eb0c16e72d0dc2",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 记录 11 — 11. I-GN2507-0148_20250715_215339

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=4f27c0cfb109...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: 44845c71-e8ff-4520-9085-81b9fcd1a4cd
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "cab09adc65e283f0160a59c02094ea74",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "I-GN2507-0148_20250715_215339.pdf"
}
```

### 接口返回

HTTP 200 · 20230ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "cab09adc65e283f0160a59c02094ea74",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Tax Invoice",
          "invoiceNumber": "I-GN2507-0148",
          "invoiceCode": "",
          "invoiceDate": "2025-07-07",
          "totalNetAmount": "2250.0",
          "totalAmount": "2250.0",
          "totalTaxAmount": "0.0",
          "currency": "MYR",
          "page": [
            "1"
          ]
        },
        "billTo": {
          "billToName": "PP CHIN HIN SDN.BHD - KUALA LUMPUR",
          "billToComposite": "A-1-9,PUSAT PERDAGANGAN KUCHAI, NO.2,JALAN 1/127 OFF JALAN KUCHAI LAMA, 58200 KUALA LUMPUR.",
          "billToCity": "",
          "billToStateOrProvince": "",
          "billToCountry": "",
          "billToCountryCode": "",
          "billToFax": "03-79817575",
          "billToPostalCode": "",
          "billToTelephone": "03-79817878",
          "billToTaxIdentificationNumber": "",
          "billToRecipient": "",
          "billToBankAccount": "",
          "billToBankOfAccount": "",
          "billToEmail": ""
        },
        "billFrom": {
          "billFromName": "KST KEAN SENG TRADING SDN. BHD.",
          "billFromComposite": "Lot 7103, Kampung Sungai Kunyit, 08300 Gurun, Kedah Darul Aman, Malaysia.",
          "billFromCity": "",
          "billFromStateOrProvince": "",
          "billFromCountry": "",
          "billFromCountryCode": "",
          "billFromFax": "",
          "billFromPostalCode": "",
          "billFromTelephone": "+6019-409 4655, +6012-408 8708",
          "billFromTaxIdentificationNumber": "C5884056070",
          "billFromBusinessRegistrationNumber": "200701018630 (776644-P)",
          "billFromBankAccount": "",
          "billFromBankOfAccount": "",
          "billFromEmail": "kstkeanseng6655@gmail.com"
        },
        "bussiness": {
          "purchaseOrderNumber": "PO K2 515996",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "",
          "deliveryOrderNumber": "DO-GN2507-0148"
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "Net 60 days",
          "dueDate": "2025-09-05",
          "paymentCurrency": "",
          "exchangeRate": "",
          "paidAmount": ""
        }
      },
      "detail": {
        "detailOfGoodsOrServices": [
          {
            "articleID": "",
            "articleName": "",
            "description": "",
            "quantity": "100.0",
            "unitOfMeasure": "PCS",
            "unitPrice": "22.5",
            "netAmount": "2250.0",
            "taxRate": "",
            "tax": "0.0",
            "grossAmount": "2250.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          }
        ],
        "detailOfTaxSummary": [
          {
            "taxCategory": "SST",
            "taxRate": "0%",
            "netTaxableAmount": "0.0",
            "tax": "0.0"
          }
        ],
        "originalInvoiceReferences": []
      }
    }
  ],
  "traceId": "11e686d189feb000",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 汇总

| # | 文件 | 票据数 | 发票号 | 日期 | 金额 | 开票方 |
|---|---|---|---|---|---|---|
| 11 | EBM INV2507-296 | 1 | INV2507-296 | 2025-07-02 | 5286.12 | EBM MARKETING SDN BHD |
| 12 | EBM INV2507-297 | 1 | INV2507-297 | 2025-07-10 | 5286.12 | EBM MARKETING SDN BHD |
| 13 | ES248690 | 1 | ES248690 | 2025-07-15 | 2665.0 | ES ENG SOON TRADING SDN BHD |
| 14 | F-SAL60 Invoice | 1 | IV-65266 | 2025-05-31 | 2810.0 | VIVO RAISER SDN. BHD. |
| 15 | HOE GUAN L0013052 (扫描1) | 1 | L0013052 | 2025-06-20 | 10780.0 | HOE GUAN BRICKWORKS SDN BHD |
| 16 | HOE GUAN L0013052 (扫描2) | 1 | L0013052 | 2025-06-20 | 10780.0 | HOE GUAN BRICKWORKS SDN BHD |
| 17 | HOE GUAN L0013084 | 1 | L0018084 ⚠️ | 2025-06-30 | 43120.0 | HOE GUAN BRICK WORKS SDN BHD ⚠️ |
| 18 | I-GN2507-0123 | 1 | I-GN2507-0123 | 2025-07-05 | 1150.0 | KST KEAN SENG TRADING SDN BHD |
| 19 | I-GN2507-0144 | 1 | I-GN2507-0144 | 2025-07-07 | 1200.0 | KST KEAN SENG TRADING SDN BHD |
| 20 | I-GN2507-0148 | 1 | I-GN2507-0148 | 2025-07-07 | 2250.0 | KST KEAN SENG TRADING SDN BHD |

- 票据数 **10/10 正确**（本批全为单页单票）
- 关键字段（docType/号/日期/金额/币种/开票方/收票方）填充率 **70/70 = 100%**

### 抽查核验（对照原件）

| # | 文件 | 核验结论 |
|---|---|---|
| 14 | F-SAL60 Invoice | ✅ **全对**。文件名的 `F-SAL60` 是票尾表单编号（`Doc. No: F-SAL72` 位），非发票号；票面 Invoice No. 确为 `IV-65266`，金额 2,810.00、日期 31-May-2025 均正确 |
| 15 | HOE GUAN L0013052 | ✅ **全对**。`L0013052` / 20-06-2025 / 10,780.00 / 注册号 13984-K 全部匹配 |
| 16 | HOE GUAN L0013052（同发票另一次扫描） | ✅ **全对，且与 #15 结果完全一致** —— 同一发票两次扫描识别结果稳定 |
| 17 | HOE GUAN L0013084 | ⚠️ **2 处字符级瑕疵**（见下） |

### 唯一缺陷：#17 的两处字符误读

| 字段 | 票面实际 | 识别结果 | 说明 |
|---|---|---|---|
| `invoiceNumber` | `L0013084` | `L0018084` | 第 5 位 `3` 误读为 `8` |
| `billFromName` | `HOE GUAN BRICKWORKS SDN BHD` | `HOE GUAN BRICK WORKS SDN BHD` | `BRICKWORKS` 被拆成两词 |

该文件其余字段全部正确：日期 30/06/2025、金额 43,120.00、注册号 13984-K、收票方、明细 7 行。

**归因**：原件为 CamScanner 扫描件，字迹褪色、有污点，属模型 OCR 字符精度问题，**非代码或 prompt 缺陷**。
同一供应商同版式的 #15/#16 全部识别正确，可确认是**单张扫描质量导致的个案**，不是系统性问题。
prompt 层面无法修复此类误读——票面本身就模糊。若业务上要求发票号零误差，
可考虑对该字段加校验规则（如与 PO/DO 号交叉验证）或引入人工复核环节。