import { Fragment } from "react";

/** Small, text-only Markdown renderer. Raw HTML is always escaped by React. */
function inline(text: string) {
  return text.split(/(\[[^\]]+\]\(https?:\/\/[^\s)]+\)|https?:\/\/[^\s<>]+|\*\*[^*]+\*\*)/g).map((part, i) => {
    const markdown = part.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
    if (markdown) return <a href={markdown[2]} key={i} target="_blank" rel="noopener noreferrer">{markdown[1]}</a>;
    if (/^https?:\/\//.test(part)) return <a href={part} key={i} target="_blank" rel="noopener noreferrer">{part}</a>;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}
export function ReportContent({ text }: { text: string }) {
  return <div className="p-pad p-report">{text.split(/\n\s*\n/).map((block, i) => {
    const heading = block.match(/^#{1,6}\s+([^\n]+)(?:\n([\s\S]*))?$/);
    if (heading) return <section key={i}><h3>{inline(heading[1])}</h3>{heading[2] && <p>{inline(heading[2])}</p>}</section>;
    return <p key={i}>{inline(block)}</p>;
  })}</div>;
}
