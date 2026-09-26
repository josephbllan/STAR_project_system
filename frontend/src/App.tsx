import { Navigate, Route, Routes } from "react-router-dom";
import { AdminPage } from "./screens/AdminPage";
import { AuditPage } from "./screens/AuditPage";
import { CasesPage } from "./screens/CasesPage";
import { DatasetsPage } from "./screens/DatasetsPage";
import { DeferredPage } from "./screens/DeferredPage";
import { IndexPage } from "./screens/IndexPage";
import { LoginPage } from "./screens/LoginPage";
import { MfaEnrolPage } from "./screens/MfaEnrolPage";
import { MfaPage } from "./screens/MfaPage";
import { RequireSession } from "./screens/RequireSession";
import { ReviewPage } from "./screens/ReviewPage";
import { SearchPage } from "./screens/SearchPage";
import { SettingsPage } from "./screens/SettingsPage";
import { Shell } from "./screens/Shell";
import { WorkersPage } from "./screens/WorkersPage";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/login/mfa" element={<MfaPage />} />
      <Route element={<RequireSession />}>
        <Route element={<Shell />}>
          <Route path="/datasets" element={<DatasetsPage />} />
          <Route path="/workers" element={<WorkersPage />} />
          <Route path="/index" element={<Navigate to="/register-evidence" replace />} />
          <Route path="/register-evidence" element={<IndexPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/cases" element={<CasesPage />} />
          <Route path="/cases/:id" element={<CasesPage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="/account/mfa/enrol" element={<MfaEnrolPage />} />
          <Route path="/video" element={<DeferredPage name="Video" />} />
          <Route path="/analytics" element={<DeferredPage name="Analytics" />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/datasets" replace />} />
    </Routes>
  );
}
