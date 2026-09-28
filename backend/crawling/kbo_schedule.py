from common.cron_tving import collect_schedule

if __name__ == "__main__":
    games, vectors = collect_schedule()
    print(f"일정 적재 완료: 경기={games}, 벡터={vectors}")
