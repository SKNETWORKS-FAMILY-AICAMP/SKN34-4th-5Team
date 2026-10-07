import type { RouteDraftData } from "./route-draft";
import type { ChatContext, ChatCourse, ChatRoutePath } from "./chat/types";
import { courseToStops } from "./chat/course";
import { currentCourse } from "./chat/current-course";
import { activeLegModes } from "./course-directions";
import { renumberMapPoints } from "./drawn-course";
import { MAX_ROUTE_STOPS, sameStop } from "./nearby-places";
import { plainRichDoc } from "./community-rich-content";
import { routeContentToText } from "./route-content";

/** The writer owns one immutable course. Chat, map and storage consume projections of it. */
export type CourseState = {
  data: RouteDraftData;
  revision: number;
  originSource: "custom" | "current";
  autoFilled: { title?: string; content?: string };
};
export type CourseAction =
  | { type: "patch"; patch: Partial<RouteDraftData>; originSource?: CourseState["originSource"] }
  | { type: "stadium"; code: string }
  | { type: "clear" }
  | { type: "apply"; course: ChatCourse; stadiumCode: string; how: "replace" | "append" }
  | { type: "restore"; data: RouteDraftData }
  | { type: "undo"; before: CourseState; expectedRevision: number };

function normalize(data: RouteDraftData): RouteDraftData {
  let { stops, start } = data;
  const plannerMode = data.plannerMode ?? (stops.some(stop => stop.isMapPoint && stop.category === "동선 지점") ? "draw" : "places");
  // Migrate old embedded origins at the boundary. New edits always have one origin field.
  if (stops[0]?.placeId === "route:origin" || (!start && stops[0]?.isMapPoint)) {
    const first = stops[0];
    start = start ?? { lat: first.lat!, lng: first.lng!, name: first.name || "출발지" };
    stops = stops.slice(1);
  }
  stops = renumberMapPoints(stops.slice(0, MAX_ROUTE_STOPS), Boolean(start));
  return { ...data, plannerMode, stops, start, legModes: activeLegModes(start ? [start, ...stops] : stops, data.legModes ?? {}) };
}

export function createCourseState(data: RouteDraftData): CourseState {
  return { data: normalize(data), revision: 0, originSource: "custom", autoFilled: {} };
}

export function reduceCourse(state: CourseState, action: CourseAction): CourseState {
  let data = state.data, autoFilled = state.autoFilled, originSource = state.originSource;
  if (action.type === "undo") {
    if (action.expectedRevision !== state.revision) return state;
    return { ...action.before, revision: state.revision + 1 };
  }
  if (action.type === "restore") {
    data = action.data; autoFilled = {}; originSource = "custom";
  } else if (action.type === "stadium") {
    if (data.stadiumCode === action.code) return state;
    data = { ...data, stadiumCode: action.code, stops: [], start: undefined, legModes: {}, travelMode: "walk", plannerCompleted: false, chatCourseKey: undefined };
  } else if (action.type === "clear") {
    data = { ...data, stops: [], start: undefined, legModes: {}, travelMode: "walk", plannerCompleted: false };
  } else if (action.type === "patch") {
    data = { ...data, ...action.patch };
    originSource = action.originSource ?? originSource;
    if (action.patch.travelMode !== undefined && action.patch.travelMode !== state.data.travelMode && action.patch.legModes === undefined) data.legModes = {};
  } else if (action.type === "apply") {
    const { course, how, stadiumCode } = action;
    const sameStadium = stadiumCode === data.stadiumCode;
    const base = how === "append" && sameStadium ? data.stops : [];
    const incoming = courseToStops(course, undefined, data.stops).filter(stop => !base.some(previous => sameStop(previous, stop)));
    const start = how === "replace" && course.writerState ? course.writerState.origin ?? undefined
      : how === "replace" && course.origin ? course.origin : sameStadium ? data.start : undefined;
    const title = course.writerState?.title ?? course.title;
    const fillTitle = title !== undefined && ((!data.title.trim() && !data.chatCourseKey) || data.title === autoFilled.title);
    const fillContent = Boolean(course.content) && (!routeContentToText(data.content, data.contentFormat).trim() || (data.contentFormat === undefined && data.content === autoFilled.content));
    data = { ...data, stadiumCode, stops: [...base, ...incoming], start, plannerMode: "places",
      travelMode: course.travelMode ?? "walk",
      legModes: course.edit && sameStadium ? course.legModes ?? data.legModes ?? {} : how === "append" && sameStadium ? data.legModes ?? {} : {},
      plannerCompleted: course.writerState?.completed ?? true, chatCourseKey: course.writerKey,
      ...(fillTitle ? { title: title! } : {}),
      ...(fillContent ? { content: course.content!, contentFormat: undefined, contentDoc: plainRichDoc(course.content!) } : {}) };
    autoFilled = { ...autoFilled, ...(fillTitle ? { title } : {}), ...(fillContent ? { content: course.content } : {}) };
    originSource = "custom";
  }
  data = normalize(data);
  if (JSON.stringify(data) === JSON.stringify(state.data) && originSource === state.originSource) return state;
  const onlyTab = JSON.stringify({ ...data, tab: undefined }) === JSON.stringify({ ...state.data, tab: undefined }) && originSource === state.originSource;
  return { data, autoFilled, originSource, revision: state.revision + Number(!onlyTab) };
}

/** Route geometry is derived data and may only accompany the geometry it was calculated for. */
export function courseGeometryKey(data: RouteDraftData) {
  return JSON.stringify([data.stadiumCode, data.start, data.stops.map(p => [p.visitId, p.placeId, p.lat, p.lng]), data.travelMode, data.legModes ?? {}]);
}

export function courseContext(state: CourseState, routePath?: ChatRoutePath): ChatContext {
  const data = state.data;
  return { stadium: data.stadiumCode, intent: "route",
    currentCourse: currentCourse(data.stops, data.stadiumCode, data.travelMode, data.legModes ?? {}, data.start,
      { title: data.title, origin: data.start ?? null, completed: data.plannerCompleted ?? false }),
    ...(data.start ? { origin: data.start } : {}), ...(routePath ? { routePath } : {}) };
}
