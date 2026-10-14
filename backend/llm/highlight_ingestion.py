"""Explicit fresh full-transcript separate ingestion; no generation or production DB writes."""
from bisect import bisect_left, bisect_right
import hashlib
import html
import json
import math
from pathlib import Path
import random
import re
import subprocess
import time
import uuid
import httpx
from google.genai import errors
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from copy import copy, deepcopy
from threading import Lock, Event, local
from . import highlight_multimodal as mm

number, timestamp, duration, media, vector = mm.number, mm.timestamp, mm.duration, mm.media, mm.vector
DIM, COLLECTION, MODALITIES = mm.DIM, mm.COLLECTION, mm.MODALITIES


def cut(source, row, target):
    source = Path(source).resolve(strict=True)
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    video, audio = target / (row['id'] + '.mp4'), target / (row['id'] + '.wav')
    span = row['end'] - row['start']
    # Reuse the prior accurate input-seek extraction; original image-only cut is unchanged.
    common = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', str(row['start']), '-i', str(source), '-t', str(span)]
    subprocess.run(common + ['-map', '0:v:0', '-an', '-vf', 'scale=640:-2', '-c:v', 'libx264', str(video)], check=True)
    subprocess.run(common + ['-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000', str(audio)], check=True)
    return video, audio

def transcript(path, duration):
    """Real WebVTT only; preserve cue times (rolling ASR phrases may repeat)."""
    rows = []
    for block in re.split(r"\n\n+", Path(path).read_text().replace("\r", "")):
        lines = block.splitlines()
        timing = next((i for i, line in enumerate(lines) if " --> " in line), None)
        if timing is None:
            continue
        left, right = lines[timing].split(" --> ")
        start, end = timestamp(left), timestamp(right.split()[0])
        # YouTube auto-caption final cue may extend up to 2s past EOF; clip it.
        if not 0 <= start < duration or not start < end <= duration + 2:
            raise ValueError("Transcript outside original media")
        text = html.unescape(re.sub(r"<[^>]*>", "", " ".join(lines[timing + 1:]))).strip()
        if text:
            rows.append({"start": start, "end": min(end, duration), "text": text,
                         "index": len(rows), "raw": block.strip(), "source_end": end})
    if not rows:
        raise ValueError("No timestamped transcript")
    return sorted(rows, key=lambda r: r["start"])

def transcript_segments(video_id, cues, duration):
    """Cue-aligned 15–25s spans, at least 5s overlap, with a feasible full tail."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("Invalid video identity")
    duration = number(duration)
    cues = sorted(cues, key=lambda cue: number(cue["start"]))
    for cue in cues:
        start, end = number(cue["start"]), number(cue["end"])
        if not 0 <= start < end <= duration or end - start > 120:
            raise ValueError("Transcript cue exceeds provider limit or media bounds")
    if not cues or max(c["end"] for c in cues) - cues[0]["start"] < 15:
        raise ValueError("Transcript coverage cannot meet minimum 15 seconds")
    starts = sorted({c["start"] for c in cues})
    ends = sorted({c["end"] for c in cues})
    # Keep only boundaries that can reach the final cue. Greedy starts alone
    # strand a long cue/tail even when an earlier row could safely extend.
    feasible = {}
    for i in range(len(starts) - 1, -1, -1):
        start = starts[i]
        options = []
        for end in ends[bisect_left(ends, start + 15):bisect_right(ends, start + 25)]:
            if not 15 <= end - start <= 25:
                continue
            following = starts[i + 1:bisect_right(starts, end - 5)]
            if end == ends[-1] or any(feasible.get(s) and feasible[s][-1] > end for s in following):
                options.append(end)
        feasible[start] = options
    if not feasible[starts[0]]:
        raise ValueError('Transcript cannot meet 15–25s/5s at actual cue boundaries')
    rows, start, end = [], starts[0], -1
    while end < ends[-1]:
        options = [e for e in feasible[start] if e > end]
        end = options[0]
        for candidate in options:
            following = starts[bisect_right(starts, start):bisect_right(starts, candidate - 5)]
            if candidate == ends[-1] or (following and feasible[following[-1]] and feasible[following[-1]][-1] > candidate):
                end = candidate
                break
        row = {"video_id": video_id, "start": start, "end": end}
        describe_row(row, cues)
        rows.append(row)
        if end != ends[-1]:
            start = next(s for s in reversed(starts[bisect_right(starts, start):bisect_right(starts, end - 5)])
                         if feasible.get(s) and feasible[s][-1] > end)
    return rows


def describe_row(row, cues):
    row['cues'] = [dict(c) for c in cues if c['start'] < row['end'] and c['end'] > row['start']]
    texts, previous = [], None
    for cue in row['cues']:
        tokens, overlap = cue['text'].split(), 0
        if previous is not None and cue['start'] <= previous['end'] + .001:
            old = previous['text'].split()
            overlap = next((n for n in range(min(len(old), len(tokens)), 0, -1) if old[-n:] == tokens[:n]), 0)
        if tokens[overlap:]:
            texts.append(' '.join(tokens[overlap:]))
        previous = cue
    row['text'] = ' '.join(texts)
    row['id'] = str(uuid.uuid5(uuid.NAMESPACE_URL,
        f"transcript-separate-v1:{row['video_id']}:{row['start']:.3f}:{row['end']:.3f}"))


def extract_row(plan, original, target):
    """Only an observed under15 clip may advance to a real cue end within25s."""
    row = deepcopy(original)
    next_end = next((r['end'] for r in plan['rows'] if r['start'] > row['start']), float('inf'))
    evidence = []
    while True:
        video, audio = cut(plan['media'], row, target)
        spans = {'video': duration(video), 'audio': duration(audio)}
        if min(spans.values()) >= 15:
            if evidence:
                row['extraction_adjustment'] = {'planned_id': original['id'], 'planned_end': original['end'],
                                                'reason': 'actual_under15_next_source_cue_end', 'attempts': evidence}
            return row, video, audio, spans
        evidence.append({'id': row['id'], 'end': row['end'], 'actual_lengths': spans})
        ends = sorted({c['end'] for c in plan['cues'] if row['end'] < c['end'] < next_end and c['end'] - row['start'] <= 25})
        if not ends:
            exc = ValueError('Actual separate modality cannot meet15 at valid source cue end')
            mode = min(spans, key=spans.get)
            exc.ingestion_evidence = {'id': row['id'], 'video_id': row['video_id'], 'modality': mode,
                'actual_duration': spans[mode], 'minimum': 15, 'reason': 'no_feasible_source_cue_end', 'attempts': evidence}
            raise exc
        row['end'] = ends[0]
        describe_row(row, plan['cues'])

def transcript_gaps(cues, duration):
    gaps, end = [], 0.
    for cue in sorted(cues, key=lambda c: c["start"]):
        if cue["start"] > end:
            gaps.append([end, cue["start"]])
        end = max(end, cue["end"])
    if end < duration:
        gaps.append([end, duration])
    return gaps

def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False))
    temporary.replace(path)

class IngestionAttempts:
    """One admission per actual HTTP attempt, persisted before sending."""
    def __init__(self, maximum, path=None, *, clock=time.monotonic):
        if type(maximum) is not int or maximum < 1:
            raise ValueError('Invalid explicit HTTP attempt cap')
        self.maximum, self.path = maximum, path
        self.counts = {}
        self.lock, self.stop = Lock(), Event()
        self.clock = clock
        self.updated, self.tokens, self.cooldown_until = clock(), 8., 0.

    def pause(self, delay):
        if self.stop.is_set() or self.stop.wait(delay):
            raise RuntimeError('Ingestion provider attempt budget stopped')

    def cooldown(self, delay):
        with self.lock:
            self.cooldown_until = max(self.cooldown_until, self.clock() + delay)

    def acquire(self, operation):
        while True:
            with self.lock:
                if self.stop.is_set() or sum(self.counts.values()) >= self.maximum:
                    raise RuntimeError('Ingestion provider attempt budget exhausted or stopped')
                now = self.clock()
                self.tokens = min(8., self.tokens + max(0., now - self.updated) * 50.)
                self.updated = max(now, self.updated)
                delay = max(0., self.cooldown_until - now, (1. - self.tokens) / 50.)
                if delay <= 1e-9:
                    self.tokens -= 1.
                    self.counts[operation] = self.counts.get(operation, 0) + 1
                    if self.path:
                        write_json(self.path, self.counts)
                    return
            self.pause(delay)

def transcript_plan(manifest):
    if not isinstance(manifest, list) or not 1 <= len(manifest) <= 10:
        raise ValueError("Transcript ingestion requires 1–10 explicit sources")
    plans, seen = [], set()
    for item in manifest:
        vid = item["video_id"]
        if vid in seen:
            raise ValueError("Duplicate source")
        seen.add(vid)
        source = Path(item["media"]).resolve(strict=True)
        caption = Path(item["transcript"]).resolve(strict=True)
        seconds = duration(source)
        cues = transcript(caption, seconds)
        rows = transcript_segments(vid, cues, seconds)
        plans.append({"video_id": vid, "media": str(source), "transcript": str(caption),
                      "duration": seconds, "cue_count": len(cues), "cues": cues, "rows": rows,
                      "caption_sha256": hashlib.sha256(caption.read_bytes()).hexdigest(),
                      "media_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                      "uncovered_intervals": transcript_gaps(cues, seconds),
                      "provenance": item["provenance"]})
    return plans

class BoundedGoogle(mm.Google):
    """All official SDK attempts share the existing durable admission and cooldown."""
    def _request(self, operation, invoke):
        budget = self.attempt_budget
        for attempt in range(4):
            budget.acquire(operation)
            self.call_attempts[operation] = self.call_attempts.get(operation, 0) + 1
            try:
                result = invoke()
                if operation == 'embedContent':
                    # LangChain text and SDK media responses expose different metadata.
                    self.last_embedding_metadata = dict(getattr(self._verification, 'embedding_metadata', {}))
                return result
            except Exception as wrapped:
                exc, details = mm.provider_error(wrapped)
                retry_delay = 0.
                if isinstance(exc, errors.APIError):
                    error = exc.details if isinstance(exc.details, dict) else {}
                    error = error.get('error', error)
                    for item in error.get('details', []):
                        if isinstance(item, dict) and str(item.get('@type', '')).endswith('/google.rpc.RetryInfo'):
                            value = item.get('retryDelay', '')
                            if isinstance(value, str) and re.fullmatch(r'\d+(?:\.\d+)?s', value):
                                retry_delay = max(retry_delay, number(float(value[:-1])))
                    if exc.code in (429, 503):
                        response = getattr(exc, 'response', None)
                        header = response.headers.get('Retry-After') if response is not None else None
                        if header:
                            try:
                                seconds = float(header)
                            except ValueError:
                                try:
                                    seconds = parsedate_to_datetime(header).timestamp() - time.time()
                                except (ValueError, TypeError, OverflowError):
                                    seconds = 0.
                            if math.isfinite(seconds):
                                retry_delay = max(retry_delay, seconds)
                        delay = retry_delay if retry_delay > 0 else 2. ** (attempt + 1) + random.uniform(0., 1.)
                        if exc.code == 429:
                            budget.cooldown(delay)
                        if attempt < 3:
                            budget.pause(delay)
                            continue
                    raise RuntimeError(f"Google {operation} HTTP {exc.code} " + json.dumps(details)) from None
                if isinstance(exc, (httpx.TransportError, TimeoutError, ConnectionError)):
                    if attempt < 3:
                        budget.pause(2. ** (attempt + 1) + random.uniform(0., 1.))
                        continue
                    raise RuntimeError(f"Google {operation} network failure") from None
                raise


def validate_ingestion(specialist, *, pending=False):
    """Full separate readback; legacy image-only corpora retain their original validator."""
    root, rows = specialist.root, specialist.rows
    report = json.loads((root / ('ingestion_pending.json' if pending else 'ingestion_complete.json')).read_text())
    if (report.get('status') != ('planned' if pending else 'complete') or report.get('segments') != len(rows)
            or report.get('index_mode') != 'separate' or report.get('dimensions') != DIM):
        raise ValueError('Incomplete separate transcript corpus')
    planned = json.loads((root / 'ingestion_pending.json').read_text())
    if planned.get('status') != 'planned' or any(report.get(k) != v for k, v in planned.items() if k != 'status'):
        raise ValueError('Invalid separate completion/plan identity')
    per_video = report.get('per_video')
    if (not isinstance(per_video, dict) or set(per_video) != {row['video_id'] for row in rows.values()}
            or report.get('videos') != len(per_video)
            or any(per_video[vid].get('segments') != sum(row['video_id'] == vid for row in rows.values()) for vid in per_video)):
        raise ValueError('Invalid separate full-source coverage')
    attempts = json.loads((root / 'ingestion_attempts.json').read_text())
    cap = report.get('max_calls')
    if (type(cap) is not int or cap < len(rows) * 6 or not isinstance(attempts, dict)
            or set(attempts) != {'countTokens', 'embedContent'}
            or any(type(n) is not int or not len(rows) * 3 <= n <= len(rows) * 12 for n in attempts.values())
            or sum(attempts.values()) > cap or (not pending and report.get('call_attempts') != attempts)):
        raise ValueError('Invalid separate HTTP attempt ledger')
    probes = json.loads((root / 'embedding_probe.json').read_text())
    if not isinstance(probes, list) or len(probes) != len(rows) or {p.get('id') for p in probes} != set(rows):
        raise ValueError('Invalid separate probe identities')
    for probe in probes:
        row = rows[probe['id']]
        span = number(row['end']) - number(row['start'])
        provenance = row.get('provenance')
        if (not isinstance(provenance, dict)
                or any(not isinstance(provenance.get(k), str) or not re.fullmatch(r'[0-9a-f]{64}', provenance[k])
                       or provenance[k] != per_video[row['video_id']].get(k) for k in ('caption_sha256', 'media_sha256'))):
            raise ValueError('Invalid separate row source provenance')
        if (not 15 <= span <= 25 or probe.get('readback') is not True or probe.get('video_id') != row['video_id']
                or probe.get('dimensions') != {m: DIM for m in MODALITIES}
                or not isinstance(probe.get('tokens'), dict) or set(probe['tokens']) != set(MODALITIES)
                or any(type(n) is not int or not 0 < n <= 8192 for n in probe['tokens'].values())):
            raise ValueError('Invalid separate modality probe')
        for mode, suffix, maximum in (('video', '.mp4', 25), ('audio', '.wav', 25)):
            path = root / 'clips' / (row['id'] + suffix)
            seconds = number(probe.get(mode + '_duration'))
            actual = number(duration(path))
            if (not 15 <= seconds <= maximum or abs(seconds - span) > .25
                    or not 15 <= actual <= maximum or abs(actual - span) > .25
                    or hashlib.sha256(path.read_bytes()).hexdigest() != row.get(mode + '_sha256')):
                raise ValueError('Invalid separate clip hash/duration')
    return report


def ingest_transcripts(manifest, workspace, *, dry_run=False, max_calls=None, google=None, readback=False, workers=8):
    """One fresh corpus, bounded extraction/provider jobs, one owning DB writer."""
    root = Path(workspace).resolve()
    if readback:
        if not root.is_dir() or not (root / 'ingestion_complete.json').is_file():
            raise ValueError('Incomplete separate transcript corpus')
        specialist = mm.Specialist(root, None)
        try:
            return validate_ingestion(specialist)
        finally:
            specialist.close()
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError('Transcript workers must be 1–8')
    plans = transcript_plan(manifest)
    count = sum(len(p['rows']) for p in plans)
    report = {'status': 'planned', 'index_mode': 'separate', 'videos': len(plans), 'segments': count,
              'model': 'gemini-embedding-2', 'dimensions': DIM,
              'count_tokens_calls': count * 3, 'embedding_requests': count * 3,
              'max_provider_attempts': count * 24, 'cost_usd': None,
              'provider_rpm': 3000, 'provider_burst': 8, 'max_retries': 3, 'workers': workers,
              'parser_policy': 'vtt-cue-min15-max25-overlap5-v3',
              'plan_sha256': hashlib.sha256(json.dumps(plans, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
              'per_video': {p['video_id']: {k: p[k] for k in ('duration', 'cue_count', 'caption_sha256', 'media_sha256', 'uncovered_intervals')}
                            | {'segments': len(p['rows']), 'min_span': min(r['end'] - r['start'] for r in p['rows']),
                               'max_span': max(r['end'] - r['start'] for r in p['rows'])} for p in plans}}
    if dry_run:
        return report
    if type(max_calls) is not int or max_calls < count * 6:
        raise ValueError('Explicit --transcript-max-calls must cover 6 HTTP/row; retries consume the same cap (not USD)')
    if root.exists():
        raise ValueError('Use a NEW separate corpus path; no overwrite or resume')
    provider = google or BoundedGoogle()
    from qdrant_client import models
    # Exclusive creation fails closed if another run created this path since preflight.
    root.mkdir(parents=True)
    report['max_calls'] = max_calls
    write_json(root / 'ingestion_pending.json', report)
    specialist = None
    budget = IngestionAttempts(max_calls, root / 'ingestion_attempts.json')
    write_json(budget.path, {})
    try:
        specialist = mm.Specialist(root, provider, ingesting=True)
        specialist.db.create_collection(COLLECTION, vectors_config={m: models.VectorParams(size=DIM, distance=models.Distance.COSINE) for m in MODALITIES})
        write_json(root / 'index_mode.json', 'separate')
        (root / 'web.lock').touch()
        jobs = iter((plan, original) for plan in plans for original in plan['rows'])
        probes = []
        def prepare(job):
            plan, original = job
            row, video, audio, spans = extract_row(plan, original, root / 'clips')
            row['provenance'] = {'source': plan['provenance'], 'caption_sha256': plan['caption_sha256'], 'media_sha256': plan['media_sha256']}
            worker = copy(provider)
            worker.call_attempts, worker.last_embedding_metadata = {}, {}
            worker._verification = local()
            worker.attempt_budget = budget
            for mode, maximum in (('video', 25), ('audio', 25)):
                if not 15 <= spans[mode] <= maximum or abs(spans[mode] - (row['end'] - row['start'])) > .25:
                    exc = ValueError('Actual separate modality duration outside 15–25s bounds')
                    exc.ingestion_evidence = {'id': row['id'], 'video_id': row['video_id'], 'start': row['start'], 'end': row['end'],
                        'modality': mode, 'actual_duration': spans[mode], 'minimum': 15, 'maximum': maximum,
                        'reason': 'actual_duration_outside_bounds'}
                    raise exc
            values, tokens, metadata = {}, {}, {}
            for mode, part in zip(MODALITIES, (media(video, 'video/mp4'), media(audio, 'audio/wav'), {'text': row['text']})):
                counted = worker.call('gemini-embedding-2', 'countTokens', {'contents': [{'parts': [part]}]})
                amount = counted.get('totalTokens')
                if type(amount) is not int or not 0 < amount <= 8192:
                    exc = ValueError('Separate modality exceeds token limit or count unavailable')
                    exc.ingestion_evidence = {'id': row['id'], 'video_id': row['video_id'], 'start': row['start'], 'end': row['end'],
                        'modality': mode, 'token_type': type(amount).__name__, 'tokens': amount if type(amount) is int else None,
                        'reason': 'over_limit' if type(amount) is int and amount > 8192 else 'invalid_or_missing_count'}
                    raise exc
                tokens[mode] = amount
                values[mode] = vector(worker.embed(part))
                metadata[mode] = dict(worker.last_embedding_metadata)
            row['video_sha256'] = hashlib.sha256(video.read_bytes()).hexdigest()
            row['audio_sha256'] = hashlib.sha256(audio.read_bytes()).hexdigest()
            probe = {'id': row['id'], 'video_id': row['video_id'], 'tokens': tokens,
                     'video_duration': spans['video'], 'audio_duration': spans['audio'],
                     'dimensions': {m: DIM for m in MODALITIES}, 'readback': True, 'provider_metadata': metadata}
            return row, values, probe
        failure, failures = None, []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            inflight = set()
            def enqueue():
                while len(inflight) < workers and failure is None and not budget.stop.is_set():
                    job = next(jobs, None)
                    if job is None:
                        break
                    inflight.add(pool.submit(prepare, job))
            enqueue()
            while inflight:
                try:
                    done, _ = wait(inflight, return_when=FIRST_COMPLETED)
                    for future in done:
                        inflight.remove(future)
                        try:
                            row, values, probe = future.result()
                        except BaseException as exc:
                            failure = failure or exc
                            budget.stop.set()
                            failures.append({'error_type': type(exc).__name__, 'evidence': getattr(exc, 'ingestion_evidence', {})})
                            continue
                        specialist.db.upsert(COLLECTION, [models.PointStruct(id=row['id'], vector=values, payload=row)], wait=True)
                        saved = specialist.db.retrieve(COLLECTION, [row['id']], with_vectors=True)[0]
                        if (saved.payload != row or not isinstance(saved.vector, dict) or set(saved.vector) != set(MODALITIES)
                                or any(len(vector(v)) != DIM for v in saved.vector.values())):
                            raise ValueError('Separate readback failed')
                        specialist.rows[row['id']] = row
                        specialist.sql.execute('INSERT INTO transcript VALUES (?, ?)', (row['id'], row['text']))
                        specialist.sql.commit()
                        write_json(root / 'segments.json', specialist.rows)
                        probes.append(probe)
                        write_json(root / 'embedding_probe.json', probes)
                    enqueue()
                except BaseException:
                    budget.stop.set()
                    raise
        if failure is not None:
            report['failures'] = failures
            raise failure
        specialist.validate_corpus(ingesting=True)
        validate_ingestion(specialist, pending=True)
        report.update(status='complete', call_attempts=dict(budget.counts), readback=True)
        write_json(root / 'ingestion_complete.json', report)
        return report
    except BaseException as exc:
        budget.stop.set()
        report.update(status='failed', indexed=len(specialist.rows) if specialist else 0,
                      error_type=type(exc).__name__, call_attempts=dict(budget.counts))
        write_json(root / 'ingestion_failure.json', report)
        raise
    finally:
        if specialist is not None:
            specialist.close()


def add_ingestion_arguments(parser):
    parser.add_argument('--transcript-manifest', help='Explicit original media/VTT/provenance manifest; separate opt-in')
    parser.add_argument('--transcript-corpus', help='Fresh separate named-vector corpus path')
    parser.add_argument('--transcript-max-calls', type=int, help='All HTTP attempts including counts/retries, not a USD budget')
    parser.add_argument('--transcript-workers', type=int, default=8, help='1–8 shared3000RPM/burst8 workers; default8')
    parser.add_argument('--transcript-readback', action='store_true', help='Full completed separate corpus readback; no API')


def run_ingestion_command(options):
    """Both official collect/index commands share this exact explicit path."""
    active = options['transcript_manifest'] or options['transcript_readback']
    if not active:
        if options['transcript_corpus'] or options['transcript_max_calls'] is not None or options['transcript_workers'] != 8:
            raise ValueError('--transcript-manifest or --transcript-readback required')
        return None
    if (not options['transcript_corpus'] or options.get('video_ids')
            or options.get('refill_players') or options.get('reanalyze') or options.get('no_index')
            or options.get('team') or options.get('since')
            or (options['transcript_manifest'] and options['transcript_readback'])):
        raise ValueError('Transcript path uses only explicit manifest/corpus; no legacy filters or mixed modes')
    manifest = [] if options['transcript_readback'] else json.loads(Path(options['transcript_manifest']).read_text())
    return ingest_transcripts(manifest, options['transcript_corpus'], dry_run=options['dry_run'],
                              max_calls=options['transcript_max_calls'], workers=options['transcript_workers'],
                              readback=options['transcript_readback'])

