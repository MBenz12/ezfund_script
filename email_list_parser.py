import logging
import os
from typing import *
from email.utils import parseaddr

logger = logging.getLogger()


def validate_email(email: str) -> bool:
    addr = parseaddr(email)
    return not (addr[0] == '' and addr[1] == '')


def parse():
    email_lists = {}

    files = os.listdir("email_lists")

    for file in files:

        with open("email_lists/" + file, "r") as list_file:
            lines = list(map(lambda x: x.strip(), list_file.readlines()))

        if not (lines[0] == "ACCOUNT" and lines[2] == "TO" and lines[4] == "CC"):
            logger.error(f"Email list invalid! Check the formatting of {file}.\nSkipping this email list.")
            continue

        email_lists[lines[1]] = {"to": lines[3], "cc": lines[5:]}

        for email in [lines[1], lines[3]] + lines[5:]:
            if not validate_email(email):
                logger.error(f"Invalid email! Check the email {email}.\nSkipping this email list.")
                continue

    return email_lists
