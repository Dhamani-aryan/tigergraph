import type { AnswerView } from "@/contracts";

/** A document counts as referenced when an evidence ref names its anchor (e.g. "R6"). */
export function documentReferenced(docId: string, answer: AnswerView): boolean {
  const anchor = docId.includes(":") ? docId.split(":").pop() ?? docId : docId;
  if (!anchor) return false;
  const re = new RegExp(`(^|[^A-Za-z0-9_])${anchor.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}($|[^A-Za-z0-9_])`);
  return answer.case.evidence.some((e) => re.test(e.ref) || e.ref === docId);
}
