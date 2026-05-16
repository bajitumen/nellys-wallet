// Clerk JS bootstrap.
//
// The clerk-js script is loaded with `async`, so its finish-load could
// happen either before or after this file runs. Handle both:
//   - if Clerk is already defined when we get here, mount immediately;
//   - otherwise listen for the `clerk:loaded` event that the script tag's
//     onload handler dispatches.
// Guard against double-init in case both paths fire.

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
      // Just the avatar — no name; matches the icon-only refresh / + buttons.
      showName: false,
      afterSignOutUrl: '/sign-in',
    });
  }
}

// Try right now in case clerk-js already loaded.
mountClerkWidgets();
// Also listen for the script's onload event in case it hadn't yet.
window.addEventListener('clerk:loaded', mountClerkWidgets);
