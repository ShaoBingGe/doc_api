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
    GEMINI_API_KEY: str = ""
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
    QWEN_MODEL: str = "qwen-vl-plus"         # 生产 OCR（视觉）；vl-plus 抽取质量/速度均衡
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
