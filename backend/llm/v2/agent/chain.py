from langchain_core.runnables import RunnableBranch, RunnablePassthrough
from .classifier import guard_question, route_agent
from llm.enum import AgentType
from llm.v1.rag.persona import FIXED
from .baseball_chain import baseball_chain
from .community_chain import community_chain
from .course_chain import course_chain
from .stadium_chain import stadium_chain
from .travel_chain import travel_chain

# 에이전트 분기 체인
agent_branch = RunnableBranch(
    (
        lambda x: x["route"] == AgentType.BASEBALL.value,
        baseball_chain,  # 경기 일정 / 결과 / 순위 / 선수 / 야구 규칙
    ),
    (
        lambda x: x["route"] == AgentType.STADIUM.value,
        stadium_chain,  # 구장 정보 / 티켓 / 가격 / 좌석 / 반입 / 재입장 / 주차 / 시설 / 구장 내 먹거리
    ),
    (
        lambda x: x["route"] == AgentType.TRAVEL.value,
        travel_chain,  # 구장 주변 맛집 / 카페 / 숙박 / 관광 / 산책 / 공원 / 실내 놀거리 / 편의점
    ),
    (
        lambda x: x["route"] == AgentType.COURSE.value,
        course_chain,  # 경기 전후 코스 / 하루 일정 / 이동 동선 / 장소 조합
    ),
    (
        lambda x: x["route"] == AgentType.COMMUNITY.value,
        community_chain,  # 커뮤니티 게시글 / 팬 반응 / 승부예측 / 팬 투표 조회
    ),

    # 위 어떤 도메인에도 해당하지 않을 때 실행되는 기본 분기
    lambda _: "처리할 수 없는 요청입니다.",
)

# 메인 체인: 입력 {"question", "chat_history"?, "context"?} → 가드 → 라우팅 → 도메인 에이전트
# context 는 선택 사항이고 {"stadium", "intent", "origin"} 중 있는 것만 채워져 온다 (없어도 이전과 동일하게 동작).
chain = (
    RunnablePassthrough.assign(
        is_pass=lambda state: guard_question(
            state["question"], state.get("chat_history"), state.get("context"),
        )
    )
    | RunnableBranch(
        # PASS면 담당 도메인을 분류해 다음 에이전트 분기로
        (
            lambda state: state["is_pass"],
            RunnablePassthrough.assign(
                route=lambda state: route_agent(
                    state["question"], state.get("chat_history"), state.get("context"),
                )["route"]
            )
            | agent_branch, # 분야마다 에이전트를 동적할당
        ),
        # else = NON_PASS
        lambda _: FIXED["scope"],
    )
)
