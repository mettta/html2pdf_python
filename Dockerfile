FROM ubuntu:24.04

# Install dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    python3 \
    python3-pip \
    python3-venv \
    sudo \
    vim \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Download and install Google Chrome
RUN wget -q -O google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && apt-get install -y ./google-chrome.deb \
    && rm google-chrome.deb

# Create a new non-root user and group.
# NOTE: It is important that a non-root user is used because otherwise the
# Chrome Driver fails with: "User data directory is already in use"
# https://github.com/SeleniumHQ/selenium/issues/15327#issuecomment-2688613182
RUN groupadd -r html2print && useradd -r -m -g html2print html2print

# Grant the new user sudo privileges.
RUN echo "html2print ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/html2print

# Create a virtual environment in the user's home directory.
RUN python3 -m venv /opt/venv

# Ensure the virtual environment is used by modifying the PATH.
ENV PATH="/opt/venv/bin:$PATH"

# Install StrictDoc. Set default StrictDoc installation from PyPI but allow
# overriding it with an environment variable.
ARG HTML2PRINT_SOURCE="pypi"
ENV HTML2PRINT_SOURCE=${HTML2PRINT_SOURCE}

RUN if [ "$HTML2PRINT_SOURCE" = "pypi" ]; then \
      pip install --no-cache-dir --upgrade pip && \
      pip install --no-cache-dir html2print; \
    else \
      pip install --no-cache-dir --upgrade pip && \
      pip install --no-cache-dir git+https://github.com/mettta/html2pdf_python.git@${HTML2PRINT_SOURCE}; \
    fi; \
    chmod -R 777 /opt/venv;

USER html2print

# Set the working directory to the user's home directory.
WORKDIR /data

ENTRYPOINT ["/bin/bash"]
