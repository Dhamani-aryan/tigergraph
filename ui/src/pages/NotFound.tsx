import { Link } from "react-router-dom";
import { Compass } from "lucide-react";

export function NotFound({ title = "Page not found", detail = "This address does not match any page in the console." }: { title?: string; detail?: string }) {
  return (
    <div className="mx-auto flex max-w-lg flex-col items-center gap-3 py-20 text-center">
      <Compass className="h-10 w-10 text-tg-400" aria-hidden />
      <h1 className="text-xl font-semibold text-white">{title}</h1>
      <p className="text-sm text-zinc-400">{detail}</p>
      <Link to="/" className="mt-2 rounded-lg bg-tg-500 px-4 py-2 text-sm font-semibold text-ink-950 hover:bg-tg-400">
        Back to the overview
      </Link>
    </div>
  );
}
