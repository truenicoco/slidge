#!/usr/bin/env python3

"""
A script to download and extract the latest docs artifact from builds.sr.ht

First CLI arg is extraction target dir.
"""

import io
import os
import re
import tarfile
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

BASE_URL = "https://builds.sr.ht"
USER = "nicoco"
REPO = "slidge"
BRANCH = "master"
JOB_DEFINITION = "ci.yml"
ARTIFACT_FILENAME = "slidge-docs.tar.gz"
FILE_NAME = "slidge-docs.tar.gz"
CACHE_FILE = Path(os.getenv("HOME")) / ".latest_slidge_docs"
DESTINATION = Path(sys.argv[1])

feed_url = f"{BASE_URL}/~{USER}/{REPO}/commits/{BRANCH}/{JOB_DEFINITION}/rss.xml"

feed_string = requests.get(feed_url).content.decode()

feed_tree = ET.ElementTree(ET.fromstring(feed_string))

job_url = feed_tree.find("channel").find("item").find("link").text

try:
    if job_url == CACHE_FILE.read_text():
        print("Nothing new, exiting")
        exit(0)
except FileNotFoundError:
    pass

for f in DESTINATION.glob("**/*.html"):
    f.unlink()

job_html = requests.get(job_url).content.decode()

artifact_url = re.search(f"https://.*{FILE_NAME}", job_html).group(0)

artifact_bytes = requests.get(artifact_url).content

with tarfile.open(fileobj=io.BytesIO(artifact_bytes), mode="r:gz") as tar:
    tar.extractall(DESTINATION)

CACHE_FILE.write_text(job_url)
