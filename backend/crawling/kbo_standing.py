from common.cron_tving import collect_standing

if __name__ == "__main__":
    teams, vectors = collect_standing()
    print(f"순위 적재 완료: 팀={teams}, 벡터={vectors}")
