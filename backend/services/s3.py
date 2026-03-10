import io
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_permission
from config import MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_SECURE
from database import Bucket, User, get_db, generate_id

router = APIRouter(prefix="/api/v1/s3", tags=["s3"])

# Lazy MinIO client
_minio_client = None


def get_minio() -> Minio:
    global _minio_client
    if _minio_client is None:
        _minio_client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE,
        )
    return _minio_client


# ── Schemas ──────────────────────────────────────────────────────────────

class CreateBucketRequest(BaseModel):
    name: str
    region: str = "us-east-1"


class PresignRequest(BaseModel):
    expires_in: int = 3600


# ── Bucket endpoints ────────────────────────────────────────────────────

@router.get("/buckets")
async def list_buckets(
    user: User = Depends(require_permission("s3:ListBuckets")),
    db: AsyncSession = Depends(get_db),
):
    if user.is_root:
        result = await db.execute(select(Bucket))
    else:
        result = await db.execute(select(Bucket).where(Bucket.owner_id == user.id))
    buckets = result.scalars().all()
    return [
        {
            "id": b.id,
            "name": b.name,
            "owner_id": b.owner_id,
            "region": b.region,
            "versioning_enabled": b.versioning_enabled,
            "created_at": b.created_at.isoformat(),
        }
        for b in buckets
    ]


@router.post("/buckets")
async def create_bucket(
    body: CreateBucketRequest,
    user: User = Depends(require_permission("s3:CreateBucket")),
    db: AsyncSession = Depends(get_db),
):
    # Check name uniqueness in DB
    result = await db.execute(select(Bucket).where(Bucket.name == body.name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Bucket name already taken")

    # Create in MinIO
    try:
        client = get_minio()
        if not client.bucket_exists(body.name):
            client.make_bucket(body.name)
    except S3Error as e:
        raise HTTPException(status_code=400, detail=f"MinIO error: {e.message}")

    bucket = Bucket(
        id=generate_id(),
        name=body.name,
        owner_id=user.id,
        region=body.region,
    )
    db.add(bucket)
    await db.flush()

    return {
        "id": bucket.id,
        "name": bucket.name,
        "owner_id": bucket.owner_id,
        "region": bucket.region,
        "created_at": bucket.created_at.isoformat(),
    }


@router.delete("/buckets/{bucket_name}")
async def delete_bucket(
    bucket_name: str,
    user: User = Depends(require_permission("s3:DeleteBucket")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Bucket).where(Bucket.name == bucket_name))
    bucket = result.scalar_one_or_none()
    if not bucket:
        raise HTTPException(status_code=404, detail="Bucket not found")

    # Check if empty in MinIO
    try:
        client = get_minio()
        objects = list(client.list_objects(bucket_name, recursive=False))
        if objects:
            raise HTTPException(status_code=400, detail="Bucket is not empty")
        client.remove_bucket(bucket_name)
    except S3Error as e:
        raise HTTPException(status_code=400, detail=f"MinIO error: {e.message}")

    await db.delete(bucket)
    return {"detail": "Bucket deleted"}


# ── Object endpoints ────────────────────────────────────────────────────

@router.get("/buckets/{bucket_name}/objects")
async def list_objects(
    bucket_name: str,
    prefix: str = "",
    delimiter: str = "/",
    user: User = Depends(require_permission("s3:ListObjects")),
    db: AsyncSession = Depends(get_db),
):
    await _check_bucket_exists(bucket_name, db)

    try:
        client = get_minio()
        results = []

        objects = client.list_objects(
            bucket_name, prefix=prefix or None, recursive=False
        )
        for obj in objects:
            if obj.is_dir:
                results.append({
                    "key": obj.object_name,
                    "is_prefix": True,
                    "size": None,
                    "last_modified": None,
                })
            else:
                results.append({
                    "key": obj.object_name,
                    "is_prefix": False,
                    "size": _format_size(obj.size),
                    "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
                })
        return results
    except S3Error as e:
        raise HTTPException(status_code=400, detail=f"MinIO error: {e.message}")


@router.get("/buckets/{bucket_name}/objects/{key:path}")
async def get_object(
    bucket_name: str,
    key: str,
    user: User = Depends(require_permission("s3:GetObject")),
    db: AsyncSession = Depends(get_db),
):
    await _check_bucket_exists(bucket_name, db)

    try:
        client = get_minio()
        url = client.presigned_get_object(
            bucket_name, key, expires=timedelta(hours=1)
        )
        return {"url": url, "key": key, "bucket": bucket_name}
    except S3Error as e:
        raise HTTPException(status_code=404, detail=f"Object not found: {e.message}")


@router.put("/buckets/{bucket_name}/objects/{key:path}")
async def upload_object(
    bucket_name: str,
    key: str,
    file: UploadFile = File(...),
    user: User = Depends(require_permission("s3:PutObject")),
    db: AsyncSession = Depends(get_db),
):
    await _check_bucket_exists(bucket_name, db)

    try:
        client = get_minio()
        data = await file.read()
        client.put_object(
            bucket_name,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=file.content_type or "application/octet-stream",
        )
        return {"detail": "Object uploaded", "key": key, "size": len(data)}
    except S3Error as e:
        raise HTTPException(status_code=400, detail=f"Upload failed: {e.message}")


@router.delete("/buckets/{bucket_name}/objects/{key:path}")
async def delete_object(
    bucket_name: str,
    key: str,
    user: User = Depends(require_permission("s3:DeleteObject")),
    db: AsyncSession = Depends(get_db),
):
    await _check_bucket_exists(bucket_name, db)

    try:
        client = get_minio()
        client.remove_object(bucket_name, key)
        return {"detail": "Object deleted"}
    except S3Error as e:
        raise HTTPException(status_code=400, detail=f"Delete failed: {e.message}")


@router.post("/buckets/{bucket_name}/objects/{key:path}/presign")
async def presign_object(
    bucket_name: str,
    key: str,
    body: PresignRequest = PresignRequest(),
    user: User = Depends(require_permission("s3:GetObject")),
    db: AsyncSession = Depends(get_db),
):
    await _check_bucket_exists(bucket_name, db)

    try:
        client = get_minio()
        url = client.presigned_get_object(
            bucket_name, key, expires=timedelta(seconds=body.expires_in)
        )
        return {"url": url, "expires_in": body.expires_in}
    except S3Error as e:
        raise HTTPException(status_code=404, detail=f"Object not found: {e.message}")


# ── Helpers ──────────────────────────────────────────────────────────────

async def _check_bucket_exists(bucket_name: str, db: AsyncSession):
    result = await db.execute(select(Bucket).where(Bucket.name == bucket_name))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Bucket not found")


def _format_size(size_bytes: int) -> str:
    if size_bytes is None:
        return "--"
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
