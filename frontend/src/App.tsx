import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router";
import { Layout } from "./components/Layout";
import { EmptyState } from "./components/ui";
import { LiveProvider } from "./hooks/live";
import { PreferencesProvider } from "./hooks/preferences";
import { ToastProvider } from "./hooks/toast";
import { ApiError } from "./lib/api";
import { AlertsPage } from "./pages/Alerts";
import { DataQualityPage } from "./pages/DataQuality";
import { DefectDetailPage } from "./pages/DefectDetail";
import { DefectsPage } from "./pages/Defects";
import { EquipmentDetailPage } from "./pages/EquipmentDetail";
import { EquipmentPage } from "./pages/Equipment";
import { HistoricalPage } from "./pages/Historical";
import { OverviewPage } from "./pages/Overview";
import { RunsPage } from "./pages/Runs";
import { SettingsPage } from "./pages/Settings";

export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        refetchOnWindowFocus: true,
        // Don't hammer a backend that is down or retry client errors.
        retry: (count, err) => (err instanceof ApiError && err.status >= 400 && err.status < 500 ? false : count < 2),
        retryDelay: (n) => Math.min(1000 * 2 ** n, 8000),
      },
    },
  });
}

export function AppRoutes() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/equipment" element={<EquipmentPage />} />
        <Route path="/equipment/:id" element={<EquipmentDetailPage />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/defects" element={<DefectsPage />} />
        <Route path="/defects/:id" element={<DefectDetailPage />} />
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/data-quality" element={<DataQualityPage />} />
        <Route path="/historical" element={<HistoricalPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<EmptyState title="Page not found">Use the navigation to choose a page.</EmptyState>} />
      </Routes>
    </Layout>
  );
}

const client = createQueryClient();

export default function App() {
  return (
    <QueryClientProvider client={client}>
      <PreferencesProvider>
        <ToastProvider>
          <LiveProvider>
            <BrowserRouter>
              <AppRoutes />
            </BrowserRouter>
          </LiveProvider>
        </ToastProvider>
      </PreferencesProvider>
    </QueryClientProvider>
  );
}
