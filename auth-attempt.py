import os
import msal

def exchange_code_for_token(tenant_id, client_id, client_secret, auth_code, redirect_uri):
    """
    Exchanges an authorization code for tokens using MSAL.
    """
    # The authority URL defines where the app signs in
    authority = f"https://login.microsoftonline.com/{tenant_id}"

    # Initialize the confidential client (for web apps with a secret)
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret,
    )

    # Exchange the authorization code for tokens
    # Note: Redirect URI must exactly match the one used to get the code
    result = app.acquire_token_by_authorization_code(
        code=auth_code,
        scopes=["https://graph.microsoft.com/.default"], # .default includes all granted permissions
        redirect_uri=redirect_uri
    )

    return result

# --- CONFIGURATION ---
TENANT_ID = os.getenv("MS_TENANT_ID", "common")  # or your specific Directory (tenant) ID
CLIENT_ID = os.getenv("MS_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("MS_REDIRECT_URI", "http://localhost:8000/api/auth/callback")
AUTHORIZATION_CODE = ""

# --- EXECUTION ---
token_response = exchange_code_for_token(TENANT_ID, CLIENT_ID, CLIENT_SECRET, AUTHORIZATION_CODE, REDIRECT_URI)

if "access_token" in token_response:
    print("Token exchange successful!")
    print(f"Access Token: {token_response['access_token'][:50]}...")
    # MSAL also provides account information and ID tokens if scopes included 'openid'
    if "id_token_claims" in token_response:
        print(f"User: {token_response['id_token_claims'].get('preferred_username')}")
else:
    print("Error exchanging code:")
    print(f"Error: {token_response.get('error')}")
    print(f"Description: {token_response.get('error_description')}")
