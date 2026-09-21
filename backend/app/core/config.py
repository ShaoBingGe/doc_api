"""
Application configuration via Pydantic BaseSettings.

读取顺序: 环境变量 > .env 文件 > 默认值
原型阶段默认 SQLite + 本地文件存储 + 同步任务运行。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────────
    APP_NAME: str = "ApiAnything"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    # `app.*` 命名空间的日志级别。第三方库仍留在 WARNING（见 main._configure_logging）。
    # 置 WARNING 可关掉业务 INFO；DEBUG 会把 prompt/响应长度等细节也打出来。
    LOG_LEVEL: str = "INFO"

    # ── Database ──────────────────────────────────────────────────────────
    # 原型：SQLite；生产：postgresql+asyncpg://...
    DATABASE_URL: str = "sqlite:///./data/apianything.db"
    # 连接池。默认 5+10 在开放平台并发下会被抽干（2026-08-26 线上事故：
    # 取 token / 轮询 / 识别落库集体阻塞 30s 后抛 QueuePool timeout）。
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 30

    # ── MyInvois（马来西亚税局）查验增强 ─────────────────────────────────
    # 路径：PDF 二维码 → uuid → 网关 verify（返回税局原始 UBL）→ 与 OCR 消融。
    # 票面无二维码则直接走纯 OCR。整链 fail-open：查验不通只影响"是否校正"，
    # 不影响识别本身（§五 降级而非 500）。
    MYINVOIS_ENABLED: bool = False
    MYINVOIS_TIMEOUT_S: float = 15.0
    # 单份文档最多查验几个二维码（多票据 PDF 实测可达 12 张）
    MYINVOIS_MAX_LOOKUPS: int = 12
    # 查验总时间预算；超出的二维码标 skipped，不拖垮单次识别延迟
    MYINVOIS_BUDGET_S: float = 30.0
    # True = 仅 verifyStatus=VALID 才覆盖 OCR；CANCELLED/INVALID 只上报不覆盖
    MYINVOIS_REQUIRE_VALID: bool = True
    # 查验与 OCR 并行后，OCR 先返回时**还愿意为查验多等多久**（宽限期）。
    # 并行只能把代价压到 max(OCR, 查验)，压不住"查验比 OCR 还慢"的情况：
    # SIT 实测网关 verify 是同步转调税局，延迟偶发飙到 10s+（正常 0.3–0.9s）。
    # 超过宽限期就放弃本次增强、直接返回 OCR 结果 —— 增强是尽力而为，
    # 不该让一次慢查验拖垮接口响应。0 = 不等（OCR 一好就返回）。
    MYINVOIS_JOIN_GRACE_S: float = 8.0
    # 二维码扫描段的刹车（与 verify 循环的 MAX_LOOKUPS / BUDGET_S 相互独立）。
    # 扫描是纯 CPU 的整页渲染，跑在线程池里且无法被 cancel —— 没有上限时
    # 一份上百页 PDF 会占死线程数十秒，让后续查验全卡在 join 宽限期。
    MYINVOIS_SCAN_MAX_PAGES: int = 16        # 与 OCR 实际读取页数对齐
    MYINVOIS_SCAN_BUDGET_S: float = 12.0
    MYINVOIS_GATEWAY_BASE_URL: str = ""      # 如 http://api-sit.piaozone.com/gjfp
    MYINVOIS_GATEWAY_CLIENT_ID: str = ""
    MYINVOIS_GATEWAY_CLIENT_SECRET: str = ""
    MYINVOIS_GATEWAY_COMPANY_ID: str = ""    # 企业 ouno

    # ── File Storage ──────────────────────────────────────────────────────
    STORAGE_BACKEND: str = "local"          # local | s3
    UPLOAD_DIR: str = "./data/uploads"      # LocalStorage 存储目录
    MAX_UPLOAD_SIZE_MB: int = 20

    # S3（仅 STORAGE_BACKEND=s3 时生效）
    S3_BUCKET: str = ""
    S3_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""

    # ── Task Runner ───────────────────────────────────────────────────────
    TASK_RUNNER: str = "sync"               # sync | celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"

    # ── AI Processors ─────────────────────────────────────────────────────
    DEFAULT_PROCESSOR: str = "mock"         # mock | gemini | openai
    # ── 主力 / 备份模型（两台服务器配置一致，见 factory.resolve_chain）────────
    # 主力优先；主力**不可用**（没配 key / 全部账号熔断）或**严重满负荷**时
    # 走备份。两边同一份配置：新加坡机器有 gemini key → 走 gemini；
    # 大陆机器没有（Gemini 网络不可达）→ 主力天然不可用，直接走 qwen，
    # 不产生任何一次注定超时的 gemini 尝试。
    PRIMARY_PROCESSOR: str = "gemini"
    BACKUP_PROCESSOR: str = "qwen"
    # 「严重满负荷」阈值：主力池里**每个**健康账号的在途都 ≥ 该值时切备份。
    # 用 min 而非总量——只要还有账号相对空闲就该继续用主力。
    # 0 = 关闭饱和切换（只在主力全部熔断时才切）。
    PRIMARY_SATURATION_PER_KEY: int = 4
    # 主力调用失败后是否用备份重试同一份文档。只对 **provider 级** 故障生效
    # （配额耗尽 / 网络不可达 / 鉴权失败）；文档级错误（400 坏文件）不重试——
    # 换个模型同样会失败，只是把一次失败变成两次、延迟翻倍。
    ENABLE_BACKUP_FAILOVER: bool = True
    # ── 单次模型调用硬超时（2026-08-31 事故）────────────────────────────────
    # genai.Client 默认**无 HTTP 超时**：Gemini 上游 503（Deadline expired）
    # 期间实测一条同步请求在服务端静默挂起 8+ 分钟、无任何处理器日志，直到
    # Caddy 600s read_timeout 先行掐断客户端（502）。超时缺失还意味着异步
    # worker 线程可能被一次调用永久占死。这里给两个 provider 各设硬上限：
    # 超时抛出的异常含 "timeout" 字样 → is_provider_failure 命中 → 立即切备份。
    #   gemini：单页实测 5–27s、25 页大文档实测 ~209s → 300s 上限留足余量；
    #   qwen  ：qwen3-vl-plus 实测 ~34s/页、16 页单次调用可达 ~9 分钟 → 600s。
    GEMINI_HTTP_TIMEOUT_S: float = 300.0
    QWEN_HTTP_TIMEOUT_S: float = 600.0
    # ── schema 硬约束开关（docs/plan-schema-not-passed-to-model.md）──────────
    # True 时提取链路（开放平台 / 工作区 / 迭代评测）把 active 版本的
    # composed_schema 作为 response_schema 随调用传给模型（Gemini 硬约束输出
    # 形状；qwen 忽略）。历史缺陷：prompt 存在时 schema 从未到达模型，所有国家
    # 模板的 schema description 在生产里是死字。
    #
    # 2026-08-28 默认值 False → True，依据两份实测报告：
    #   - docs/experiment-jp-schema-ab.md：JP 模板（规则几乎全在 description）
    #     字段准确率 dev +187%、held-out test +100%，9/9 样本全升；
    #     且不传时会吐出违反 schema 的非法类型（page 输出成 int）。
    #   - MY 模板：Phase 2 三样本 18/18 开=关、Phase 3 黄金集 4 seed 开≥关。
    # 成本：输入 token **零增加**（response_schema 不计入 prompt token），
    # 输出 token 与耗时上升，来自「真的多提取了字段」。
    # 回退：置 False 即逐字节回到旧行为（开关本身保留，见 Phase 4 runbook）。
    PASS_SCHEMA_TO_MODEL: bool = True
    GEMINI_API_KEY: str = ""
    # 多账号 key 池（分号/逗号分隔）。配了它就覆盖 GEMINI_API_KEY；
    # 调度用最少在途 + 429 熔断（processors/key_pool.py），加账号只改这一行。
    GEMINI_API_KEYS: str = ""
    # Default Gemini model used when an ApiDefinition's model_name is null
    # (production OCR) and as the first chain step's model when
    # LLM_FALLBACK_CHAIN isn't configured. Sensible Q2-2026 default.
    GEMINI_MODEL: str = "gemini-2.5-flash"
    # Gemini 3.x 思考档：low / high。票据抽取以照抄票面为主，low 足够且更快更省；
    # 对 2.x 模型无效（那一代用 thinking_budget）。
    GEMINI_THINKING_LEVEL: str = "low"
    OPENAI_API_KEY: str = ""
    # 阿里云百炼 / DashScope（qwen-vl-ocr 视觉 OCR + qwen-plus 文本），OpenAI 兼容端点。
    # 大陆云实例用它替代不可达的 Gemini。
    QWEN_API_KEY: str = ""
    # qwen 多账号 key 池（分号/逗号分隔），与 GEMINI_API_KEYS 同一套调度。
    QWEN_API_KEYS: str = ""
    QWEN_MODEL: str = "qwen3.8-max"          # 生产 OCR（视觉）；默认用新一代高能力多模态
    QWEN_TEXT_MODEL: str = "qwen-plus"       # 反思/优化（文本）
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # Failover chain for reflection / optimizer LLM calls. Semicolon-separated
    # `provider|model` entries; first one that succeeds wins. The chain ONLY
    # applies to text-completion calls (reflection, module_optimizer,
    # meta_optimizer), NOT to the production OCR call (which uses the
    # ApiDefinition's configured processor).
    # Example: "gemini|gemini-2.5-flash;openai|gpt-4o-mini;mock|"
    LLM_FALLBACK_CHAIN: str = ""

    # ── Skill optimization (ADR-001) ─────────────────────────────────────────
    # Held-out validation gate for iteration rounds: optimize on a TRAIN split,
    # score/select versions on a held-out VAL split (the noise samples) so a
    # round's "improvement" must generalize, not overfit the same samples.
    # Default OFF — when off, run_orchestrator behaves byte-identically.
    SKILL_HELDOUT_GATE: bool = False
    # Fraction of samples reserved as the held-out val split (the trailing
    # samples — anchors stay in train). Min 1 val sample.
    SKILL_HELDOUT_VAL_FRAC: float = 0.25
    # Pre-iteration noise-sample gate: when ON, starting an iteration requires
    # 3 anchors + N noise = 12 confirmed samples (so the held-out val split is
    # meaningful). Default OFF → existing MIN_SAMPLES (3) behavior unchanged.
    SKILL_NOISE_GATE: bool = False
    # Edit discipline for the optimize step: only optimize SYSTEMATIC-error
    # modules (defect, not one-off lapse) and clip to the top-L by severity
    # (learning-rate). Default OFF → all under-target modules optimized.
    SKILL_EDIT_DISCIPLINE: bool = False
    # Render attached OcrSkill content into composed prompts (P2).
    # 2026-07-07 默认开启：无挂载时 resolve()→{} 与 OFF 逐字节等价（composer
    # `(skill_content or {})`），仅管理员显式「挂到字段」后产生增量——挂载本就
    # 期望生效，OFF 反而让 attach 成静默空操作（晋升→挂载→生效闭环断点）。
    # 回退：置 False 即不渲染（skill_ids 仅存储）。
    SKILL_LIBRARY_RENDER: bool = True
    # P3 slow-update: at compose time, render a version-level PROTECTED guardian
    # block (deterministic, from each field's cross-round accuracy trajectory) so
    # stable fields are pinned and volatile ones flagged. Not stored in any
    # module body → step edits can't touch it. Default OFF → composer unchanged.
    SKILL_SLOW_UPDATE: bool = False
    # P3 meta-memory: accumulate accepted/rejected edit-op stats into run.metrics
    # and (when ON) surface a proposal-bias hint to the optimizer.
    # 2026-07-07 默认开启（随 SKILL_TYPED_EDITS 一起灰度通过）：L1.3 二号灰度
    # 实测 27 次 hint 注入、hint 文本有意义（"replace 拒绝率 67% → 优先 append"）、
    # 准确率单调不降。fail-safe：无 typed 记忆时 hint 为空、零行为变化。
    SKILL_META_MEMORY: bool = True
    # ADR-002: optimizer emits TYPED bounded edits (append/replace/delete on a
    # per-field rule section) instead of wholesale-rewriting ocr_prompt. Enables
    # aggregate/clip/buffer/classify/meta wiring. The ocr_prompt body is a HARD
    # frozen invariant when ON (typed mode without edits skips the field, never
    # falls back to wholesale rewrite — L1.3 fix f6907ea).
    # 2026-07-07 默认开启（ADR-002 §9.3 达成）：两次真实灰度通过——
    # L1（my-invoice-abf4f0 · gemini）+ L1.3（my-invoice-11ec4a-c1 · qwen），
    # 两 provider 五指标全过（正文逐字节冻结 PASS、准确率单调、verifier
    # unavailable=0、edit_ops buffer 过滤生效）。回退：置 False 即字节级等价
    # 回旧整段重写路径（规则段为加性载体，关闭即不渲染）。
    SKILL_TYPED_EDITS: bool = True

    # ── 并发准入闸（单体服务的唯一咽喉）──────────────────────────────────────
    # 同步端点与异步 worker 共用一个闸，所以这里配的是**全服务**并发上限。
    # 两个维度必须同时满足，见 services/extract_gate.py 的说明：
    #   文档数 —— 对接方明确要求不超过 3；
    #   页数   —— 实测每页渲染约占 30MB 内存，只限文档数挡不住 16 页大文档
    #             （3×16 页 ≈ 1.4GB，直接撑爆 2G 的机器）。
    # 按机器内存调：阿里云(1.6G) 24 页 ≈ 720MB，腾讯云(1.9G) 可给到 18。
    GATE_MAX_DOCS: int = 3
    GATE_MAX_PAGES: int = 24
    # 同步端点等槽位的上限；超时返回"服务繁忙"而非无限挂着（调用方有 HTTP 超时）。
    SYNC_GATE_WAIT_SEC: float = 120.0

    # ── 异步任务（开放平台 analyze/async + tasks/query）──────────────────────
    # 上传文件先落盘再排队，队列里只有路径不含字节 —— 排队本身几乎不耗内存。
    ASYNC_SPOOL_DIR: str = "./data/async_spool"
    # 任务行保留天数（异步接口文档第 7 条：默认 10 天后过期删除）。
    ASYNC_TASK_TTL_DAYS: int = 10
    # worker 轮询空闲间隔（秒）；有待处理任务时不等待，立即取下一个。
    ASYNC_POLL_INTERVAL_SEC: float = 2.0
    # 单个任务的处理重试次数（异步接口文档第 9 条：默认最多 3 次）。
    ASYNC_MAX_RETRIES: int = 3
    # 整份文档一张票据都没识别出来（全是 docType=other）时判为失败，而不是
    # 返回一份「errcode=0000 但 data 全是空壳」的成功。实测 2026-08-26 下午
    # 203 个任务里有 39 个是这种形态，对接方看到的就是「整个文件不能识别」。
    # 置 False 可退回旧行为（当成成功返回空壳）。
    FAIL_WHEN_NO_INVOICE_FOUND: bool = True
    # 队列深度上限（PENDING+RUNNING）：全局 + 单 client。没有上限时，一个
    # 循环提交的 client 能把磁盘写满（spool 文件 10 天才过期）进而拖死 SQLite
    # 写入。检查在写盘**之前**——超限的提交不在磁盘上留任何字节。
    # 上界估算：200 × 20MB(上传上限) = 4GB，远低于两台机器的可用磁盘。
    ASYNC_MAX_QUEUE_DEPTH: int = 200
    ASYNC_MAX_QUEUE_PER_CLIENT: int = 50
    # 终态结果的内存读缓存条数上限。轮询是高频读，缓存挡在 SQLite 前面；
    # 单条结果 5–50KB，256 条最坏约 12MB，有界。
    TASK_CACHE_SIZE: int = 256
    TASK_CACHE_TTL_SEC: int = 3600

    # ── 慢任务留档 ────────────────────────────────────────────────────────
    # 识别耗时超过此值的任务保留原件，便于事后单独重跑取证
    #（判断"慢"是文件本身还是并发排队 —— 实测同批 3 页文件耗时能差 3.5 倍）。
    SLOW_TASK_KEEP_SEC: float = 50.0
    # 留档原件的保留时长；过期只删文件不删任务行。
    SLOW_SPOOL_TTL_HOURS: int = 24

    # ── 提取结果缓存（同文件不重复烧模型）──────────────────────────────────
    # 键 = client_id + templateId + 文件内容 sha256（服务端自算，不依赖调用方
    # 传 fileHash —— 实测他们基本不传）。
    # TTL 短是刻意的：15 分钟只覆盖"重复提交/重试"这一个场景，同时把模板升级后
    # 吐旧结果的风险窗口压到最小。**若调长到小时级，必须把 prompt 版本加进键**
    # （见 models/extraction_cache.py 的说明），否则模板升级会被缓存架空。
    EXTRACT_CACHE_ENABLED: bool = True
    EXTRACT_CACHE_TTL_MIN: int = 15

    # ── Security ──────────────────────────────────────────────────────────
    SECRET_KEY: str = "CHANGE-ME-IN-PRODUCTION-32-bytes!!"
    API_KEY_PREFIX: str = "sk-"

    # ── Auth / JWT（角色与权限管理）────────────────────────────────────────
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 12          # 登录令牌有效期（12h）
    # 安装时创建的超级管理员（原型默认值，生产环境务必改）
    SUPER_ADMIN_USERNAME: str = "admin"
    SUPER_ADMIN_PASSWORD: str = "666666"
    # 普通用户「邮箱+验证码」登录的固定验证码（原型阶段；接邮件下发后改）
    NORMAL_USER_LOGIN_CODE: str = "666666"

    # ── CORS ──────────────────────────────────────────────────────────────
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # ── Pagination ────────────────────────────────────────────────────────
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

    @field_validator("UPLOAD_DIR", mode="before")
    @classmethod
    def ensure_upload_dir(cls, v: str) -> str:
        Path(v).mkdir(parents=True, exist_ok=True)
        return v

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @staticmethod
    def _split_keys(plural: str, singular: str) -> list[str]:
        """`<X>_API_KEYS`（分号/逗号分隔，去重保序）优先；未配则回退
        单个 `<X>_API_KEY`。空列表 = 该 provider 未配置 = 不可用。"""
        keys = [k.strip() for k in plural.replace(",", ";").split(";") if k.strip()]
        if not keys and singular.strip():
            keys = [singular.strip()]
        return list(dict.fromkeys(keys))

    @property
    def gemini_api_keys(self) -> list[str]:
        return self._split_keys(self.GEMINI_API_KEYS, self.GEMINI_API_KEY)

    @property
    def qwen_api_keys(self) -> list[str]:
        return self._split_keys(self.QWEN_API_KEYS, self.QWEN_API_KEY)


@lru_cache
def get_settings() -> Settings:
    return Settings()
