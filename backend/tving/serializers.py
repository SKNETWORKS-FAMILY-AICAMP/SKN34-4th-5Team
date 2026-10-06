from rest_framework import serializers

from baseball.models import ProviderSnapshot


class SourceSerializer(serializers.Serializer):
    name = serializers.CharField()
    url = serializers.URLField()


class ResourceMetadataSerializer(serializers.Serializer):
    fetchedAt = serializers.DateTimeField()
    providerFetchedAt = serializers.DateTimeField()
    updatedAt = serializers.DateTimeField(allow_null=True)
    lastSyncedAt = serializers.DateTimeField(allow_null=True)
    source = SourceSerializer()
    stale = serializers.BooleanField()
    warning = serializers.CharField(allow_null=True)


class IndividualRankingsSerializer(serializers.Serializer):
    pitchers = serializers.ListField(child=serializers.DictField())
    hitters = serializers.ListField(child=serializers.DictField())


class StandingsMetadataSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("ready", "pending"))
    date = serializers.DateField(allow_null=True)
    updatedAt = serializers.DateTimeField(allow_null=True)
    isFallback = serializers.BooleanField()


class RankingStatusSerializer(serializers.Serializer):
    pitchers = serializers.ChoiceField(choices=("ready", "pending"))
    hitters = serializers.ChoiceField(choices=("ready", "pending"))


class ScheduleDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    status = serializers.ChoiceField(choices=("ready", "empty", "pending", "error"))
    gameCount = serializers.IntegerField(min_value=0)


class DailyDataSerializer(ResourceMetadataSerializer):
    fetchedAt = serializers.DateTimeField(allow_null=True)
    providerFetchedAt = serializers.DateTimeField(allow_null=True)
    date = serializers.DateField()
    games = serializers.ListField(child=serializers.DictField())
    scheduleStatus = serializers.ChoiceField(choices=("ready", "empty", "pending", "error"))
    standingsMeta = StandingsMetadataSerializer()
    individualRankingsStatus = RankingStatusSerializer()
    standings = serializers.ListField(child=serializers.DictField())
    individualRankings = IndividualRankingsSerializer()
    sourceUpdatedAt = serializers.DateTimeField(allow_null=True)
    nextCheckAt = serializers.DateTimeField(allow_null=True)
    mode = serializers.ChoiceField(choices=("fixed-interval",))


class MonthDataSerializer(ResourceMetadataSerializer):
    fetchedAt = serializers.DateTimeField(allow_null=True)
    providerFetchedAt = serializers.DateTimeField(allow_null=True)
    year = serializers.IntegerField()
    month = serializers.RegexField(r"^\d{4}-\d{2}$")
    today = serializers.DateField()
    games = serializers.ListField(child=serializers.DictField())
    days = ScheduleDaySerializer(many=True)
    loading = serializers.BooleanField()


class DetailProgressSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=("idle", "partial"))
    generation = serializers.CharField(allow_null=True)
    startedAt = serializers.DateTimeField(allow_null=True)
    completedAt = serializers.DateTimeField(allow_null=True)
    teamTotal = serializers.IntegerField()
    teamDone = serializers.IntegerField()
    athleteTotal = serializers.IntegerField()
    athleteDone = serializers.IntegerField()
    failures = serializers.ListField(child=serializers.CharField())
    strategy = serializers.ChoiceField(choices=("on-demand",))


class TeamDataSerializer(ResourceMetadataSerializer):
    code = serializers.CharField()
    teamName = serializers.CharField()
    shortName = serializers.CharField()
    teamImageUrl = serializers.URLField(allow_null=True)
    backgroundImage = serializers.URLField(allow_null=True)
    seasonTitle = serializers.CharField()
    mainRecords = serializers.ListField(child=serializers.DictField())
    boxRecords = serializers.ListField(child=serializers.DictField())
    schedule = serializers.ListField(child=serializers.DictField())
    rankings = serializers.DictField()
    rosters = serializers.DictField()
    shortcuts = serializers.ListField(child=serializers.DictField())
    collecting = serializers.BooleanField()
    progress = DetailProgressSerializer()


class AthleteDataSerializer(ResourceMetadataSerializer):
    profile = serializers.DictField()
    seasonTitle = serializers.CharField()
    seasonRecords = serializers.ListField(child=serializers.DictField())
    careerTitle = serializers.CharField()
    careerColumns = serializers.ListField(child=serializers.DictField())
    careerRows = serializers.ListField(child=serializers.DictField())
    collecting = serializers.BooleanField()
    progress = DetailProgressSerializer()


class ErrorResponseSerializer(serializers.Serializer):
    data = serializers.JSONField(allow_null=True)
    error = serializers.CharField(allow_null=True)
    code = serializers.CharField(required=False)


class DailyResponseSerializer(ErrorResponseSerializer):
    data = DailyDataSerializer(allow_null=True)


class MonthResponseSerializer(ErrorResponseSerializer):
    data = MonthDataSerializer(allow_null=True)


class TeamResponseSerializer(ErrorResponseSerializer):
    data = TeamDataSerializer(allow_null=True)


class AthleteResponseSerializer(ErrorResponseSerializer):
    data = AthleteDataSerializer(allow_null=True)


class StatusResponseSerializer(ErrorResponseSerializer):
    data = DetailProgressSerializer(allow_null=True)


class SnapshotSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    resourceKind = serializers.ChoiceField(choices=ProviderSnapshot.KINDS)
    resourceKey = serializers.CharField()
    payload = serializers.JSONField()
    sourceFetchedAt = serializers.DateTimeField()
    lastSyncedAt = serializers.DateTimeField(allow_null=True)
    createdAt = serializers.DateTimeField()
    updatedAt = serializers.DateTimeField()


class SnapshotMutationSerializer(serializers.Serializer):
    resourceKind = serializers.ChoiceField(choices=ProviderSnapshot.KINDS, required=False)
    resourceKey = serializers.CharField(max_length=16, required=False)
    payload = serializers.JSONField()
    sourceFetchedAt = serializers.DateTimeField()


class SnapshotResponseSerializer(ErrorResponseSerializer):
    data = SnapshotSerializer(allow_null=True)


class SnapshotPageSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    pageSize = serializers.IntegerField()
    results = SnapshotSerializer(many=True)


class SnapshotPageResponseSerializer(ErrorResponseSerializer):
    data = SnapshotPageSerializer(allow_null=True)


class EntityPageSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    pageSize = serializers.IntegerField()
    results = serializers.ListField(child=serializers.DictField())


class EntityPageResponseSerializer(ErrorResponseSerializer):
    data = EntityPageSerializer(allow_null=True)


class PlayerEntitySerializer(serializers.Serializer):
    externalCode = serializers.CharField()
    teamCode = serializers.CharField()
    name = serializers.CharField()
    imageUrl = serializers.URLField(allow_null=True)
    positions = serializers.ListField(child=serializers.CharField())
    backNumber = serializers.CharField()
    profileLastSyncedAt = serializers.DateTimeField(allow_null=True)


class PlayerMutationSerializer(serializers.Serializer):
    externalCode = serializers.RegexField(r"^\d{4,12}$", required=False)
    teamCode = serializers.ChoiceField(choices=("SS", "KT", "LG", "HT", "OB", "NC", "HH", "LT", "SK", "WO"))
    name = serializers.CharField(max_length=80)


class PlayerEntityResponseSerializer(ErrorResponseSerializer):
    data = PlayerEntitySerializer(allow_null=True)
