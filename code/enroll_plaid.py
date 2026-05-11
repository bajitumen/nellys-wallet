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

import plaid
from plaid.api import plaid_api
from plaid.model.country_code import CountryCode
from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products

import config  # noqa: F401 — side-effect import: loads .env and validates required vars
from db import SessionLocal, init_db
from models import PlaidItem, User

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


def save_item(client: plaid_api.PlaidApi, public_token: str) -> None:
    exchange_response = client.item_public_token_exchange(
        ItemPublicTokenExchangeRequest(public_token=public_token)
    )
    access_token = exchange_response.access_token
    plaid_item_id = exchange_response.item_id

    institution_name = None
    try:
        item_resp = client.item_get(ItemGetRequest(access_token=access_token))
        institution_id = getattr(item_resp.item, "institution_id", None)
        if institution_id:
            inst_resp = client.institutions_get_by_id(
                InstitutionsGetByIdRequest(
                    institution_id=institution_id,
                    country_codes=[CountryCode("US")],
                )
            )
            institution_name = inst_resp.institution.name
    except plaid.ApiException:
        pass

    with SessionLocal() as session:
        user = current_user(session)
        item = PlaidItem(
            user_id=user.id,
            plaid_item_id=plaid_item_id,
            institution_name=institution_name,
        )
        item.set_access_token(access_token)
        session.add(item)
        session.commit()
        print(f"Saved PlaidItem id={item.id}  institution={institution_name or '?'}")


def make_handler(client: plaid_api.PlaidApi, html: str):
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
                save_item(client, public_token)
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
        creds = user.get_plaid_credentials()
        if not creds:
            raise SystemExit("User has no Plaid credentials configured.")
        plaid_client_id, plaid_secret = creds
        user_id_for_link = f"user-{user.id}"

    configuration = plaid.Configuration(
        host=plaid.Environment.Production,
        api_key={"clientId": plaid_client_id, "secret": plaid_secret},
    )
    client = plaid_api.PlaidApi(plaid.ApiClient(configuration))

    link_request = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id=user_id_for_link),
        client_name="Nelly's Wallet",
        products=[Products("transactions")],
        optional_products=[Products("investments")],
        country_codes=[CountryCode("US")],
        language="en",
    )
    link_response = client.link_token_create(link_request)
    link_token = link_response.link_token
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
