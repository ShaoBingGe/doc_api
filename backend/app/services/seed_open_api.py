"""开放平台种子：为 Chinkin（振兴）开通 templateId=7 的马来西亚定制模板。

幂等——重复执行只补缺失部分，不重置已有凭证、不重复建 API。

    cd backend && python -m app.services.seed_open_api

产出：
  1. Tenant「Chinkin」
  2. OpenApiClient  client_id=TN_RACzZVvvVh7MHg2xT（与生产日志一致）
     首次创建时生成 client_secret 并打印**一次**；已存在则不动。
  3. ApiDefinition  从 MY 国家模板初始化，external_template_id=7，
     归属 Chinkin 租户，状态 active（可立即对外调用）。

调用方式（与生产一致）：
    POST /base/oauth/token  {client_id, timestamp, sign=MD5(id+secret+ts)}
    POST /ai/knowledge/nlpService/document/analyze?access_token=...
         multipart: templateId=7, fileHash, file, clientId
"""

from __future__ import annotations

import hashlib
import secrets
import sys
import uuid

from sqlalchemy.orm import Session

from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
from app.models.open_api_client import OpenApiClient
from app.models.user import Tenant

# 与生产日志切片一致的接入标识
CHINKIN_TENANT_NAME = "Chinkin"
CHINKIN_CLIENT_ID = "TN_RACzZVvvVh7MHg2xT"
CHINKIN_TEMPLATE_ID = 7
CHINKIN_COUNTRY = "MY"


def _get_or_create_tenant(db: Session) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.name == CHINKIN_TENANT_NAME).first()
    if tenant:
        return tenant
    tenant = Tenant(id=uuid.uuid4(), name=CHINKIN_TENANT_NAME, is_active=True)
    db.add(tenant)
    db.flush()
    return tenant


def _get_or_create_client(db: Session, tenant: Tenant) -> tuple[OpenApiClient, str | None]:
    """→ (client, 新生成的明文 secret 或 None)。已存在则不重置密钥。"""
    client = (
        db.query(OpenApiClient)
        .filter(OpenApiClient.client_id == CHINKIN_CLIENT_ID)
        .first()
    )
    if client:
        if client.tenant_id != tenant.id:
            client.tenant_id = tenant.id  # 修正归属，不动密钥
        return client, None

    plain_secret = secrets.token_urlsafe(24)
    client = OpenApiClient(
        id=uuid.uuid4(),
        client_id=CHINKIN_CLIENT_ID,
        client_secret=plain_secret,
        client_secret_hash=hashlib.sha256(plain_secret.encode()).hexdigest(),
        name=CHINKIN_TENANT_NAME,
        tenant_id=tenant.id,
        is_active=True,
    )
    db.add(client)
    db.flush()
    return client, plain_secret


def _get_or_create_template_api(db: Session, tenant: Tenant) -> ApiDefinition:
    """建 templateId=7 的 MY 定制 API（已存在则直接返回）。"""
    existing = (
        db.query(ApiDefinition)
        .filter(ApiDefinition.external_template_id == CHINKIN_TEMPLATE_ID)
        .first()
    )
    if existing:
        return existing

    # 复用国家模板初始化（建 v1 + 全部字段模块），再打上开放平台标识
    from app.ocr_optimizer.service import preset_init

    result = preset_init.init_from_country_template(
        db, CHINKIN_COUNTRY, user_id=None, tenant_id=tenant.id,
    )
    api_def = db.query(ApiDefinition).filter(
        ApiDefinition.id == uuid.UUID(result["api_definition_id"])
    ).one()

    api_def.external_template_id = CHINKIN_TEMPLATE_ID
    api_def.name = f"{CHINKIN_TENANT_NAME} MY Invoice (templateId={CHINKIN_TEMPLATE_ID})"
    api_def.description = (
        f"{CHINKIN_TENANT_NAME} 定制的马来西亚票据提取模板，"
        f"对外以 templateId={CHINKIN_TEMPLATE_ID} 调用开放平台接口。"
    )
    # 直接可用：开放平台调用方不走工作区的「待验证 → 发布」流程
    api_def.status = ApiDefinitionStatus.active.value
    db.flush()
    return api_def


def refresh_prompt_from_country_template(db: Session, api_def: ApiDefinition) -> bool:
    """把国家模板的最新内容刷进该 API 的 active 版本。→ 是否实际刷新。

    **仅当该版本 origin=init**（即从未经过客户定制/迭代）时才刷新——客户迭代过的
    版本里含累积的客户反馈，直接覆盖会把这些反馈冲掉。平台升级国家模板后，
    存量的「纯初始化」API 需要这一步才能吃到新规则（composed_prompt 是建 API 时
    的快照，不随 yaml 变动）。
    """
    from app.ocr_optimizer.models import OcrPromptVersion
    from app.ocr_optimizer.service import template_loader
    from app.ocr_optimizer.service.composer import GLOBAL_OUTPUT_CONTRACT_DETAILS

    v = db.query(OcrPromptVersion).filter(
        OcrPromptVersion.id == api_def.prompt_version_id).one_or_none()
    if v is None or v.origin != "init":
        return False

    d = template_loader.decompose_country_template(CHINKIN_COUNTRY)
    new_prompt = d["prompt_format"].rstrip() + "\n\n" + GLOBAL_OUTPUT_CONTRACT_DETAILS + "\n"
    # 只比 prompt 正文会漏掉「只改了 schema description」的模板升级 —— schema 同样要比。
    if new_prompt == v.composed_prompt and d["json_schema"] == v.composed_schema:
        return False

    v.composed_prompt = new_prompt
    v.country_global_text = d["country_global_text"]
    v.composed_schema = d["json_schema"]
    api_def.response_schema = d["json_schema"]
    db.flush()
    return True


def seed(db: Session) -> dict:
    tenant = _get_or_create_tenant(db)
    client, new_secret = _get_or_create_client(db, tenant)
    api_def = _get_or_create_template_api(db, tenant)
    refreshed = refresh_prompt_from_country_template(db, api_def)
    db.commit()
    return {
        "tenant_id": str(tenant.id),
        "tenant_name": tenant.name,
        "client_id": client.client_id,
        "client_secret": new_secret,  # None = 已存在，未重置
        "api_definition_id": str(api_def.id),
        "api_code": api_def.api_code,
        "external_template_id": api_def.external_template_id,
        "status": api_def.status,
        "prompt_refreshed": refreshed,
    }


def main() -> int:
    from app.core.database import SessionLocal, create_tables, ensure_external_template_id_column

    create_tables()
    ensure_external_template_id_column()

    db = SessionLocal()
    try:
        info = seed(db)
    finally:
        db.close()

    print("=" * 68)
    print(f"客户       : {info['tenant_name']}  (tenant_id={info['tenant_id']})")
    print(f"client_id  : {info['client_id']}")
    if info["client_secret"]:
        print(f"client_secret: {info['client_secret']}   ← 仅此一次显示，请妥善保存")
    else:
        print("client_secret: (已存在，未重置)")
    print(f"templateId : {info['external_template_id']}")
    print(f"api_code   : {info['api_code']}   status={info['status']}")
    if info.get("prompt_refreshed"):
        print("prompt     : 已从国家模板刷新（吃到最新切分规则）")
    print("=" * 68)
    print("调用示例：")
    print("  1) POST /base/oauth/token")
    print('     {"client_id":"%s","timestamp":"<unix秒>",' % info["client_id"])
    print('      "sign":"MD5(client_id+client_secret+timestamp)"}')
    print("  2) POST /ai/knowledge/nlpService/document/analyze?access_token=<token>")
    print("     headers: client-platform: common")
    print("     form   : templateId=%s, fileHash=<md5>, file=@doc.pdf, clientId=%s"
          % (info["external_template_id"], info["client_id"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
