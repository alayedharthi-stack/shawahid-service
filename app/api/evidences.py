from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.schemas.evidence import EvidenceUpdate, EvidenceOut
from app.services.evidences import get_evidence_by_id, update_evidence, delete_evidence

router = APIRouter(prefix="/evidences", tags=["evidences"])


@router.patch("/{evidence_id}", response_model=EvidenceOut)
def patch_evidence(evidence_id: int, data: EvidenceUpdate, db: Session = Depends(get_db)):
    """
    Update evidence fields. In MVP, no teacher auth yet — the caller must supply
    the correct teacher_id via query param to enforce ownership. Admin path skips this check.
    """
    evidence = get_evidence_by_id(db, evidence_id)
    if not evidence:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="الشاهد غير موجود")
    return update_evidence(db, evidence, data.model_dump(exclude_none=True))


@router.delete("/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_evidence(evidence_id: int, db: Session = Depends(get_db)):
    evidence = get_evidence_by_id(db, evidence_id)
    if not evidence:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="الشاهد غير موجود")
    delete_evidence(db, evidence)
