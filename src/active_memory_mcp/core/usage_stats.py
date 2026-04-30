"""Usage statistics tracking for ActiveMemory.

Tracks document access, search queries, and generates heatmap data.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy import func, and_

from ..storage.db import get_session, Document
from ..core.config import config

logger = logging.getLogger(__name__)


class DocumentAccessLog:
    """SQLAlchemy model for document access logging.
    
    Note: This is defined here to avoid circular imports.
    The model should be added to db.py in production.
    """
    pass  # Placeholder - will be added to db.py


def log_document_access(
    document_id: int,
    access_type: str,
    user_context: str = None,
    ip_address: str = None,
):
    """Log document access for heatmap generation.
    
    Args:
        document_id: ID of accessed document
        access_type: 'search', 'view', 'context', 'update'
        user_context: Optional context (e.g., 'telegram', 'web', 'mcp')
        ip_address: Optional IP address
    """
    if not config.visualization.enable_heatmap:
        return
    
    session = get_session()
    try:
        # We'll use the existing access_log table from Phase 5
        # Just add a new action type for document access
        from ..storage.db import AccessLog
        
        log = AccessLog(
            action=f"document_{access_type}",
            details=json.dumps({
                "document_id": document_id,
                "user_context": user_context,
            }),
            ip_address=ip_address,
            success=True,
        )
        session.add(log)
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"Error logging access: {e}")
    finally:
        session.close()


def get_heatmap_data(
    days: int = 30,
    resolution: str = None,
) -> Dict:
    """Generate heatmap data for document access.
    
    Args:
        days: Number of days to look back
        resolution: 'daily', 'hourly', or None (from config)
        
    Returns:
        Dict with heatmap data for visualization
    """
    if resolution is None:
        resolution = config.visualization.heatmap_resolution
    
    session = get_session()
    try:
        from ..storage.db import AccessLog
        
        since = datetime.utcnow() - timedelta(days=days)
        
        # Query access logs for document-related actions
        query = session.query(
            func.date(AccessLog.timestamp).label('date'),
            func.hour(AccessLog.timestamp).label('hour'),
            AccessLog.action,
            func.count().label('count'),
        ).filter(
            and_(
                AccessLog.timestamp >= since,
                AccessLog.action.in_([
                    'document_search',
                    'document_view',
                    'document_context',
                    'document_update',
                ])
            )
        ).group_by('date', 'hour', 'action')
        
        results = query.all()
        
        # Process results into heatmap format
        if resolution == 'hourly':
            return _process_hourly_heatmap(results, days)
        else:
            return _process_daily_heatmap(results, days)
    
    except Exception as e:
        print(f"Error generating heatmap: {e}")
        return {"error": str(e)}
    finally:
        session.close()


def _process_daily_heatmap(results, days: int) -> Dict:
    """Process results into daily heatmap."""
    heatmap_data = {}
    
    for row in results:
        date_str = row.date.isoformat() if row.date else "unknown"
        if date_str not in heatmap_data:
            heatmap_data[date_str] = {
                "search": 0,
                "view": 0,
                "context": 0,
                "update": 0,
            }
        
        action_type = row.action.replace("document_", "")
        if action_type in heatmap_data[date_str]:
            heatmap_data[date_str][action_type] = row.count
    
    # Fill missing dates with zeros
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    for i in range(days):
        date = (today - timedelta(days=i)).isoformat()
        if date not in heatmap_data:
            heatmap_data[date] = {
                "search": 0,
                "view": 0,
                "context": 0,
                "update": 0,
            }
    
    return {
        "type": "daily",
        "data": heatmap_data,
        "days": days,
    }


def _process_hourly_heatmap(results, days: int) -> Dict:
    """Process results into hourly heatmap."""
    # 2D array: day x hour
    heatmap = [[0 for _ in range(24)] for _ in range(days)]
    
    for row in results:
        if row.date and row.hour is not None:
            day_idx = (datetime.utcnow().date() - row.date).days
            if 0 <= day_idx < days:
                heatmap[day_idx][row.hour] += row.count
    
    return {
        "type": "hourly",
        "data": heatmap,
        "days": days,
        "hours": list(range(24)),
    }


def get_hot_documents(limit: int = 10, days: int = 7) -> List[Dict]:
    """Get most frequently accessed documents.
    
    Args:
        limit: Number of documents to return
        days: Look back period
        
    Returns:
        List of document info with access counts
    """
    session = get_session()
    try:
        from ..storage.db import AccessLog
        
        since = datetime.utcnow() - timedelta(days=days)
        
        # Extract document IDs from access log details
        results = session.query(
            AccessLog.details,
            func.count().label('access_count'),
        ).filter(
            and_(
                AccessLog.timestamp >= since,
                AccessLog.action.like('document_%'),
            )
        ).group_by(AccessLog.details).order_by(
            func.count().desc()
        ).limit(limit * 3).all()  # Fetch more to filter valid ones
        
        doc_counts = {}
        for row in results:
            try:
                details = json.loads(row.details) if isinstance(row.details, str) else row.details
                doc_id = details.get("document_id")
                if doc_id:
                    doc_counts[doc_id] = row.access_count
            except Exception as e:
                logger.warning(f"Access log processing error: {e}")
                continue
        
        # Fetch document details
        hot_docs = []
        for doc_id, count in sorted(doc_counts.items(), key=lambda x: -x[1])[:limit]:
            doc = session.query(Document).filter(Document.id == doc_id).first()
            if doc:
                hot_docs.append({
                    "id": doc.id,
                    "filename": doc.filename,
                    "filetype": doc.filetype,
                    "access_count": count,
                })
        
        return hot_docs
    
    except Exception as e:
        print(f"Error getting hot documents: {e}")
        return []
    finally:
        session.close()


def get_usage_stats(days: int = 30) -> Dict:
    """Get comprehensive usage statistics.
    
    Returns:
        Dict with various usage metrics
    """
    session = get_session()
    try:
        from ..storage.db import AccessLog
        
        since = datetime.utcnow() - timedelta(days=days)
        
        # Total accesses
        total = session.query(func.count()).filter(
            AccessLog.timestamp >= since,
            AccessLog.action.like('document_%'),
        ).scalar() or 0
        
        # By action type
        by_action = dict(
            session.query(
                AccessLog.action,
                func.count(),
            ).filter(
                AccessLog.timestamp >= since,
                AccessLog.action.like('document_%'),
            ).group_by(AccessLog.action).all()
        )
        
        # Unique documents accessed
        unique_docs = session.query(
            func.count(func.distinct(
                func.json_extract(AccessLog.details, '$.document_id')
            ))
        ).filter(
            AccessLog.timestamp >= since,
            AccessLog.action.like('document_%'),
        ).scalar() or 0
        
        return {
            "total_accesses": total,
            "by_action": by_action,
            "unique_documents": unique_docs,
            "period_days": days,
        }
    
    except Exception as e:
        print(f"Error getting usage stats: {e}")
        return {}
    finally:
        session.close()
