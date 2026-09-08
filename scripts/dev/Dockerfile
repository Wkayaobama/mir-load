# Ubuntu shell for mr-load on any OS with Docker (macOS, Windows, Linux).
#   docker build -t mrload-ubuntu -f scripts/dev/Dockerfile .
#   scripts/ubuntu_shell.sh          # opens bash with the repo mounted at /work
FROM ubuntu:24.04
COPY scripts/bootstrap_ubuntu.sh /tmp/bootstrap_ubuntu.sh
# strip CRLF in case the build context came from a Windows checkout without .gitattributes
RUN sed -i 's/\r$//' /tmp/bootstrap_ubuntu.sh && bash /tmp/bootstrap_ubuntu.sh --system-only && rm -rf /var/lib/apt/lists/*
WORKDIR /work
CMD ["bash"]
