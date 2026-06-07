import { Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "./components/Layout";
import PlanningPage from "./pages/Planning";
import BudgetPage from "./pages/Budget";
import RulesPage from "./pages/Rules";
import IncomePage from "./pages/Income";
import SpendingPage from "./pages/Spending";
import DashboardPage from "./pages/Dashboard";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/spending" element={<SpendingPage />} />
        <Route path="/income" element={<IncomePage />} />
        <Route path="/budget" element={<BudgetPage />} />
        <Route path="/planning" element={<PlanningPage />} />
        <Route path="/rules" element={<RulesPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
