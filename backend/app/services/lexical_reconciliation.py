"""Bounded, repeatable backfill/repair of Elasticsearch from accepted S3 artifacts."""
import logging

from app.db.models import OperationState, Video

logger = logging.getLogger(__name__)


def reconcile_lexical_index(db, storage, elastic, *, limit=10):
    state = db.get(OperationState, "lexical_index")
    details = dict(state.details or {}) if state else {}
    cursor = details.get("cursor", "")
    videos = db.query(Video).filter(Video.video_id > cursor, Video.status != "deleting",
        Video.segments_status == "completed", Video.segments_path.isnot(None)).order_by(Video.video_id).limit(limit).all()
    snapshots = [(video.video_id, video.segments_path) for video in videos]
    db.rollback()
    indexed, removed, errors = 0, 0, []
    for video_id, revision in snapshots:
        try:
            elastic.prepare(video_id, revision, storage.read_json(revision))
            # No database transaction is held during network calls. A concurrent
            # delete/change makes this revision obsolete; remove only that revision.
            video = db.query(Video).filter_by(video_id=video_id).populate_existing().first()
            accepted = video is not None and video.status != "deleting" and video.segments_path == revision
            db.rollback()
            if accepted:
                indexed += 1
            else:
                elastic.delete(video_id, revision)
                removed += 1
        except Exception as exc:
            db.rollback()
            logger.exception("Lexical indexing failed for video %s", video_id)
            errors.append({"video_id": video_id, "error": type(exc).__name__})
    # Scan passages as well as manifests: even a crash before manifest publication
    # must not leave deleted or superseded content permanently orphaned.
    after = details.get("cleanup_cursor")
    try:
        revisions, after = elastic.revisions(after, limit=limit)
        for item in revisions:
            try:
                video = db.query(Video).filter_by(video_id=item["video_id"]).populate_existing().first()
                keep = (video is not None and video.status != "deleting" and video.segments_path
                    and elastic.revision_token(video.video_id, video.segments_path) == item["revision_token"])
                db.rollback()
                if not keep:
                    elastic.delete(item["video_id"], token=item["revision_token"])
                    removed += 1
            except Exception as exc:
                db.rollback()
                errors.append({"video_id": item["video_id"], "error": type(exc).__name__})
    except Exception as exc:
        errors.append({"operation": "scan", "error": type(exc).__name__})
    return {"cursor": snapshots[-1][0] if len(snapshots) == limit else "",
            "cleanup_cursor": after, "checked_videos": indexed, "removed_revisions": removed, "errors": errors}
