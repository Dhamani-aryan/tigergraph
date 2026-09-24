import { HashRouter, Route, Routes, useLocation } from "react-router-dom";
import { useEffect } from "react";
import { DataProvider } from "@/data/DataProvider";
import type { DataClient } from "@/data/client";
import { AppShell } from "@/components/layout/AppShell";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { Overview } from "@/pages/Overview";
import { CaseDetail } from "@/pages/CaseDetail";
import { HowItWorks } from "@/pages/HowItWorks";
import { NotFound } from "@/pages/NotFound";

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);
  return null;
}

export function AppRoutes() {
  const location = useLocation();
  return (
    <AppShell>
      <ErrorBoundary label="This page" resetKey={location.pathname}>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/case/:caseId" element={<CaseDetail />} />
          <Route path="/how-it-works" element={<HowItWorks />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </ErrorBoundary>
    </AppShell>
  );
}

export default function App({ client }: { client?: DataClient }) {
  return (
    <DataProvider client={client}>
      <HashRouter>
        <ScrollToTop />
        <AppRoutes />
      </HashRouter>
    </DataProvider>
  );
}
