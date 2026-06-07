import { Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "./components/Layout";
import { AuthGate } from "./components/AuthGate";
import PlanningPage from "./pages/Planning";
import BudgetPage from "./pages/Budget";
import RulesPage from "./pages/Rules";
import IncomePage from "./pages/Income";
import SpendingPage from "./pages/Spending";
import DashboardPage from "./pages/Dashboard";
import SignInPage from "./pages/SignIn";
import SignUpPage from "./pages/SignUp";
import PlaidSetupPage from "./pages/PlaidSetup";
import PlaidFaqPage from "./pages/PlaidFaq";

export default function App({ clerkEnabled }: { clerkEnabled: boolean }) {
  const guarded = (node: React.ReactNode) =>
    clerkEnabled ? <AuthGate>{node}</AuthGate> : node;

  return (
    <Routes>
      <Route path="/sign-in/*" element={<SignInPage />} />
      <Route path="/sign-up/*" element={<SignUpPage />} />
      <Route path="/settings/plaid" element={guarded(<PlaidSetupPage />)} />
      <Route path="/settings/plaid/faq" element={<PlaidFaqPage />} />
      <Route element={guarded(<Layout />)}>
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
