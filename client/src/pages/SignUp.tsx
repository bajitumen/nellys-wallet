import { SignUp } from "@clerk/clerk-react";

export default function SignUpPage() {
  return (
    <div className="auth-page">
      <SignUp signInUrl="/sign-in" forceRedirectUrl="/" />
    </div>
  );
}
