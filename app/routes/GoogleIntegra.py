import os
from flask import Blueprint, session, redirect, request, jsonify
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from flask_cors import CORS


google_auth_bp = Blueprint("google_auth", __name__)

CLIENT_SECRETS_FILE = "client_secret.json"

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/generative-language.peruserquota",
    "https://www.googleapis.com/auth/generative-language.retriever",
    "https://www.googleapis.com/auth/drive.readonly"  # Added for debugging token refresh
]

@google_auth_bp.route("/api/auth/google/login")
def google_login():
    # Force clear any existing session to ensure a fresh login
    session.clear()
    
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri="http://localhost:5000/api/auth/google/callback"
    )

    # Use offline access and consent prompt to ensure account selection
    auth_url, state = flow.authorization_url(
        prompt="consent",
        access_type='offline',
        include_granted_scopes='true'
    )
    session["state"] = state

    return redirect(auth_url)


@google_auth_bp.route("/api/auth/google/callback")
def google_callback():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=session["state"],
        redirect_uri="http://localhost:5000/api/auth/google/callback"
    )

    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials

    if not creds.id_token:
        return jsonify({"error": "ID token missing from credentials"}), 500

    # Store token in session
    session["google_access_token"] = creds.token
    session["google_id_token"] = creds.id_token
    if creds.refresh_token:
        session["google_refresh_token"] = creds.refresh_token


    #  IMPORTANT: redirect to FRONTEND
    return redirect("http://localhost:3000/oauth/callback")

@google_auth_bp.route("/api/auth/google/session", methods=["GET"])
def get_google_session():
    token = session.get("google_access_token")

    if not token:
        return jsonify({"error": "Unauthorized"}), 401

    return jsonify({
        "access_token": token
    })



# @app.route('/ask')
# def ask_gemini():
#     if 'credentials' not in flask.session:
#         return flask.redirect('/login')

#     # Load credentials from session to authenticate the Gemini client
#     creds = Credentials(**flask.session['credentials'])
    
#     # Configure the Gemini API with the USER'S token
#     genai.configure(credentials=creds)
#     model = genai.GenerativeModel('gemini-1.5-flash')
    
#     response = model.generate_content("Give me a one-sentence tip for web development.")
#     return f"Gemini response using your account: {response.text}"


