"""
Run this script to link a bank or brokerage via Plaid Link.
Opens a browser. On success, creates a PlaidItem row in the DB
with the access token Fernet-encrypted at rest.

Pre-Clerk: attaches to the placeholder user (first user in the DB).
Post-Clerk: will attach to the authenticated Clerk user.
"""

import http.server
import json
import webbrowser
from urllib.parse import parse_qs, urlparse

import config  # noqa: F401 — side-effect import: loads .env and validates required vars
import plaid_link
from db import SessionLocal, init_db
from models import User
from providers import plaid_client_for

PORT = 8766


def current_user(session):
    return session.query(User).first()


def build_html(link_token: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <title>Plaid Link</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      background: #fafafa;
      color: #1a1a1a;
      height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      text-align: center;
    }}
    .container {{ max-width: 400px; padding: 2rem; }}
    h2 {{ font-weight: 500; font-size: 1.4rem; margin-bottom: 0.75rem; }}
    #status {{ color: #666; font-size: 0.95rem; line-height: 1.5; }}
  </style>
</head>
<body>
  <div class="container">
    <h2>Connect a Bank or Brokerage</h2>
    <p id="status">Opening Plaid Link...</p>
    <button id="another" onclick="handler.open()" style="display:none;margin-top:1.5rem;padding:0.6rem 1.4rem;font-family:inherit;font-size:0.9rem;color:#fff;background:#1a1a1a;border:none;border-radius:6px;cursor:pointer;">Connect Another</button>
  </div>
  <script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
  <script>
    let count = 0;
    const handler = Plaid.create({{
      token: "{link_token}",
      onSuccess: function(public_token, metadata) {{
        count++;
        document.getElementById("status").textContent = "Linking account...";
        fetch("/exchange?public_token=" + encodeURIComponent(public_token))
          .then(r => r.json())
          .then(data => {{
            document.getElementById("status").textContent =
              count + (count === 1 ? " institution" : " institutions") + " linked.";
            document.getElementById("another").style.display = "inline-block";
          }});
      }},
      onExit: function(err, metadata) {{
        if (err) {{
          document.getElementById("status").textContent =
            "Error: " + err.error_code + " - " + (err.display_message || err.error_message);
        }} else if (count === 0) {{
          document.getElementById("status").textContent =
            "Cancelled. Refresh to try again.";
        }}
      }}
    }});
    handler.open();
  </script>
</body>
</html>"""


def make_handler(client, html: str):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/exchange":
                qs = parse_qs(parsed.query)
                public_token = qs.get("public_token", [None])[0]
                if not public_token:
                    self.send_response(400)
                    self.end_headers()
                    return
                with SessionLocal() as session:
                    user = current_user(session)
                    item = plaid_link.exchange_and_save(
                        client, session, user, public_token
                    )
                    print(f"Saved PlaidItem id={item.id}  "
                          f"institution={item.institution_name or '?'}")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode())
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode())

        def log_message(self, _format, *_args):
            pass

    return Handler


def main() -> None:
    init_db()
    with SessionLocal() as session:
        user = current_user(session)
        if user is None:
            raise SystemExit("No user in DB. Run: python code/cli.py seed-me")
        try:
            client = plaid_client_for(user)
        except ValueError as e:
            raise SystemExit(str(e))
        link_token = plaid_link.create_link_token(client, user)
        user_id_for_link = f"user-{user.id}"

    print(f"Link token created: {link_token[:20]}...")
    html = build_html(link_token)
    handler_cls = make_handler(client, html)

    print(f"Opening Plaid Link in your browser (user id={user_id_for_link})...")
    print("Connect institutions, click 'Connect Another' for each, Ctrl+C when done.")
    server = http.server.HTTPServer(("localhost", PORT), handler_cls)
    webbrowser.open(f"http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDone. Run 'python code/app.py' to see your dashboard.")


if __name__ == "__main__":
    main()
