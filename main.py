import base64
import os
import time
from datetime import datetime
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytz
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import email_list_parser

TIMEZONE = pytz.timezone("UTC")


def login(credential_file: str, account: str):
    """
    :param credential_file: a JSON file containing the application's credentials
    :param account: the account's email address, as a string
    :return: an authenticated Credentials object
    """
    scopes = ['https://mail.google.com/']
    credentials = None

    if os.path.exists(f'tokens/{account}'):
        credentials = Credentials.from_authorized_user_file(f'tokens/{account}', scopes)

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                credential_file, scopes)
            credentials = flow.run_console()
        with open(f'tokens/{account}', 'w') as token:
            token.write(credentials.to_json())

    return credentials


def parse_email_body(message, account_client, msg_id) -> MIMEBase:
    """
    :param message: the MessagePart as a JSON object
    :param account_client: an authenticated Credentials object
    :param msg_id: the ID of the message being parsed
    :return: a (potentially nested) MIME object
    """
    mime_type, mime_subtype = message.get("mimeType").split("/")

    if mime_type == "text":
        content = base64.urlsafe_b64decode(message['body']['data'].encode("ASCII")).decode("utf-8")
        _part = MIMEText(_text=content, _subtype=mime_subtype)

    elif message.get("filename"):
        if 'data' in message['body']:
            data = message['body']['data']
        else:
            attachment_id = message['body']['attachmentId']
            attachment = account_client.users() \
                .messages() \
                .attachments() \
                .get(userId='me',
                     messageId=msg_id,
                     id=attachment_id).execute()

            data = attachment['data']
        attachment_data = base64.urlsafe_b64decode(data.encode('UTF-8'))

        _part = MIMEBase(mime_type, mime_subtype)
        _part.set_payload(attachment_data)
        _part.add_header('Content-Disposition', 'attachment', filename=message['filename'])

    else:
        _part = MIMEBase(_maintype=mime_type, _subtype=mime_subtype)
        for subpart in message.get("parts"):
            _part.attach(parse_email_body(subpart, account_client, msg_id))

    return _part


def apply_forwarding_rule(account_client, account, email_list):
    """
    :param account_client: an authenticated Credentials object
    :param account: the account's email address
    :param email_list: a dictionary containing the to and cc addresses
    :return: None
    """

    try:
        result = account_client.users().messages().list(userId='me', labelIds=['UNREAD']).execute()
        unread_email_ids = result.get('messages', [])

        unread_emails = []

        # perform a batch email request, rather than 1 by 1
        batch = account_client.new_batch_http_request()
        for _id in map(lambda x: x.get("id"), unread_email_ids):
            request = account_client.users().messages().get(userId='me', format="full", id=_id)
            batch.add(request, callback=lambda ignore, x, y: unread_emails.append((x, y)))
        batch.execute()

        processed_ids = []
        for unread_email, exception in unread_emails:
            if exception:
                print("Error:", exception)
                continue

            try:
                # get subject
                subject = ""
                for header in unread_email.get("payload").get("headers"):
                    if header.get("name") == "Subject":
                        subject = header.get("value")

                _part = parse_email_body(unread_email.get("payload"), account_client, unread_email.get("id"))

                message = MIMEMultipart()
                message['from'] = account
                message['to'] = email_list.get("to")
                message['cc'] = ",".join(email_list.get("cc"))
                message['In-Reply-To'] = unread_email.get("id")
                message['References'] = unread_email.get("id")
                message['subject'] = subject
                message.attach(_part)

                reply_message = {'raw': base64.urlsafe_b64encode(message.as_string().encode()).decode(),
                                 'threadId': unread_email.get("threadId")}

                account_client.users().messages().send(userId="me", body=reply_message).execute()

                processed_ids.append(unread_email.get("id"))

                print(f"Replied email from {account} to {message.get('to')} with subject: {subject}")

            except RuntimeError:
                continue

        batch = account_client.new_batch_http_request()
        for _id in processed_ids:
            request = account_client.users().messages().modify(userId='me', id=_id,
                                                               body={'removeLabelIds': ['UNREAD']})
            batch.add(request)
        batch.execute()

    except HttpError as error:
        print(f'An error occurred: {error}')


def main():
    email_lists = email_list_parser.parse()

    # each email account for which there is a forwarding rule (specified in /email_lists/) needs a client object
    # to send requests
    clients = []

    # get list of all accounts we have forwarding rules for
    for account in email_lists.keys():
        try:
            creds = login("app_credentials.json", account)
            clients.append((account, build('gmail', 'v1', credentials=creds)))

            # if the user authenticated the wrong account
            if clients[-1][1].users().getProfile(userId='me').execute().get("emailAddress") != account:
                del clients[-1]
                raise ValueError

            print(f"Successfully authenticated {account}.")
        except ValueError as e:
            print(f"Failed to authorize {account}. Skipping the associated email list.")

    dtime = datetime.now(TIMEZONE)
    # shut off 5 minutes before midnight, let PythonAnywhere restart at midnight
    while not (dtime.hour == 23 and dtime.minute >= 55):
        dtime = datetime.now(TIMEZONE)

        for account, client in clients:
            apply_forwarding_rule(client, account, email_lists.get(account))

        time.sleep(5)


if __name__ == "__main__":
    main()
