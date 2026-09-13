# Ubuntu shell for mr-load on any OS with Docker (macOS, Windows, Linux).
#   scripts/ubuntu_shell.sh          # builds this image once, opens bash with the repo mounted at /work
# The venv is baked into the image at /opt/venv (not on the bind mount: fast on
# Windows/macOS and survives `docker run --rm`); .mrload/ state stays on the mount.
FROM ubuntu:24.04
COPY scripts/bootstrap_ubuntu.sh /tmp/bootstrap_ubuntu.sh
# strip CRLF in case the build context came from a Windows checkout without .gitattributes
RUN sed -i 's/\r$//' /tmp/bootstrap_ubuntu.sh && bash /tmp/bootstrap_ubuntu.sh --system-only && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-dev.txt /tmp/
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install -q --upgrade pip && /opt/venv/bin/pip install -q -r /tmp/requirements-dev.txt
RUN git config --global --add safe.directory '*'
ENV MRLOAD_VENV=/opt/venv PATH=/opt/venv/bin:$PATH
WORKDIR /work
CMD ["bash"]
