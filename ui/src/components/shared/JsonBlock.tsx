import { useMemo, useState } from "react";
import { Check, Copy, Download } from "lucide-react";
import { Button } from "@/components/ui/primitives";
import { redact } from "@/lib/redact";

/** Pretty, redacted JSON with copy and download of the same redacted text. */
export function JsonBlock({ value, filename, maxHeight = "32rem", label }: { value: unknown; filename: string; maxHeight?: string; label: string }) {
  const text = useMemo(() => JSON.stringify(redact(value), null, 2) ?? "undefined", [value]);
  const redactions = useMemo(() => (text.match(/"\[REDACTED\]"/g) ?? []).length, [text]);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="overflow-hidden rounded-lg border border-ink-700 bg-ink-950">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-ink-700 px-3 py-2">
        <span className="font-mono text-[12px] text-zinc-400">{filename}</span>
        <div className="flex items-center gap-2">
          {redactions > 0 && <span className="text-[11px] text-amber-300">{redactions} credential-like value{redactions === 1 ? "" : "s"} redacted</span>}
          <Button size="sm" onClick={copy} aria-label={`Copy ${label}`}>
            {copied ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
            {copied ? "Copied" : "Copy"}
          </Button>
          <Button size="sm" onClick={download} aria-label={`Download ${label}`}>
            <Download className="h-3.5 w-3.5" aria-hidden /> Download
          </Button>
        </div>
      </div>
      <pre className="scroll-thin overflow-auto p-3 text-[12px] leading-5 text-zinc-300" style={{ maxHeight }} tabIndex={0} aria-label={label}>
        {text}
      </pre>
    </div>
  );
}
