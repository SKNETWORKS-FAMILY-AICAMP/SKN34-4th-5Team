"""community 체인: 커뮤니티 게시글 / 팬 반응 / 승부예측 / 팬 투표 조회."""
from .common import build_domain_chain

RULES = """커뮤니티 담당이다. 게시글은 search_community_posts, 승부예측·팬 투표는 get_prediction_games 결과만 근거로 답한다.
팬 투표 집계는 실제 승리 확률이 아니라고 밝히고, 게시글 내용은 개인 의견으로 전한다."""

CATEGORIES = None  # 커뮤니티는 문서 RAG 없이 DB 도구만 쓴다

TOOLS = (
    "search_community_posts", "get_prediction_games", "get_games",
)

community_chain = build_domain_chain(RULES, CATEGORIES, TOOLS)
