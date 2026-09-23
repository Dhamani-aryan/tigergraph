import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { defaultClient, type DataBundle, type DataClient } from "./client";
import { buildRows, type CaseRow } from "./diagnostics";

type State =
  | { status: "loading" }
  | { status: "ready"; bundle: DataBundle; rows: CaseRow[] }
  | { status: "error"; message: string };

const DataContext = createContext<State>({ status: "loading" });

export function DataProvider({ client = defaultClient, children }: { client?: DataClient; children: ReactNode }) {
  const [state, setState] = useState<State>({ status: "loading" });
  useEffect(() => {
    let live = true;
    client
      .loadBundle()
      .then((bundle) => live && setState({ status: "ready", bundle, rows: buildRows(bundle) }))
      .catch((error: unknown) => live && setState({ status: "error", message: error instanceof Error ? error.message : String(error) }));
    return () => {
      live = false;
    };
  }, [client]);
  const value = useMemo(() => state, [state]);
  return <DataContext.Provider value={value}>{children}</DataContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useData(): State {
  return useContext(DataContext);
}
