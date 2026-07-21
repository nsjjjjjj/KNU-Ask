__all__ = ["HybridSearch", "cosine_similarity"]


def __getattr__(name):
    # AIService가 업무 규칙만 가져올 때 hybrid -> AIService로 되돌아가는
    # 순환 import를 만들지 않도록 검색 구현은 실제 사용 시점에 불러온다.
    if name in __all__:
        from app.services.search.hybrid import HybridSearch, cosine_similarity
        return {"HybridSearch": HybridSearch, "cosine_similarity": cosine_similarity}[name]
    raise AttributeError(name)
