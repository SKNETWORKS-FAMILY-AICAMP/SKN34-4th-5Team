"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { loadKakaoMaps, type KakaoMap, type KakaoMaps, type KakaoOverlay } from "@/lib/kakao-maps";
import { collectNearbyPlaces, resolveStadium } from "@/lib/nearby-search";
import { distanceMeters, MAX_ROUTE_STOPS, mergePlaces, moveStop, NEARBY_RADIUS, NEARBY_SEARCHES, PLACE_CATEGORIES, sameStop, visiblePlaces, type CategoryFilter, type NearbyPlace, type NearbyStadium, type PlaceCategory } from "@/lib/nearby-places";
import type { RouteStop, TripRoute } from "@/lib/routes";
import { coursePointLabel, renumberMapPoints, undoDrawnPoint, withUntrackedPoints } from "@/lib/drawn-course";
import { createStopHistory, observeStops, redoStops, undoStops } from "@/lib/stop-history";
import { GUIDE_ORIGIN_SET, GUIDE_ORIGIN_TARGET, type GuidePoint } from "@/lib/route-guide-events";
import { CourseTravelPanel, useCourseDirections, useTravelOverlay } from "./course-travel";
import { StadiumParkingMapDialog } from "./stadium-parking-map-dialog";
import type { TourResult } from "@/lib/tour-places";
import { fetchTourPlaces } from "@/lib/tour-api";
import { createClientId } from "@/lib/client-id";
import type { TravelMode } from "@/lib/course-directions";

type PlannerProps = {
  plannerMode?: "places" | "draw";
  stadium: NearbyStadium; stops: RouteStop[]; onChange: (stops: RouteStop[]) => void;
  initialStart?: TripRoute["start"]; onStartChange: (start: TripRoute["start"]) => void;
  initialTravelMode?: TravelMode; onTravelModeChange?: (mode: TravelMode) => void;
  /** 바깥(챗봇 코스 담기)에서 이동수단을 바꿀 때 */
  travelMode?: TravelMode;
  /** 챗봇 코스를 담을 때마다 version 이 오른다 → 내 코스 탭을 열고 코스 전체를 보여준다 */
  courseApplied?: { version: number; stadiumCode: string } | null;
  courseName: string; onCourseNameChange: (name: string) => void;
  onSaveCourse: () => Promise<void>; saving: boolean; saveError: string;
  startWithAllPlaces?: boolean;
  onCompletionChange?: (completed: boolean) => void;
  /** When true, press "코스 완성" once the stops can be completed, then report back. */
  autoComplete?: boolean;
  /** 가이드용 샘플 화면의 지도일 때만 가이드의 출발점 안내를 받는다 */
  guide?: boolean;
  onAutoCompleted?: () => void;
  /** Non-empty while the chatbot is building a new course; unlocks a completed course. */
  unlockRequest?: string;
};
const distanceLabel = (distance: number) => distance < 1000 ? `${Math.round(distance)}m` : `${(distance / 1000).toFixed(1)}km`;
const placeLink = (place: RouteStop) => place.placeId && /^\d+$/.test(place.placeId) ? `https://place.map.kakao.com/${place.placeId}` : `https://map.kakao.com/link/map/${encodeURIComponent(place.name)},${place.lat},${place.lng}`;

function CategoryIcon({ kind }: { kind: PlaceCategory }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={PLACE_CATEGORIES.find((c) => c.id === kind)!.icon} /></svg>;
}

type StepControls = { canUndo: boolean; canRedo: boolean; onUndo: () => void; onRedo: () => void };

function RouteStops({ stops, onChange, onFocus, separateStart = false, readOnly = false, steps }: { stops: RouteStop[]; onChange: PlannerProps["onChange"]; onFocus?: (stop: RouteStop) => void; separateStart?: boolean; readOnly?: boolean; steps?: StepControls }) {
  return <div className="planner-route-list">
    <div className="planner-route-heading"><strong>내가 고른 방문 순서</strong><span className="planner-route-heading-side">{steps && <span className="planner-history" role="group" aria-label="방문 순서 되돌리기">
      <button type="button" disabled={readOnly || !steps.canUndo} onClick={steps.onUndo} aria-label="되돌리기" title="되돌리기 (Ctrl+Z)">↶</button>
      <button type="button" disabled={readOnly || !steps.canRedo} onClick={steps.onRedo} aria-label="앞으로 돌리기" title="앞으로 돌리기 (Ctrl+Shift+Z)">↷</button>
    </span>}<span>{stops.length} / {MAX_ROUTE_STOPS}</span></span></div>
    {stops.length === 0 ? <div className="planner-empty"><span aria-hidden="true">출발 → 1 → 2</span><strong>첫 번째 지점을 골라보세요</strong><p>빈 지도에 직접 지점을 찍거나<br />장소 정보를 보고 ‘코스에 담기’를 누르세요.</p></div> : <ol>{stops.map((stop, index) => <li key={stop.visitId ?? `${stop.placeId ?? stop.name}:${index}`}>
      <span className="planner-stop-number">{coursePointLabel(stops, index, separateStart)}</span>
      <button type="button" className="planner-stop-name" onClick={() => onFocus?.(stop)}><strong>{stop.name}</strong><small>{stop.category}</small></button>
      <div className="planner-stop-actions">
        <button type="button" disabled={readOnly || index === 0} aria-label={`${stop.name} 위로 이동`} onClick={() => onChange(moveStop(stops, index, -1))}>↑</button>
        <button type="button" disabled={readOnly || index === stops.length - 1} aria-label={`${stop.name} 아래로 이동`} onClick={() => onChange(moveStop(stops, index, 1))}>↓</button>
        <button type="button" disabled={readOnly} aria-label={`${stop.name} 코스에서 삭제`} onClick={() => onChange(stops.filter((_, i) => i !== index))}>×</button>
      </div>
    </li>)}</ol>}
    <p className="planner-small">{readOnly ? "코스 수정을 누르면 방문 순서를 다시 바꿀 수 있어요." : "위아래 화살표로 순서를 바꿀 수 있어요."}<br />{(stops[0]?.isMapPoint || stops[0]?.isDrawnPoint) ? "출발지를 포함해 최대 12개 지점을 담을 수 있어요." : "최대 12곳까지 코스에 담을 수 있어요."}</p>
  </div>;
}

export function NearbyRoutePlanner(props: PlannerProps) {
  const [ready, setReady] = useState<{ maps: KakaoMaps; stadium: NearbyStadium } | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    loadKakaoMaps().then(async (maps) => {
      const stadium = await resolveStadium(maps, props.stadium, controller.signal);
      if (!controller.signal.aborted) setReady({ maps, stadium });
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "지도를 불러오지 못했어요.");
    });
    return () => controller.abort();
  }, [props.stadium, attempt]);
  if (ready) return <LoadedPlanner {...props} maps={ready.maps} stadium={ready.stadium} />;
  return <div className="planner-workspace">
    <div className="planner-loading" role="status">{error ? <><strong>지도를 연결하지 못했어요</strong><p>{error}</p><button type="button" className="button button-secondary" onClick={() => { setError(""); setAttempt((a) => a + 1); }}>다시 연결</button></> : <><span className="writer-spinner" aria-hidden="true" /><strong>구장 위치와 지도를 불러오고 있어요</strong><p>반경 2.5km에서 나만의 하루를 찾아보세요.</p></>}</div>
    <aside className="planner-side"><RouteStops stops={props.stops} onChange={props.onChange} /></aside>
  </div>;
}

function LoadedPlanner({ maps, plannerMode = "places", stadium, stops, onChange: onStopsChange, initialStart, onStartChange, initialTravelMode, onTravelModeChange, travelMode, courseApplied, courseName, onCourseNameChange, onSaveCourse, saving, saveError, startWithAllPlaces = false, onCompletionChange, autoComplete = false, onAutoCompleted, unlockRequest = "", guide = false }: PlannerProps & { maps: KakaoMaps }) {
  const drawOnly = plannerMode === "draw";
  const [courseCompleted, setCourseCompleted] = useState(false);
  // 사용 가이드: while set, the map only accepts a press on this dashed start-point target.
  const [guideOrigin, setGuideOrigin] = useState<GuidePoint | null>(null);
  useEffect(() => {
    if (!guide) return;
    const onTarget = (event: Event) => {
      const point = (event as CustomEvent<GuidePoint | null>).detail;
      setGuideOrigin(point && Number.isFinite(point.lat) && Number.isFinite(point.lng) ? { lat: point.lat, lng: point.lng } : null);
    };
    window.addEventListener(GUIDE_ORIGIN_TARGET, onTarget);
    return () => window.removeEventListener(GUIDE_ORIGIN_TARGET, onTarget);
  }, [guide]);
  const stopSnapshot = useRef(stops);
  useLayoutEffect(() => { stopSnapshot.current = stops; }, [stops]);
  const onChange = useCallback((next: RouteStop[]) => {
    if (courseCompleted) return;
    const numbered = renumberMapPoints(next);
    stopSnapshot.current = numbered;
    onStopsChange(numbered);
  }, [courseCompleted, onStopsChange]);
  // Undo/redo of the visit list. Adjusted during render so every incoming list is recorded once.
  const [stopHistory, setStopHistory] = useState(() => createStopHistory(stops));
  const observedHistory = observeStops(stopHistory, stops);
  if (observedHistory !== stopHistory) setStopHistory(observedHistory);
  const applyStep = (step: ReturnType<typeof undoStops>, message: string) => {
    if (courseCompleted || !step) return;
    setStopHistory(step.history);
    stopSnapshot.current = step.stops;
    onStopsChange(step.stops);
    setNotice(message);
  };
  const stepBack = () => applyStep(undoStops(observedHistory), "방문 순서를 한 단계 되돌렸어요.");
  const stepForward = () => applyStep(redoStops(observedHistory), "방문 순서를 한 단계 앞으로 돌렸어요.");
  const stepKeys = useRef({ stepBack, stepForward });
  useLayoutEffect(() => { stepKeys.current = { stepBack, stepForward }; });
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
      const key = event.key.toLowerCase();
      if (key === "z" && !event.shiftKey) { event.preventDefault(); stepKeys.current.stepBack(); }
      else if ((key === "z" && event.shiftKey) || key === "y") { event.preventDefault(); stepKeys.current.stepForward(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const [drawHistory, setDrawHistory] = useState<string[]>(() => stops.filter((stop) => (stop.isMapPoint || stop.isDrawnPoint) && stop.placeId).map((stop) => stop.visitId ?? stop.placeId!));
  useEffect(() => {
    onCompletionChange?.(courseCompleted);
    return () => onCompletionChange?.(false);
  }, [courseCompleted, onCompletionChange]);
  const [mapLocationPicking, setMapLocationPicking] = useState(false);
  const [mapLocation, setMapLocation] = useState<{ lat: number; lng: number } | null>(null);
  const [mapLocationStatus, setMapLocationStatus] = useState<"idle" | "loading" | "error">("idle");
  const [mapLocationMessage, setMapLocationMessage] = useState("");
  const mapLocationRequest = useRef(0);
  useEffect(() => () => { mapLocationRequest.current += 1; }, []);
  const travel = useCourseDirections(stops, true, initialStart, (point) => {
    const current = stopSnapshot.current;
    // An embedded origin is replaced, not retained as a new waypoint.
    const first = current[0];
    if (initialStart || !(first?.isMapPoint || first?.isDrawnPoint)) return false;
    const origin = { ...point, name: "출발지", category: "출발", placeId: "route:origin", isDrawnPoint: true };
    // A bare map point (or an earlier origin) is the start itself; a real place (e.g. from the chatbot) stays as the first visit.
    const replacesFirst = first.isMapPoint || first.placeId === "route:origin";
    onChange(replacesFirst ? [origin, ...current.slice(1)] : [origin, ...current].slice(0, MAX_ROUTE_STOPS));
    return true;
  }, initialTravelMode, onTravelModeChange, () => {
    mapLocationRequest.current += 1;
    setMapLocationPicking(false);
    setMapLocationStatus("idle");
  });
  useEffect(() => { onStartChange(travel.location ?? undefined); }, [travel.location, onStartChange]);
  const canComplete = stops.length > 0 && !travel.picking && !travel.locating && (travel.origin === "first" || Boolean(travel.location));
  const canSaveCourse = canComplete && Boolean(courseName.trim()) && !saving;
  const separateStart = travel.origin !== "first";
  const drawing = !courseCompleted && !travel.picking && !mapLocationPicking && !guideOrigin;
  const mapNode = useRef<HTMLDivElement>(null);
  useEffect(() => { if (travel.picking) mapNode.current?.scrollIntoView({ behavior: "smooth", block: "center" }); }, [travel.picking]);
  const [map, setMap] = useState<KakaoMap | null>(null);
  useTravelOverlay(map, maps, travel, { lineColor: drawOnly ? "#2868dc" : undefined });
  const [viewport, setViewport] = useState(0);
  const [listArea, setListArea] = useState<{ stadium: string; south: number; north: number; west: number; east: number } | null>(null);
  const activeListArea = listArea?.stadium === stadium.code ? listArea : null;
  const [nearPoint, setNearPoint] = useState<{ stadium: string; lat: number; lng: number; pointId?: string } | null>(null);
  const activeNearPoint = nearPoint?.stadium === stadium.code ? nearPoint : null;
  const [places, setPlaces] = useState<NearbyPlace[]>([]);
  const [categories, setCategories] = useState<PlaceCategory[]>(() => drawOnly ? [] : PLACE_CATEGORIES.map((category) => category.id));
  const allSelected = categories.length === PLACE_CATEGORIES.length;
  const preferred = useRef<CategoryFilter>("all");
  const [subcategories, setSubcategories] = useState<Partial<Record<PlaceCategory, string[]>>>({});
  const [openCategory, setOpenCategory] = useState<PlaceCategory | null>(null);
  const filtersNode = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!openCategory) return;
    const closeOutside = (event: PointerEvent) => {
      if (!filtersNode.current?.contains(event.target as Node)) setOpenCategory(null);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [openCategory]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<RouteStop | NearbyPlace | null>(null);
  const [hovered, setHovered] = useState<RouteStop | NearbyPlace | null>(null);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const holdPreview = useCallback(() => clearTimeout(hoverTimer.current), []);
  const showPreview = useCallback((place: RouteStop) => {
    clearTimeout(hoverTimer.current);
    hoverTimer.current = setTimeout(() => setHovered(place), 1000);
  }, []);
  const hidePreview = useCallback(() => { clearTimeout(hoverTimer.current); hoverTimer.current = setTimeout(() => setHovered(null), 200); }, []);
  useEffect(() => () => clearTimeout(hoverTimer.current), []);
  const [sideTab, setSideTab] = useState<"places" | "route">(() => startWithAllPlaces || stops.length === 0 ? "places" : "route");
  const [progress, setProgress] = useState({ done: false, completed: 0, failures: 0 });
  const [attempt, setAttempt] = useState(0);
  const [listLimit, setListLimit] = useState(30);
  const [notice, setNotice] = useState("");
  const [tour, setTour] = useState<{ status: TourResult["status"] | "loading"; truncated: boolean }>({ status: "loading", truncated: false });
  const [tourAttempt, setTourAttempt] = useState(0);

  // ── 챗봇 코스 담기 연동 ──
  const travelRef = useRef(travel);
  const fitRef = useRef(fitCourse);
  useLayoutEffect(() => { travelRef.current = travel; fitRef.current = fitCourse; });
  useEffect(() => {
    const current = travelRef.current;
    if (travelMode && travelMode !== current.mode) current.setMode(travelMode);
  }, [travelMode]);
  const appliedSeen = useRef(0);
  useEffect(() => {
    if (!map || !courseApplied || courseApplied.stadiumCode !== stadium.code || appliedSeen.current === courseApplied.version) return;
    appliedSeen.current = courseApplied.version;
    // 챗봇 데이터의 구장 좌표는 주소 지오코딩이라 종합운동장 한가운데로 찍히기도 한다 → 지도가 찾은 구장 위치로 맞춘다
    const current = stopSnapshot.current;
    const snapped = current.map((stop) => stop.placeId?.startsWith("chat:stadium:") && distanceMeters(stop, stadium) > 30
      ? { ...stop, name: stadium.name, lat: stadium.lat, lng: stadium.lng, address: stadium.address || stop.address }
      : stop);
    if (snapped.some((stop, index) => stop !== current[index])) {
      // 코스 완성으로 잠긴 상태여도 보정은 해야 하므로 잠금 검사가 있는 onChange 대신 원래 콜백을 쓴다
      const numbered = renumberMapPoints(snapped);
      stopSnapshot.current = numbered;
      onStopsChange(numbered);
    }
    clearTimeout(hoverTimer.current);
    setSelected(null); setHovered(null); setNearPoint(null);
    setSideTab("route"); setCourseCompleted(true);
    // 새 지점이 그려진 다음 프레임에 맞춘다 (한 번만 처리하므로 취소하지 않는다)
    requestAnimationFrame(() => fitRef.current());
  }, [map, courseApplied, stadium, onStopsChange]);

  const undoPoint = useCallback(() => {
    const result = undoDrawnPoint(stopSnapshot.current, withUntrackedPoints(stopSnapshot.current, drawHistory));
    setDrawHistory(result.history);
    if (!result.removed) return;
    onChange(result.stops);
    setSelected(null); setHovered(null); clearTimeout(hoverTimer.current);
    setNotice(`${result.removed.name} 한 곳을 되돌렸어요.`);
  }, [drawHistory, onChange]);

  function resetCourse() {
    if (courseCompleted) return;
    onChange([]);
    setDrawHistory([]); setCourseCompleted(false);
    travel.reset();
    clearTimeout(hoverTimer.current); setSelected(null); setHovered(null);
    setNearPoint(null); setListLimit(30); setSideTab("route");
    setNotice("코스의 모든 지점과 경로를 초기화했어요.");
  }

  useEffect(() => {
    if (!map || !drawing) return;
    const addPoint = (event: { latLng: { getLat(): number; getLng(): number } }) => {
      const current = stopSnapshot.current;
      const lat = event.latLng.getLat(), lng = event.latLng.getLng();
      if (!drawOnly) setNearPoint({ stadium: stadium.code, lat, lng });
      setListArea(null); setListLimit(30); setSideTab("places");
      if (current.length >= MAX_ROUTE_STOPS) { setNotice("출발지를 포함해 최대 12개 지점까지 담을 수 있어요."); return; }
      const previous = current.at(-1);
      if (previous && Math.abs(previous.lat - lat) < .000001 && Math.abs(previous.lng - lng) < .000001) return;
      const placeId = `map:${createClientId()}`;
      if (!drawOnly) setNearPoint({ stadium: stadium.code, lat, lng, pointId: placeId });
      const name = drawOnly ? (current.length === 0 ? "출발 지점" : `동선 지점 ${current.length}`) : "";
      onChange([...current, { name, category: drawOnly ? "동선 지점" : "직접 지정", lat, lng, placeId, isMapPoint: true }]);
      setDrawHistory((history) => [...history, placeId]);
      setSelected(null); setHovered(null); clearTimeout(hoverTimer.current);
      setNotice(current.length ? "지점을 추가했어요. 우클릭하면 방금 찍은 지점만 되돌려요." : "출발지를 정했어요. 다음 지점을 좌클릭하세요.");
    };
    const canvas = mapNode.current;
    const suppressMenu = (event: MouseEvent) => event.preventDefault();
    maps.event.addListener(map, "click", addPoint);
    maps.event.addListener(map, "rightclick", undoPoint);
    canvas?.addEventListener("contextmenu", suppressMenu);
    return () => {
      maps.event.removeListener(map, "click", addPoint);
      maps.event.removeListener(map, "rightclick", undoPoint);
      canvas?.removeEventListener("contextmenu", suppressMenu);
    };
  }, [map, maps, drawing, onChange, undoPoint, stadium.code, drawOnly]);

  useEffect(() => {
    if (!map || !guideOrigin) return;
    const position = new maps.LatLng(guideOrigin.lat, guideOrigin.lng);
    map.setCenter(position);
    const button = document.createElement("button");
    button.type = "button";
    button.id = "planner-guide-origin";
    button.className = "planner-guide-origin";
    button.setAttribute("aria-label", "가이드가 지정한 출발점");
    const label = document.createElement("span");
    label.textContent = "출발점";
    button.appendChild(label);
    button.onclick = (event) => {
      event.stopPropagation(); maps.event.preventMap();
      const placeId = `map:${createClientId()}`;
      const next = renumberMapPoints([{ name: "", category: "직접 지정", lat: guideOrigin.lat, lng: guideOrigin.lng, placeId, isMapPoint: true }]);
      setCourseCompleted(false);
      stopSnapshot.current = next;
      onStopsChange(next);
      setDrawHistory([placeId]);
      setSelected(null); setHovered(null); clearTimeout(hoverTimer.current);
      setGuideOrigin(null);
      setNotice("출발지를 정했어요.");
      window.dispatchEvent(new CustomEvent(GUIDE_ORIGIN_SET));
    };
    const overlay = new maps.CustomOverlay({ map, position, content: button, yAnchor: .5, zIndex: 30, clickable: true });
    return () => overlay.setMap(null);
  }, [map, maps, guideOrigin, onStopsChange]);

  useEffect(() => {
    if (!map || !mapLocationPicking) return;
    const pick = (event: { latLng: { getLat(): number; getLng(): number } }) => {
      setMapLocation({ lat: event.latLng.getLat(), lng: event.latLng.getLng() });
      setMapLocationPicking(false);
      setMapLocationStatus("idle");
      setMapLocationMessage("지도에 내 위치 핀을 표시했어요.");
    };
    maps.event.addListener(map, "click", pick);
    return () => maps.event.removeListener(map, "click", pick);
  }, [map, maps, mapLocationPicking]);

  useEffect(() => {
    if (!map) return;
    const idleCursor = mapLocationPicking ? "crosshair" : drawing || travel.picking ? "default" : "grab";
    const dragStart = () => map.setCursor("grabbing");
    const dragEnd = () => map.setCursor(idleCursor);
    map.setCursor(idleCursor);
    maps.event.addListener(map, "dragstart", dragStart);
    maps.event.addListener(map, "dragend", dragEnd);
    return () => {
      maps.event.removeListener(map, "dragstart", dragStart);
      maps.event.removeListener(map, "dragend", dragEnd);
    };
  }, [map, maps, drawing, travel.picking, mapLocationPicking]);

  function completeCourse() {
    setCourseCompleted(true); setSideTab("route"); setNearPoint(null);
    clearTimeout(hoverTimer.current); setHovered(null); setSelected(null);
    setNotice("코스를 완성했어요. 코스 수정을 눌러야 다시 편집할 수 있어요.");
    fitCourse();
  }

  // Asking the chatbot for a new course releases the completed one so it can be rebuilt.
  useEffect(() => {
    if (!unlockRequest || !courseCompleted) return;
    let cancelled = false;
    void Promise.resolve().then(() => {
      if (cancelled) return;
      setCourseCompleted(false);
      setNotice("챗봇이 새 코스를 짜고 있어서 코스 완성을 풀었어요.");
    });
    return () => { cancelled = true; };
  }, [unlockRequest, courseCompleted]);

  // A course handed over by the chatbot is shown as already completed.
  useEffect(() => {
    if (!autoComplete || !canComplete || !map) return;
    let cancelled = false;
    void Promise.resolve().then(() => {
      if (cancelled) return;
      completeCourse();
      onAutoCompleted?.();
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- completeCourse reads the latest render; rerun only when the request or readiness changes
  }, [autoComplete, canComplete, map]);

  function fitCourse() {
    if (!map || !travel.points.length) return;
    clearTimeout(hoverTimer.current);
    setSelected(null); setHovered(null);
    const points = [...travel.points, ...(travel.data?.legs.flatMap((leg) => leg.paths.flat()) ?? [])];
    const coordinates = points.map(point => new maps.LatLng(point.lat, point.lng));
    const element = mapNode.current;
    if (!element || element.clientWidth < 100 || element.clientHeight < 100) return;
    map.relayout();
    const width = element.clientWidth, height = element.clientHeight;
    const left = 22, right = 22, top = 52, bottom = 24;
    const bounds = new maps.LatLngBounds();
    coordinates.forEach(point => bounds.extend(point));
    map.setBounds(bounds, top, right, bottom, left);
    // Use actual screen coordinates to find the closest zoom that fits the course.
    const measure = () => {
      const projected = coordinates.map(point => map.getProjection().containerPointFromCoords(point));
      return {
        minX: Math.min(...projected.map(p => p.x)), maxX: Math.max(...projected.map(p => p.x)),
        minY: Math.min(...projected.map(p => p.y)), maxY: Math.max(...projected.map(p => p.y)),
      };
    };
    let box = measure();
    while (map.getLevel() > 1 && (box.maxX - box.minX) * 2 <= width - left - right && (box.maxY - box.minY) * 2 <= height - top - bottom) {
      const level = map.getLevel();
      map.setLevel(level - 1);
      if (map.getLevel() === level) break;
      box = measure();
    }
    map.setCenter(map.getProjection().coordsFromContainerPoint({
      x: (box.minX + box.maxX) / 2 - (left - right) / 2,
      y: (box.minY + box.maxY) / 2 - (top - bottom) / 2,
    }));
  }

  const fitArea = useCallback((target: KakaoMap) => {
    const latDelta = NEARBY_RADIUS / 111195;
    const lngDelta = latDelta / Math.cos(stadium.lat * Math.PI / 180);
    const bounds = new maps.LatLngBounds();
    bounds.extend(new maps.LatLng(stadium.lat - latDelta, stadium.lng - lngDelta));
    bounds.extend(new maps.LatLng(stadium.lat + latDelta, stadium.lng + lngDelta));
    target.setBounds(bounds, 35, 30, 35, 30);
  }, [maps, stadium]);

  useEffect(() => {
    if (!mapNode.current) return;
    const element = mapNode.current;
    const target = new maps.Map(element, { center: new maps.LatLng(stadium.lat, stadium.lng), level: 6 });
    fitArea(target);
    // Apply the initial zoom synchronously, before the map is painted.
    target.setLevel(Math.max(1, target.getLevel() - 2));
    target.setCenter(new maps.LatLng(stadium.lat, stadium.lng));
    const frame = requestAnimationFrame(() => setMap(target));
    const circle = new maps.Circle({ map: target, center: new maps.LatLng(stadium.lat, stadium.lng), radius: NEARBY_RADIUS, strokeWeight: 2, strokeColor: "#4f82d8", strokeOpacity: .55, strokeStyle: "dash", fillColor: "#608eed", fillOpacity: .035 });
    const idle = () => setViewport((v) => v + 1);
    maps.event.addListener(target, "idle", idle);
    const observer = new ResizeObserver(() => { target.relayout(); idle(); });
    observer.observe(element);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); maps.event.removeListener(target, "idle", idle); circle.setMap(null); element.replaceChildren(); };
  }, [maps, stadium, fitArea]);

  useEffect(() => {
    if (!map || !mapLocation) return;
    const marker = document.createElement("span");
    marker.className = "planner-my-location-marker";
    marker.setAttribute("role", "img");
    marker.setAttribute("aria-label", "내 위치");
    const overlay = new maps.CustomOverlay({ map, position: new maps.LatLng(mapLocation.lat, mapLocation.lng), content: marker, xAnchor: .5, yAnchor: 1, zIndex: 20 });
    return () => overlay.setMap(null);
  }, [map, maps, mapLocation]);

  function showMyLocation() {
    if (!map || mapLocationStatus === "loading") return;
    if (mapLocationPicking) {
      setMapLocationPicking(false); setMapLocationStatus("idle"); setMapLocationMessage("내 위치 핀 지정을 취소했어요.");
      return;
    }
    if (!window.isSecureContext || !navigator.geolocation) {
      setMapLocationPicking(true); setMapLocationStatus("idle"); setMapLocationMessage("HTTP 주소에서는 자동 위치 확인이 제한돼요. 지도에서 현재 위치를 눌러 핀을 표시해 주세요.");
      return;
    }
    const request = ++mapLocationRequest.current;
    setMapLocationStatus("loading"); setMapLocationMessage("내 위치를 확인하고 있어요.");
    navigator.geolocation.getCurrentPosition(({ coords }) => {
      if (request !== mapLocationRequest.current) return;
      const location = { lat: coords.latitude, lng: coords.longitude };
      setMapLocation(location); setMapLocationStatus("idle"); setMapLocationMessage("");
      map.setCenter(new maps.LatLng(location.lat, location.lng));
    }, () => {
      if (request !== mapLocationRequest.current) return;
      setMapLocationPicking(true); setMapLocationStatus("idle"); setMapLocationMessage("자동 위치를 확인하지 못했어요. 지도에서 현재 위치를 눌러 핀을 표시해 주세요.");
    }, { enableHighAccuracy: true, timeout: 10_000, maximumAge: 0 });
  }

  useEffect(() => {
    const controller = new AbortController();
    collectNearbyPlaces(maps, stadium, controller.signal, () => preferred.current, (incoming, completed, failures) => {
      setPlaces((previous) => mergePlaces(previous, incoming));
      setProgress({ done: false, completed, failures });
    }).then(({ completed, failures }) => {
      if (!controller.signal.aborted) setProgress({ done: true, completed, failures });
    }).catch(() => { /* Aborted requests cannot update a different stadium. */ });
    return () => controller.abort();
  }, [maps, stadium, attempt]);

  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30_000);
    let active = true;
    fetchTourPlaces(stadium, fetch, controller.signal).then((result: TourResult) => {
      if (!active || controller.signal.aborted) return;
      setPlaces((previous) => mergePlaces(previous, result.places));
      setTour({ status: result.status, truncated: result.truncated });
    }).catch(() => { if (active) setTour({ status: "error", truncated: false }); })
      .finally(() => clearTimeout(timeout));
    return () => { active = false; clearTimeout(timeout); controller.abort(); };
  }, [stadium, tourAttempt]);

  const subcategoryLabel = (place: NearbyPlace) => place.kind === "food" ? place.cuisine : place.subcategory ?? place.category;
  const visible = useMemo(() => {
    const matching = places.filter((place) => {
      const choices = subcategories[place.kind];
      return (!choices?.length || choices.includes(subcategoryLabel(place))) && `${place.name} ${place.detail}`.toLowerCase().includes(query.trim().toLowerCase());
    });
    return drawOnly ? [] : visiblePlaces(matching, categories);
  }, [places, categories, subcategories, query, drawOnly]);
  // Keep the last requested area until the user applies a new area or resets it.
  // Map pins remain available when panning outside that area.
  const listedPlaces = activeNearPoint ? visible.filter((place) => distanceMeters(activeNearPoint, place) <= 70) : activeListArea ? visible.filter((place) =>
    place.lat >= activeListArea.south && place.lat <= activeListArea.north &&
    place.lng >= activeListArea.west && place.lng <= activeListArea.east
  ) : visible;
  const showCurrentArea = () => {
    if (!map) return;
    const bounds = map.getBounds();
    const sw = bounds.getSouthWest(), ne = bounds.getNorthEast();
    setNearPoint(null);
    setListArea({ stadium: stadium.code, south: sw.getLat(), north: ne.getLat(), west: sw.getLng(), east: ne.getLng() });
    setListLimit(30);
    setSideTab("places");
  };
  const tourCount = places.filter((place) => place.tourContentId).length;
  const loading = !progress.done || tour.status === "loading";
  const preview = hovered ?? selected;
  const selectedPlace = places.find((place) => preview && sameStop(place, preview));
  const currentSelection = selectedPlace ?? preview;
  const replacementIndex = (current: RouteStop[], place: RouteStop) => activeNearPoint?.pointId && distanceMeters(activeNearPoint, place) <= 70 ? current.findIndex(stop => (stop.visitId ?? stop.placeId) === activeNearPoint.pointId) : -1;
  const addCoursePlace = (place: RouteStop) => {
    if (courseCompleted) return;
    const current = stopSnapshot.current;
    const replacing = replacementIndex(current, place);
    if (replacing >= 0 && [current[replacing - 1], current[replacing + 1]].some(item => item && sameStop(item, place))) { setNotice("바로 앞뒤 지점과 같은 장소예요."); return; }
    if (replacing < 0 && current.length && sameStop(current[current.length - 1], place)) { setNotice("바로 직전 지점과 같은 장소예요. 다른 장소를 들른 뒤 다시 담을 수 있어요."); return; }
    if (replacing < 0 && current.length >= MAX_ROUTE_STOPS) { setNotice("코스에는 최대 12개 지점까지 담을 수 있어요."); return; }
    const stop: RouteStop = { visitId: createClientId(), name: place.name, lat: place.lat, lng: place.lng, category: place.category, placeId: place.placeId ?? `place:${createClientId()}`, tourContentId: place.tourContentId, address: place.address, ...(drawing ? { isDrawnPoint: true } : {}) };
    if (replacing >= 0) {
      const previous = current[replacing];
      onChange(current.map((item, index) => index === replacing ? { ...stop, isDrawnPoint: Boolean(previous.isMapPoint || previous.isDrawnPoint) } : item));
      setDrawHistory(history => history.map(id => id === (previous.visitId ?? previous.placeId) ? stop.visitId! : id));
      setNearPoint(null);
    } else {
      onChange([...current, stop]);
      if (drawing) setDrawHistory((history) => [...history, stop.visitId!]);
    }
    clearTimeout(hoverTimer.current); setHovered(null); setSelected(place); setSideTab("route");
    setNotice(replacing >= 0 ? `찍은 지점을 ${place.name}(으)로 변경했어요.` : `${place.name}을(를) 코스에 담았어요.`);
  };
  const selectPlace = useCallback((place: RouteStop) => {
    clearTimeout(hoverTimer.current); setHovered(null);
    setSelected(place);
    setNotice("");
    if (map) map.setCenter(new maps.LatLng(place.lat, place.lng));
  }, [map, maps]);

  useEffect(() => {
    if (!map) return;
    const overlays: KakaoOverlay[] = [];
    const overlay = (content: HTMLElement, lat: number, lng: number, zIndex: number, yAnchor = .5, clickable = drawing && !drawOnly) => {
      overlays.push(new maps.CustomOverlay({ map, position: new maps.LatLng(lat, lng), content, yAnchor, zIndex, clickable }));
    };
    const unselected = visible.filter((p) => !stops.some((stop) => sameStop(stop, p)));
    const stadiumPlace: RouteStop = { name: stadium.name, lat: stadium.lat, lng: stadium.lng, address: stadium.address, category: "야구장", placeId: `stadium:${stadium.code}` };
    if (!stops.some((stop) => sameStop(stop, stadiumPlace))) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "planner-stadium-marker";
      button.textContent = `⚾ ${stadium.name}`;
      button.setAttribute("aria-label", `야구장: ${stadium.name}`);
      button.onmouseenter = () => showPreview(stadiumPlace);
      button.onmouseleave = hidePreview;
      button.onfocus = () => showPreview(stadiumPlace);
      button.onblur = hidePreview;
      button.onclick = (event) => { event.stopPropagation(); maps.event.preventMap(); selectPlace(stadiumPlace); };
      button.oncontextmenu = (event) => { if (drawing) { event.preventDefault(); event.stopPropagation(); maps.event.preventMap(); undoPoint(); } };
      overlay(button, stadium.lat, stadium.lng, 8, 1, true);
    }
    const projection = map.getProjection();
    const positions = unselected.map((place) => ({
      place,
      point: projection.containerPointFromCoords(new maps.LatLng(place.lat, place.lng)),
    }));
    // Recompute screen-space crowding after zoom/pan; isolated places keep icons.
    const crowded = new Set(positions.filter((entry, index) => positions.some((other, otherIndex) =>
      index !== otherIndex && Math.hypot(entry.point.x - other.point.x, entry.point.y - other.point.y) < 40
    )).map(({ place }) => place.placeId));
    const makePin = (place: NearbyPlace) => {
      const focused = Boolean(selected && sameStop(place, selected));
      const compact = crowded.has(place.placeId) && !focused;
      const category = PLACE_CATEGORIES.find((c) => c.id === place.kind)!;
      const button = document.createElement("button");
      button.type = "button"; button.className = `planner-pin planner-pin-${place.kind}${focused ? " is-focused" : ""}${compact ? " planner-pin-dot" : ""}`;
      button.style.setProperty("--pin-color", category.color);
      button.setAttribute("aria-label", `${category.label}: ${place.name}`);
      if (drawOnly) { button.tabIndex = -1; button.setAttribute("aria-hidden", "true"); button.style.pointerEvents = "none"; }
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("aria-hidden", "true");
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path"); path.setAttribute("d", category.icon); svg.appendChild(path); button.appendChild(svg);
      button.onmouseenter = () => showPreview(place);
      button.onmouseleave = hidePreview;
      button.onfocus = () => showPreview(place);
      button.onblur = hidePreview;
      button.onclick = (event) => { event.stopPropagation(); maps.event.preventMap(); selectPlace(place); };
      button.oncontextmenu = (event) => { if (drawing) { event.preventDefault(); event.stopPropagation(); maps.event.preventMap(); undoPoint(); } };
      overlay(button, place.lat, place.lng, selected && sameStop(place, selected) ? 10 : 3);
    };
    unselected.forEach(makePin);
    for (const [index, stop] of stops.entries()) {
      const button = document.createElement("button"); button.type = "button"; button.className = `${stop.isMapPoint || stop.isDrawnPoint ? "planner-drawn-pin" : "planner-number-pin"}${selected && sameStop(stop, selected) ? " is-focused" : ""}`;
      const label = document.createElement("span"); label.textContent = coursePointLabel(stops, index, separateStart); button.appendChild(label);
      button.setAttribute("aria-label", `코스 ${coursePointLabel(stops, index, separateStart)}: ${stop.name}`);
      const isStadiumStop = stop.placeId === `stadium:${stadium.code}`;
      if (drawOnly && !isStadiumStop) { button.tabIndex = -1; button.setAttribute("aria-hidden", "true"); button.style.pointerEvents = "none"; }
      const place = places.find((p) => sameStop(p, stop)) ?? stop;
      button.onmouseenter = () => showPreview(place);
      button.onmouseleave = hidePreview;
      button.onfocus = () => showPreview(place);
      button.onblur = hidePreview;
      button.onclick = (event) => {
        event.stopPropagation(); maps.event.preventMap();
        if (isStadiumStop) { selectPlace(stop); return; }
        clearTimeout(hoverTimer.current); setHovered(null); setSelected(null);
        setNearPoint({ stadium: stadium.code, lat: stop.lat, lng: stop.lng, pointId: stop.visitId ?? stop.placeId });
        setListArea(null); setListLimit(30); setSideTab("places");
        setNotice(`주변 장소를 선택하면 ${coursePointLabel(stops, index, separateStart)}번 지점을 변경해요.`);
      };
      button.oncontextmenu = (event) => { if (drawing) { event.preventDefault(); event.stopPropagation(); maps.event.preventMap(); undoPoint(); } };
      overlay(button, stop.lat, stop.lng, 12, stop.isMapPoint || stop.isDrawnPoint ? 1 : .5, isStadiumStop || (drawing && !drawOnly));
    }
    return () => overlays.forEach((item) => item.setMap(null));
  }, [map, maps, visible, stops, selected, selectPlace, viewport, places, drawing, drawOnly, undoPoint, showPreview, hidePreview, stadium, separateStart]);

  function chooseCategory(next: CategoryFilter) {
    clearTimeout(hoverTimer.current); setHovered(null);
    preferred.current = next !== "all" && !categories.includes(next) ? next : "all";
    setCategories((current) => next === "all" ? (current.length === PLACE_CATEGORIES.length ? [] : PLACE_CATEGORIES.map((category) => category.id)) : current.includes(next) ? current.filter((category) => category !== next) : [...current, next]);
    if (next === "all" && !allSelected) setSubcategories({});
    setOpenCategory(null);
    setQuery(""); setListLimit(30); setSideTab("places"); setSelected(null); setNotice("");
  }
  function addStop() {
    if (currentSelection) addCoursePlace(currentSelection);
  }
  const canReplacePoint = Boolean(currentSelection && replacementIndex(stops, currentSelection) >= 0);
  const alreadyAdded = currentSelection && stops.length > 0 && sameStop(stops[stops.length - 1], currentSelection);
  const stadiumSelected = currentSelection?.placeId === `stadium:${stadium.code}`;

  return <div className={`nearby-planner${drawOnly ? " is-route-draw-only" : ""}${courseCompleted ? " is-course-completed" : ""}`}>
    {!drawOnly && <div ref={filtersNode} className="planner-filters" aria-label="장소 카테고리" onKeyDown={(event) => { if (event.key === "Escape") { setOpenCategory(null); filtersNode.current?.querySelector<HTMLButtonElement>(`[data-category-arrow="${openCategory}"]`)?.focus(); } }}>
      <button type="button" className="planner-filter" aria-pressed={allSelected} onClick={() => chooseCategory("all")}>전체</button>
      {PLACE_CATEGORIES.map((category) => {
        const categoryPlaces = places.filter((place) => place.kind === category.id);
        const options = [...new Set(categoryPlaces.map(subcategoryLabel))].sort((a, b) => a.localeCompare(b, "ko"));
        const choices = subcategories[category.id] ?? [];
        const active = categories.includes(category.id);
        const toggleSubcategory = (label: string) => {
          setSubcategories((previous) => {
            const values = previous[category.id] ?? [];
            return { ...previous, [category.id]: values.includes(label) ? values.filter((value) => value !== label) : [...values, label] };
          });
          setCategories((previous) => previous.includes(category.id) ? previous : [...previous, category.id]);
          preferred.current = category.id;
          setListLimit(30); setSelected(null); setSideTab("places");
        };
        return <div className="planner-category-group" key={category.id} style={{ "--pin-color": category.color } as CSSProperties}>
          <div className="planner-category-controls">
            <button type="button" className="planner-filter" aria-pressed={active} onClick={() => chooseCategory(category.id)}><CategoryIcon kind={category.id} />{category.label}<span>{categoryPlaces.length}</span></button>
            <button type="button" className="planner-category-arrow" data-category-arrow={category.id} aria-label={`${category.label} 소분류 선택`} aria-expanded={openCategory === category.id} aria-controls={`planner-subcategories-${category.id}`} onClick={() => setOpenCategory((current) => current === category.id ? null : category.id)}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg></button>
          </div>
          {openCategory === category.id && <div className="planner-subcategory-menu" id={`planner-subcategories-${category.id}`} role="group" aria-label={`${category.label} 소분류`}>
            <strong>{category.label} 소분류</strong>
            <button type="button" className="planner-subcategory-reset" onClick={() => { setSubcategories((previous) => ({ ...previous, [category.id]: [] })); setCategories((previous) => previous.includes(category.id) ? previous : [...previous, category.id]); setListLimit(30); setSelected(null); setSideTab("places"); }}>소분류 전체 보기</button>
            {options.length === 0 ? <p>조회된 장소가 없어요.</p> : options.map((label) => <label key={label}><input type="checkbox" checked={choices.includes(label)} onChange={() => toggleSubcategory(label)} /><span>{label}</span><small>{categoryPlaces.filter((place) => subcategoryLabel(place) === label).length}</small></label>)}
          </div>}
        </div>;
      })}
    </div>}
    <div className="planner-drawing-toolbar">
      <span id="drawing-help">{courseCompleted ? "코스가 완성되어 편집이 잠겼어요. 수정하려면 코스 수정을 눌러 주세요." : drawOnly ? "지도 빈 곳 클릭: 동선 지점 추가 · 우클릭: 마지막 지점 되돌리기" : "빈 곳 클릭: 지점 추가 · 장소 핀 클릭: 정보 확인 후 코스에 담기 · 우클릭: 마지막 지점 되돌리기"}</span><button type="button" className={`planner-draw-undo planner-course-toggle${courseCompleted ? " is-completed" : ""}`} aria-pressed={courseCompleted} disabled={!courseCompleted && !canComplete} onClick={() => { if (courseCompleted) { setCourseCompleted(false); setNotice("코스를 다시 수정할 수 있어요."); return; } completeCourse(); }}>{courseCompleted ? "코스 수정" : "코스 완성"}</button><button type="button" className="planner-draw-undo" disabled={courseCompleted || (stops.length === 0 && !travel.location && !travel.picking && !travel.locating)} onClick={resetCourse}><span aria-hidden="true">↶ </span>초기화</button>
    </div>
    <div className={`planner-workspace${drawOnly ? " planner-workspace-map-only" : ""}`}>
      <div className={`planner-map-stage${travel.picking ? " is-picking-start" : ""}${drawing ? " is-drawing-course" : ""}${drawOnly ? " is-route-draw-only" : ""}`}>
        {travel.picking && <div className="course-pick-hint">지도를 눌러 출발 위치를 지정하세요 <button type="button" onClick={travel.cancelPicking}>취소</button></div>}
        <div ref={mapNode} className="planner-map-canvas" aria-label={`${stadium.name} 주변 장소 지도`} aria-describedby={drawing ? "drawing-help" : undefined} />
        <div className="planner-map-view-actions">
          {!drawOnly && <button type="button" className="planner-current-area" disabled={!map} onClick={showCurrentArea}>지금 지도에서 보기</button>}
          <button type="button" className="planner-fit-course" aria-label="코스 전체 보기" title="코스 전체 보기" disabled={!map || !travel.points.length} onClick={fitCourse}><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 4H4v5m11-5h5v5M4 15v5h5m11-5v5h-5" /><path d="m9 15 3-6 3 6" /></svg></button>
        </div>
        <div className="planner-map-controls"><button type="button" title={mapLocationPicking ? "내 위치 핀 지정 취소" : "내 위치 표시"} aria-label={mapLocationPicking ? "내 위치 핀 지정 취소" : "내 위치 표시"} aria-pressed={mapLocationPicking} disabled={!map || mapLocationStatus === "loading" || travel.picking} onClick={showMyLocation}>⌖</button><button type="button" aria-label="지도 확대" onClick={() => map && map.setLevel(Math.max(1, map.getLevel() - 1))}>+</button><button type="button" aria-label="지도 축소" onClick={() => map && map.setLevel(Math.min(10, map.getLevel() + 1))}>−</button></div>
        {mapLocationMessage && <p className={`planner-location-feedback${mapLocationStatus === "error" ? " is-error" : ""}`} role={mapLocationStatus === "error" ? "alert" : "status"}>{mapLocationMessage}</p>}
        {currentSelection && <section className="planner-place-card" aria-label="선택한 장소" onMouseEnter={holdPreview} onMouseLeave={hidePreview}>
          <button type="button" className="planner-card-close" aria-label="장소 상세 닫기" onClick={() => { clearTimeout(hoverTimer.current); setSelected(null); setHovered(null); }}>×</button>
          <span className="planner-place-category">{selectedPlace?.subcategory ?? currentSelection.category}{selectedPlace ? ` · 구장에서 ${distanceLabel(selectedPlace.distance)}` : ""}</span><h3>{currentSelection.name}</h3>
          {currentSelection.tourContentId && <span className="planner-source-badge">{currentSelection.placeId?.startsWith("tour:") ? "한국관광공사" : "카카오 · 한국관광공사"}</span>}
          <p>{currentSelection.address || "주소 정보가 없는 장소예요."}</p>
          {selectedPlace?.phone && <p>{selectedPlace.phone}</p>}
          <div className="planner-card-actions">
            <a href={placeLink(currentSelection)} target="_blank" rel="noreferrer">{currentSelection.placeId && /^\d+$/.test(currentSelection.placeId) ? "카카오맵 상세 ↗" : "카카오맵 위치 보기 ↗"}</a>
            <div className="planner-card-course-actions">
              {stadiumSelected && <StadiumParkingMapDialog stadiumCode={stadium.code} className="planner-parking-map-trigger" />}
              <button type="button" disabled={courseCompleted || Boolean(alreadyAdded) || (!canReplacePoint && stops.length >= MAX_ROUTE_STOPS)} onClick={addStop}>{courseCompleted ? "코스 수정 후 담기" : alreadyAdded ? "바로 직전 지점이에요" : canReplacePoint ? "이 지점으로 지정" : stops.length >= MAX_ROUTE_STOPS ? "최대 12곳까지" : "+ 코스에 담기"}</button>
            </div>
          </div>
        </section>}
      </div>
      {!drawOnly && <aside className="planner-side">
        <div className="planner-side-tabs" role="tablist" aria-label="장소 목록"><button type="button" role="tab" id="nearby-places-tab" aria-controls="nearby-side-panel" aria-selected={sideTab === "places"} onClick={() => setSideTab("places")}>주변 장소 <b>{listedPlaces.length}</b></button><button type="button" role="tab" id="nearby-route-tab" aria-controls="nearby-side-panel" aria-selected={sideTab === "route"} onClick={() => setSideTab("route")}>내 코스 <b>{stops.length}</b></button></div>
        <div id="nearby-side-panel" role="tabpanel" aria-labelledby={sideTab === "places" ? "nearby-places-tab" : "nearby-route-tab"} className="planner-side-content">
          {sideTab === "route" ? <><CourseTravelPanel travel={travel} stops={stops} showDirections={courseCompleted} originReplacement={courseCompleted ? <div className="course-save" aria-busy={saving}>
            <label htmlFor="planner-course-name">코스 이름</label>
            <input id="planner-course-name" autoComplete="off" value={courseName} maxLength={80} disabled={saving} placeholder="코스 이름을 입력하세요" onChange={(event) => onCourseNameChange(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); if (canSaveCourse) void onSaveCourse(); } }} />
            <button type="button" className="course-save-button" disabled={!canSaveCourse} onClick={() => void onSaveCourse()}>{saving ? "저장 중…" : "코스 저장"}</button>
            <small>저장한 코스는 코스 둘러보기에 공개돼요.</small>
            {saveError && <p role="alert" className="course-save-error">{saveError}</p>}
          </div> : undefined} onFit={fitCourse} /><RouteStops stops={stops} onChange={onChange} separateStart={separateStart} readOnly={courseCompleted} steps={{ canUndo: observedHistory.past.length > 0, canRedo: observedHistory.future.length > 0, onUndo: stepBack, onRedo: stepForward }} onFocus={(stop) => selectPlace(places.find((p) => sameStop(stop, p)) ?? stop)} /></> : <>
            <label className="planner-search"><span className="sr-only">불러온 장소에서 찾기</span><input type="search" value={query} placeholder="불러온 장소에서 찾기" onChange={(event) => { setQuery(event.target.value); setListLimit(30); }} onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }} /></label>
            <div className="planner-list-caption"><span>{activeNearPoint ? "찍은 지점에서 가까운 순 · 직선거리" : "구장에서 가까운 순 · 직선거리"}{activeNearPoint && <small>찍은 지점 반경 70m 내 시설</small>}{activeListArea && <small>선택한 지도 범위 내 장소</small>}</span><button type="button" disabled={!activeListArea && !activeNearPoint} title="지도 범위 해제" aria-label="지도 범위 해제" onClick={() => { setListArea(null); setNearPoint(null); setListLimit(30); }}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 10a9 9 0 1 1 2 8M3 4v6h6" /></svg></button></div>
            {listedPlaces.length === 0 ? <div className="planner-empty"><strong>{loading ? "주변 장소를 찾고 있어요" : "조건에 맞는 장소가 없어요"}</strong><p>{loading ? "조회되는 장소부터 차례로 표시할게요." : activeNearPoint ? "이 지점의 70m 안에는 현재 조건에 맞는 시설이 없어요. 다른 지점을 누르거나 범위를 해제해 보세요." : activeListArea ? "다른 위치에서 ‘지금 지도에서 보기’를 누르거나 지도 범위를 해제해 보세요." : "다른 카테고리나 검색어로 살펴보세요."}</p></div> : <ul className="planner-place-list">{[...listedPlaces].sort((a, b) => activeNearPoint ? distanceMeters(activeNearPoint, a) - distanceMeters(activeNearPoint, b) : a.distance - b.distance).slice(0, listLimit).map((place) => <li key={place.placeId}><button type="button" aria-pressed={Boolean(selected && sameStop(place, selected))} onClick={() => selectPlace(place)}>
              <span className={`planner-list-icon planner-pin-${place.kind}`} style={{ "--pin-color": PLACE_CATEGORIES.find((c) => c.id === place.kind)!.color } as CSSProperties}><CategoryIcon kind={place.kind} /></span>
              <span className="planner-list-name"><strong>{place.name}</strong><small>{place.subcategory ?? place.category}{place.kind === "food" ? ` · ${place.cuisine}` : ""} · {distanceLabel(activeNearPoint ? distanceMeters(activeNearPoint, place) : place.distance)}</small>{place.tourContentId && <span className="planner-source-badge">관광공사</span>}</span><span className="planner-list-arrow">{stops.some((stop) => sameStop(stop, place)) ? "✓" : "›"}</span>
            </button></li>)}</ul>}
            {listedPlaces.length > listLimit && <button type="button" className="planner-more" onClick={() => setListLimit((n) => n + 30)}>장소 더 보기 ({Math.min(listLimit, listedPlaces.length)}/{listedPlaces.length})</button>}
          </>}
        </div>
      </aside>}
    </div>
    {!drawOnly && <><div className="planner-status" role="status"><span>{!progress.done && <span className="writer-spinner" aria-hidden="true" />}{progress.done ? `${places.length}곳 조회 · 현재 ${visible.length}곳 표시` : `주변 장소 불러오는 중 (${progress.completed}/${NEARBY_SEARCHES.length})`}{progress.failures > 0 ? ` · 일부 검색 실패 (${progress.failures})` : ""}</span>{progress.done && progress.failures > 0 && <button type="button" onClick={() => { setProgress({ done: false, completed: 0, failures: 0 }); setAttempt((a) => a + 1); }}>실패한 검색 다시 시도</button>}<span>{notice}</span></div>
    <div className="planner-tour-status" role="status"><span>{tour.status === "loading" ? "관광공사 장소도 찾고 있어요…" : tour.status === "unconfigured" ? "관광공사 장소는 아직 연결되지 않았어요." : tour.status === "error" ? "관광공사 장소를 불러오지 못했어요." : `관광공사 ${tourCount}곳 반영${tour.status === "partial" ? " · 일부 조회에 실패했어요." : ""}${tour.truncated ? " · 가까운 장소부터 일부 조회했어요." : ""}`}</span>{(tour.status === "error" || tour.status === "partial") && <button type="button" onClick={() => { setTour({ status: "loading", truncated: false }); setTourAttempt((value) => value + 1); }}>관광공사 다시 불러오기</button>}</div>
    <p className="planner-help">가까이 모인 장소는 작은 점으로 보여요. 확대해서 장소 사이가 벌어지면 카테고리 아이콘으로 바뀌어요. 점이나 아이콘을 눌러 장소를 확인하세요.<br />코스에 장소를 담으면 내 코스에서 실제 경로와 예상 이동 시간을 확인할 수 있어요.</p>
    <details className="planner-data-note"><summary>장소 표시 기준</summary><p>구장 중심에서 직선거리 2.5km 안의 카카오 검색 결과와 한국관광공사의 관광·문화·일부 레포츠 장소를 표시해요. 공원·산책길, 관광 명소, 박물관·전시관 등으로 분류하고 두 곳에 등록된 장소는 이름·주소·위치가 일치하는 경우 합쳐요. 이름이 다르게 등록된 장소는 중복될 수 있어요. 구장 주소·명칭·중심 인접 여부로 내부 시설을 제외하며, 실제 경계와 차이가 있을 수 있어요. 카테고리를 눌러 표시하거나 숨길 수 있고, 전체 버튼으로 모두 선택하거나 해제할 수 있어요. 선택한 분류에서 조회된 장소는 개수 제한 없이 지도에 모두 표시해요. 오른쪽 목록은 더 보기를 눌러 이어서 확인할 수 있어요. 제공되는 정보와 검색 결과 수에 제한이 있어 주변의 모든 장소가 포함되지는 않아요.</p></details></>}
  </div>;
}
