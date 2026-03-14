import asyncio
import json
import os
import shutil
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import docker
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_permission
from config import LAMBDA_MAX_CONCURRENT, LAMBDA_CODE_DIR
from database import Function, FunctionInvocation, User, get_db, generate_id

router = APIRouter(prefix="/api/v1/lambda", tags=["lambda"])

# Lazy Docker client
_docker_client = None


def get_docker():
    global _docker_client
    if _docker_client is None:
        _docker_client = docker.from_env()
    return _docker_client


# Concurrency limiter for invocations
_invoke_semaphore = asyncio.Semaphore(LAMBDA_MAX_CONCURRENT)

# Runtime → Docker image name mapping
RUNTIME_IMAGES = {
    "python3.11": "awsclone-lambda-python3.11",
    "node20": "awsclone-lambda-node20",
}


# ── Schemas ──────────────────────────────────────────────────────────────

class FunctionConfigUpdate(BaseModel):
    timeout: Optional[int] = None
    memory_limit: Optional[int] = None
    environment: Optional[dict] = None
    handler: Optional[str] = None


class InvokeRequest(BaseModel):
    payload: dict = {}


# ── Function CRUD ────────────────────────────────────────────────────────

@router.get("/functions")
async def list_functions(
    user: User = Depends(require_permission("lambda:ListFunctions")),
    db: AsyncSession = Depends(get_db),
):
    if user.is_root:
        result = await db.execute(select(Function))
    else:
        result = await db.execute(select(Function).where(Function.owner_id == user.id))
    functions = result.scalars().all()
    return [_fn_to_dict(f) for f in functions]


@router.get("/functions/{function_id}")
async def get_function(
    function_id: str,
    user: User = Depends(require_permission("lambda:GetFunction")),
    db: AsyncSession = Depends(get_db),
):
    fn = await _get_function(function_id, db, user)
    return _fn_to_dict(fn)


@router.post("/functions")
async def create_function(
    metadata: str = Form(...),
    code: UploadFile = File(...),
    user: User = Depends(require_permission("lambda:CreateFunction")),
    db: AsyncSession = Depends(get_db),
):
    try:
        meta = json.loads(metadata)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid metadata JSON")

    name = meta.get("name")
    runtime = meta.get("runtime", "python3.11")
    handler = meta.get("handler", "handler.handler")
    timeout = meta.get("timeout", 30)
    memory_limit = meta.get("memory_limit", 128)
    environment = meta.get("environment", {})

    if not name:
        raise HTTPException(status_code=400, detail="Function name is required")

    if runtime not in RUNTIME_IMAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported runtime: {runtime}. Supported: {list(RUNTIME_IMAGES.keys())}")

    # Check name uniqueness
    result = await db.execute(select(Function).where(Function.name == name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Function name already exists")

    function_id = generate_id()
    code_dir = LAMBDA_CODE_DIR / function_id
    code_dir.mkdir(parents=True, exist_ok=True)

    # Save and extract zip
    zip_path = code_dir / "code.zip"
    try:
        content = await code.read()
        with open(zip_path, "wb") as f:
            f.write(content)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(code_dir)
    except zipfile.BadZipFile:
        shutil.rmtree(code_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Invalid ZIP file")

    fn = Function(
        id=function_id,
        name=name,
        owner_id=user.id,
        runtime=runtime,
        handler=handler,
        code_path=str(code_dir),
        timeout=timeout,
        memory_limit=memory_limit,
        environment=json.dumps(environment),
    )
    db.add(fn)
    await db.flush()
    return _fn_to_dict(fn)


@router.put("/functions/{function_id}/code")
async def update_function_code(
    function_id: str,
    code: UploadFile = File(...),
    user: User = Depends(require_permission("lambda:UpdateFunction")),
    db: AsyncSession = Depends(get_db),
):
    fn = await _get_function(function_id, db, user)
    code_dir = Path(fn.code_path)

    # Clear old code (keep directory)
    for item in code_dir.iterdir():
        if item.name != "code.zip":
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

    # Save and extract new zip
    zip_path = code_dir / "code.zip"
    try:
        content = await code.read()
        with open(zip_path, "wb") as f:
            f.write(content)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(code_dir)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid ZIP file")

    fn.updated_at = datetime.utcnow()
    await db.flush()
    return _fn_to_dict(fn)


@router.put("/functions/{function_id}/config")
async def update_function_config(
    function_id: str,
    body: FunctionConfigUpdate,
    user: User = Depends(require_permission("lambda:UpdateFunction")),
    db: AsyncSession = Depends(get_db),
):
    fn = await _get_function(function_id, db, user)

    if body.timeout is not None:
        fn.timeout = body.timeout
    if body.memory_limit is not None:
        fn.memory_limit = body.memory_limit
    if body.handler is not None:
        fn.handler = body.handler
    if body.environment is not None:
        fn.environment = json.dumps(body.environment)

    fn.updated_at = datetime.utcnow()
    await db.flush()
    return _fn_to_dict(fn)


@router.delete("/functions/{function_id}")
async def delete_function(
    function_id: str,
    user: User = Depends(require_permission("lambda:DeleteFunction")),
    db: AsyncSession = Depends(get_db),
):
    fn = await _get_function(function_id, db, user)

    # Remove code directory
    code_dir = Path(fn.code_path)
    if code_dir.exists():
        shutil.rmtree(code_dir, ignore_errors=True)

    await db.delete(fn)
    return {"detail": "Function deleted"}


# ── Invocation ───────────────────────────────────────────────────────────

@router.post("/functions/{function_id}/invoke")
async def invoke_function(
    function_id: str,
    body: InvokeRequest,
    user: User = Depends(require_permission("lambda:InvokeFunction")),
    db: AsyncSession = Depends(get_db),
):
    fn = await _get_function(function_id, db, user)
    image = RUNTIME_IMAGES.get(fn.runtime)
    if not image:
        raise HTTPException(status_code=400, detail=f"No runtime image for: {fn.runtime}")

    invocation_id = generate_id()
    invocation = FunctionInvocation(
        id=invocation_id,
        function_id=fn.id,
        status="pending",
        input=json.dumps(body.payload),
        started_at=datetime.utcnow(),
    )
    db.add(invocation)
    await db.flush()

    # Write event payload to code directory
    code_dir = Path(fn.code_path)
    event_file = code_dir / "_event.json"
    with open(event_file, "w") as f:
        json.dump(body.payload, f)

    # Parse user environment
    user_env = {}
    try:
        user_env = json.loads(fn.environment)
    except (json.JSONDecodeError, TypeError):
        pass

    # Run in ephemeral container with concurrency limit
    async with _invoke_semaphore:
        invocation.status = "running"
        await db.flush()

        start_time = time.time()
        container = None
        try:
            client = get_docker()
            container = client.containers.run(
                image=image,
                detach=True,
                volumes={str(code_dir): {"bind": "/var/task", "mode": "ro"}},
                environment={
                    **user_env,
                    "HANDLER": fn.handler,
                    "FUNCTION_NAME": fn.name,
                    "MEMORY_LIMIT": str(fn.memory_limit),
                },
                mem_limit=f"{fn.memory_limit}m",
                nano_cpus=500_000_000,  # 0.5 CPU for all Lambda
                network_disabled=True,
                name=f"awsclone-lambda-{invocation_id}",
                labels={"awsclone": "true", "type": "lambda"},
            )

            # Wait for container to finish
            try:
                result = container.wait(timeout=fn.timeout)
                exit_code = result.get("StatusCode", -1)
            except Exception:
                # Timeout
                try:
                    container.kill()
                except Exception:
                    pass
                invocation.status = "timeout"
                invocation.error = f"Function timed out after {fn.timeout}s"
                invocation.duration_ms = int((time.time() - start_time) * 1000)
                invocation.completed_at = datetime.utcnow()
                await db.flush()

                # Cleanup
                try:
                    container.remove(force=True)
                except Exception:
                    pass

                # Clean up event file
                event_file.unlink(missing_ok=True)

                return _invocation_to_dict(invocation)

            duration_ms = int((time.time() - start_time) * 1000)

            # Capture logs
            logs = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace").strip()

            # Parse output
            output = None
            error = None
            try:
                parsed = json.loads(logs.split("\n")[-1])  # Last line is the JSON output
                if parsed.get("status") == "success":
                    invocation.status = "success"
                    output = json.dumps(parsed.get("output"), default=str)
                else:
                    invocation.status = "error"
                    error = parsed.get("error", "Unknown error")
                    if parsed.get("trace"):
                        error += "\n" + parsed["trace"]
            except (json.JSONDecodeError, IndexError):
                if exit_code == 0:
                    invocation.status = "success"
                    output = logs
                else:
                    invocation.status = "error"
                    error = logs or f"Exit code: {exit_code}"

            invocation.output = output
            invocation.error = error
            invocation.duration_ms = duration_ms
            invocation.completed_at = datetime.utcnow()

        except docker.errors.ImageNotFound:
            invocation.status = "error"
            invocation.error = f"Runtime image not found: {image}. Run the setup script to build Lambda images."
            invocation.duration_ms = int((time.time() - start_time) * 1000)
            invocation.completed_at = datetime.utcnow()
        except docker.errors.APIError as e:
            invocation.status = "error"
            invocation.error = f"Docker error: {e.explanation}"
            invocation.duration_ms = int((time.time() - start_time) * 1000)
            invocation.completed_at = datetime.utcnow()
        finally:
            # Always clean up container
            if container:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            # Clean up event file
            event_file.unlink(missing_ok=True)

    # Update function last invocation time
    fn.last_invocation_at = datetime.utcnow()
    await db.flush()

    return _invocation_to_dict(invocation)


@router.get("/functions/{function_id}/invocations")
async def list_invocations(
    function_id: str,
    limit: int = 20,
    user: User = Depends(require_permission("lambda:GetFunction")),
    db: AsyncSession = Depends(get_db),
):
    await _get_function(function_id, db, user)
    result = await db.execute(
        select(FunctionInvocation)
        .where(FunctionInvocation.function_id == function_id)
        .order_by(FunctionInvocation.started_at.desc())
        .limit(limit)
    )
    invocations = result.scalars().all()
    return [_invocation_to_dict(i) for i in invocations]


@router.get("/functions/{function_id}/invocations/{invocation_id}")
async def get_invocation(
    function_id: str,
    invocation_id: str,
    user: User = Depends(require_permission("lambda:GetFunction")),
    db: AsyncSession = Depends(get_db),
):
    await _get_function(function_id, db, user)  # verify ownership
    result = await db.execute(
        select(FunctionInvocation).where(
            FunctionInvocation.id == invocation_id,
            FunctionInvocation.function_id == function_id,
        )
    )
    invocation = result.scalar_one_or_none()
    if not invocation:
        raise HTTPException(status_code=404, detail="Invocation not found")
    return _invocation_to_dict(invocation)


# ── Helpers ──────────────────────────────────────────────────────────────

async def _get_function(function_id: str, db: AsyncSession, user: User) -> Function:
    result = await db.execute(select(Function).where(Function.id == function_id))
    fn = result.scalar_one_or_none()
    if not fn:
        raise HTTPException(status_code=404, detail="Function not found")
    if not user.is_root and fn.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Function not found")
    return fn


def _fn_to_dict(fn: Function) -> dict:
    env = {}
    try:
        env = json.loads(fn.environment)
    except (json.JSONDecodeError, TypeError):
        pass
    return {
        "id": fn.id,
        "name": fn.name,
        "owner_id": fn.owner_id,
        "runtime": fn.runtime,
        "handler": fn.handler,
        "timeout": fn.timeout,
        "memory_limit": fn.memory_limit,
        "environment": env,
        "last_invocation_at": fn.last_invocation_at.isoformat() if fn.last_invocation_at else None,
        "created_at": fn.created_at.isoformat(),
        "updated_at": fn.updated_at.isoformat(),
    }


def _invocation_to_dict(inv: FunctionInvocation) -> dict:
    return {
        "id": inv.id,
        "function_id": inv.function_id,
        "status": inv.status,
        "input": inv.input,
        "output": inv.output,
        "error": inv.error,
        "duration_ms": inv.duration_ms,
        "started_at": inv.started_at.isoformat() if inv.started_at else None,
        "completed_at": inv.completed_at.isoformat() if inv.completed_at else None,
    }
