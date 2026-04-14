from __future__ import annotations

import io
from pathlib import Path

from flask import current_app

try:
    import boto3
except Exception:  # pragma: no cover
    boto3 = None


ORDER_IMAGE_SECTION = "uploaded_images"
ORDER_DOCUMENT_SECTION = "uploaded_documents"
ORDER_SHEET_SECTION = "generated_order_sheets"
ORDER_META_SECTION = "meta"


def storage_backend() -> str:
    return str(current_app.config.get("STORAGE_BACKEND", "local") or "local").strip().lower()


def is_s3_backend() -> bool:
    return storage_backend() == "s3"


def ensure_order_storage(order_id: int) -> None:
    if is_s3_backend():
        _ensure_s3_prefix(_order_root_key(order_id))
        _ensure_s3_prefix(_order_section_key(order_id, ORDER_IMAGE_SECTION))
        _ensure_s3_prefix(_order_section_key(order_id, ORDER_DOCUMENT_SECTION))
        _ensure_s3_prefix(_order_section_key(order_id, ORDER_SHEET_SECTION))
        _ensure_s3_prefix(_order_section_key(order_id, ORDER_META_SECTION))
        return

    for base in _local_order_dirs(order_id):
        base.mkdir(parents=True, exist_ok=True)


def save_order_file(
    order_id: int,
    section: str,
    filename: str,
    data: bytes,
    *,
    content_type: str | None = None,
) -> str:
    if is_s3_backend():
        key = _order_file_key(order_id, section, filename)
        _s3_client().put_object(
            Bucket=_s3_bucket(),
            Key=key,
            Body=data,
            ContentType=content_type or "application/octet-stream",
        )
        return _s3_uri(key)

    directory = _local_order_dir(order_id, section)
    directory.mkdir(parents=True, exist_ok=True)
    full_path = directory / filename
    full_path.write_bytes(data)
    return str(full_path)


def save_order_text(order_id: int, section: str, filename: str, text: str) -> str:
    return save_order_file(
        order_id,
        section,
        filename,
        text.encode("utf-8"),
        content_type="application/json",
    )


def save_global_file(
    section: str,
    filename: str,
    data: bytes,
    *,
    content_type: str | None = None,
) -> str:
    if is_s3_backend():
        key = _global_file_key(section, filename)
        _s3_client().put_object(
            Bucket=_s3_bucket(),
            Key=key,
            Body=data,
            ContentType=content_type or "application/octet-stream",
        )
        return _s3_uri(key)

    base_dir = _local_global_dir(section)
    base_dir.mkdir(parents=True, exist_ok=True)
    full_path = base_dir / filename
    full_path.write_bytes(data)
    return str(full_path)


def read_bytes(storage_path: str) -> bytes:
    storage_path = _resolve_virtual_path(storage_path)
    if _is_s3_uri(storage_path):
        bucket, key = _parse_s3_uri(storage_path)
        stream = io.BytesIO()
        _s3_client().download_fileobj(bucket, key, stream)
        return stream.getvalue()
    return Path(storage_path).read_bytes()


def exists(storage_path: str) -> bool:
    storage_path = _resolve_virtual_path(storage_path)
    if _is_s3_uri(storage_path):
        bucket, key = _parse_s3_uri(storage_path)
        try:
            _s3_client().head_object(Bucket=bucket, Key=key)
            return True
        except Exception:  # pragma: no cover
            return False
    return Path(storage_path).exists()


def delete(storage_path: str) -> None:
    if not storage_path:
        return
    storage_path = _resolve_virtual_path(storage_path)
    if _is_s3_uri(storage_path):
        bucket, key = _parse_s3_uri(storage_path)
        _s3_client().delete_object(Bucket=bucket, Key=key)
        return
    path = Path(storage_path)
    if path.exists():
        path.unlink()


def delete_order_storage(order_id: int) -> None:
    if is_s3_backend():
        _delete_s3_prefix(_order_root_key(order_id))
        return

    for folder in _local_order_dirs(order_id):
        if folder.exists() and folder.is_dir():
            for child in sorted(folder.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    child.rmdir()
            folder.rmdir()


def local_path(storage_path: str) -> Path | None:
    storage_path = _resolve_virtual_path(storage_path)
    if not storage_path or _is_s3_uri(storage_path):
        return None
    return Path(storage_path)


def _local_order_dirs(order_id: int) -> list[Path]:
    return [
        _local_order_dir(order_id, ORDER_IMAGE_SECTION),
        _local_order_dir(order_id, ORDER_DOCUMENT_SECTION),
        _local_order_dir(order_id, ORDER_SHEET_SECTION),
        _local_order_dir(order_id, ORDER_META_SECTION),
    ]


def _local_order_dir(order_id: int, section: str) -> Path:
    return Path(current_app.config["UPLOAD_DIR"]) / str(order_id) / str(section).strip("/")


def _local_global_dir(section: str) -> Path:
    return Path(current_app.config["UPLOAD_DIR"]) / section


def _s3_client():
    if boto3 is None:  # pragma: no cover
        raise RuntimeError("S3 storage backend requires boto3 to be installed.")
    region = str(current_app.config.get("AWS_REGION", "") or "").strip() or None
    return boto3.client("s3", region_name=region)


def _s3_bucket() -> str:
    bucket = str(current_app.config.get("S3_BUCKET", "") or "").strip()
    if not bucket:
        raise RuntimeError("S3_BUCKET must be configured when STORAGE_BACKEND=s3.")
    return bucket


def _s3_prefix() -> str:
    prefix = str(current_app.config.get("S3_PREFIX", "") or "").strip().strip("/")
    return f"{prefix}/" if prefix else ""


def _s3_uri(key: str) -> str:
    return f"s3://{_s3_bucket()}/{key}"


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    value = str(uri or "")
    without_scheme = value.replace("s3://", "", 1)
    bucket, _, key = without_scheme.partition("/")
    return bucket, key


def _is_s3_uri(value: str) -> bool:
    return str(value or "").startswith("s3://")


def _is_order_uri(value: str) -> bool:
    return str(value or "").startswith("order://")


def _resolve_virtual_path(storage_path: str) -> str:
    if not _is_order_uri(storage_path):
        return storage_path
    raw = str(storage_path or "").replace("order://", "", 1)
    order_token, _, remainder = raw.partition("/")
    try:
        order_id = int(order_token)
    except (TypeError, ValueError):
        return storage_path
    section, _, filename = remainder.partition("/")
    if not section or not filename:
        return storage_path
    if is_s3_backend():
        return _s3_uri(_order_file_key(order_id, section, filename))
    return str(_local_order_dir(order_id, section) / filename)


def _order_root_key(order_id: int) -> str:
    return f"{_s3_prefix()}orders/{int(order_id)}/"


def _order_section_key(order_id: int, section: str) -> str:
    return f"{_order_root_key(order_id)}{section.strip('/')}/"


def _order_file_key(order_id: int, section: str, filename: str) -> str:
    return f"{_order_section_key(order_id, section)}{filename}"


def _global_file_key(section: str, filename: str) -> str:
    clean_section = str(section or "").strip().strip("/")
    prefix = _s3_prefix()
    return f"{prefix}{clean_section}/{filename}" if clean_section else f"{prefix}{filename}"


def _ensure_s3_prefix(key: str) -> None:
    _s3_client().put_object(Bucket=_s3_bucket(), Key=key, Body=b"")


def _delete_s3_prefix(prefix: str) -> None:
    client = _s3_client()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_s3_bucket(), Prefix=prefix):
        contents = page.get("Contents", [])
        if not contents:
            continue
        objects = [{"Key": row["Key"]} for row in contents if row.get("Key")]
        if objects:
            client.delete_objects(Bucket=_s3_bucket(), Delete={"Objects": objects})
