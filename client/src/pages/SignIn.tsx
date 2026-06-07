import { SignIn } from "@clerk/clerk-react";

export default function SignInPage() {
  return (
    <div className="auth-page">
      <SignIn signUpUrl="/sign-up" forceRedirectUrl="/" />
    </div>
  );
}
