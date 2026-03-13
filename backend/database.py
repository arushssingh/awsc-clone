import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, Index, event
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, relationship

from config import DATABASE_URL


engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def generate_id():
    return uuid.uuid4().hex[:16]


class Base(DeclarativeBase):
    pass


# ── IAM ──────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=generate_id)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=True)
    password_hash = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    is_root = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    github_token = Column(String, nullable=True)

    roles = relationship("Role", secondary="user_roles", back_populates="users")
    api_keys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(String, primary_key=True, default=generate_id)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    key_id = Column(String, unique=True, nullable=False)
    key_secret_hash = Column(String, nullable=False)
    description = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="api_keys")


class Role(Base):
    __tablename__ = "roles"

    id = Column(String, primary_key=True, default=generate_id)
    name = Column(String, unique=True, nullable=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", secondary="user_roles", back_populates="roles")
    policies = relationship("Policy", secondary="role_policies", back_populates="roles")


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id = Column(String, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class Policy(Base):
    __tablename__ = "policies"

    id = Column(String, primary_key=True, default=generate_id)
    name = Column(String, unique=True, nullable=False)
    document = Column(Text, nullable=False)  # JSON string
    created_at = Column(DateTime, default=datetime.utcnow)

    roles = relationship("Role", secondary="role_policies", back_populates="policies")


class RolePolicy(Base):
    __tablename__ = "role_policies"

    role_id = Column(String, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    policy_id = Column(String, ForeignKey("policies.id", ondelete="CASCADE"), primary_key=True)


# ── EC2 ──────────────────────────────────────────────────────────────────

class Instance(Base):
    __tablename__ = "instances"

    id = Column(String, primary_key=True, default=generate_id)
    name = Column(String, nullable=False)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False)
    docker_container_id = Column(String, nullable=True)
    image = Column(String, nullable=False, default="ubuntu:22.04")
    instance_type = Column(String, nullable=False, default="t2.micro")
    state = Column(String, nullable=False, default="stopped")
    vpc_id = Column(String, ForeignKey("vpcs.id"), nullable=True)
    private_ip = Column(String, nullable=True)
    port_mappings = Column(Text, nullable=True)  # JSON
    cpu_limit = Column(Float, default=0.5)
    memory_limit = Column(Integer, default=256)  # MB
    environment = Column(Text, default="{}")  # JSON
    command = Column(String, nullable=True)
    # Deploy fields (GitHub/ZIP code deploy into this instance)
    github_repo = Column(String, nullable=True)        # "owner/repo"
    github_branch = Column(String, nullable=True)
    github_webhook_id = Column(Integer, nullable=True)
    webhook_secret = Column(String, nullable=True)
    project_type = Column(String, nullable=True)       # static, vite, nextjs, python, etc.
    project_label = Column(String, nullable=True)       # Human-readable
    build_log = Column(Text, nullable=True)
    docker_image_tag = Column(String, nullable=True)    # built image tag
    subdomain = Column(String, nullable=True, unique=True)  # custom subdomain e.g. "mysite"
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── VPC ──────────────────────────────────────────────────────────────────

class VPC(Base):
    __tablename__ = "vpcs"

    id = Column(String, primary_key=True, default=generate_id)
    name = Column(String, nullable=False)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False)
    docker_network_id = Column(String, nullable=True)
    cidr_block = Column(String, nullable=False, default="10.0.0.0/16")
    state = Column(String, nullable=False, default="available")
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Lambda ───────────────────────────────────────────────────────────────

class Function(Base):
    __tablename__ = "functions"

    id = Column(String, primary_key=True, default=generate_id)
    name = Column(String, unique=True, nullable=False)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False)
    runtime = Column(String, nullable=False, default="python3.11")
    handler = Column(String, nullable=False, default="handler.handler")
    code_path = Column(String, nullable=False)
    timeout = Column(Integer, nullable=False, default=30)
    memory_limit = Column(Integer, nullable=False, default=128)
    environment = Column(Text, default="{}")  # JSON
    last_invocation_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FunctionInvocation(Base):
    __tablename__ = "function_invocations"

    id = Column(String, primary_key=True, default=generate_id)
    function_id = Column(String, ForeignKey("functions.id", ondelete="CASCADE"), nullable=False)
    status = Column(String, nullable=False, default="pending")
    input = Column(Text, nullable=True)
    output = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)


# ── Route 53 ─────────────────────────────────────────────────────────────

class Domain(Base):
    __tablename__ = "domains"

    id = Column(String, primary_key=True, default=generate_id)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False)
    domain = Column(String, unique=True, nullable=False)
    target_type = Column(String, nullable=False)  # instance | static | external
    target_id = Column(String, nullable=True)
    target_address = Column(String, nullable=True)
    ssl_enabled = Column(Boolean, default=True)
    caddy_route_id = Column(String, nullable=True)
    state = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── CloudWatch ───────────────────────────────────────────────────────────

class Metric(Base):
    __tablename__ = "metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    metric_name = Column(String, nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(String, nullable=False, default="Percent")
    dimensions = Column(Text, default="{}")  # JSON

    __table_args__ = (
        Index("idx_metrics_ts_name", "metric_name", "timestamp"),
    )


class Alarm(Base):
    __tablename__ = "alarms"

    id = Column(String, primary_key=True, default=generate_id)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    metric_name = Column(String, nullable=False)
    comparison = Column(String, nullable=False)  # gt | lt | gte | lte
    threshold = Column(Float, nullable=False)
    period_seconds = Column(Integer, nullable=False, default=300)
    state = Column(String, nullable=False, default="OK")
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Deploy ──────────────────────────────────────────────────────────────

class Deployment(Base):
    __tablename__ = "deployments"

    id = Column(String, primary_key=True, default=generate_id)
    name = Column(String, nullable=False)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False)
    project_type = Column(String, nullable=True)       # static, vite, cra, nextjs, node-server, python, dockerfile
    project_label = Column(String, nullable=True)       # Human-readable label
    status = Column(String, nullable=False, default="uploading")  # uploading, building, running, failed, stopped
    build_log = Column(Text, nullable=True)
    docker_image_id = Column(String, nullable=True)
    docker_container_id = Column(String, nullable=True)
    port = Column(Integer, nullable=True)
    env_vars = Column(Text, default="{}")               # JSON
    github_repo = Column(String, nullable=True)        # "owner/repo"
    github_branch = Column(String, nullable=True)      # "main"
    github_webhook_id = Column(Integer, nullable=True)
    webhook_secret = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── S3 ───────────────────────────────────────────────────────────────────

class Bucket(Base):
    __tablename__ = "buckets"

    id = Column(String, primary_key=True, default=generate_id)
    name = Column(String, unique=True, nullable=False)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False)
    region = Column(String, default="us-east-1")
    versioning_enabled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Enable WAL mode for SQLite (better concurrent read performance) ──────

@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# ── Database initialization ──────────────────────────────────────────────

async def init_db():
    """Create all tables if they don't exist, and migrate existing ones."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Safe migrations for existing databases
        from sqlalchemy import text
        migrations = [
            "ALTER TABLE users ADD COLUMN github_token TEXT",
            "ALTER TABLE deployments ADD COLUMN github_repo TEXT",
            "ALTER TABLE deployments ADD COLUMN github_branch TEXT",
            "ALTER TABLE deployments ADD COLUMN github_webhook_id INTEGER",
            "ALTER TABLE deployments ADD COLUMN webhook_secret TEXT",
            # Instance deploy fields
            "ALTER TABLE instances ADD COLUMN github_repo TEXT",
            "ALTER TABLE instances ADD COLUMN github_branch TEXT",
            "ALTER TABLE instances ADD COLUMN github_webhook_id INTEGER",
            "ALTER TABLE instances ADD COLUMN webhook_secret TEXT",
            "ALTER TABLE instances ADD COLUMN project_type TEXT",
            "ALTER TABLE instances ADD COLUMN project_label TEXT",
            "ALTER TABLE instances ADD COLUMN build_log TEXT",
            "ALTER TABLE instances ADD COLUMN docker_image_tag TEXT",
            "ALTER TABLE instances ADD COLUMN subdomain TEXT UNIQUE",
        ]
        for sql in migrations:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass  # Column already exists


async def get_db() -> AsyncSession:
    """Dependency that yields a database session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
