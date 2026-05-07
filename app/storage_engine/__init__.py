"""
storage_engine — single source of truth for file storage in Shawahid AI.

Phase-7 contract
================
Every byte that lands on disk and every duplicate-detection decision
is now driven by this package. Other layers (services.storage,
api.media, webhook, media_engine) talk to it through this public API:

    paths
        storage_root, build_teacher_storage_path, ensure_within_storage_root,
        safe_filename, teacher_root, relative_to_storage_root

    hashing
        compute_content_hash, hash_text, hash_url, hash_bytes

    dedup
        find_duplicate_by_hash, is_duplicate_for_teacher, mark_duplicate

    file_store
        save_uploaded_file, save_uploaded_file_legacy, download_and_save,
        read_stored_file, delete_stored_file, file_exists, get_file_size

    evidence_store
        attach_file_to_evidence, attach_preview_to_evidence,
        attach_thumbnail_to_evidence, build_evidence_storage_ref,
        evidence_file_exists

    cleanup
        find_orphan_files, find_missing_files, find_broken_evidence_paths,
        build_cleanup_report

    validators
        validate_storage_path, validate_mime_type, validate_file_size,
        validate_teacher_scope, validate_safe_url, StorageValidationError

    schemas
        StoredFile, EvidenceStorageRef
"""
from __future__ import annotations

from app.storage_engine.cleanup import (
    BrokenPathRow,
    CleanupReport,
    MissingFileRow,
    OrphanFile,
    build_cleanup_report,
    find_broken_evidence_paths,
    find_missing_files,
    find_orphan_files,
)
from app.storage_engine.dedup import (
    find_duplicate_by_hash,
    is_duplicate_for_teacher,
    mark_duplicate,
)
from app.storage_engine.evidence_store import (
    attach_file_to_evidence,
    attach_preview_to_evidence,
    attach_thumbnail_to_evidence,
    build_evidence_storage_ref,
    evidence_file_exists,
)
from app.storage_engine.file_store import (
    delete_stored_file,
    download_and_save,
    file_exists,
    get_file_size,
    read_stored_file,
    save_uploaded_file,
    save_uploaded_file_legacy,
)
from app.storage_engine.hashing import (
    compute_content_hash,
    hash_bytes,
    hash_text,
    hash_url,
)
from app.storage_engine.paths import (
    build_teacher_storage_path,
    ensure_within_storage_root,
    relative_to_storage_root,
    safe_filename,
    storage_root,
    teacher_root,
)
from app.storage_engine.schemas import EvidenceStorageRef, StoredFile
from app.storage_engine.validators import (
    DEFAULT_ALLOWED_MIME,
    DEFAULT_MAX_FILE_MB,
    StorageValidationError,
    validate_file_size,
    validate_mime_type,
    validate_safe_url,
    validate_storage_path,
    validate_teacher_scope,
)

__all__ = [
    # schemas
    "StoredFile",
    "EvidenceStorageRef",
    # paths
    "storage_root",
    "teacher_root",
    "build_teacher_storage_path",
    "safe_filename",
    "ensure_within_storage_root",
    "relative_to_storage_root",
    # hashing
    "compute_content_hash",
    "hash_bytes",
    "hash_text",
    "hash_url",
    # dedup
    "find_duplicate_by_hash",
    "is_duplicate_for_teacher",
    "mark_duplicate",
    # file_store
    "save_uploaded_file",
    "save_uploaded_file_legacy",
    "download_and_save",
    "read_stored_file",
    "delete_stored_file",
    "file_exists",
    "get_file_size",
    # evidence_store
    "attach_file_to_evidence",
    "attach_preview_to_evidence",
    "attach_thumbnail_to_evidence",
    "build_evidence_storage_ref",
    "evidence_file_exists",
    # cleanup
    "OrphanFile",
    "MissingFileRow",
    "BrokenPathRow",
    "CleanupReport",
    "find_orphan_files",
    "find_missing_files",
    "find_broken_evidence_paths",
    "build_cleanup_report",
    # validators
    "StorageValidationError",
    "DEFAULT_ALLOWED_MIME",
    "DEFAULT_MAX_FILE_MB",
    "validate_storage_path",
    "validate_mime_type",
    "validate_file_size",
    "validate_teacher_scope",
    "validate_safe_url",
]
