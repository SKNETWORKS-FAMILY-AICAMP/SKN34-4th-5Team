"use client";

import { Children, cloneElement, isValidElement, useState, type ComponentProps } from "react";
import Link from "next/link";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import "@/styles/chat-answer.css";

import { safeChatUrl } from "@/lib/media-url";
export { safeChatUrl } from "@/lib/media-url";

function ChatImage({ src, alt, title }: ComponentProps<"img">) {
  const [failed, setFailed] = useState(false);
  const label = alt?.trim() || "참고 이미지";
  if (!src || failed) return <span className="chat-image-fallback" role="img" aria-label={label}>{label} · 이미지를 표시할 수 없어요.</span>;
  // Standard image keeps approved sources direct; no optimizer/proxy expands the trust boundary.
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt={label} title={title} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(true)} />;
}

export function ChatAnswer({ text }: { text: string }) {
  return <div className="chat-answer"><Markdown
    skipHtml
    remarkPlugins={[remarkGfm, remarkBreaks]}
    urlTransform={(url, key) => safeChatUrl(url, key === "src")}
    components={{
      h1: ({ children }) => <h3>{children}</h3>,
      h2: ({ children }) => <h3>{children}</h3>,
      h4: ({ children }) => <h3>{children}</h3>,
      h5: ({ children }) => <h3>{children}</h3>,
      h6: ({ children }) => <h3>{children}</h3>,
      p: ({ node, children }) => {
        const content = node?.children.filter(child => child.type !== "text" || child.value.trim());
        const link = content?.length === 1 && content[0].type === "element" && content[0].tagName === "a" ? content[0] : undefined;
        const href = typeof link?.properties.href === "string" ? safeChatUrl(link.properties.href) : undefined;
        const url = href ? new URL(href, "https://chat.invalid") : undefined;
        const detail = url && (href?.startsWith("/")
          ? (/^\/(?:standings\/(?:players\/[0-9]+|teams\/[A-Z0-9]+)|stadiums\/[A-Z]+|routes\/[A-Za-z0-9-]+)$/.test(url.pathname)
            || (["/community", "/community/teams"].includes(url.pathname) && url.searchParams.has("post")))
          : url.origin === "https://www.tving.com" && /^\/sports\/kbo\/(?:athlete\/[0-9]+|team\/[A-Z]+)$/.test(url.pathname));
        return <p>{detail ? Children.map(children, child => isValidElement<{ className?: string }>(child)
          ? cloneElement(child, { className: "chat-detail-link" }) : child) : children}</p>;
      },
      a: ({ href, children, title, className }) => !href ? <span>{children}</span> : href.startsWith("/")
        ? <Link href={href} title={title} className={className} prefetch={false}>{children}{className === "chat-detail-link" && <span aria-hidden="true"> →</span>}</Link>
        : <a href={href} title={title} className={className} target="_blank" rel="noopener noreferrer">{children}{className === "chat-detail-link" && <span aria-hidden="true"> →</span>}</a>,
      img: ({ src, alt, title }) => <ChatImage key={typeof src === "string" ? src : "blocked"} src={src} alt={alt} title={title} />,
      table: ({ children }) => <div className="chat-table-scroll" role="region" aria-label="답변 표" tabIndex={0}><table>{children}</table></div>,
    }}
  >{text}</Markdown></div>;
}
