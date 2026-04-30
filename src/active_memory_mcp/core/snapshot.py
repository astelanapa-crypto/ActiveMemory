"""Snapshot functionality for ActiveMemory.

Automatic database backups with retention policy.
Supposts PostgreSQL (pg_dump) and SQLite (file copy + gzip).
"""

import gzip
import os
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Optional

from ..core.config import config
from ..storage.db import get_backend, get_engine
from ..storage.db import init_db  # For restore


def _ensure_snapshot_dir() -> Path:
    """Ensure snapshot directory exists."""
    snapshot_dir = Path(config.export.snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    return snapshot_dir


def _postgresql_dump(output_path: str) -> bool:
    """Create PostgreSQL dump using pg_dump."""
    try:
        db = config.db
        env = os.environ.copy()
        if db.password:
            env["PGPASSWORD"] = db.password

        cmd = [
            "pg_dump",
            "-h", db.host,
            "-p", str(db.port),
            "-U", db.user,
            "-d", db.database,
            "-f", output_path,
            "--no-owner",
            "--no-acl",
        ]

        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            from ..storage.db import logger
            logger.error(f"pg_dump failed: {result.stderr}")
            return False
        return True
    except Exception as e:
        from ..storage.db import logger
        logger.error(f"PostgreSQL snapshot failed: {e}")
        return False


def _sqlite_snapshot(output_path: str) -> bool:
    """Create SQLite snapshot (copy + gzip)."""
    try:
        engine = get_engine()
        db_path = engine.url.database

        if not db_path or not os.path.exists(db_path):
            from ..storage.db import logger
            logger.error(f"SQLite database not found: {db_path}")
            return False

        with open(db_path, 'rb') as f_in:
            with gzip.open(output_path, 'wb') as f_out:
                f_out.write(f_in.read())
        return True
    except Exception as e:
        from ..storage.db import logger
        logger.error(f"SQLite snapshot failed: {e}")
        return False


def create_snapshot(name: Optional[str] = None) -> Dict:
    """Create a new database snapshot.

    Args:
        name: Optional name suffix (default: timestamp).

    Returns:
        dict with "success", "path", "size_bytes", "name"
    """
    snapshot_dir = _ensure_snapshot_dir()
    timestamp = datetime.now(timezone.utc)

    if name:
        safe_name = "".join(c for c in name if c.isalnum() or c in '-_')
        filename = f"active_memory_{safe_name}_{timestamp.strftime('%Y%m%d_%H%M%S')}"
    else:
        filename = f"active_memory_{timestamp.strftime('%Y%m%d_%H%M%S')}"

    backend = get_backend()

    if backend == "postgresql":
        filepath = snapshot_dir / f"{filename}.sql"
        success = _postgresql_dump(str(filepath))
    else:
        filepath = snapshot_dir / f"{filename}.db.gz"
        success = _sqlite_snapshot(str(filepath))

    if success:
        size = os.path.getsize(filepath)
        return {
            "success": True,
            "name": filepath.name,
            "path": str(filepath),
            "size_bytes": size,
            "created_at": timestamp.isoformat(),
        }
    else:
        return {"success": False, "error": "Snapshot creation failed"}


def list_snapshots() -> List[Dict]:
    """List all available snapshots.

    Returns:
        List of dicts with "name", "path", "size_bytes", "created_at", "age_days"
    """
    snapshot_dir = _ensure_snapshot_dir()
    snapshots = []

    for f in snapshot_dir.iterdir():
        if f.suffix in ('.sql', '.gz'):
            stat = f.stat()
            created = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc)
            age_days = (datetime.now(timezone.utc) - created).days

            snapshots.append({
                "name": f.name,
                "path": str(f),
                "size_bytes": stat.st_size,
                "created_at": created.isoformat(),
                "age_days": age_days,
            })

    return sorted(snapshots, key=lambda x: x["created_at"], reverse=True)


def restore_snapshot(snapshot_name: str) -> Dict:
    """Restore database from a snapshot.

    Args:
        snapshot_name: Name of snapshot file.

    Returns:
        dict with "success", "message"
    """
    snapshot_dir = _ensure_snapshot_dir()
    snapshot_path = snapshot_dir / snapshot_name

    if not snapshot_path.exists():
        return {"success": False, "error": f"Snapshot not found: {snapshot_name}"}

    backend = get_backend()

    try:
        if backend == "postgresql":
            # Restore from SQL dump
            db = config.db
            env = os.environ.copy()
            if db.password:
                env["PGPASSWORD"] = db.password

            cmd = [
                "psql",
                "-h", db.host,
                "-p", str(db.port),
                "-U", db.user,
                "-d", db.database,
                "-f", str(snapshot_path),
            ]

            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode != 0:
                return {"success": False, "error": f"Restore failed: {result.stderr}"}
        else:
            # Restore SQLite from gzipped backup
            engine = get_engine()
            db_path = engine.url.database

            # Backup current DB first
            if db_path and os.path.exists(db_path):
                backup_path = f"{db_path}.backup"
                os.rename(db_path, backup_path)

            try:
                with gzip.open(str(snapshot_path), 'rb') as f_in:
                    with open(db_path, 'wb') as f_out:
                        f_out.write(f_in.read())
            except Exception:
                # Restore backup on failure
                if os.path.exists(f"{db_path}.backup"):
                    os.rename(f"{db_path}.backup", db_path)
                raise

            # Remove backup on success
            if os.path.exists(f"{db_path}.backup"):
                os.remove(f"{db_path}.backup")

        # Reinitialize DB connection
        init_db()

        return {"success": True, "message": f"Restored from {snapshot_name}"}

    except Exception as e:
        return {"success": False, "error": str(e)}


def delete_snapshot(snapshot_name: str) -> Dict:
    """Delete a specific snapshot.

    Returns:
        dict with "success", "message"
    """
    snapshot_dir = _ensure_snapshot_dir()
    snapshot_path = snapshot_dir / snapshot_name

    if not snapshot_path.exists():
        return {"success": False, "error": f"Snapshot not found: {snapshot_name}"}

    try:
        os.remove(snapshot_path)
        return {"success": True, "message": f"Deleted {snapshot_name}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def cleanup_old_snapshots() -> Dict:
    """Remove snapshots older than retention_days.

    Returns:
        dict with "success", "deleted_count", "deleted_files"
    """
    snapshots = list_snapshots()
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.export.snapshot_retention_days)

    deleted = []
    for snap in snapshots:
        created_at = datetime.fromisoformat(snap["created_at"])
        if created_at < cutoff:
            try:
                os.remove(snap["path"])
                deleted.append(snap["name"])
            except Exception:
                pass

    return {
        "success": True,
        "deleted_count": len(deleted),
        "deleted_files": deleted,
    }


def get_snapshot_stats() -> Dict:
    """Get snapshot statistics."""
    snapshots = list_snapshots()
    total_size = sum(s["size_bytes"] for s in snapshots)

    return {
        "count": len(snapshots),
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "retention_days": config.export.snapshot_retention_days,
        "snapshot_dir": config.export.snapshot_dir,
        "auto_snapshot_enabled": config.export.auto_snapshot,
    }
