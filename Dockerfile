# BountyHound Local - Vast.ai Optimized Docker Image
# Base: vllm/vllm-openai (includes CUDA, PyTorch, vLLM)
# Build: docker build -t bountyhound-local .
# Push:  docker push yourusername/bountyhound-local:latest

FROM vllm/vllm-openai:latest

LABEL maintainer="BountyHound Local"
LABEL description="Autonomous bug bounty hunting swarm for H100 NVL GPUs (1-2 GPU)"

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    redis-server \
    nmap \
    golang-go \
    curl \
    wget \
    git \
    jq \
    chromium-browser \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpangocairo-1.0-0 \
    libgtk-3-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Go recon tools
ENV GOPATH=/root/go
ENV PATH=$PATH:/root/go/bin
RUN go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest && \
    go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest && \
    go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest

# Set workspace
WORKDIR /workspace/bountyhound-local

# Copy project files
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir bountyhound huggingface_hub[cli] ray[default]

# Install Playwright chromium
RUN playwright install chromium --with-deps 2>/dev/null || python -m playwright install chromium

# Copy full project
COPY . .

# Create directories
RUN mkdir -p /workspace/models /workspace/data /workspace/bounty-findings \
    /workspace/redis-data logs pids

# Environment defaults for Vast.ai
ENV HF_HOME=/workspace/models
ENV BHL_DB_PATH=/workspace/data/bountyhound.db
ENV BHL_VAST_AI=1
ENV PYTHONPATH=/workspace/bountyhound-local
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

# Make scripts executable
RUN chmod +x scripts/*.sh install.sh

# Expose ports
EXPOSE 8000 5555 8100 8101 8102 8103 8104 6379

# On-start entrypoint
COPY scripts/vast-onstart.sh /vast-onstart.sh
RUN chmod +x /vast-onstart.sh

# Default: start all services
CMD ["/bin/bash", "/vast-onstart.sh"]
