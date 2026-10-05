import { Fragment, type ReactNode } from "react";
import type { ChatCoursePlace } from "@/lib/chat/types";

export function ChatPlaceSource({ place }: { place: ChatCoursePlace }) {
  if (!place.placeUrl) return null;
  const label = /^https:\/\/nol\.yanolja\.com\/stay\/domestic\/\d+$/.test(place.placeUrl) ? "야놀자" : "출처";
  return <a className="chat-place-source" href={place.placeUrl} target="_blank" rel="noopener noreferrer"
    aria-label={`${place.name} 장소 정보 출처 (새 창)`} title={`${place.name} 장소 정보 확인`}>{label} <span aria-hidden="true">↗</span></a>;
}

// Render a small, predictable Markdown subset as React elements. Model output is never HTML.
function conditionSource(part: string) {
  const match = /^\[(야놀자|메뉴 근거|후기 근거)\]\((https:\/\/[^\s<>"\\()]+)\)$/.exec(part);
  if (!match) return null;
  try {
    const url = new URL(match[2]);
    if (url.protocol !== "https:" || url.username || url.password || !url.hostname.includes(".")) return null;
    if (match[1] === "야놀자" && !/^https:\/\/nol\.yanolja\.com\/stay\/domestic\/\d+$/.test(match[2])) return null;
    return { label: match[1], url: match[2] };
  } catch {
    return null;
  }
}

function inlineText(text: string): ReactNode {
  return text.split(/(\*\*[^*\n]+\*\*|`[^`\n]+`|\[(?:야놀자|메뉴 근거|후기 근거)\]\(https:\/\/[^\s<>"\\()]+\))/g).map((part, index) => {
    const source = conditionSource(part);
    if (source) return <a key={index} className="chat-place-source" href={source.url} target="_blank" rel="noopener noreferrer"
      aria-label={`${source.label} 확인 (새 창)`}>{source.label} <span aria-hidden="true">↗</span></a>;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    return <Fragment key={index}>{part}</Fragment>;
  });
}

export function ChatAnswer({ text, places = [] }: { text: string; places?: ChatCoursePlace[] }) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  const cited = new Set<number>();
  const lineText = (line: string) => {
    const sources = places.flatMap((place, i) => {
      if (cited.has(i) || !place.placeUrl || !line.includes(place.name) || (place.time && !line.includes(place.time))) return [];
      cited.add(i);
      if (line.includes(`[야놀자](${place.placeUrl})`)) return [];
      return [<ChatPlaceSource key={i} place={place} />];
    });
    return <>{inlineText(line)}{sources}</>;
  };
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    if (/^\s*```/.test(line)) {
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !/^\s*```/.test(lines[index])) code.push(lines[index++]);
      index += 1;
      blocks.push(<pre key={blocks.length}><code>{code.join("\n")}</code></pre>);
      continue;
    }
    const heading = /^#{1,6}\s+(.+)$/.exec(line);
    if (heading) {
      blocks.push(<h3 key={blocks.length}>{inlineText(heading[1])}</h3>);
      index += 1;
      continue;
    }
    if (/^\s*(?:[-*+]\s+|\d+[.)]\s+)\S/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const matcher = ordered ? /^\s*\d+[.)]\s+(.+)$/ : /^\s*[-*+]\s+(.+)$/;
      const items: ReactNode[] = [];
      while (index < lines.length) {
        const match = matcher.exec(lines[index]);
        if (!match) break;
        items.push(<li key={items.length}>{lineText(match[1])}</li>);
        index += 1;
      }
      blocks.push(ordered ? <ol key={blocks.length}>{items}</ol> : <ul key={blocks.length}>{items}</ul>);
      continue;
    }
    const paragraph = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !/^\s*(?:#{1,6}\s|```|[-*+]\s|\d+[.)]\s)/.test(lines[index])) {
      paragraph.push(lines[index++]);
    }
    blocks.push(<p key={blocks.length}>{paragraph.map((part, lineIndex) => <Fragment key={lineIndex}>{lineIndex > 0 && <br />}{lineText(part)}</Fragment>)}</p>);
  }
  return <div className="chat-answer">{blocks}</div>;
}

