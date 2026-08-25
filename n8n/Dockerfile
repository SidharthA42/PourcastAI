# Bakes python3 + this project's requirements into the n8n image AT BUILD TIME.
#
# Uses the -debian tag deliberately: n8n's default `latest` tag (as of the
# v2.x image line) ships without ANY package manager - no apk, no apt-get,
# nothing (see https://github.com/n8n-io/n8n/issues/23603, an open n8n-side
# issue, not something fixable from our end). The -debian tag still has
# apt-get, so that's what we build from here.
#
# We also install as root at BUILD time, then hand back to the image's own
# non-root 'node' user before running n8n normally - installing at container
# *startup* fails silently under the non-root user and crash-loops, which is
# the "Restarting (1)" behavior seen before this fix.
FROM n8nio/n8n:latest-debian

USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --break-system-packages --no-cache-dir -r /tmp/requirements.txt

USER node
