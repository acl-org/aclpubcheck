import os.path

import google_auth_oauthlib.flow
import google.auth.transport.requests
import google.oauth2.credentials
import googleapiclient.discovery


def sheets_service():
    """Loads credentials and opens a Google Sheets API client.

     A credentials.json file should be in the current directory.
     https://developers.google.com/workspace/guides/create-credentials
     A token.json file will be written to the current directory to avoid
     repeatedly asking the user to login.

    :return: the Google Sheets API client
    """
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    creds = None
    if os.path.exists('token.json'):
        creds = google.oauth2.credentials.Credentials.from_authorized_user_file(
            'token.json', scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(google.auth.transport.requests.Request())
        else:
            iaf = google_auth_oauthlib.flow.InstalledAppFlow
            flow = iaf.from_client_secrets_file('credentials.json', scopes)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return googleapiclient.discovery.build('sheets', 'v4', credentials=creds)
