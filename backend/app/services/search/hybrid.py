from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notice, NoticeChunk, NoticeEmbedding, NoticeMetadata
from app.schemas import QueryFilters
from app.services.ai import AIService
from app.services.notice_status import effective_status


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


class HybridSearch:
    def __init__(self, db: Session, ai: AIService | None = None) -> None:
        self.db = db
        self.ai = ai or AIService()

    def search(self, message: str, filters: QueryFilters, limit: int = 5) -> list[dict]:
        vector = self.ai.embedding(message)
        base = (
            select(Notice, NoticeMetadata, NoticeEmbedding, NoticeChunk)
            .join(NoticeMetadata, NoticeMetadata.notice_id == Notice.id)
            .join(NoticeEmbedding, NoticeEmbedding.notice_id == Notice.id)
            .outerjoin(NoticeChunk, NoticeChunk.notice_id == Notice.id)
            .where(Notice.is_archived.is_(False), Notice.ai_processed.is_(True))
        )
        stmt = base
        # 카테고리는 1차 후보군에만 적용하고 0건이면 완화 검색으로 다시 찾는다.
        if filters.category:
            stmt = stmt.where(NoticeMetadata.category == filters.category)
        rows = self.db.execute(stmt).all()
        if not rows and filters.category:
            rows = self.db.execute(base).all()

        results_by_notice: dict[int, dict] = {}
        now = datetime.now(timezone.utc)
        normalized_message = message.lower()
        contact_query = any(term in normalized_message for term in ("전화", "연락처", "담당자", "담당 부서", "문의처", "어디에 문의"))
        procedure_query = any(term in normalized_message for term in ("방법", "절차", "어떻게", "어디서", "하는 법"))
        specialized_terms = ("창업", "입대", "군입대", "질병", "육아", "임신", "출산")
        semantic_embeddings = self.ai.embedding_provider in {"openai", "ollama"}
        vector_weight = 0.18 if semantic_embeddings else 0.05
        for notice, metadata, embedding, chunk in rows:
            if notice.source_type == "staff_directory" and not contact_query:
                continue
            candidate_vector = chunk.embedding if chunk is not None else embedding.embedding
            candidate_model = chunk.embedding_model if chunk is not None else embedding.embedding_model
            query_model = getattr(self.ai, "embedding_model_name", None)
            # 서로 다른 임베딩 모델의 벡터는 좌표 공간 자체가 다르므로 코사인
            # 유사도를 계산하면 의미 없는 점수가 된다. 부분 재인덱싱 중에는
            # 현재 모델로 만든 문서만 의미 검색에 사용하고 나머지는 키워드,
            # 메타데이터, 최신성 점수로 안전하게 검색한다.
            compatible_embedding = bool(query_model and candidate_model == query_model)
            vector_score = cosine_similarity(vector, list(candidate_vector)) if compatible_embedding else 0.0
            title_text = notice.title.lower()
            chunk_text = chunk.search_text if chunk is not None else ""
            text = (
                f"{notice.title} {' '.join(metadata.keywords or [])} "
                f"{' '.join(metadata.synonyms or [])} {metadata.search_text or ''} "
                f"{notice.content} {notice.attachment_text} {chunk_text}"
            ).lower()
            hits = sum(1 for keyword in filters.keywords if keyword.lower() in text)
            keyword_score = min(hits / max(len(filters.keywords), 1), 1.0)
            title_hits = sum(1 for keyword in filters.keywords if keyword.lower() in title_text)
            title_score = min(title_hits / max(len(filters.keywords), 1), 1.0)
            metadata_score = 0.0
            if filters.category and metadata.category == filters.category:
                metadata_score += 0.25
            if filters.sub_category and metadata.sub_category and filters.sub_category in metadata.sub_category:
                metadata_score += 0.25
            if filters.academic_year and metadata.academic_year == filters.academic_year:
                metadata_score += 0.1
            if filters.semester and metadata.semester == filters.semester:
                metadata_score += 0.1
            current_status = effective_status(notice)
            status_score = {"active": 0.15, "upcoming": 0.1, "always": 0.12, "unknown": 0.03, "expired": 0.0}.get(current_status, 0.0)
            if filters.time_scope == "current" and current_status == "expired":
                status_score -= 0.3
            freshness_score = 0.0
            if filters.time_scope == "current" and notice.source_type in {"official_notice", "event"}:
                published_at = notice.published_at
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
                age_days = max((now - published_at).days, 0)
                freshness_score = max(0.0, 1.0 - age_days / 365) * 0.12
            source_score = min(max(notice.source_priority, 0), 120) / 120 * 0.2
            if procedure_query and notice.source_type == "academic_guide":
                source_score += 0.2
            specialization_penalty = sum(
                0.18 for term in specialized_terms if term in title_text and term not in normalized_message
            )
            subcategory_match = False
            if filters.sub_category:
                subject = filters.sub_category.lower()
                authoritative_body_match = (
                    notice.source_type == "staff_directory"
                    and subject in text
                )
                if subject not in title_text and not authoritative_body_match:
                    continue
                subcategory_match = True
            # 실임베딩을 쓰는 경우에는 표현이 전혀 겹치지 않는 자연어 질문도
            # 높은 의미 유사도가 확인되면 후보로 남긴다. 어휘 폴백에는 적용하지
            # 않아 해시 충돌이 근거 없는 답변으로 이어지는 것을 막는다.
            semantic_match = semantic_embeddings and compatible_embedding and vector_score >= 0.49
            if filters.keywords and hits == 0 and not subcategory_match and not semantic_match:
                continue
            score = (
                vector_score * vector_weight + keyword_score * 0.32 + title_score * 0.30
                + metadata_score + status_score + freshness_score + source_score - specialization_penalty
            )
            item = {
                "notice": notice, "metadata": metadata, "score": round(score, 5),
                "chunk_text": chunk.text if chunk is not None else notice.content,
            }
            previous = results_by_notice.get(notice.id)
            if previous is None or item["score"] > previous["score"]:
                results_by_notice[notice.id] = item
        # 근거가 약한 결과를 억지로 답변에 사용하지 않는다.
        results = sorted(
            results_by_notice.values(),
            key=lambda item: (item["score"], item["notice"].source_priority, item["notice"].published_at),
            reverse=True,
        )
        filtered_results = [item for item in results if item["score"] >= 0.34][:limit]
        if not filtered_results and filters.category:
            return self.search(message, filters.model_copy(update={"category": None}), limit)
        return filtered_results
