// clerk-js loads async; this file may run before or after — handle both paths
// and guard against double-init.

let clerkInited = false;

async function mountClerkWidgets() {
  if (clerkInited) return;
  if (typeof Clerk === 'undefined') return;
  clerkInited = true;
  try {
    await Clerk.load();
  } catch (err) {
    console.warn('Clerk failed to initialize', err);
    clerkInited = false;
    return;
  }

  const signInEl = document.getElementById('clerk-sign-in');
  if (signInEl) {
    Clerk.mountSignIn(signInEl, { afterSignInUrl: '/', afterSignUpUrl: '/' });
  }
  const signUpEl = document.getElementById('clerk-sign-up');
  if (signUpEl) {
    Clerk.mountSignUp(signUpEl, { afterSignInUrl: '/', afterSignUpUrl: '/' });
  }
  const userBtnEl = document.getElementById('clerk-user-button');
  if (userBtnEl && Clerk.user) {
    Clerk.mountUserButton(userBtnEl, {
      showName: false,
      afterSignOutUrl: '/sign-in',
    });
  }
}

mountClerkWidgets();
window.addEventListener('clerk:loaded', mountClerkWidgets);
