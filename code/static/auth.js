// Clerk JS bootstrap. The clerk-js script is loaded with `async` from the
// CDN; we wait for the `clerk:loaded` event (fired in _layout.html via
// onload on the script tag) and then mount whichever widgets the current
// page exposes via the placeholder divs.

window.addEventListener('clerk:loaded', async () => {
  if (typeof Clerk === 'undefined') return;
  try {
    await Clerk.load();
  } catch (err) {
    console.warn('Clerk failed to initialize', err);
    return;
  }

  // Sign-in / sign-up pages mount the matching widget into an empty div.
  const signInEl = document.getElementById('clerk-sign-in');
  if (signInEl) {
    Clerk.mountSignIn(signInEl, {
      // After a successful sign-in, send the user back to the dashboard;
      // the backend's /sign-in route doesn't itself do anything useful.
      afterSignInUrl: '/',
      afterSignUpUrl: '/',
    });
  }
  const signUpEl = document.getElementById('clerk-sign-up');
  if (signUpEl) {
    Clerk.mountSignUp(signUpEl, {
      afterSignInUrl: '/',
      afterSignUpUrl: '/',
    });
  }

  // The sidebar's user button (avatar + dropdown with sign-out).
  const userBtnEl = document.getElementById('clerk-user-button');
  if (userBtnEl && Clerk.user) {
    Clerk.mountUserButton(userBtnEl, {
      // Show username next to the avatar so the dropdown isn't a mystery icon.
      showName: true,
      // Where to send the user after they click "Sign out".
      afterSignOutUrl: '/sign-in',
    });
  }
});
