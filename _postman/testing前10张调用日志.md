# testing/ 前 10 张 · invoicase.cn 生产调用日志与比对

**工具**：Postman Collection + newman 6（`_postman/testing_top10.postman_collection.json`）  
**环境**：生产 https://invoicase.cn　templateId=7（Chinkin MY）　processor=qwen3-vl-plus  
**结果**：请求 11 个 · 断言 122 项 · 失败 0 项  
**样本**：`testing/` 按文件名排序前 10 张（无人工 GT，逐张对照原件人工核验）

> ⚠️ 断言全过只证明**响应结构**合规，不证明**票据数量与取值**正确——本次即在断言全绿的情况下
> 发现一处漏检（见文末「核验发现」）。

---

## 记录 1 — 1. POST /base/oauth/token

### 请求

```
POST invoicasecn/base/oauth/token
Content-Type: application/json
Accept: */*
Cache-Control: no-cache
Postman-Token: caa9980c-81f8-4426-84c1-e847210922e7
```

**请求体**：

```json
{
  "client_id": "TN_RACzZVvvVh7MHg2xT",
  "timestamp": "1787502443",
  "sign": "4e486081f8c1e2ff2129fc11e544d7ee"
}
```

### 接口返回

HTTP 200 · 156ms

```json
{
  "errcode": "0000",
  "description": "操作成功",
  "access_token": "674ded9d...ab9b",
  "token_type": "bearer",
  "expires_in": 129600
}
```

**断言**：2/2 通过

---

## 记录 2 — 2. DO LX 20250715 635262_20250716_101515

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=674ded9d3280...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: 87b8cb9e-2737-4d5a-9aee-25b81082f3f8
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "96ffad3ed78b8dd77a6ddaa4ace82e96",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "DO LX 20250715 635262_20250716_101515.pdf"
}
```

### 接口返回

HTTP 200 · 25746ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "96ffad3ed78b8dd77a6ddaa4ace82e96",
          "docType": "other",
          "nameOfInvoice": "",
          "invoiceType": "Delivery Order",
          "invoiceNumber": "",
          "invoiceCode": "",
          "invoiceDate": "2025-07-15",
          "totalNetAmount": "",
          "totalAmount": "",
          "totalTaxAmount": "",
          "currency": "MYR",
          "page": [
            "1"
          ]
        },
        "billTo": {
          "billToName": "TAGHILL PROJECTS SDN BHD",
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
          "billFromName": "AMSTEEL MILLS MARKETING S/B",
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
          "purchaseOrderNumber": "5G2PPC",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "W1516140",
          "deliveryOrderNumber": "359869"
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "",
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
            "description": "12.0MM HTD BARS (CTL)",
            "quantity": "0.043",
            "unitOfMeasure": "",
            "unitPrice": "",
            "netAmount": "",
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
            "description": "16.0MM HTD BARS (CTL)",
            "quantity": "1.837",
            "unitOfMeasure": "",
            "unitPrice": "",
            "netAmount": "",
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
            "description": "20.0MM HTD BARS (CTL)",
            "quantity": "0.319",
            "unitOfMeasure": "",
            "unitPrice": "",
            "netAmount": "",
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
            "description": "25.0MM HTD BARS (CTL)",
            "quantity": "3.84",
            "unitOfMeasure": "",
            "unitPrice": "",
            "netAmount": "",
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
            "description": "32.0MM HTD BARS (CTL)",
            "quantity": "7.951",
            "unitOfMeasure": "",
            "unitPrice": "",
            "netAmount": "",
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
            "description": "40.0MM HTD BARS (CTL)",
            "quantity": "0.864",
            "unitOfMeasure": "",
            "unitPrice": "",
            "netAmount": "",
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
  "traceId": "cdd570f8d8728c0d",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 记录 3 — 3. DOC_07_15_25006_20250715_215945

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=674ded9d3280...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: 1127dafa-cf69-4f36-8e1b-c0bf54a124be
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "2e1d781d2140fdd172cf9d077bddc77b",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "DOC_07_15_25006_20250715_215945.pdf"
}
```

### 接口返回

HTTP 200 · 64883ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "2e1d781d2140fdd172cf9d077bddc77b",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Commercial Invoice",
          "invoiceNumber": "0020723",
          "invoiceCode": "",
          "invoiceDate": "2025-07-01",
          "totalNetAmount": "100.0",
          "totalAmount": "110.0",
          "totalTaxAmount": "10.0",
          "currency": "MYR",
          "page": [
            "1"
          ]
        },
        "billTo": {
          "billToName": "PP CHIN HIN SDN BHD",
          "billToComposite": "MENARA CHIN HIN, LEVEL 22-23, 8TH & STELLAR, NO 1, JALAN NAGA EMAS, SRI PETALING, 57000 KL",
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
          "billFromName": "GREENSEAL PRODUCTS (M) SDN BHD",
          "billFromComposite": "NO. 5 & 7 JALAN 35/10A, TAMAN PERINDUSTRIAN IKS, MUKIM BATU 68100 BATU CAVES KUALA LUMPUR",
          "billFromCity": "",
          "billFromStateOrProvince": "",
          "billFromCountry": "",
          "billFromCountryCode": "",
          "billFromFax": "",
          "billFromPostalCode": "",
          "billFromTelephone": "+603-6188 2298",
          "billFromTaxIdentificationNumber": "W10-1808-21032194",
          "billFromBusinessRegistrationNumber": "198601003000",
          "billFromBankAccount": "",
          "billFromBankOfAccount": "",
          "billFromEmail": ""
        },
        "bussiness": {
          "purchaseOrderNumber": "",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "",
          "deliveryOrderNumber": "00019700"
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "",
          "dueDate": "2025-07-15",
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
            "description": "GS 108 -WHITE 5L(GS1080) (GREENSHIELD 108)",
            "quantity": "1.0",
            "unitOfMeasure": "PAIL",
            "unitPrice": "100.0",
            "netAmount": "100.0",
            "taxRate": "",
            "tax": "10.0",
            "grossAmount": "110.0",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          }
        ],
        "detailOfTaxSummary": [
          {
            "taxCategory": "SST",
            "taxRate": "10%",
            "netTaxableAmount": "100.0",
            "tax": "10.0"
          }
        ],
        "originalInvoiceReferences": []
      }
    }
  ],
  "traceId": "4ea8da5a73b4137a",
  "docPages": 6
}
```

**断言**：12/12 通过

---

## 记录 4 — 4. DOC_07_15_25008_20250715_215948

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=674ded9d3280...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: 186338ee-e4b0-469a-bb02-b08310a2eab6
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "c18d865e71cf95eaf57cba15af898e2f",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "DOC_07_15_25008_20250715_215948.pdf"
}
```

### 接口返回

HTTP 200 · 16192ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "c18d865e71cf95eaf57cba15af898e2f",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Commercial Invoice",
          "invoiceNumber": "0020723",
          "invoiceCode": "",
          "invoiceDate": "2025-07-01",
          "totalNetAmount": "100.0",
          "totalAmount": "110.0",
          "totalTaxAmount": "10.0",
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
          "billFromName": "GREENSEAL PRODUCTS (M) SDN BHD",
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
          "purchaseOrderNumber": "",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "",
          "deliveryOrderNumber": "00019700"
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "",
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
            "description": "GS 108 -WHITE 5L(GS1080)\n(GREENSHIELD 108)",
            "quantity": "1.0",
            "unitOfMeasure": "",
            "unitPrice": "100.0",
            "netAmount": "100.0",
            "taxRate": "",
            "tax": "10.0",
            "grossAmount": "110.0",
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
            "netTaxableAmount": "100.0",
            "tax": "10.0"
          }
        ],
        "originalInvoiceReferences": []
      }
    }
  ],
  "traceId": "804971a13620dc81",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 记录 5 — 5. DOKA 492090615_20260401_170124

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=674ded9d3280...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: a60eefdb-f257-44c2-b7a3-9d10307af069
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "35fafe0014a7ac22747ac9998b0062df",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "DOKA 492090615_20260401_170124.pdf"
}
```

### 接口返回

HTTP 200 · 114533ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "35fafe0014a7ac22747ac9998b0062df",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Commercial Invoice",
          "invoiceNumber": "492090615",
          "invoiceCode": "",
          "invoiceDate": "2026-03-30",
          "totalNetAmount": "31649.25",
          "totalAmount": "33548.21",
          "totalTaxAmount": "1898.96",
          "currency": "MYR",
          "page": [
            "1",
            "2",
            "3",
            "4",
            "5"
          ]
        },
        "billTo": {
          "billToName": "PP CHIN HIN SDN. BHD.",
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
          "billFromName": "Doka Formwork Malaysia Sdn Bhd",
          "billFromComposite": "",
          "billFromCity": "",
          "billFromStateOrProvince": "",
          "billFromCountry": "",
          "billFromCountryCode": "",
          "billFromFax": "",
          "billFromPostalCode": "",
          "billFromTelephone": "",
          "billFromTaxIdentificationNumber": "C21753455040",
          "billFromBusinessRegistrationNumber": "201101043119",
          "billFromBankAccount": "",
          "billFromBankOfAccount": "",
          "billFromEmail": ""
        },
        "bussiness": {
          "purchaseOrderNumber": "492001079",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "",
          "deliveryOrderNumber": ""
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "60 days net",
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
            "description": "Standard 1.50m crimped spigot",
            "quantity": "164.0",
            "unitOfMeasure": "",
            "unitPrice": "164.0",
            "netAmount": "243.55",
            "taxRate": "",
            "tax": "14.613",
            "grossAmount": "258.163",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "Standard 1.50m crimped spigot",
            "quantity": "161.0",
            "unitOfMeasure": "",
            "unitPrice": "161.0",
            "netAmount": "243.55",
            "taxRate": "",
            "tax": "14.613",
            "grossAmount": "258.163",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "Standard 1.50m crimped spigot",
            "quantity": "52.0",
            "unitOfMeasure": "",
            "unitPrice": "52.0",
            "netAmount": "243.55",
            "taxRate": "",
            "tax": "14.613",
            "grossAmount": "258.163",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "Standard 2.00m crimped spigot",
            "quantity": "376.0",
            "unitOfMeasure": "",
            "unitPrice": "376.0",
            "netAmount": "293.17",
            "taxRate": "",
            "tax": "17.5902",
            "grossAmount": "310.7602",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "Standard 2.00m crimped spigot",
            "quantity": "160.0",
            "unitOfMeasure": "",
            "unitPrice": "160.0",
            "netAmount": "293.17",
            "taxRate": "",
            "tax": "17.5902",
            "grossAmount": "310.7602",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "Standard 2.00m crimped spigot",
            "quantity": "560.0",
            "unitOfMeasure": "",
            "unitPrice": "560.0",
            "netAmount": "293.17",
            "taxRate": "",
            "tax": "17.5902",
            "grossAmount": "310.7602",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "Standard 1.50m hanging spigot",
            "quantity": "423.0",
            "unitOfMeasure": "",
            "unitPrice": "423.0",
            "netAmount": "192.3",
            "taxRate": "",
            "tax": "11.538",
            "grossAmount": "203.838",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "Standard 1.50m hanging spigot",
            "quantity": "200.0",
            "unitOfMeasure": "",
            "unitPrice": "200.0",
            "netAmount": "192.3",
            "taxRate": "",
            "tax": "11.538",
            "grossAmount": "203.838",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "Standard 2.00m hanging spigot",
            "quantity": "196.0",
            "unitOfMeasure": "",
            "unitPrice": "196.0",
            "netAmount": "273.55",
            "taxRate": "",
            "tax": "16.413",
            "grossAmount": "289.963",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "Standard 2.00m hanging spigot",
            "quantity": "450.0",
            "unitOfMeasure": "",
            "unitPrice": "450.0",
            "netAmount": "106.95",
            "taxRate": "",
            "tax": "6.417",
            "grossAmount": "113.367",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "Ledger 0.73m",
            "quantity": "649.0",
            "unitOfMeasure": "",
            "unitPrice": "649.0",
            "netAmount": "106.95",
            "taxRate": "",
            "tax": "6.417",
            "grossAmount": "113.367",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "Ledger 0.73m",
            "quantity": "162.0",
            "unitOfMeasure": "",
            "unitPrice": "162.0",
            "netAmount": "106.95",
            "taxRate": "",
            "tax": "6.417",
            "grossAmount": "113.367",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "Ledger 0.73m",
            "quantity": "253.0",
            "unitOfMeasure": "",
            "unitPrice": "253.0",
            "netAmount": "106.95",
            "taxRate": "",
            "tax": "6.417",
            "grossAmount": "113.367",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
     
  … （截断）
```

**断言**：12/12 通过

---

## 记录 6 — 6. DOKA 492090616_20260401_170125

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=674ded9d3280...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: dcfad508-62ff-4ed7-82ff-36639d246db8
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "6e9396c9c67ea87408f0dc1e14224f8c",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "DOKA 492090616_20260401_170125.pdf"
}
```

### 接口返回

HTTP 200 · 18813ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "6e9396c9c67ea87408f0dc1e14224f8c",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Commercial Invoice",
          "invoiceNumber": "492090616",
          "invoiceCode": "",
          "invoiceDate": "2026-03-30",
          "totalNetAmount": "3116.57",
          "totalAmount": "3303.56",
          "totalTaxAmount": "186.99",
          "currency": "MYR",
          "page": [
            "1",
            "2",
            "3"
          ]
        },
        "billTo": {
          "billToName": "PP CHIN HIN SDN. BHD.",
          "billToComposite": "No A-1-9 Pusat Perdagangan Kuchai, No 2, Jln 1/127, Jln Kuchai Lama 58200 Kuala Lumpur Wilayah Persekutuan Kuala Lumpur, MALAYSIA",
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
          "billFromName": "Doka Formwork Malaysia Sdn Bhd",
          "billFromComposite": "Lot 6, Jalan Teknologi Taman Sains Selangor 1 Kota Damansara 47810 Petaling Jaya Selangor, MALAYSIA",
          "billFromCity": "",
          "billFromStateOrProvince": "",
          "billFromCountry": "",
          "billFromCountryCode": "",
          "billFromFax": "",
          "billFromPostalCode": "",
          "billFromTelephone": "",
          "billFromTaxIdentificationNumber": "C21753455040",
          "billFromBusinessRegistrationNumber": "201101043119",
          "billFromBankAccount": "",
          "billFromBankOfAccount": "",
          "billFromEmail": ""
        },
        "bussiness": {
          "purchaseOrderNumber": "492001079",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "",
          "deliveryOrderNumber": ""
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "60 days net",
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
            "description": "Scaffold storage rack 1.12x1.12m",
            "quantity": "90.0",
            "unitOfMeasure": "",
            "unitPrice": "1386.04",
            "netAmount": "124743.6",
            "taxRate": "",
            "tax": "7484.62",
            "grossAmount": "132228.22",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          },
          {
            "articleID": "",
            "articleName": "",
            "description": "Doka multi-trip transport box 1.20x0.80m",
            "quantity": "13.0",
            "unitOfMeasure": "",
            "unitPrice": "2391.1",
            "netAmount": "31084.3",
            "taxRate": "",
            "tax": "1865.06",
            "grossAmount": "32949.36",
            "orderNumber": "",
            "salesOrderNumber": "",
            "deliveryOrderNumber": "",
            "purchaseOrderNumber": ""
          }
        ],
        "detailOfTaxSummary": [
          {
            "taxCategory": "SST",
            "taxRate": "6%",
            "netTaxableAmount": "3116.57",
            "tax": "186.99"
          }
        ],
        "originalInvoiceReferences": []
      }
    }
  ],
  "traceId": "2bf8f8e84986434b",
  "docPages": 3
}
```

**断言**：12/12 通过

---

## 记录 7 — 7. E Invoice IV-65034_20260330_102941

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=674ded9d3280...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: 00be6d18-c998-4cea-bbc7-dc923ac0ca49
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "d5d757ed5b9c88796a9c7a0514521cba",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "E Invoice IV-65034_20260330_102941.pdf"
}
```

### 接口返回

HTTP 200 · 22321ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "d5d757ed5b9c88796a9c7a0514521cba",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Commercial Invoice",
          "invoiceNumber": "IV-65034",
          "invoiceCode": "",
          "invoiceDate": "2026-03-26",
          "totalNetAmount": "71149.93",
          "totalAmount": "71149.93",
          "totalTaxAmount": "0.0",
          "currency": "MYR",
          "page": [
            "1"
          ]
        },
        "billTo": {
          "billToName": "PP CHIN HIN SDN BHD(PERAK)",
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
          "billFromName": "SINTARI SDN BHD",
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
          "purchaseOrderNumber": "A1 543824",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "",
          "deliveryOrderNumber": "DO-65017"
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "14 DAYS",
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
            "description": "MSR BARS 6MM X 12M (375PCS) (3 BDLS)",
            "quantity": "2.997",
            "unitOfMeasure": "",
            "unitPrice": "2390.0",
            "netAmount": "7162.83",
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
            "description": "HTD BARS 10MM X 12M (138PCS) (5 BDLS)",
            "quantity": "5.11",
            "unitOfMeasure": "",
            "unitPrice": "2490.0",
            "netAmount": "12723.9",
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
            "description": "HTD BARS 12MM X 12M (96PCS) (8 BDLS)",
            "quantity": "8.184",
            "unitOfMeasure": "",
            "unitPrice": "2340.0",
            "netAmount": "19150.56",
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
            "description": "HTD BARS 16MM X 12M (54PCS) (14 BDLS)",
            "quantity": "14.336",
            "unitOfMeasure": "",
            "unitPrice": "2240.0",
            "netAmount": "32112.64",
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
  "traceId": "841ad2857aee2e48",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 记录 8 — 8. EBM INV2506-287-1_20250715_103330

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=674ded9d3280...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: 67e7ea30-b25e-468a-8c59-4cfe4118f383
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "55c1b7c0880542d389242118a8e9c613",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "EBM INV2506-287-1_20250715_103330.pdf"
}
```

### 接口返回

HTTP 200 · 22117ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "55c1b7c0880542d389242118a8e9c613",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Commercial Invoice",
          "invoiceNumber": "INV2506-287",
          "invoiceCode": "",
          "invoiceDate": "2025-06-24",
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
          "purchaseOrderNumber": "W1 514031",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "",
          "deliveryOrderNumber": "DO251575"
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
            "description": "SAGAPAVE 80MM - STD RED",
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
  "traceId": "016ddcaf492e432b",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 记录 9 — 9. EBM INV2507-293_20250715_103430

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=674ded9d3280...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: f8781d94-fbf1-49d7-8ae2-c5b7e2f66f36
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "e9401a8efd90cbc3321cf13415b4eb71",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "EBM INV2507-293_20250715_103430.pdf"
}
```

### 接口返回

HTTP 200 · 19784ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "e9401a8efd90cbc3321cf13415b4eb71",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Commercial Invoice",
          "invoiceNumber": "INV2507-293",
          "invoiceCode": "",
          "invoiceDate": "2025-07-03",
          "totalNetAmount": "",
          "totalAmount": "5286.12",
          "totalTaxAmount": "",
          "currency": "MYR",
          "page": [
            "1"
          ]
        },
        "billTo": {
          "billToName": "PP CHIN HIN SDN BHD",
          "billToComposite": "A-1-9, PUSAT PERDAGANGAN KUCHAI\nNO.2, JALAN 1/127 OFF JALAN KUCHAI LAMA\n58200 KUALA LUMPUR",
          "billToCity": "",
          "billToStateOrProvince": "",
          "billToCountry": "",
          "billToCountryCode": "",
          "billToFax": "",
          "billToPostalCode": "",
          "billToTelephone": "03-7981 7878",
          "billToTaxIdentificationNumber": "",
          "billToRecipient": "",
          "billToBankAccount": "",
          "billToBankOfAccount": "",
          "billToEmail": ""
        },
        "billFrom": {
          "billFromName": "EBM MARKETING SDN BHD",
          "billFromComposite": "23-1, PLAZA WANGSA MAJU, JALAN MAJU RIA 2, SEKSYEN 10, 53300 KUALA LUMPUR.",
          "billFromCity": "",
          "billFromStateOrProvince": "",
          "billFromCountry": "",
          "billFromCountryCode": "",
          "billFromFax": "",
          "billFromPostalCode": "",
          "billFromTelephone": "012-359 7922",
          "billFromTaxIdentificationNumber": "",
          "billFromBusinessRegistrationNumber": "201801036457 (1298487-V)",
          "billFromBankAccount": "",
          "billFromBankOfAccount": "",
          "billFromEmail": "ebmmsb18@gmail.com"
        },
        "bussiness": {
          "purchaseOrderNumber": "W1 515443",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "",
          "deliveryOrderNumber": "DO251653"
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
            "description": "SAGAPAVER 80MM - STD RED",
            "quantity": "121.52",
            "unitOfMeasure": "",
            "unitPrice": "43.5",
            "netAmount": "5286.12",
            "taxRate": "",
            "tax": "",
            "grossAmount": "5286.12",
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
            "unitPrice": "0",
            "netAmount": "0",
            "taxRate": "",
            "tax": "",
            "grossAmount": "0",
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
  "traceId": "c96759cd37572f80",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 记录 10 — 10. EBM INV2507-294_20250715_103433

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=674ded9d3280...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: 6415c44a-6360-4265-8203-9dd3e9200b69
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "42ea718713f362f6c2f565b1c762c232",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "EBM INV2507-294_20250715_103433.pdf"
}
```

### 接口返回

HTTP 200 · 18515ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "42ea718713f362f6c2f565b1c762c232",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Commercial Invoice",
          "invoiceNumber": "INV2507-294",
          "invoiceCode": "",
          "invoiceDate": "2025-07-03",
          "totalNetAmount": "",
          "totalAmount": "5286.12",
          "totalTaxAmount": "",
          "currency": "MYR",
          "page": [
            "1"
          ]
        },
        "billTo": {
          "billToName": "PP CHIN HIN SDN BHD",
          "billToComposite": "A-1-9, PUSAT PERDAGANGAN KUCHAI\nNO.2, JALAN 1/127 OFF JALAN KUCHAI LAMA\n58200 KUALA LUMPUR",
          "billToCity": "",
          "billToStateOrProvince": "",
          "billToCountry": "",
          "billToCountryCode": "",
          "billToFax": "03-7981 7575",
          "billToPostalCode": "",
          "billToTelephone": "03-7981 7878",
          "billToTaxIdentificationNumber": "",
          "billToRecipient": "",
          "billToBankAccount": "",
          "billToBankOfAccount": "",
          "billToEmail": ""
        },
        "billFrom": {
          "billFromName": "EBM MARKETING SDN BHD",
          "billFromComposite": "23-1, PLAZA WANGSA MAJU, JALAN MAJU RIA 2, SEKSYEN 10, 53300 KUALA LUMPUR",
          "billFromCity": "",
          "billFromStateOrProvince": "",
          "billFromCountry": "",
          "billFromCountryCode": "",
          "billFromFax": "",
          "billFromPostalCode": "",
          "billFromTelephone": "012-359 7922",
          "billFromTaxIdentificationNumber": "",
          "billFromBusinessRegistrationNumber": "201801036457 (1298487-V)",
          "billFromBankAccount": "",
          "billFromBankOfAccount": "",
          "billFromEmail": "ebmmsb18@gmail.com"
        },
        "bussiness": {
          "purchaseOrderNumber": "W1 515443",
          "contractNumber": "",
          "startDate": "",
          "endDate": "",
          "salesOrderNumber": "",
          "deliveryOrderNumber": "DO251652"
        },
        "payment": {
          "paymentMethod": "",
          "paymentStatus": "",
          "paymentTerms": "",
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
            "grossAmount": "5286.12",
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
            "grossAmount": "0.0",
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
  "traceId": "ab3253a9aad33d06",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 记录 11 — 11. EBM INV2507-295_20250715_103432

### 请求

```
POST invoicasecn/ai/knowledge/nlpService/document/analyze?access_token=674ded9d3280...
client-platform: common
Accept: */*
Cache-Control: no-cache
Postman-Token: 88028bc8-45a9-43a4-a543-fdfaec05d23c
```

**请求参数（multipart/form-data）**：

```json
{
  "templateId": "7",
  "fileHash": "bf3729c21311e659776108ca0d53c986",
  "clientId": "TN_RACzZVvvVh7MHg2xT",
  "file": "EBM INV2507-295_20250715_103432.pdf"
}
```

### 接口返回

HTTP 200 · 17755ms

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [
    {
      "header": {
        "basic": {
          "sourceFileHash": "bf3729c21311e659776108ca0d53c986",
          "docType": "invoice",
          "nameOfInvoice": "",
          "invoiceType": "Commercial Invoice",
          "invoiceNumber": "INV2507-295",
          "invoiceCode": "",
          "invoiceDate": "2025-07-03",
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
          "deliveryOrderNumber": "DO251651"
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
  "traceId": "65ad804423e0976c",
  "docPages": 1
}
```

**断言**：12/12 通过

---

## 核验发现

逐张对照原件人工核验（断言之外的实质检查）：

| # | 文件 | 页 | 返回票据数 | 实际票据数 | 核验结论 |
|---|---|---|---|---|---|
| 1 | DO LX 635262 | 1 | 1 | 1 | ✅ 正确。送货单（DELIVERY ORDER, FOR REFERENCE PURPOSE），票面无发票号与金额，返回空值属实；docType 正确判为 other |
| 2 | DOC_07_15_25006 | 6 | **1** | **6** | ❌ **漏检 5 张**。6 页是 6 张独立 INVOICE（0020723/0150725/0310725/0490725/0500725/0540725） |
| 3 | DOC_07_15_25008 | 1 | 1 | 1 | ✅ 正确 |
| 4 | DOKA 492090615 | 5 | 1 | 1 | ✅ 正确。单张跨 5 页发票（Page 1/5～5/5，后 4 页为 Attachment），金额 33548.21 正确 |
| 5 | DOKA 492090616 | 3 | 1 | 1 | ✅ 正确（同上，跨页单张） |
| 6 | E Invoice IV-65034 | 1 | 1 | 1 | ✅ 正确 |
| 7-10 | EBM INV2506-287-1 / 2507-293/294/295 | 1 | 1 | 1 | ✅ 正确。四张金额同为 5286.12 属实（同批 SAGAPAVER 货），非串号 |

### 漏检根因（已用实验定位）

把 `DOC_07_15_25006` 拆成 6 个单页 PDF 分别调同一接口：

| 页 | 票面真值（号/额） | 单页识别 | 结果 |
|---|---|---|---|
| 1 | 0020723 / 110.0 | 0020723 / 110.0 | ✅ |
| 2 | 0150725 / 7260.0 | 0150725 / 7260.0 | ✅ |
| 3 | 0310725 / 1931.6 | 0310725 / 1931.6 | ✅ |
| 4 | 0490725 / 8855.0 | 0490725 / 8855.0 | ✅ |
| 5 | 0500725 / 2599.8 | 0500725 / 2599.8 | ✅ |
| 6 | 0540725 / 1062.6 | 0540725 / 1062.6 | ✅ |

**单页逐张调用 6/6 全对，整份一次调用只返回 1 张** → 模型有识别能力，问题在两层：

1. **`QwenProcessor._MAX_PAGES = 5` 硬截断**（`backend/app/processors/qwen_processor.py:40`）——
   6 页文档只送前 5 页，第 6 张发票根本没进模型视野。
2. **MY 国家模板对多票据的指令过弱**——`MY_invoice_prompt.yaml` 全文仅一句「文档可能包含多张票据、
   跨页票据、印刷模糊等情况，请尽可能准确识别」。返回的 `page: ["1"]` 表明模型只认了第 1 页。
   对比日本模板 v8 有 9 条实体切分规则 + 7 条计数防幻觉规则。

两项都属**平台级资产**（影响所有 MY 客户与全部调用的成本/延迟），按 CLAUDE.md 红线需走 PR +
黄金集 A/B 验证，故本次仅报告不擅改。